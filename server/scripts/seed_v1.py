"""Seed v1 data: advertisers, products, campaigns, personas, persona_scripts.

Idempotent — deterministic UUIDs (uuid5) are derived from stable string keys
(org_id, sku, slug) so re-running the script upserts existing rows
rather than duplicating them.

Persona avatars referenced in ``seed/personas/*.json`` point at *source*
URLs (e.g. ``https://i.pravatar.cc/...``). On seed, the script downloads
each one and re-uploads it to Tigris under a UUID-keyed path
(``personas/{persona_id}/avatar.{ext}``) with ``public-read`` ACL — the
row in ``personas.avatar_image_url`` then carries the Tigris URL the
consumer feed serves to clients. The key is keyed on the persona UUID
rather than the slug so the URL is *capability-based*: anyone with the
URL can fetch it (the bucket allows public read), but a slug
list-attack ("did the team seed a persona named foo-bar?") can't
enumerate the bucket. If the S3 env vars are unset, the upload step is
skipped and the source URL is persisted unchanged (useful for purely
local DB-only smoke runs).

Usage:
    cd server
    DATABASE_URL=postgres://... uv run python scripts/seed_v1.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg

# Path-insertion has to happen before importing any soshop_server / soshop
# module — this script lives in `server/scripts/` and is run with
# `uv run python scripts/seed_v1.py`, which puts the script's directory
# on sys.path but not the package src/ directory.
_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from soshop_server.assets.client import s3_client  # noqa: E402
from soshop_server.assets.service import public_url_for_key, upload_bytes  # noqa: E402
from soshop_server.core.config import settings  # noqa: E402

logger = logging.getLogger("seed_v1")

# Stable namespace for deriving deterministic UUIDs from logical keys.
# Changing this value would orphan all previously seeded rows, so don't.
SEED_NAMESPACE = uuid.UUID("e3b0c442-98fc-1c14-9afb-f4c8996fb924")

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_DIR = REPO_ROOT / "seed"
PERSONA_DIR = SEED_DIR / "personas"


def sid(*parts: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, "|".join(parts))


# ---------------------------------------------------------------------------
# Catalog (advertisers / products / campaigns / product media)
#
# The seed catalog is loaded from disk rather than inlined here so that
# proprietary brand data stays out of the repo. Mirroring the `.env`
# pattern, the loader prefers a git-ignored real `catalog.json` and falls
# back to the committed brand-free `catalog.json.example`. The four
# module-level names below (`ADVERTISERS`, `PRODUCTS`, `CAMPAIGNS`,
# `MEDIA_BY_SKU`) are populated from the chosen file's keys — downstream
# code and tests reference them exactly as before.
#
# Each entry mirrors the column set its `upsert_*` function expects:
#   * advertisers: org_id, name, contact_email
#   * products: advertiser_org_id, sku, title, description,
#     deep_link_url, landing_url, price_cents, currency, status
#   * campaigns: slug, advertiser_org_id, name, budget_cents,
#     daily_cap_cents, start_at, end_at, targeting, status, product_skus
#   * media_by_sku: { sku: [ {kind, url}, ... ] } — position-ordered,
#     position 0 is the feature image, positions >=1 are gallery images.
# ---------------------------------------------------------------------------
CATALOG_PATH = SEED_DIR / "catalog.json"
CATALOG_EXAMPLE_PATH = SEED_DIR / "catalog.json.example"


def load_catalog() -> dict[str, Any]:
    """Load the seed catalog, preferring real data over the example.

    Mirrors the `.env` / `.env.example` convention: a git-ignored
    ``catalog.json`` holds the real (possibly proprietary) seed, while
    the committed ``catalog.json.example`` keeps a fresh clone runnable.
    Returns the parsed object with the four top-level keys the seed
    needs (``advertisers``, ``products``, ``campaigns``, ``media_by_sku``).
    """
    path = CATALOG_PATH if CATALOG_PATH.exists() else CATALOG_EXAMPLE_PATH
    if not path.exists():
        raise RuntimeError(
            f"No seed catalog found: expected {CATALOG_PATH} or {CATALOG_EXAMPLE_PATH}",
        )
    return json.loads(path.read_text(encoding="utf-8"))


_CATALOG = load_catalog()

ADVERTISERS: list[dict[str, Any]] = _CATALOG["advertisers"]
PRODUCTS: list[dict[str, Any]] = _CATALOG["products"]
CAMPAIGNS: list[dict[str, Any]] = _CATALOG["campaigns"]
MEDIA_BY_SKU: dict[str, list[dict[str, str]]] = _CATALOG["media_by_sku"]


# ---------------------------------------------------------------------------
# Persona loading
# ---------------------------------------------------------------------------
def load_personas() -> list[dict[str, Any]]:
    # Prefer real persona files; fall back to the committed `.json.example`
    # templates on a fresh clone (mirrors the catalog loader and the
    # `.env` / `.env.example` convention). The `*.json` glob does not match
    # the `.json.example` suffix, so the fallback fires only when no real
    # persona has been dropped in.
    files = sorted(PERSONA_DIR.glob("*.json"))
    if not files:
        files = sorted(PERSONA_DIR.glob("*.json.example"))
    if not files:
        raise RuntimeError(f"No persona JSON files found in {PERSONA_DIR}")
    return [json.loads(p.read_text(encoding="utf-8")) for p in files]


# ---------------------------------------------------------------------------
# Persona avatar — source-URL → Tigris upload
# ---------------------------------------------------------------------------
# Map common content types to file extensions so the Tigris key reflects
# the actual bytes (some CDNs route on extension, and `Content-Type`
# alone isn't always honored downstream).
_CONTENT_TYPE_TO_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def _avatar_key(persona_id: uuid.UUID, content_type: str) -> str:
    """Build the Tigris key for a persona's avatar.

    Keyed by `persona_id` (a UUID) rather than the human-readable slug
    so the public URL is *capability-based*: knowing the URL is enough
    to fetch the bytes (the bucket allows public read), but the URL
    cannot be enumerated by guessing slugs from a list of fictional
    influencer names. The bucket itself is not list-able publicly, so
    the only way to discover an avatar URL is via the API
    (`/feed/cursor` → `personas.avatar_image_url`) — i.e. the same
    surface where the rest of the persona metadata flows.
    """
    ext = _CONTENT_TYPE_TO_EXT.get(content_type.lower(), "jpg")
    return f"personas/{persona_id}/avatar.{ext}"


async def _avatar_already_in_tigris(key: str) -> bool:
    """Idempotency probe — skip the re-upload if the object exists.

    `head_object` raises (typically a `ClientError` with `404` code) when
    the key is absent. We treat any failure as "not present" and let the
    upload path run — a real auth/network error will then surface there.
    """
    if not settings.BUCKET_NAME:
        return False
    try:
        async with s3_client() as s3:
            await s3.head_object(Bucket=settings.BUCKET_NAME, Key=key)
        return True
    except Exception:
        return False


def _fetch_avatar_bytes(source_url: str) -> tuple[bytes, str]:
    """Download `source_url` synchronously, return (bytes, content_type).

    The seed script is one-shot and runs against a small list of personas,
    so we don't bother spinning up an async HTTP client just to wait on
    10 small downloads. `User-Agent` is set explicitly because some
    public avatar services 403 on missing headers.
    """
    req = urllib.request.Request(  # noqa: S310 — source URLs come from a checked-in seed file
        source_url,
        headers={"User-Agent": "soshop-seed/1.0 (+https://soshop.tv)"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — same as above
        data = resp.read()
        content_type = resp.headers.get_content_type() or "image/jpeg"
    return data, content_type


async def _ensure_persona_avatar_uploaded(persona_id: uuid.UUID, slug: str, source_url: str) -> str:
    """Make sure the persona's avatar lives in Tigris, return its public URL.

    The Tigris key is namespaced by `persona_id` (a UUID), not the slug
    — see `_avatar_key` for the rationale. `slug` is still threaded
    through purely for log lines so an operator scanning seed output
    can correlate which human-readable persona was just (re)uploaded.

    When `BUCKET_NAME` isn't set (e.g. someone is iterating on the DB
    seed without storage configured), this falls back to the source URL
    so the seed still completes and the consumer feed renders *something*
    via the persona's gradient + remote image. With Tigris configured,
    the source bytes are downloaded once and uploaded under a stable
    key; subsequent runs hit the `head_object` short-circuit and skip
    the network round-trip entirely.
    """
    if not settings.BUCKET_NAME:
        logger.info("avatar.skip slug=%s reason=bucket_not_configured", slug)
        return source_url

    # Probe under all common extensions before downloading, so a re-run
    # after a content-type flip doesn't double-upload. JPEG is the most
    # likely format from pravatar/picsum so it's the first guess.
    for guess_ct in ("image/jpeg", "image/png", "image/webp"):
        candidate = _avatar_key(persona_id, guess_ct)
        if await _avatar_already_in_tigris(candidate):
            logger.info("avatar.cache_hit slug=%s key=%s", slug, candidate)
            return public_url_for_key(candidate)

    data, content_type = _fetch_avatar_bytes(source_url)
    key = _avatar_key(persona_id, content_type)
    public_url = await upload_bytes(
        key=key,
        data=data,
        content_type=content_type,
        acl="public-read",
    )
    logger.info(
        "avatar.uploaded slug=%s key=%s bytes=%d content_type=%s",
        slug,
        key,
        len(data),
        content_type,
    )
    return public_url


# ---------------------------------------------------------------------------
# Upserts
# ---------------------------------------------------------------------------
async def upsert_advertisers(conn: asyncpg.Connection) -> dict[str, uuid.UUID]:
    out: dict[str, uuid.UUID] = {}
    for a in ADVERTISERS:
        ad_id = sid("advertiser", a["org_id"])
        await conn.execute(
            """
            INSERT INTO advertisers (id, org_id, name, contact_email)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (org_id) DO UPDATE SET
                name = EXCLUDED.name,
                contact_email = EXCLUDED.contact_email
            """,
            ad_id,
            a["org_id"],
            a["name"],
            a["contact_email"],
        )
        out[a["org_id"]] = ad_id
    return out


# ---------------------------------------------------------------------------
# Product media (gallery) — ordered list of placeholder image URLs per
# product SKU. The seed pipeline:
#
#   1. `upsert_products` writes/refreshes the row in `products`.
#   2. `upsert_product_media` walks this map and, per (sku, position):
#        a. probes Tigris under `products/{product_id}/media/{position}.{ext}`
#        b. if absent, downloads the source URL and uploads to that key
#           with `public-read`
#        c. inserts/updates a `product_media` row pointing at the
#           Tigris public URL
#
# Ordering convention (matches `migrations/0001_initial_schema.up.sql`
# and the consumer-feed gallery in `web/`):
#
#   position 0 — feature image (the single hero source of truth)
#   position 1+ — additional gallery images
#
# When `BUCKET_NAME` is unset the upload step is skipped and the source
# URL is persisted unchanged, matching the existing avatar fallback.
# ---------------------------------------------------------------------------


# Recognised content types when materialising media keys. The seed
# downloads decide content-type from the HTTP response; this map covers
# the image formats picsum / Tigris serve plus the video MIME types the
# `kind=video` path still supports if real product clips are wired up.
_MEDIA_CONTENT_TYPE_TO_EXT: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
}


def _media_key(product_id: uuid.UUID, position: int, ext: str) -> str:
    return f"products/{product_id}/media/{position:02d}.{ext.lstrip('.')}"


async def _media_already_in_tigris(key: str) -> bool:
    if not settings.BUCKET_NAME:
        return False
    try:
        async with s3_client() as s3:
            await s3.head_object(Bucket=settings.BUCKET_NAME, Key=key)
        return True
    except Exception:
        return False


def _fetch_media_bytes(source_url: str) -> tuple[bytes, str]:
    """Download a media URL synchronously. Mirrors `_fetch_avatar_bytes`
    but tolerates larger payloads (product videos, when wired up, can
    run a few MB).
    """
    req = urllib.request.Request(  # noqa: S310
        source_url,
        headers={"User-Agent": "soshop-seed/1.0 (+https://soshop.tv)"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        data = resp.read()
        content_type = resp.headers.get_content_type() or "application/octet-stream"
    return data, content_type


async def _ensure_product_media_uploaded(
    product_id: uuid.UUID,
    sku: str,
    position: int,
    kind: str,
    source_url: str,
) -> str | None:
    """Return the public Tigris URL for a product-media slot.

    Returns the source URL unchanged when `BUCKET_NAME` is unset.
    Returns ``None`` (and logs) when the source download fails — caller
    skips that media row so the rest of the gallery still seeds.
    """
    if not settings.BUCKET_NAME:
        logger.info(
            "product_media.skip sku=%s position=%d reason=bucket_not_configured",
            sku,
            position,
        )
        return source_url

    expected_ext = "mp4" if kind == "video" else "jpg"
    candidate = _media_key(product_id, position, expected_ext)
    if await _media_already_in_tigris(candidate):
        logger.info(
            "product_media.cache_hit sku=%s position=%d key=%s",
            sku,
            position,
            candidate,
        )
        return public_url_for_key(candidate)

    try:
        data, content_type = _fetch_media_bytes(source_url)
    except Exception as exc:
        logger.warning(
            "product_media.fetch_failed sku=%s position=%d url=%s err=%s",
            sku,
            position,
            source_url,
            exc,
        )
        return None

    ext = _MEDIA_CONTENT_TYPE_TO_EXT.get(content_type.lower(), expected_ext)
    key = _media_key(product_id, position, ext)
    public_url = await upload_bytes(
        key=key,
        data=data,
        content_type=content_type,
        acl="public-read",
    )
    logger.info(
        "product_media.uploaded sku=%s position=%d key=%s bytes=%d content_type=%s",
        sku,
        position,
        key,
        len(data),
        content_type,
    )
    return public_url


async def upsert_product_media(
    conn: asyncpg.Connection,
    product_ids: dict[str, uuid.UUID],
) -> int:
    """Walk `MEDIA_BY_SKU`, upload missing assets, write `product_media`.

    Returns the count of media rows touched. Idempotent — each (product,
    position) pair gets a single row and is updated in place on re-run.
    Products without a `MEDIA_BY_SKU` entry are left alone so the new
    code path doesn't clobber any out-of-band rows an operator added.
    """
    n_rows = 0
    for sku, items in MEDIA_BY_SKU.items():
        product_id = product_ids.get(sku)
        if product_id is None:
            logger.warning("product_media.skip sku=%s reason=product_not_seeded", sku)
            continue
        for position, item in enumerate(items):
            public_url = await _ensure_product_media_uploaded(
                product_id=product_id,
                sku=sku,
                position=position,
                kind=item["kind"],
                source_url=item["url"],
            )
            if public_url is None:
                continue
            await conn.execute(
                """
                INSERT INTO product_media
                    (product_id, position, kind, url, updated_at)
                VALUES ($1, $2, $3, $4, NOW())
                ON CONFLICT (product_id, position) DO UPDATE SET
                    kind = EXCLUDED.kind,
                    url = EXCLUDED.url,
                    updated_at = NOW()
                """,
                product_id,
                position,
                item["kind"],
                public_url,
            )
            n_rows += 1
    return n_rows


async def upsert_products(
    conn: asyncpg.Connection,
    advertiser_ids: dict[str, uuid.UUID],
) -> dict[str, uuid.UUID]:
    out: dict[str, uuid.UUID] = {}
    for p in PRODUCTS:
        ad_id = advertiser_ids[p["advertiser_org_id"]]
        prod_id = sid("product", p["advertiser_org_id"], p["sku"])
        await conn.execute(
            """
            INSERT INTO products
                (id, advertiser_id, sku, title, description,
                 deep_link_url, landing_url, price_cents, currency, status, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
            ON CONFLICT (advertiser_id, sku) DO UPDATE SET
                title = EXCLUDED.title,
                description = EXCLUDED.description,
                deep_link_url = EXCLUDED.deep_link_url,
                landing_url = EXCLUDED.landing_url,
                price_cents = EXCLUDED.price_cents,
                currency = EXCLUDED.currency,
                status = EXCLUDED.status,
                updated_at = NOW()
            """,
            prod_id,
            ad_id,
            p["sku"],
            p["title"],
            p["description"],
            p["deep_link_url"],
            p["landing_url"],
            p["price_cents"],
            p["currency"],
            p["status"],
        )
        out[p["sku"]] = prod_id
    return out


async def upsert_campaigns(
    conn: asyncpg.Connection,
    advertiser_ids: dict[str, uuid.UUID],
    product_ids: dict[str, uuid.UUID],
) -> dict[str, uuid.UUID]:
    out: dict[str, uuid.UUID] = {}
    for c in CAMPAIGNS:
        ad_id = advertiser_ids[c["advertiser_org_id"]]
        camp_id = sid("campaign", c["slug"])
        await conn.execute(
            """
            INSERT INTO campaigns
                (id, advertiser_id, name, budget_cents, daily_cap_cents,
                 start_at, end_at, targeting, status, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6::timestamptz, $7::timestamptz,
                    $8::jsonb, $9, NOW())
            ON CONFLICT (id) DO UPDATE SET
                advertiser_id = EXCLUDED.advertiser_id,
                name = EXCLUDED.name,
                budget_cents = EXCLUDED.budget_cents,
                daily_cap_cents = EXCLUDED.daily_cap_cents,
                start_at = EXCLUDED.start_at,
                end_at = EXCLUDED.end_at,
                targeting = EXCLUDED.targeting,
                status = EXCLUDED.status,
                updated_at = NOW()
            """,
            camp_id,
            ad_id,
            c["name"],
            c["budget_cents"],
            c["daily_cap_cents"],
            datetime.fromisoformat(c["start_at"]),
            datetime.fromisoformat(c["end_at"]),
            json.dumps(c["targeting"]),
            c["status"],
        )
        out[c["slug"]] = camp_id

        for sku in c["product_skus"]:
            prod_id = product_ids.get(sku)
            if prod_id is None:
                raise RuntimeError(
                    f"Campaign {c['slug']!r} references unknown product SKU {sku!r}",
                )
            await conn.execute(
                """
                INSERT INTO campaign_products (campaign_id, product_id)
                VALUES ($1, $2)
                ON CONFLICT (campaign_id, product_id) DO NOTHING
                """,
                camp_id,
                prod_id,
            )
    return out


async def upsert_personas_and_scripts(
    conn: asyncpg.Connection,
    product_ids: dict[str, uuid.UUID],
) -> tuple[set[uuid.UUID], set[uuid.UUID], int]:
    """Upsert the personas + persona_scripts that match the on-disk seed.

    Returns ``(persona_ids, script_ids, n_scripts)`` — the caller uses
    the id sets to prune rows from previous seed runs that have been
    removed from disk (the seed is the source of truth for which
    sellers exist).
    """
    personas = load_personas()
    n_scripts = 0
    seen_personas: set[uuid.UUID] = set()
    seen_scripts: set[uuid.UUID] = set()

    for p in personas:
        persona_id = sid("persona", p["slug"])
        # Mirror the source avatar into Tigris under a UUID-keyed path.
        # The column ends up holding the Tigris public URL (or the source
        # URL verbatim when storage isn't configured — the consumer
        # feed handles either).
        avatar_url = await _ensure_persona_avatar_uploaded(
            persona_id=persona_id,
            slug=p["slug"],
            source_url=p["avatar_image_url"],
        )
        await conn.execute(
            """
            INSERT INTO personas
                (id, name, avatar_image_url, voice_id, voice_pipeline_id,
                 personality, backstory, traits, persona_kind, status, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                avatar_image_url = EXCLUDED.avatar_image_url,
                voice_id = EXCLUDED.voice_id,
                voice_pipeline_id = EXCLUDED.voice_pipeline_id,
                personality = EXCLUDED.personality,
                backstory = EXCLUDED.backstory,
                traits = EXCLUDED.traits,
                persona_kind = EXCLUDED.persona_kind,
                status = EXCLUDED.status,
                updated_at = NOW()
            """,
            persona_id,
            p["name"],
            avatar_url,
            p["voice_id"],
            p["voice_pipeline_id"],
            p["personality"],
            p["backstory"],
            p["traits"],
            p["persona_kind"],
            p["status"],
        )

        for idx, s in enumerate(p["scripts"]):
            script_id = sid("persona_script", p["slug"], str(idx))
            product_id: uuid.UUID | None = None
            sku = s.get("product_sku")
            if sku:
                product_id = product_ids.get(sku)
                if product_id is None:
                    raise RuntimeError(
                        f"Persona {p['slug']!r} script {idx} references unknown SKU {sku!r}",
                    )

            # The schema (migration 0001) declares persona_scripts.script_text NOT NULL.
            # The spec asks for freestyle entries with script_text = null. We honour
            # the *intent* (a freestyle entry has no canned script, just a hook/CTA) by
            # storing an empty string, which is the closest faithful representation under
            # the current schema. Application code can treat '' as "freestyle".
            script_text = s.get("script_text") or ""
            await conn.execute(
                """
                INSERT INTO persona_scripts
                    (id, persona_id, product_id, script_text, hook, cta, weight, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    persona_id = EXCLUDED.persona_id,
                    product_id = EXCLUDED.product_id,
                    script_text = EXCLUDED.script_text,
                    hook = EXCLUDED.hook,
                    cta = EXCLUDED.cta,
                    weight = EXCLUDED.weight,
                    updated_at = NOW()
                """,
                script_id,
                persona_id,
                product_id,
                script_text,
                s.get("hook"),
                s.get("cta"),
                int(s.get("weight", 1)),
            )
            seen_scripts.add(script_id)
            n_scripts += 1
        seen_personas.add(persona_id)

    return seen_personas, seen_scripts, n_scripts


async def prune_stale_personas(
    conn: asyncpg.Connection,
    *,
    keep_persona_ids: set[uuid.UUID],
    keep_script_ids: set[uuid.UUID],
) -> tuple[int, int, int]:
    """Drop personas / scripts / ads from previous seed runs.

    The seed JSON files are the source of truth for which sellers and
    pitches exist. Anything still in the DB that isn't covered by the
    current seed (e.g. the Big Lou / Chef Marco / etc. v1 cast that
    pre-dated the collapse to two hosts) is removed so the
    rotation doesn't accidentally pull a retired persona. Order matters:
    ``channel_ad_log -> ads -> persona_scripts -> personas`` because
    every step has an FK referencing the previous one without
    ``ON DELETE CASCADE``.

    Returns ``(deleted_ads, deleted_scripts, deleted_personas)``.
    """
    keep_persona_list = list(keep_persona_ids)
    keep_script_list = list(keep_script_ids)

    # ads.persona_id / ads.script_id reference personas / persona_scripts
    # without ON DELETE; nuke any ad rows that point at retired sellers
    # or retired scripts before we touch their parent rows. The
    # channel_ad_log FK to ads is also RESTRICT, so wipe it first.
    await conn.execute(
        """
        DELETE FROM channel_ad_log
        WHERE ad_id IN (
            SELECT id FROM ads
            WHERE persona_id <> ALL($1::uuid[])
               OR script_id  <> ALL($2::uuid[])
        )
        """,
        keep_persona_list,
        keep_script_list,
    )
    deleted_ads = int(
        await conn.fetchval(
            """
                WITH d AS (
                    DELETE FROM ads
                    WHERE persona_id <> ALL($1::uuid[])
                       OR script_id  <> ALL($2::uuid[])
                    RETURNING 1
                )
                SELECT count(*) FROM d
                """,
            keep_persona_list,
            keep_script_list,
        )
        or 0
    )
    deleted_scripts = int(
        await conn.fetchval(
            """
                WITH d AS (
                    DELETE FROM persona_scripts
                    WHERE id <> ALL($1::uuid[])
                    RETURNING 1
                )
                SELECT count(*) FROM d
                """,
            keep_script_list,
        )
        or 0
    )
    # The schema defines channels.persona_id REFERENCES personas(id)
    # without ON DELETE, so retired personas trip the FK on delete.
    # Clear the references on any channel still pointing at a persona
    # we're about to drop; ``bind_channel_personas`` below reassigns
    # them to the current cast a few steps later in the same transaction.
    await conn.execute(
        """
        UPDATE channels
        SET persona_id = NULL, updated_at = NOW()
        WHERE persona_id IS NOT NULL
          AND persona_id <> ALL($1::uuid[])
        """,
        keep_persona_list,
    )
    deleted_personas = int(
        await conn.fetchval(
            """
                WITH d AS (
                    DELETE FROM personas
                    WHERE id <> ALL($1::uuid[])
                    RETURNING 1
                )
                SELECT count(*) FROM d
                """,
            keep_persona_list,
        )
        or 0
    )
    return deleted_ads, deleted_scripts, deleted_personas


async def rebuild_ads(
    conn: asyncpg.Connection,
    product_ids: dict[str, uuid.UUID],
) -> int:
    """Replace the ads table with one row per (persona, script, product).

    The model is "one fixed seller per channel, cycling through
    that seller's products" — each persona's scripts become their own
    contiguous block of ``ads.sequence_order`` so the channel loop's
    per-persona filter sees a coherent rotation. We don't try to
    preserve the existing ``ads.id`` values: the rotation cursor is
    advanced by ``current_ad_sequence`` (not by ad id), and the
    channel_ad_log is already wiped in :func:`prune_stale_personas`.

    Returns the number of ad rows written.
    """
    personas = load_personas()
    # `sequence_order` has a UNIQUE constraint, so we wipe ads first
    # and reassign deterministic 1..N indexes. Order personas by name
    # so the assignment is stable across re-seeds (channel-0 picks up
    # the alphabetically-first seller).
    #
    # `channel_ad_log.ad_id` REFERENCES ads(id) without ON DELETE
    # (see the initial schema migration), so any surviving log entries for the
    # current cast trip the FK on the wipe below. The log is
    # operational (it tracks which ad is airing right now); the
    # channel loop refills it on the next rotation tick. Wiping it
    # alongside the ads rebuild keeps the seed atomic.
    await conn.execute("DELETE FROM channel_ad_log")
    await conn.execute("DELETE FROM ads")
    personas_sorted = sorted(personas, key=lambda p: p["name"])

    seq = 0
    for p in personas_sorted:
        persona_id = sid("persona", p["slug"])
        for idx, s in enumerate(p["scripts"]):
            sku = s.get("product_sku")
            if not sku:
                continue
            product_id = product_ids.get(sku)
            if product_id is None:
                continue
            script_id = sid("persona_script", p["slug"], str(idx))
            seq += 1
            await conn.execute(
                """
                INSERT INTO ads
                    (advertiser_id, persona_id, product_id, script_id, sequence_order)
                SELECT advertiser_id, $1, $2, $3, $4
                FROM products WHERE id = $2
                """,
                persona_id,
                product_id,
                script_id,
                seq,
            )
    return seq


async def bind_channel_personas(conn: asyncpg.Connection) -> int:
    """Stamp each existing channel row with its locked persona.

    Sorts personas by name (stable) and zips them onto channels by
    ``ordinal`` so channel-0 gets the alphabetically-first seller and
    so on. Channels beyond the persona count keep ``persona_id = NULL``;
    the reconciler will retire them when ``settings.CHANNEL_COUNT`` is
    rerun, and the channel loop refuses to start without a bound
    persona (see ``run_channel_loop``). Returns the number of channels
    whose ``persona_id`` was assigned.
    """
    personas = await conn.fetch("SELECT id FROM personas ORDER BY name")
    if not personas:
        return 0
    bound = 0
    for ordinal, p in enumerate(personas):
        result = await conn.execute(
            "UPDATE channels SET persona_id = $1, updated_at = NOW() WHERE ordinal = $2",
            p["id"],
            ordinal,
        )
        # asyncpg returns "UPDATE <n>"; the trailing int is rowcount.
        rc = int(result.rsplit(" ", 1)[-1]) if result else 0
        bound += rc
    return bound


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    # Prefer the same Settings instance the rest of the app uses so the
    # seed picks up `.env` automatically. Fall back to os.environ for
    # callers that explicitly export `DATABASE_URL=...` ahead of the run.
    db_url = settings.DATABASE_URL or os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is not set. Aborting.", file=sys.stderr)
        return 2

    conn = await asyncpg.connect(db_url)
    try:
        async with conn.transaction():
            advertisers = await upsert_advertisers(conn)
            products = await upsert_products(conn, advertisers)
            n_media = await upsert_product_media(conn, products)
            campaigns = await upsert_campaigns(conn, advertisers, products)
            kept_personas, kept_scripts, n_scripts = await upsert_personas_and_scripts(
                conn, products
            )
            pruned_ads, pruned_scripts, pruned_personas = await prune_stale_personas(
                conn,
                keep_persona_ids=kept_personas,
                keep_script_ids=kept_scripts,
            )
            n_ads = await rebuild_ads(conn, products)
            n_channels_bound = await bind_channel_personas(conn)

        print(
            f"Seeded: {len(advertisers)} advertisers, {len(products)} products, "
            f"{n_media} product_media rows, "
            f"{len(campaigns)} campaigns, {len(kept_personas)} personas, "
            f"{n_scripts} persona_scripts, {n_ads} ads, "
            f"{n_channels_bound} channels bound to a persona. "
            f"Pruned: {pruned_personas} personas, {pruned_scripts} scripts, "
            f"{pruned_ads} ads.",
        )
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
