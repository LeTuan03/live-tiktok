"""SQL queries and row projection helpers for the ``/channels`` API.

Split out of ``channels.py`` so the route module stays focused on HTTP
concerns. The two SQL strings and the ``row_to_ad_snapshot`` projection
are used by ``list_channels`` and ``get_channel_current_ad`` and are not
referenced outside this package.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import Field

from soshop_server.api.deps import StrictModel


class PersonaSnapshot(StrictModel):
    id: UUID
    display_name: str | None
    avatar_url: str | None


class ProductMediaSnapshot(StrictModel):
    position: int
    kind: str
    url: str


class ProductSnapshot(StrictModel):
    id: UUID
    title: str
    price_cents: int | None
    currency: str
    image_url: str | None
    deep_link_url: str | None
    landing_url: str | None
    media: list[ProductMediaSnapshot] = Field(
        default_factory=list,
        description=(
            "Ordered product-media gallery (image kind). Empty when the "
            "product has no `product_media` rows. The feed uses "
            "this to render the paginated gallery without waiting on the "
            "agent's `product_pitch.show` data-channel event."
        ),
    )


class AdvertiserSnapshot(StrictModel):
    id: UUID
    name: str


class AdSnapshot(StrictModel):
    """The ad currently airing on a channel — drives the shop CTA overlay."""

    id: UUID
    product: ProductSnapshot
    persona: PersonaSnapshot
    advertiser: AdvertiserSnapshot
    deep_link_url: str | None = Field(
        description=(
            "Echoes the product's deep link so the client can render the "
            "Shop CTA without traversing the product subtree."
        ),
    )
    started_at: datetime
    duration_ms: int


# Pull every running channel together with its most recently logged ad
# (if any). The lateral join short-circuits to one row per channel; we
# then left-join into ads/products/personas/advertisers so the route
# layer doesn't need a second round-trip per channel. Position-0
# product_media is the conventional hero image (see
# migrations/0001_initial_schema.up.sql); `product_media_json` aggregates
# the full image gallery so the feed can paint the dots + preload all
# slides on first paint, instead of waiting for the agent's
# `product_pitch.show` text-stream to deliver the same list.
RUNNING_CHANNELS_WITH_AD_SQL = """
SELECT
    c.id                AS channel_id,
    c.ordinal           AS ordinal,
    c.livekit_room_name AS livekit_room_name,
    cal.started_at      AS ad_started_at,
    a.id                AS ad_id,
    a.duration_ms       AS ad_duration_ms,
    p.id                AS product_id,
    p.title             AS product_title,
    p.price_cents       AS product_price_cents,
    p.currency          AS product_currency,
    p.deep_link_url     AS product_deep_link_url,
    p.landing_url       AS product_landing_url,
    pm.url              AS product_image_url,
    pmg.media_json      AS product_media_json,
    pe.id               AS persona_id,
    pe.name             AS persona_name,
    pe.avatar_image_url AS persona_avatar_url,
    adv.id              AS advertiser_id,
    adv.name            AS advertiser_name
FROM channels c
LEFT JOIN LATERAL (
    SELECT ad_id, started_at, ended_at
    FROM channel_ad_log
    WHERE channel_id = c.id
    ORDER BY started_at DESC
    LIMIT 1
) cal ON true
LEFT JOIN ads        a   ON a.id   = cal.ad_id
LEFT JOIN products   p   ON p.id   = a.product_id
LEFT JOIN personas   pe  ON pe.id  = a.persona_id
LEFT JOIN advertisers adv ON adv.id = a.advertiser_id
LEFT JOIN LATERAL (
    SELECT url
    FROM product_media
    WHERE product_id = p.id AND kind = 'image'
    ORDER BY position
    LIMIT 1
) pm ON true
LEFT JOIN LATERAL (
    SELECT json_agg(
        json_build_object('position', position, 'kind', kind, 'url', url)
        ORDER BY position
    ) AS media_json
    FROM product_media
    WHERE product_id = p.id AND kind = 'image'
) pmg ON true
WHERE c.status = 'running'
ORDER BY c.ordinal
"""


# Single-channel variant. Same shape as the list query but without the
# running-status filter — a channel that has been retired since
# `started_at` should still surface its last-aired ad rather than 404
# (the row itself still exists; the client can decide whether to drop
# the channel based on the absence in the list endpoint).
#
# The lateral join prefers a row with ``ended_at IS NULL`` (the airing
# ad) and falls back to the most recent row otherwise. Without the
# preference, a mid-rotation joiner can hit the brief window between
# ``_record_channel_ad_ended(N)`` (in the orchestrator's ``finally``)
# and ``_record_channel_ad_started(N+1)`` (start of next iteration)
# and get an ad whose pitch is already wrapping. The ``ORDER BY``
# clause's ``ended_at IS NULL DESC`` puts the live row first
# (``NULL`` sorts FIRST under ``IS NULL DESC``, since boolean ``IS
# NULL`` evaluates to ``true`` which sorts last under ``DESC``...
# wait — to be unambiguous we use ``ORDER BY (ended_at IS NULL) DESC,
# started_at DESC`` which evaluates the predicate per row and orders
# ``true`` first under ``DESC``).
CHANNEL_CURRENT_AD_SQL = """
SELECT
    cal.started_at      AS ad_started_at,
    a.id                AS ad_id,
    a.duration_ms       AS ad_duration_ms,
    p.id                AS product_id,
    p.title             AS product_title,
    p.price_cents       AS product_price_cents,
    p.currency          AS product_currency,
    p.deep_link_url     AS product_deep_link_url,
    p.landing_url       AS product_landing_url,
    pm.url              AS product_image_url,
    pmg.media_json      AS product_media_json,
    pe.id               AS persona_id,
    pe.name             AS persona_name,
    pe.avatar_image_url AS persona_avatar_url,
    adv.id              AS advertiser_id,
    adv.name            AS advertiser_name
FROM channels c
LEFT JOIN LATERAL (
    SELECT ad_id, started_at, ended_at
    FROM channel_ad_log
    WHERE channel_id = c.id
    ORDER BY (ended_at IS NULL) DESC, started_at DESC
    LIMIT 1
) cal ON true
LEFT JOIN ads        a   ON a.id   = cal.ad_id
LEFT JOIN products   p   ON p.id   = a.product_id
LEFT JOIN personas   pe  ON pe.id  = a.persona_id
LEFT JOIN advertisers adv ON adv.id = a.advertiser_id
LEFT JOIN LATERAL (
    SELECT url
    FROM product_media
    WHERE product_id = p.id AND kind = 'image'
    ORDER BY position
    LIMIT 1
) pm ON true
LEFT JOIN LATERAL (
    SELECT json_agg(
        json_build_object('position', position, 'kind', kind, 'url', url)
        ORDER BY position
    ) AS media_json
    FROM product_media
    WHERE product_id = p.id AND kind = 'image'
) pmg ON true
WHERE c.id = $1
"""


def row_to_ad_snapshot(row: asyncpg.Record | dict) -> AdSnapshot | None:
    """Project a joined channel/ad row into an ``AdSnapshot`` (or ``None``)."""
    ad_id = row["ad_id"]
    if ad_id is None:
        return None
    return AdSnapshot(
        id=ad_id,
        product=ProductSnapshot(
            id=row["product_id"],
            title=row["product_title"],
            price_cents=(
                int(row["product_price_cents"]) if row["product_price_cents"] is not None else None
            ),
            currency=row["product_currency"],
            image_url=row["product_image_url"],
            deep_link_url=row["product_deep_link_url"],
            landing_url=row["product_landing_url"],
            media=_decode_product_media(
                row.get("product_media_json")
                if isinstance(row, dict)
                else row["product_media_json"]
            ),
        ),
        persona=PersonaSnapshot(
            id=row["persona_id"],
            display_name=row["persona_name"],
            avatar_url=row["persona_avatar_url"],
        ),
        advertiser=AdvertiserSnapshot(
            id=row["advertiser_id"],
            name=row["advertiser_name"],
        ),
        deep_link_url=row["product_deep_link_url"],
        started_at=row["ad_started_at"],
        duration_ms=int(row["ad_duration_ms"]),
    )


def _decode_product_media(raw: Any) -> list[ProductMediaSnapshot]:
    """Decode the `json_agg` payload returned by the SQL aggregator. asyncpg
    surfaces `json_agg` as a JSON string (no jsonb codec is registered for
    the channels routes); decode it and project into the wire model.
    Returns an empty list when the lateral aggregate had no rows (NULL)."""
    if raw is None:
        return []
    items = json.loads(raw) if isinstance(raw, str) else raw
    if not items:
        return []
    return [
        ProductMediaSnapshot(
            position=int(item["position"]),
            kind=str(item["kind"]),
            url=str(item["url"]),
        )
        for item in items
    ]
