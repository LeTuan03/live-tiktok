"""Repository for the advertiser portal routes.

Owns SQL used by `/advertisers`, `/products`, and `/campaigns`. The
dynamic-UPDATE branches in `update_product` and `update_campaign` build
their SET-clause from the (allow-listed) Pydantic fields, so the SQL is
not a fixed string — those queries stay inline in the route handler and
the route uses the raw asyncpg connection for them. Everything else
(simple INSERT/SELECT/DELETE/parameter-less UPDATEs) lives here.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from soshop_server.db.models import Advertiser, Campaign, Product

__all__ = ["AdvertiserRepository"]


class AdvertiserRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Advertisers
    # ------------------------------------------------------------------

    async def get_by_org_id(self, org_id: str) -> Advertiser | None:
        row = await self._conn.fetchrow(
            "SELECT * FROM advertisers WHERE org_id = $1",
            org_id,
        )
        return Advertiser.model_validate(dict(row)) if row else None

    async def insert_advertiser(
        self, *, org_id: str, name: str, contact_email: str | None
    ) -> Advertiser:
        # INSERT ... RETURNING always returns one row; fall back to a runtime
        # error rather than `assert` so Bandit's S101 stays clean.
        row = await self._conn.fetchrow(
            """
            INSERT INTO advertisers (org_id, name, contact_email)
            VALUES ($1, $2, $3)
            RETURNING *
            """,
            org_id,
            name,
            contact_email,
        )
        if row is None:  # pragma: no cover — defensive
            raise RuntimeError("INSERT INTO advertisers did not return a row")
        return Advertiser.model_validate(dict(row))

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------

    async def get_owned_product(self, advertiser_id: UUID, product_id: UUID) -> Product | None:
        row = await self._conn.fetchrow(
            "SELECT * FROM products WHERE id = $1 AND advertiser_id = $2",
            product_id,
            advertiser_id,
        )
        return Product.model_validate(dict(row)) if row else None

    async def insert_product(
        self,
        *,
        advertiser_id: UUID,
        sku: str,
        title: str,
        description: str | None,
        deep_link_url: str | None,
        landing_url: str | None,
        price_cents: int | None,
        currency: str,
        status: str,
    ) -> Product:
        row = await self._conn.fetchrow(
            """
            INSERT INTO products (
                advertiser_id, sku, title, description,
                deep_link_url, landing_url, price_cents, currency, status
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING *
            """,
            advertiser_id,
            sku,
            title,
            description,
            deep_link_url,
            landing_url,
            price_cents,
            currency,
            status,
        )
        if row is None:  # pragma: no cover — defensive
            raise RuntimeError("INSERT INTO products did not return a row")
        return Product.model_validate(dict(row))

    async def list_products(
        self,
        advertiser_id: UUID,
        *,
        status: str | None,
        limit: int,
        offset: int,
    ) -> list[Product]:
        if status is None:
            rows = await self._conn.fetch(
                """
                SELECT * FROM products
                WHERE advertiser_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                advertiser_id,
                limit,
                offset,
            )
        else:
            rows = await self._conn.fetch(
                """
                SELECT * FROM products
                WHERE advertiser_id = $1 AND status = $2
                ORDER BY created_at DESC
                LIMIT $3 OFFSET $4
                """,
                advertiser_id,
                status,
                limit,
                offset,
            )
        return [Product.model_validate(dict(r)) for r in rows]

    async def delete_product(self, advertiser_id: UUID, product_id: UUID) -> bool:
        """Returns True if a row was deleted, False if no matching row existed."""
        result = await self._conn.execute(
            "DELETE FROM products WHERE id = $1 AND advertiser_id = $2",
            product_id,
            advertiser_id,
        )
        return not result.endswith(" 0")

    async def assert_products_owned(
        self, advertiser_id: UUID, product_ids: list[UUID]
    ) -> list[UUID]:
        """Returns the list of `product_ids` that are NOT owned by the advertiser."""
        if not product_ids:
            return []
        rows = await self._conn.fetch(
            "SELECT id FROM products WHERE advertiser_id = $1 AND id = ANY($2::uuid[])",
            advertiser_id,
            product_ids,
        )
        found = {row["id"] for row in rows}
        return [pid for pid in product_ids if pid not in found]

    # ------------------------------------------------------------------
    # Campaigns
    # ------------------------------------------------------------------

    async def get_owned_campaign(self, advertiser_id: UUID, campaign_id: UUID) -> Campaign | None:
        row = await self._conn.fetchrow(
            "SELECT * FROM campaigns WHERE id = $1 AND advertiser_id = $2",
            campaign_id,
            advertiser_id,
        )
        return Campaign.model_validate(dict(row)) if row else None

    async def list_campaigns(
        self,
        advertiser_id: UUID,
        *,
        status: str | None,
        limit: int,
        offset: int,
    ) -> list[Campaign]:
        if status is None:
            rows = await self._conn.fetch(
                """
                SELECT * FROM campaigns
                WHERE advertiser_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                advertiser_id,
                limit,
                offset,
            )
        else:
            rows = await self._conn.fetch(
                """
                SELECT * FROM campaigns
                WHERE advertiser_id = $1 AND status = $2
                ORDER BY created_at DESC
                LIMIT $3 OFFSET $4
                """,
                advertiser_id,
                status,
                limit,
                offset,
            )
        return [Campaign.model_validate(dict(r)) for r in rows]

    async def pause_campaign(self, advertiser_id: UUID, campaign_id: UUID) -> Campaign | None:
        row = await self._conn.fetchrow(
            """
            UPDATE campaigns
               SET status = 'paused', updated_at = NOW()
             WHERE id = $1 AND advertiser_id = $2
            RETURNING *
            """,
            campaign_id,
            advertiser_id,
        )
        return Campaign.model_validate(dict(row)) if row else None
