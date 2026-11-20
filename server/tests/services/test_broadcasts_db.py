"""Livestream sessions against a real Postgres (needs migrations 0004 and 0005).

Skips when ``DATABASE_URL`` is not set so the default ``uv run pytest`` stays
hermetic. Promo videos are rows in promo_videos, uploads fake files under a temporary
``DATA_DIR``; the rows created here are deleted at the end.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from soshop.promo import jobs
from soshop.promo.script import PromoHost, PromoProduct
from soshop.services import broadcasts, catalog
from soshop.services.broadcasts import BroadcastError, ItemSpec
from soshop.services.studio import to_uuid
from soshop_server.core.config import settings
from soshop_server.db.pool import close_pool, get_pool

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set, skipping broadcast integration test",
)


@pytest.fixture
async def conn() -> AsyncIterator[asyncpg.Connection]:
    pool = await get_pool()
    async with pool.acquire() as connection:
        yield connection
    await close_pool()


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    return tmp_path


async def promo_video(
    conn: asyncpg.Connection, product: catalog.CatalogProduct, seconds: float
) -> str:
    job = jobs.new_job(
        product=PromoProduct(id=product.id, name="snapshot name", price=1000),
        host=PromoHost(name="Mai"),
        language="vi",
        shot_count=3,
        flow_model="Veo 3.1 - Lite",
    )
    job.video_key = jobs.new_key(job.id, "video", "mp4")
    job.status, job.duration_s = "ready", seconds
    await jobs.insert(conn, job)
    return job.id


async def forget_product(conn: asyncpg.Connection, product: catalog.CatalogProduct) -> None:
    await conn.execute("DELETE FROM promo_videos WHERE product_id = $1", product.id)
    await conn.execute("DELETE FROM products WHERE id = $1", to_uuid(product.id))


def uploaded_video(root: Path, seconds: float) -> str:
    uploads = root / "uploads"
    uploads.mkdir(exist_ok=True)
    name = uuid4().hex
    (uploads / f"{name}.mp4").write_bytes(b"mp4")
    (uploads / f"{name}.json").write_text(json.dumps({"duration_s": seconds}), encoding="utf-8")
    return f"/media/uploads/{name}.mp4"


async def test_broadcast_playlist_and_playback(conn: asyncpg.Connection, data_dir: Path) -> None:
    product = await catalog.create_product(
        conn, {"name": "pytest phiên son", "price": 59000, "image_url": "/media/uploads/x.jpg"}
    )
    first_video = await promo_video(conn, product, 20.0)
    second_video = await promo_video(conn, product, 30.0)
    created = await broadcasts.create_broadcast(conn, {"title": "pytest phiên live"})
    bid = created.id
    try:
        assert created.playback.status == "idle" and created.items == []
        with pytest.raises(BroadcastError):
            await broadcasts.control(conn, bid, "start")

        filled = await broadcasts.add_items(
            conn,
            bid,
            [
                ItemSpec("product", "promo", first_video),
                ItemSpec("product", "promo", second_video, duration_s=45),
                ItemSpec("intro", "upload", uploaded_video(data_dir, 5.0)),
            ],
        )
        one, two = filled.items
        assert [i.source_ref for i in filled.items] == [first_video, second_video]
        assert one.product is not None and one.product.name == "pytest phiên son"
        assert (one.slot_s, two.slot_s, filled.cycle_s) == (20.0, 45.0, 65.0)
        assert filled.intro is not None and filled.intro.playable
        assert filled.total_s is None  # continuous

        live = await broadcasts.control(conn, bid, "start")
        assert live.playback.status == "live" and live.playback.position is not None
        assert live.playback.position.phase == "intro"
        on_one = await broadcasts.control(conn, bid, "next")
        assert on_one.playback.position is not None
        assert on_one.playback.position.item_id == one.id

        # Reordering on air keeps the current video on air.
        reordered = await broadcasts.reorder_items(conn, bid, [two.id, one.id])
        assert [i.id for i in reordered.items] == [two.id, one.id]
        assert reordered.playback.position is not None
        assert reordered.playback.position.item_id == one.id

        paused = await broadcasts.control(conn, bid, "pause")
        assert paused.playback.status == "paused"
        assert paused.playback.playhead is not None and not paused.playback.playhead.playing

        # Removing the current video hands over to what follows, still paused.
        removed = await broadcasts.remove_item(conn, bid, one.id)
        assert removed.playback.status == "paused" and removed.playback.position is not None
        assert removed.playback.position.item_id == two.id

        assert await broadcasts.promo_video_usage(conn, second_video) == ["pytest phiên live"]
        with pytest.raises(BroadcastError):
            await broadcasts.delete_broadcast(conn, bid)

        resized = await broadcasts.update_item(conn, bid, two.id, {"duration_s": None})
        assert resized.items[0].slot_s == 30.0
        ended = await broadcasts.control(conn, bid, "finish")  # no outro: ends at once
        assert ended.playback.status == "ended" and ended.playback.playhead is None
    finally:
        await conn.execute("DELETE FROM broadcasts WHERE id = $1", to_uuid(bid))
        await forget_product(conn, product)


async def test_count_mode_show_ends_by_itself(conn: asyncpg.Connection, data_dir: Path) -> None:
    product = await catalog.create_product(conn, {"name": "pytest phiên hết giờ"})
    video = await promo_video(conn, product, 10.0)
    created = await broadcasts.create_broadcast(
        conn, {"title": "pytest đếm vòng", "loop_mode": "count", "loop_count": 2}
    )
    bid = to_uuid(created.id)
    try:
        await broadcasts.add_items(conn, created.id, [ItemSpec("product", "promo", video)])
        live = await broadcasts.control(conn, created.id, "start")
        assert live.total_s == 20.0 and live.playback.status == "live"
        # Pretend the show started long ago: reading it settles the end.
        await conn.execute(
            "UPDATE broadcasts SET playhead = jsonb_set(playhead, '{at_ms}', '0') WHERE id = $1",
            bid,
        )
        settled = await broadcasts.get_broadcast(conn, created.id)
        assert settled is not None and settled.playback.status == "ended"
        assert await conn.fetchval("SELECT status FROM broadcasts WHERE id = $1", bid) == "ended"
        summaries = {s.id: s for s in await broadcasts.list_broadcasts(conn)}
        assert summaries[created.id].status == "ended"
        assert summaries[created.id].item_count == 1
    finally:
        await conn.execute("DELETE FROM broadcasts WHERE id = $1", bid)
        await forget_product(conn, product)


async def test_unknown_or_unplayable_sources(conn: asyncpg.Connection, data_dir: Path) -> None:
    created = await broadcasts.create_broadcast(conn, {"title": "pytest nguồn lạ"})
    try:
        with pytest.raises(BroadcastError):
            await broadcasts.add_items(conn, created.id, [ItemSpec("product", "promo", "0" * 32)])
        with pytest.raises(BroadcastError):
            await broadcasts.add_items(
                conn, created.id, [ItemSpec("outro", "upload", "/media/uploads/none.mp4")]
            )
        with pytest.raises(BroadcastError):
            await broadcasts.reorder_items(conn, created.id, [str(uuid4())])
        assert await broadcasts.get_broadcast(conn, str(uuid4())) is None
    finally:
        await conn.execute("DELETE FROM broadcasts WHERE id = $1", to_uuid(created.id))
