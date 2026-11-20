"""Promo jobs in Postgres (needs migration 0006).

Skips when ``DATABASE_URL`` is not set so the default ``uv run pytest`` stays
hermetic. Rows created here are deleted at the end.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

from soshop.promo import jobs, runner
from soshop.promo.script import PromoHost, PromoProduct
from soshop_server.core.config import settings
from soshop_server.db.pool import close_pool, get_pool

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set, skipping promo job integration test",
)

PRODUCT_ID = "pytest-promo-product"


@pytest.fixture
async def conn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[asyncpg.Connection]:
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    pool = await get_pool()
    async with pool.acquire() as connection:
        try:
            yield connection
        finally:
            await connection.execute("DELETE FROM promo_videos WHERE product_id = $1", PRODUCT_ID)
    await close_pool()


def _job() -> jobs.PromoJob:
    return jobs.new_job(
        product=PromoProduct(id=PRODUCT_ID, name="Son dưỡng"),
        host=PromoHost(name="Mai"),
        language="vi",
        shot_count=3,
        flow_model="Veo 3.1 - Lite",
    )


async def test_round_trip_list_and_delete(conn: asyncpg.Connection) -> None:
    job = _job()
    await jobs.insert(conn, job)
    job.status = "rendering"
    job.shots = [jobs.ShotState(index=0, speech_match=0.9, clip_key=jobs.key(job.id, "a.mp4"))]
    assert await jobs.save(conn, job)

    loaded = await jobs.load(conn, job.id)
    assert loaded is not None and loaded.busy
    assert loaded.shots[0].speech_match == 0.9 and loaded.shots[0].clip_key == job.shots[0].clip_key
    assert loaded.product.name == "Son dưỡng"
    assert [j.id for j in await jobs.list_all(conn, PRODUCT_ID)] == [job.id]
    assert job.id in {j.id for j in await jobs.list_busy(conn)}
    assert set(await jobs.load_many(conn, {job.id, "0" * 32, "../x"})) == {job.id}

    await jobs.delete(conn, job.id)
    assert await jobs.load(conn, job.id) is None
    assert not await jobs.save(conn, job)  # a deleted video is never brought back
    assert await jobs.load(conn, "../etc") is None


async def test_recover_orphans_fails_jobs_left_rendering(conn: asyncpg.Connection) -> None:
    job = _job()
    job.status = "rendering"
    job.shots = [
        jobs.ShotState(index=0, status="done"),
        jobs.ShotState(index=1, status="generating"),
    ]
    await jobs.insert(conn, job)
    await runner.recover_orphans()
    loaded = await jobs.load(conn, job.id)
    assert loaded is not None and loaded.status == "failed" and loaded.error
    assert [s.status for s in loaded.shots] == ["done", "failed"]
