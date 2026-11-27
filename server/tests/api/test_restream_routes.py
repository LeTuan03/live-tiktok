"""``/stream-destinations`` and ``/broadcasts/{id}/outputs`` against a real Postgres.

Skips when ``DATABASE_URL`` is not set (needs migration 0007). Mounts only the
restream and broadcast routers, so no restreamer runs: outputs report that
pushing is unavailable here, which the control room shows as a warning.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from soshop.api.routes import broadcasts as broadcast_routes
from soshop.api.routes import restream as restream_routes
from soshop.services import restream
from soshop.services.studio import to_uuid
from soshop_server.api.errors import install_error_handlers
from soshop_server.db.pool import close_pool, get_pool

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set, skipping restream route test",
)

KEY = "live_123456789_tiktok-secret-key"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(broadcast_routes.router)
    app.include_router(restream_routes.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    await close_pool()


async def test_destinations_never_give_their_key_back(client: httpx.AsyncClient) -> None:
    bad = await client.post(
        "/stream-destinations",
        json={"platform": "tiktok", "name": "TikTok shop", "server_url": "https://x"},
    )
    assert bad.status_code == 422 and "rtmp://" in bad.text
    assert (
        await client.post(
            "/stream-destinations",
            json={"platform": "lazada", "name": "x", "server_url": "rtmp://h/live"},
        )
    ).status_code == 422

    made = await client.post(
        "/stream-destinations",
        json={
            "platform": "tiktok",
            "name": " pytest TikTok ",
            "server_url": " rtmp://push-rtmp.tiktokcdn.com/game/ ",
            "stream_key": KEY,
        },
    )
    assert made.status_code == 201, made.text
    dest = made.json()
    did = dest["id"]
    try:
        assert KEY not in made.text
        assert dest["name"] == "pytest TikTok" and dest["platform_label"] == "TikTok Live"
        assert dest["server_url"] == "rtmp://push-rtmp.tiktokcdn.com/game/"
        assert dest["has_key"] and dest["key_hint"] == "••••-key"

        listed = await client.get("/stream-destinations")
        assert listed.status_code == 200 and KEY not in listed.text
        assert any(d["id"] == did for d in listed.json()["destinations"])

        assert dest["auto_on"] is False  # switched per session unless asked
        auto = await client.patch(f"/stream-destinations/{did}", json={"auto_on": True})
        assert auto.status_code == 200 and auto.json()["auto_on"] is True
        renamed = await client.patch(f"/stream-destinations/{did}", json={"name": "Kênh 2"})
        assert renamed.json()["name"] == "Kênh 2" and renamed.json()["has_key"]  # key kept
        assert renamed.json()["auto_on"] is True  # left out: kept
        rekeyed = await client.patch(
            f"/stream-destinations/{did}", json={"stream_key": "new-session-key-9876"}
        )
        assert rekeyed.json()["key_hint"] == "••••9876"
        cleared = await client.patch(f"/stream-destinations/{did}", json={"stream_key": ""})
        assert not cleared.json()["has_key"]
        missing = await client.patch(
            "/stream-destinations/00000000-0000-0000-0000-000000000000", json={"name": "x"}
        )
        assert missing.status_code == 404
    finally:
        assert (await client.delete(f"/stream-destinations/{did}")).status_code == 204
    assert (await client.delete(f"/stream-destinations/{did}")).status_code == 404


async def test_outputs_switch_a_destination_per_broadcast(client: httpx.AsyncClient) -> None:
    bid = (await client.post("/broadcasts", json={"title": "pytest phiên đẩy luồng"})).json()["id"]
    shopee = await client.post(
        "/stream-destinations",
        json={"platform": "shopee", "name": "pytest Shopee", "server_url": "rtmp://s/live"},
    )
    did = shopee.json()["id"]
    try:
        listed = await client.get(f"/broadcasts/{bid}/outputs")
        assert listed.status_code == 200
        body = listed.json()
        assert body["available"] is False and body["problem"]  # no restreamer in this app
        mine = next(o for o in body["outputs"] if o["destination"]["id"] == did)
        assert mine["enabled"] is False and mine["state"] == "off"

        on = await client.put(f"/broadcasts/{bid}/outputs/{did}", json={"enabled": True})
        assert on.status_code == 200
        mine = next(o for o in on.json()["outputs"] if o["destination"]["id"] == did)
        assert mine["enabled"] is True

        pool = await get_pool()
        async with pool.acquire() as conn:
            # Not on air: nothing is wanted yet.
            assert all(w.broadcast_id != bid for w in await restream.wanted_outputs(conn))
            await conn.execute("UPDATE broadcasts SET status = 'live' WHERE id = $1", to_uuid(bid))
            wanted = [w for w in await restream.wanted_outputs(conn) if w.broadcast_id == bid]
            assert len(wanted) == 1 and wanted[0].url is None
            assert wanted[0].problem and "stream key" in wanted[0].problem

            await client.patch(f"/stream-destinations/{did}", json={"stream_key": "shopee-key-1"})
            wanted = [w for w in await restream.wanted_outputs(conn) if w.broadcast_id == bid]
            assert wanted[0].url == "rtmp://s/live/shopee-key-1"
            assert wanted[0].secrets == ("shopee-key-1",)

            busy = await client.delete(f"/stream-destinations/{did}")
            assert busy.status_code == 409 and "pytest phiên đẩy luồng" in busy.text
            await conn.execute("UPDATE broadcasts SET status = 'idle' WHERE id = $1", to_uuid(bid))

        off = await client.put(f"/broadcasts/{bid}/outputs/{did}", json={"enabled": False})
        mine = next(o for o in off.json()["outputs"] if o["destination"]["id"] == did)
        assert mine["enabled"] is False
        unknown = await client.put(
            f"/broadcasts/{bid}/outputs/00000000-0000-0000-0000-000000000000",
            json={"enabled": True},
        )
        assert unknown.status_code == 404
        assert (await client.get("/broadcasts/not-a-uuid/outputs")).status_code == 404
    finally:
        await client.delete(f"/stream-destinations/{did}")
        await client.delete(f"/broadcasts/{bid}")


async def test_a_destination_can_be_created_auto_on(client: httpx.AsyncClient) -> None:
    made = await client.post(
        "/stream-destinations",
        json={
            "platform": "tiktok",
            "name": "pytest TikTok tự bật",
            "server_url": "rtmp://push-rtmp.tiktokcdn.com/game/",
            "stream_key": KEY,
            "auto_on": True,
        },
    )
    assert made.status_code == 201, made.text
    did = made.json()["id"]
    try:
        assert made.json()["auto_on"] is True
        pool = await get_pool()
        async with pool.acquire() as conn:
            assert did in await restream.auto_destinations(conn)
        off = await client.patch(f"/stream-destinations/{did}", json={"auto_on": False})
        assert off.json()["auto_on"] is False
        async with pool.acquire() as conn:
            assert did not in await restream.auto_destinations(conn)
    finally:
        await client.delete(f"/stream-destinations/{did}")
