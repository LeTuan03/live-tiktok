"""Product catalog against a real Postgres (needs migration 0003).

Skips when ``DATABASE_URL`` is not set so the default ``uv run pytest`` stays
hermetic. The product created here is archived and then deleted.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

from soshop.services import catalog
from soshop_server.db.pool import close_pool, get_pool

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set, skipping catalog integration test",
)


@pytest.fixture
async def conn() -> AsyncIterator[asyncpg.Connection]:
    pool = await get_pool()
    async with pool.acquire() as connection:
        yield connection
    await close_pool()


async def test_catalog_product_round_trip(conn: asyncpg.Connection) -> None:
    product = await catalog.create_product(
        conn,
        {
            "name": "pytest son dưỡng",
            "brand": "Test",
            "price": 59000,
            "currency": "vnd",
            "highlights": ["Dưỡng ẩm", "Nhỏ gọn"],
            "usage": "Thoa lên môi.",
            "image_url": "/media/uploads/test.jpg",
        },
    )
    try:
        assert (product.price, product.currency) == (59000.0, "VND")
        assert product.highlights == ["Dưỡng ẩm", "Nhỏ gọn"]
        assert product.image_url == "/media/uploads/test.jpg"
        assert not product.image_is_placeholder

        updated = await catalog.update_product(
            conn,
            product.id,
            {"highlights": ["Một ý"], "price": None, "image_url": "https://picsum.photos/x"},
        )
        assert updated is not None
        assert updated.highlights == ["Một ý"] and updated.price is None
        assert updated.image_is_placeholder  # a stock placeholder is not the product

        assert product.id in {p.id for p in await catalog.list_products(conn)}
        assert await catalog.archive_product(conn, product.id)
        assert product.id not in {p.id for p in await catalog.list_products(conn)}
    finally:
        await conn.execute("DELETE FROM products WHERE id = $1", catalog.to_uuid(product.id))
