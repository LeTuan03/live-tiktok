"""Promo video jobs: state in Postgres, files in the media store.

One row per job in ``promo_videos`` (migration 0006). ``state`` holds the
whole :class:`PromoJob`: product and host snapshots, script, per-shot
progress, and the media-store key of every file the job made (one clip per
shot, the final video, its thumbnail and captions, the last Flow error
screenshot). Snapshots keep a video consistent with what was on screen when
it was made, even if the product is edited later.

Files live under ``promo/<job_id>/`` in the store (:mod:`soshop_server.storage`).
A re-rendered file gets a new name, so a CDN never serves an old take. Work
that needs files on disk (Flow downloads, ffmpeg) happens in
:func:`work_dir`, scratch space on the rendering machine.

The functions take an asyncpg connection or pool. Nothing here talks to the
browser, so ``tests/promo/`` stays hermetic.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from soshop.promo.script import PromoHost, PromoProduct, PromoScript
from soshop_server import storage

JobStatus = Literal["draft", "queued", "rendering", "assembling", "ready", "failed"]
ShotStatus = Literal["pending", "submitting", "generating", "downloading", "done", "failed"]


class ShotState(BaseModel):
    index: int
    status: ShotStatus = "pending"
    prompt: str = ""
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    has_clip: bool = False
    clip_key: str | None = None
    """Media-store key of the clip Flow rendered for this shot."""
    transcript: str | None = None
    """What Whisper heard in the clip."""
    speech_match: float | None = None
    """Share of the scripted words heard, in order (0 to 1)."""
    speech_start: float | None = None
    speech_end: float | None = None

    def drop_clip(self) -> str | None:
        """Forget the clip so the next render makes a new one. Returns its key."""
        key, self.clip_key, self.has_clip = self.clip_key, None, False
        return key


class PromoJob(BaseModel):
    id: str
    product: PromoProduct
    host: PromoHost
    language: Literal["vi", "en"]
    shot_count: int
    flow_model: str
    script: PromoScript | None = None
    status: JobStatus = "draft"
    stage: str = ""
    error: str | None = None
    shots: list[ShotState] = Field(default_factory=list)
    flow_project_url: str | None = None
    created_at: datetime
    updated_at: datetime
    duration_s: float | None = None
    video_key: str | None = None
    thumb_key: str | None = None
    captions_key: str | None = None
    debug_key: str | None = None
    """The Flow tab as it looked when the last step failed."""

    @property
    def busy(self) -> bool:
        return self.status in ("queued", "rendering", "assembling")

    def file_keys(self) -> list[str]:
        """Every media-store key the job points at."""
        keys = [self.video_key, self.thumb_key, self.captions_key, self.debug_key]
        keys += [shot.clip_key for shot in self.shots]
        return [key for key in keys if key]


def now() -> datetime:
    return datetime.now(UTC)


def valid_id(job_id: str) -> bool:
    return 0 < len(job_id) <= 64 and job_id.isascii() and job_id.isalnum()


def key(job_id: str, name: str) -> str:
    """The media-store key of file ``name`` of job ``job_id``."""
    return f"promo/{job_id}/{name}"


def new_key(job_id: str, stem: str, ext: str) -> str:
    """A fresh key for a newly rendered file, e.g. ``promo/<id>/video-<rev>.mp4``."""
    return key(job_id, storage.new_name(stem, ext))


def work_dir(job_id: str) -> Path:
    """Scratch directory for one job on this machine."""
    if not valid_id(job_id):
        raise ValueError(f"invalid promo job id: {job_id!r}")
    return storage.work_dir("promo", job_id)


def new_job(
    *,
    product: PromoProduct,
    host: PromoHost,
    language: Literal["vi", "en"],
    shot_count: int,
    flow_model: str,
) -> PromoJob:
    """A fresh draft job, not yet stored (see :func:`insert`)."""
    stamp = now()
    return PromoJob(
        id=uuid4().hex,
        product=product,
        host=host,
        language=language,
        shot_count=shot_count,
        flow_model=flow_model,
        created_at=stamp,
        updated_at=stamp,
    )


def _parse(raw: Any) -> PromoJob | None:
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return PromoJob.model_validate(data)
    except ValueError:
        return None


async def insert(db: Any, job: PromoJob) -> None:
    await db.execute(
        """
        INSERT INTO promo_videos (id, product_id, status, state, created_at, updated_at)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6)
        """,
        job.id,
        job.product.id,
        job.status,
        job.model_dump_json(),
        job.created_at,
        job.updated_at,
    )


async def save(db: Any, job: PromoJob) -> bool:
    """Write the whole job. False when the row is gone (the video was deleted)."""
    job.updated_at = now()
    result = await db.execute(
        """
        UPDATE promo_videos
           SET product_id = $2, status = $3, state = $4::jsonb, updated_at = $5
         WHERE id = $1
        """,
        job.id,
        job.product.id,
        job.status,
        job.model_dump_json(),
        job.updated_at,
    )
    return str(result).endswith(" 1")


async def load(db: Any, job_id: str) -> PromoJob | None:
    if not valid_id(job_id):
        return None
    raw = await db.fetchval("SELECT state FROM promo_videos WHERE id = $1", job_id)
    return _parse(raw) if raw is not None else None


async def load_many(db: Any, job_ids: set[str]) -> dict[str, PromoJob]:
    ids = sorted(i for i in job_ids if valid_id(i))
    if not ids:
        return {}
    rows = await db.fetch(
        "SELECT state FROM promo_videos WHERE id = ANY($1::text[])",
        ids,
    )
    found = (_parse(row["state"]) for row in rows)
    return {job.id: job for job in found if job is not None}


async def list_all(db: Any, product_id: str | None = None) -> list[PromoJob]:
    rows = await db.fetch(
        """
        SELECT state FROM promo_videos
         WHERE $1::text IS NULL OR product_id = $1
         ORDER BY created_at DESC
        """,
        product_id,
    )
    return [job for row in rows if (job := _parse(row["state"])) is not None]


async def list_busy(db: Any) -> list[PromoJob]:
    rows = await db.fetch(
        "SELECT state FROM promo_videos WHERE status IN ('queued', 'rendering', 'assembling')"
    )
    return [job for row in rows if (job := _parse(row["state"])) is not None]


async def delete(db: Any, job_id: str) -> None:
    await db.execute("DELETE FROM promo_videos WHERE id = $1", job_id)


def remove_files(job_id: str) -> None:
    """Delete a job's stored files and scratch directory. Blocking, best-effort."""
    if not valid_id(job_id):
        return
    storage.get_store().delete_prefix(f"promo/{job_id}")
    shutil.rmtree(storage.data_root() / "work" / "promo" / job_id, ignore_errors=True)


def delete_keys(keys: list[str | None]) -> None:
    """Delete files that a newer render replaced. Blocking, best-effort."""
    store = storage.get_store()
    for item in keys:
        if item and storage.valid_key(item):
            try:
                store.delete(item)
            except Exception:  # noqa: S112 - leftover files only cost storage
                continue
