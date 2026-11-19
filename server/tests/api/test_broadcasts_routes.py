"""``/broadcasts`` routes end to end against a real Postgres.

Skips when ``DATABASE_URL`` is not set. Mounts only the broadcasts router, so
no LiveKit or agent machinery starts.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from soshop.api.routes import broadcasts as broadcast_routes
from soshop.promo import jobs
from soshop.promo.script import PromoHost, PromoProduct
from soshop_server.api.errors import install_error_handlers
from soshop_server.core.config import settings
from soshop_server.db.pool import close_pool

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set, skipping broadcast route test",
)


@pytest.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(broadcast_routes, "_STREAM_SECONDS", 0.0)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(broadcast_routes.router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    await close_pool()


def promo_video(root: Path, seconds: float) -> str:
    job, path = jobs.new_job(
        root,
        product=PromoProduct(id="00000000-0000-0000-0000-000000000000", name="Son thử"),
        host=PromoHost(name="Mai"),
        language="vi",
        shot_count=3,
        flow_model="Veo 3.1 - Lite",
    )
    (path / jobs.VIDEO_FILE).write_bytes(b"mp4")
    job.status, job.duration_s = "ready", seconds
    jobs.save(path, job)
    return job.id


async def test_broadcast_routes(client: httpx.AsyncClient, tmp_path: Path) -> None:
    assert (await client.post("/broadcasts", json={"title": ""})).status_code == 422
    created = await client.post("/broadcasts", json={"title": "pytest route phiên"})
    assert created.status_code == 201
    bid = created.json()["id"]
    try:
        add = await client.post(
            f"/broadcasts/{bid}/items",
            json={"items": [{"source": "promo", "source_ref": promo_video(tmp_path, 12.0)}]},
        )
        assert add.status_code == 200
        item = add.json()["items"][0]
        assert item["playable"] and item["title"] == "Son thử"  # snapshot: no catalog row

        bad = await client.post(f"/broadcasts/{bid}/control", json={"action": "explode"})
        assert bad.status_code == 422
        not_live = await client.post(f"/broadcasts/{bid}/control", json={"action": "pause"})
        assert not_live.status_code == 409

        started = await client.post(f"/broadcasts/{bid}/control", json={"action": "start"})
        assert started.status_code == 200
        playback = started.json()["playback"]
        assert playback["status"] == "live" and playback["position"]["item_id"] == item["id"]
        assert isinstance(playback["server_time_ms"], int)

        events = await client.get(f"/broadcasts/{bid}/events")
        assert events.status_code == 200
        assert events.headers["content-type"].startswith("text/event-stream")
        data = [line for line in events.text.splitlines() if line.startswith("data: {")]
        assert data and f'"id":"{bid}"' in data[0]

        listed = await client.get("/broadcasts")
        assert any(
            b["id"] == bid and b["now_playing"] == "Son thử" for b in listed.json()["broadcasts"]
        )

        refused = await client.delete(f"/broadcasts/{bid}")
        assert refused.status_code == 409
        await client.post(f"/broadcasts/{bid}/control", json={"action": "stop"})
    finally:
        assert (await client.delete(f"/broadcasts/{bid}")).status_code == 204
    assert (await client.get(f"/broadcasts/{bid}")).status_code == 404
    assert (await client.get("/broadcasts/not-a-uuid/events")).status_code == 404
