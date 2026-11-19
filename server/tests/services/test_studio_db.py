"""Live studio persistence against a real Postgres.

Skips when ``DATABASE_URL`` is not set so the default ``uv run pytest`` stays
hermetic. Needs migration 0002 applied. Every row created here hangs off one
throwaway live session that is deleted at the end.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

from soshop.services import studio
from soshop_server.db.pool import close_pool, get_pool

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set, skipping live studio integration test",
)


@pytest.fixture
async def conn() -> AsyncIterator[asyncpg.Connection]:
    pool = await get_pool()
    async with pool.acquire() as connection:
        yield connection
    await close_pool()


async def test_session_products_and_live_state_round_trip(conn: asyncpg.Connection) -> None:
    session = await studio.create_session(conn, title="pytest live", model_id=None)
    try:
        a = await studio.create_product(conn, session.id, {"name": "A", "price": 89000})
        b = await studio.create_product(conn, session.id, {"name": "B"})
        c = await studio.create_product(conn, session.id, {"name": "C", "currency": "USD"})
        assert [p.position for p in (a, b, c)] == [0, 1, 2]
        assert (a.price, a.currency, b.price, c.currency) == (89000.0, "VND", None, "USD")

        reordered = await studio.reorder_products(conn, session.id, [c.id, a.id, b.id])
        assert reordered is not None
        assert [p.name for p in reordered] == ["C", "A", "B"]
        # A stale list (missing or foreign ids) must not rewrite anything.
        assert await studio.reorder_products(conn, session.id, [a.id, b.id]) is None
        assert [p.name for p in await studio.list_products(conn, session.id)] == ["C", "A", "B"]

        patched = await studio.update_product(
            conn, a.id, {"script": "talking points", "price": None}
        )
        assert patched is not None
        assert (patched.script, patched.price) == ("talking points", None)

        await studio.mark_session_live(conn, session.id, "live-test-room")
        await studio.set_product_status(conn, c.id, "presenting")
        await studio.write_live_state(
            conn, session.id, mode="presenting", current_product_id=c.id, last_line="xin chào"
        )
        live = await studio.get_session(conn, session.id)
        assert live is not None and live.status == "live" and live.live is not None
        assert (live.live.mode, live.live.current_product_id) == ("presenting", c.id)

        await studio.mark_session_ended(conn, session.id)
        ended = await studio.get_session(conn, session.id)
        assert ended is not None and ended.status == "ended" and ended.live is None
        # A product caught mid-presentation goes back to pending for the next run.
        statuses = {p.name: p.status for p in await studio.list_products(conn, session.id)}
        assert statuses["C"] == "pending"

        # State reports from a straggling agent must not resurrect an ended session.
        await studio.write_live_state(
            conn, session.id, mode="chatting", current_product_id=None, last_line=None
        )
        after = await studio.get_session(conn, session.id)
        assert after is not None and after.live is None

        assert await studio.delete_product(conn, b.id) is True
        assert await studio.delete_product(conn, b.id) is False
    finally:
        await studio.delete_session(conn, session.id)
    assert await studio.get_session(conn, session.id) is None
    assert await studio.list_products(conn, session.id) == []


async def test_malformed_ids_read_as_missing(conn: asyncpg.Connection) -> None:
    assert await studio.get_session(conn, "not-a-uuid") is None
    assert await studio.get_product(conn, "") is None
    assert await studio.get_model(conn, "123") is None
