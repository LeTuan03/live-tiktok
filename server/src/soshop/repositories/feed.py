"""Repository for the consumer feed routes.

Owns SQL used by `/feed/session`, `/feed/cursor`, `/feed/swipe`, and
`/feed/event`. Row shapes are returned as `ProductOut` / `FeedCandidate`
where the consumer is the response model, or as plain dicts / scalars
when the caller just needs raw columns. The repository never raises
HTTP errors — callers translate misses into 404s themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import asyncpg

from soshop.api.schemas.feed import ProductMediaOut, ProductOut
from soshop.services.feed_ranking import FeedCandidate

__all__ = [
    "FeedRepository",
    "PersonaMetaRow",
]


# Cooldown window: persona ids the user has seen in their last N events.
# We look across all event kinds — any event tying the user to a persona
# means it was surfaced.
RECENT_PERSONAS_SQL = """
SELECT persona_id
FROM (
    SELECT fe.persona_id, MAX(fe.ts) AS last_seen
    FROM feed_events fe
    JOIN feed_sessions fs ON fs.id = fe.session_id
    WHERE fs.user_id = $1
    GROUP BY fe.persona_id
) seen
ORDER BY last_seen DESC
LIMIT $2
"""

# Eligible candidates:
#   - persona must be ACTIVE
#   - the script must be bound to a product — every AI seller in v1
#     advertises something, so freestyle scripts (product_id IS NULL)
#     never surface in the feed.
#   - the bound product must be in at least one non-paused, non-archived,
#     non-completed campaign.
CANDIDATES_SQL = """
SELECT
    ps.id          AS script_id,
    ps.persona_id  AS persona_id,
    ps.product_id  AS product_id,
    ps.weight      AS weight
FROM persona_scripts ps
JOIN personas p ON p.id = ps.persona_id
WHERE p.status = 'active'
  AND ps.product_id IS NOT NULL
  AND EXISTS (
      SELECT 1
      FROM campaign_products cp
      JOIN campaigns c ON c.id = cp.campaign_id
      WHERE cp.product_id = ps.product_id
        AND c.status NOT IN ('paused', 'archived', 'completed')
  )
"""


@dataclass(frozen=True)
class PersonaMetaRow:
    name: str | None
    avatar_url: str | None


class FeedRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def load_session_user(self, session_id: UUID) -> str | None:
        row = await self._conn.fetchrow(
            "SELECT user_id FROM feed_sessions WHERE id = $1",
            session_id,
        )
        return row["user_id"] if row else None

    async def load_recent_personas(self, user_id: str, window: int) -> list[UUID]:
        rows = await self._conn.fetch(RECENT_PERSONAS_SQL, user_id, window)
        return [row["persona_id"] for row in rows]

    async def load_candidates(self) -> list[FeedCandidate]:
        rows = await self._conn.fetch(CANDIDATES_SQL)
        return [
            FeedCandidate(
                persona_id=row["persona_id"],
                script_id=row["script_id"],
                product_id=row["product_id"],
                weight=int(row["weight"]),
            )
            for row in rows
        ]

    async def load_persona_metas(self, persona_ids: list[UUID]) -> dict[UUID, PersonaMetaRow]:
        """Batch-fetch display name + avatar for the picked personas.

        Personas without an uploaded avatar map to ``None`` so the client
        can fall back to the persona's gradient identity.
        """
        if not persona_ids:
            return {}
        rows = await self._conn.fetch(
            "SELECT id, name, avatar_image_url FROM personas WHERE id = ANY($1::uuid[])",
            persona_ids,
        )
        return {
            row["id"]: PersonaMetaRow(name=row["name"], avatar_url=row["avatar_image_url"])
            for row in rows
        }

    async def load_products(self, product_ids: list[UUID]) -> dict[UUID, ProductOut]:
        """Batch-fetch catalog snapshots + ordered product_media rows."""
        if not product_ids:
            return {}
        rows = await self._conn.fetch(
            """
            SELECT id, sku, title, description,
                   price_cents, currency, deep_link_url, landing_url
            FROM products
            WHERE id = ANY($1::uuid[])
            """,
            product_ids,
        )
        media_rows = await self._conn.fetch(
            """
            SELECT product_id, position, kind, url
            FROM product_media
            WHERE product_id = ANY($1::uuid[])
            ORDER BY product_id, position
            """,
            product_ids,
        )
        media_by_product: dict[UUID, list[ProductMediaOut]] = {pid: [] for pid in product_ids}
        for media_row in media_rows:
            media_by_product[media_row["product_id"]].append(
                ProductMediaOut(
                    position=media_row["position"],
                    kind=media_row["kind"],
                    url=media_row["url"],
                )
            )
        return {
            row["id"]: ProductOut(
                id=row["id"],
                sku=row["sku"],
                title=row["title"],
                description=row["description"],
                price_cents=int(row["price_cents"]) if row["price_cents"] is not None else None,
                currency=row["currency"],
                deep_link_url=row["deep_link_url"],
                landing_url=row["landing_url"],
                media=media_by_product.get(row["id"], []),
            )
            for row in rows
        }

    async def insert_feed_session(
        self,
        *,
        user_id: str,
        room_name: str | None,
        device_id: str | None,
        app_version: str | None,
        country: str | None,
    ) -> UUID:
        row = await self._conn.fetchrow(
            """
            INSERT INTO feed_sessions (user_id, room_name, device_id, app_version, country)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            user_id,
            room_name,
            device_id,
            app_version,
            country,
        )
        # INSERT ... RETURNING always returns one row; fall back to a runtime
        # error rather than `assert` so Bandit's S101 stays clean.
        if row is None:  # pragma: no cover — defensive
            raise RuntimeError("INSERT INTO feed_sessions did not return a row")
        session_id: UUID = row["id"]
        return session_id

    async def insert_feed_event(
        self,
        *,
        event_id: UUID,
        session_id: UUID,
        persona_id: UUID,
        product_id: UUID | None,
        event_kind: str,
        value_ms: int | None,
    ) -> bool:
        """Insert a feed_events row keyed by `event_id`.

        Returns True when a fresh row was written, False when the
        event_id already existed (ON CONFLICT DO NOTHING).
        """
        result = await self._conn.execute(
            """
            INSERT INTO feed_events
                (id, session_id, persona_id, product_id, event_kind, value_ms)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (id) DO NOTHING
            """,
            event_id,
            session_id,
            persona_id,
            product_id,
            event_kind,
            value_ms,
        )
        # asyncpg's `execute` returns "INSERT 0 N" — N=0 on conflict.
        if isinstance(result, str) and result.startswith("INSERT"):
            try:
                return int(result.split()[-1]) > 0
            except (ValueError, IndexError):
                return False
        return False
