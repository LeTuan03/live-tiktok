"""DB-backed loaders for the SoShop agent's per-dispatch context.

Replaces the placeholder stubs in `soshop/agent/main.py` so the
LiveKit agent renders a persona prompt and an actual sales pitch that
the LLM can deliver. Each loader is best-effort: on missing rows or
malformed ids it returns the same fallback shape the stubs returned, so
the agent template renders end-to-end and the worker logs a warning
rather than crashing the dispatch.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

from soshop.agent.schemas import PopupPayload, ProductMediaItem

logger = logging.getLogger("soshop.agent.loaders")

_PERSONA_FALLBACK: dict[str, str] = {
    "name": "host",
    "backstory": "none",
    "traits": "none",
    "voice_tone": "none",
}


def _try_uuid(raw: str | None) -> UUID | None:
    if not raw:
        return None
    try:
        return UUID(raw)
    except (ValueError, AttributeError):
        return None


async def load_persona_profile(pool: asyncpg.Pool, persona_id: str) -> dict[str, str]:
    """Resolve persona identity fields used by the prompt template.

    The schema (`personas` in `migrations/0001_initial_schema.up.sql`)
    has no dedicated `voice_tone` column; the closest analogue the
    template needs is the `personality` blurb, so we map it. Returns
    placeholder values when the persona is missing so the template
    `str.format(...)` call doesn't raise on a missing key.
    """
    pid = _try_uuid(persona_id)
    if pid is None:
        logger.warning("load_persona_profile: invalid uuid %r", persona_id)
        return dict(_PERSONA_FALLBACK)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT name, backstory, traits, personality
            FROM personas
            WHERE id = $1
            """,
            pid,
        )
    if row is None:
        logger.warning("load_persona_profile: persona %s not found", pid)
        return dict(_PERSONA_FALLBACK)

    return {
        "name": row["name"] or _PERSONA_FALLBACK["name"],
        "backstory": row["backstory"] or _PERSONA_FALLBACK["backstory"],
        "traits": row["traits"] or _PERSONA_FALLBACK["traits"],
        "voice_tone": row["personality"] or _PERSONA_FALLBACK["voice_tone"],
    }


async def load_script_body(pool: asyncpg.Pool, script_id: str) -> str:
    """Resolve the script the LLM should deliver.

    Renders the pitch as a single contiguous block — hook, body, CTA in
    one paragraph the LLM treats as one breath. The previous structured
    layout (``script_text`` then ``Hook: ...`` then
    ``Call-to-action (deliver verbatim): "..."``) was cuing Llama 3.3
    70B to deliver each labeled section as a separate utterance, which
    multiplied the pitch length ~3× over the 15 s target and produced
    the audible "mini pitch → long restart → mini pitch" pattern.
    Inlining them removes the cue without losing the verbatim-CTA
    requirement (the persona prompt still calls it out, and the popup
    card still pulls the canonical CTA copy directly from the
    ``persona_scripts.cta`` column rather than from the LLM stream).
    Returns ``"none"`` when the script is missing or empty (freestyle
    mode), matching the prior stub's contract.
    """
    sid = _try_uuid(script_id)
    if sid is None:
        logger.warning("load_script_body: invalid uuid %r", script_id)
        return "none"

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT script_text, hook, cta
            FROM persona_scripts
            WHERE id = $1
            """,
            sid,
        )
    if row is None:
        logger.warning("load_script_body: script %s not found", sid)
        return "none"

    hook = (row["hook"] or "").strip()
    text = (row["script_text"] or "").strip()
    cta = (row["cta"] or "").strip()
    parts = [p for p in (hook, text, cta) if p]
    return " ".join(parts) if parts else "none"


async def load_product_context(pool: asyncpg.Pool, product_id: str | None) -> dict[str, Any] | None:
    """Resolve the product the persona is pitching.

    Returns a dict the prompt template can render line-by-line via
    `_format_product_context`. ``None`` when no product is bound (the
    pitch is a freestyle bit) or the product can't be resolved.
    """
    if product_id is None:
        return None
    pid = _try_uuid(product_id)
    if pid is None:
        logger.warning("load_product_context: invalid uuid %r", product_id)
        return None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT title, description, price_cents, currency, landing_url
            FROM products
            WHERE id = $1
            """,
            pid,
        )
    if row is None:
        logger.warning("load_product_context: product %s not found", pid)
        return None

    out: dict[str, Any] = {"product_id": str(pid), "title": row["title"]}
    if row["description"]:
        out["description"] = row["description"]
    if row["price_cents"] is not None and row["currency"]:
        cents = int(row["price_cents"])
        out["price"] = f"{cents / 100:.2f} {row['currency']}"
    if row["landing_url"]:
        out["landing_url"] = row["landing_url"]
    return out


async def load_popup_payload(pool: asyncpg.Pool, product_id: str | None) -> PopupPayload | None:
    """Build the in-call popup card from product + product_media.

    Channel-mode dispatches don't get a client-supplied ``popup_payload``
    (the channel rotation has no client at dispatch time), so the agent
    process resolves it from Postgres at swap time. Returns ``None`` when
    any required field (title / image / cta / landing_url) is missing — the
    LLM then falls back to "no popup configured" gracefully.

    Image source is ``product_media`` position 0 (the feature image; see
    ``migrations/0001_initial_schema.up.sql``). CTA is derived from the
    matching ``persona_scripts`` row if present, else a generic fallback.

    The full ordered gallery (every ``product_media`` row, image + video)
    is aggregated into ``media`` so the in-app product-focus surface can
    render a paginated swipeable carousel without a second round trip.
    """
    if product_id is None:
        return None
    pid = _try_uuid(product_id)
    if pid is None:
        logger.warning("load_popup_payload: invalid uuid %r", product_id)
        return None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                p.title,
                p.deep_link_url,
                p.landing_url,
                pm.url AS image_url,
                (
                    SELECT cta FROM persona_scripts
                    WHERE product_id = p.id AND cta IS NOT NULL AND cta <> ''
                    ORDER BY weight DESC NULLS LAST
                    LIMIT 1
                ) AS script_cta,
                COALESCE(
                    (
                        SELECT json_agg(
                            json_build_object(
                                'position', position,
                                'kind', kind,
                                'url', url
                            )
                            ORDER BY position
                        )
                        FROM product_media
                        WHERE product_id = p.id
                    ),
                    '[]'::json
                ) AS media
            FROM products p
            LEFT JOIN LATERAL (
                SELECT url FROM product_media
                WHERE product_id = p.id
                ORDER BY position
                LIMIT 1
            ) pm ON true
            WHERE p.id = $1
            """,
            pid,
        )
    if row is None:
        logger.warning("load_popup_payload: product %s not found", pid)
        return None

    fields = _extract_popup_fields(row, pid)
    if fields is None:
        return None
    title, image_url, cta, deep_link_url, landing_url = fields

    return PopupPayload(
        id=str(pid),
        title=title,
        image_url=image_url,
        cta=cta,
        deep_link_url=deep_link_url,
        landing_url=landing_url,
        media=_decode_media_items(row["media"]),
    )


def _extract_popup_fields(row: asyncpg.Record, pid: UUID) -> tuple[str, str, str, str, str] | None:
    title = (row["title"] or "").strip()
    image_url = (row["image_url"] or "").strip()
    landing_url = (row["landing_url"] or "").strip()
    deep_link_url = (row["deep_link_url"] or "").strip() or landing_url
    cta = (row["script_cta"] or "").strip() or "Tap to shop"

    if not (title and image_url and landing_url and deep_link_url):
        logger.warning(
            "load_popup_payload: product %s missing required fields "
            "(title=%s image_url=%s landing_url=%s deep_link_url=%s)",
            pid,
            bool(title),
            bool(image_url),
            bool(landing_url),
            bool(deep_link_url),
        )
        return None
    return title, image_url, cta, deep_link_url, landing_url


def _decode_media_items(raw_media: Any) -> list[ProductMediaItem]:
    # json_agg returns a JSON string via asyncpg; decode and filter out
    # anything with a kind asyncpg doesn't recognise so the wire payload
    # stays narrow.
    if not raw_media:
        return []
    decoded = json.loads(raw_media) if isinstance(raw_media, str) else raw_media
    items: list[ProductMediaItem] = []
    for item in decoded:
        kind = (item.get("kind") or "").strip()
        url = (item.get("url") or "").strip()
        if not (kind and url):
            continue
        items.append(ProductMediaItem(position=int(item["position"]), kind=kind, url=url))
    return items


async def load_persona_voice_id(
    pool: asyncpg.Pool, persona_id: str
) -> tuple[str | None, str | None]:
    """Look up the persona's `voice_id` and `avatar_image_url`.

    The voice id flows into the ElevenLabs TTS plugin and the avatar
    image url into the LemonSlice avatar plugin — both are persona-
    scoped settings the prompt loaders don't surface. Returns
    ``(None, None)`` when missing so the agent can fall back to the
    plugin defaults rather than crashing the dispatch.
    """
    pid = _try_uuid(persona_id)
    if pid is None:
        return (None, None)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT voice_id, avatar_image_url FROM personas WHERE id = $1",
            pid,
        )
    if row is None:
        return (None, None)
    return (row["voice_id"], row["avatar_image_url"])
