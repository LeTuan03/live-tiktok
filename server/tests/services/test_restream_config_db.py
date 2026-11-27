"""The TikTok destination from the settings, against a real Postgres (migration 0007).

Skips when ``DATABASE_URL`` is not set. Uses its own destination id, never the
configured one (``CONFIG_TIKTOK_ID``), so a real TikTok setup in a dev database
is left alone. Broadcasts made here start in the future so they sort ahead of
any live broadcast already in the database; all rows are deleted at the end.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import asyncpg
import pytest

from soshop.services import restream
from soshop.services.restream import RestreamError
from soshop.services.studio import to_uuid
from soshop_server.db.pool import close_pool, get_pool

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set, skipping restream config test",
)

URL = "rtmp://push-rtmp.tiktokcdn.com/game/"


@pytest.fixture
async def conn() -> AsyncIterator[asyncpg.Connection]:
    pool = await get_pool()
    async with pool.acquire() as connection:
        yield connection
    await close_pool()


async def broadcast(conn: asyncpg.Connection, status: str, started_in_days: int) -> str:
    bid = await conn.fetchval(
        """
        INSERT INTO broadcasts (title, status, started_at)
        VALUES ('pytest tiktok tự bật', $1, NOW() + make_interval(days => $2)) RETURNING id
        """,
        status,
        started_in_days,
    )
    return str(bid)


async def switch(conn: asyncpg.Connection, bid: str, did: str) -> bool | None:
    value: bool | None = await conn.fetchval(
        "SELECT enabled FROM broadcast_outputs WHERE broadcast_id = $1 AND destination_id = $2",
        to_uuid(bid),
        to_uuid(did),
    )
    return value


async def test_sync_creates_then_refreshes_one_destination(conn: asyncpg.Connection) -> None:
    did = str(uuid4())
    try:
        made = await restream.sync_config_destination(
            conn, server_url=f" {URL} ", stream_key=" key-1 ", destination_id=did
        )
        assert made == did
        await conn.execute(
            "UPDATE stream_destinations SET name = 'Kênh của tôi' WHERE id = $1", to_uuid(did)
        )
        await restream.sync_config_destination(
            conn, server_url=URL, stream_key="key-2", destination_id=did
        )
        rows = await conn.fetch(
            "SELECT platform, name, server_url, stream_key FROM stream_destinations WHERE id = $1",
            to_uuid(did),
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["platform"] == "tiktok" and row["name"] == "Kênh của tôi"  # rename kept
        assert row["server_url"] == URL and row["stream_key"] == "key-2"

        # TIKTOK_AUTO_ON=false: the destination stays, switched per session by hand.
        await restream.sync_config_destination(
            conn, server_url=URL, stream_key="key-2", auto_on=False, destination_id=did
        )
        assert did not in await restream.auto_destinations(conn)

        # A key pasted in the studio survives a restart with unchanged settings.
        await restream.update_destination(conn, did, {"stream_key": "pasted-in-studio"})
        await restream.sync_config_destination(
            conn, server_url=URL, stream_key="key-2", destination_id=did, overwrite=False
        )
        key = await conn.fetchval(
            "SELECT stream_key FROM stream_destinations WHERE id = $1", to_uuid(did)
        )
        assert key == "pasted-in-studio"

        with pytest.raises(RestreamError):
            await restream.sync_config_destination(
                conn, server_url="https://tiktok.com", stream_key="k", destination_id=did
            )
    finally:
        await conn.execute("DELETE FROM stream_destinations WHERE id = $1", to_uuid(did))
    # Without overwrite a missing destination is still created.
    try:
        await restream.sync_config_destination(
            conn, server_url=URL, stream_key="key-3", destination_id=did, overwrite=False
        )
        key = await conn.fetchval(
            "SELECT stream_key FROM stream_destinations WHERE id = $1", to_uuid(did)
        )
        assert key == "key-3"
    finally:
        await conn.execute("DELETE FROM stream_destinations WHERE id = $1", to_uuid(did))


async def started(conn: asyncpg.Connection, bid: str) -> restream.Start:
    at = await conn.fetchval("SELECT started_at FROM broadcasts WHERE id = $1", to_uuid(bid))
    return (bid, at)


async def test_take_over_gives_the_key_to_one_broadcast(conn: asyncpg.Connection) -> None:
    did = await restream.sync_config_destination(
        conn, server_url=URL, stream_key="key-1", destination_id=str(uuid4())
    )
    idle = await broadcast(conn, "idle", 30)
    first = await broadcast(conn, "live", 10)
    later = await broadcast(conn, "paused", 20)
    made = (idle, first, later)
    try:
        assert await restream.latest_start(conn) == await started(conn, later)
        assert did in await restream.auto_destinations(conn)  # TIKTOK_AUTO_ON's default
        mine = {await started(conn, b) for b in made}
        assert await restream.switched_starts(conn) & mine == set()

        assert await restream.take_over(conn, first, did)
        assert await restream.take_over(conn, later, did)
        assert await switch(conn, later, did) is True
        assert await switch(conn, first, did) is False  # one stream key, one stream
        assert await restream.switched_starts(conn) & mine == {
            await started(conn, first),
            await started(conn, later),
        }

        # Not on air: nothing to hand over.
        assert not await restream.take_over(conn, idle, did)
        assert await switch(conn, idle, did) is None

        wanted = [w for w in await restream.wanted_outputs(conn) if w.destination_id == did]
        assert [w.broadcast_id for w in wanted] == [later]
        assert wanted[0].url == f"{URL}key-1" and wanted[0].secrets == ("key-1",)

        # Pressing Start again moves the start time, so it counts as a new start.
        await conn.execute(
            "UPDATE broadcasts SET status = 'live', started_at = NOW() + interval '40 days' "
            "WHERE id = $1",
            to_uuid(idle),
        )
        assert await restream.latest_start(conn) == await started(conn, idle)
        assert await restream.take_over(conn, idle, did)
        assert await switch(conn, later, did) is False

        # A destination deleted while the API runs is skipped, not an error.
        await conn.execute("DELETE FROM stream_destinations WHERE id = $1", to_uuid(did))
        assert not await restream.take_over(conn, first, did)
    finally:
        await conn.execute("DELETE FROM stream_destinations WHERE id = $1", to_uuid(did))
        await conn.execute(
            "DELETE FROM broadcasts WHERE id = ANY($1::uuid[])", [to_uuid(b) for b in made]
        )
