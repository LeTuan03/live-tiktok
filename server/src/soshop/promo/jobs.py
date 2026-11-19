"""Promo video jobs, one directory each under ``data/promo/<job_id>/``.

``job.json`` holds the whole state (product and host snapshots, script,
per-shot progress); next to it live the reference images handed to Flow, the
raw clips Flow returns (``shot-N.mp4``), and the final ``video.mp4``,
``thumb.jpg`` and ``captions.vtt``. Snapshots mean a video stays consistent
with what was on screen when it was made, even if the product is edited later.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from soshop.promo.script import PromoHost, PromoProduct, PromoScript

JobStatus = Literal["draft", "queued", "rendering", "assembling", "ready", "failed"]
ShotStatus = Literal["pending", "submitting", "generating", "downloading", "done", "failed"]

JOB_FILE = "job.json"
VIDEO_FILE = "video.mp4"
THUMB_FILE = "thumb.jpg"
CAPTIONS_FILE = "captions.vtt"


class ShotState(BaseModel):
    index: int
    status: ShotStatus = "pending"
    prompt: str = ""
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    has_clip: bool = False
    transcript: str | None = None
    """What Whisper heard in the clip."""
    speech_match: float | None = None
    """Share of the scripted words heard, in order (0 to 1)."""
    speech_start: float | None = None
    speech_end: float | None = None


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

    @property
    def busy(self) -> bool:
        return self.status in ("queued", "rendering", "assembling")


def now() -> datetime:
    return datetime.now(UTC)


def root_dir(data_dir: Path) -> Path:
    path = data_dir / "promo"
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_dir(data_dir: Path, job_id: str) -> Path | None:
    """The job's directory, or ``None`` for an unknown or malformed id."""
    if not job_id or not all(ch.isalnum() for ch in job_id):
        return None
    path = root_dir(data_dir) / job_id
    return path if (path / JOB_FILE).is_file() else None


def new_job(
    data_dir: Path,
    *,
    product: PromoProduct,
    host: PromoHost,
    language: Literal["vi", "en"],
    shot_count: int,
    flow_model: str,
) -> tuple[PromoJob, Path]:
    job_id = uuid4().hex
    path = root_dir(data_dir) / job_id
    path.mkdir(parents=True)
    stamp = now()
    job = PromoJob(
        id=job_id,
        product=product,
        host=host,
        language=language,
        shot_count=shot_count,
        flow_model=flow_model,
        created_at=stamp,
        updated_at=stamp,
    )
    save(path, job)
    return job, path


def save(path: Path, job: PromoJob) -> None:
    """Write ``job.json`` atomically so a reader never sees half a file."""
    job.updated_at = now()
    tmp = path / f"{JOB_FILE}.tmp"
    tmp.write_text(job.model_dump_json(indent=1), encoding="utf-8")
    os.replace(tmp, path / JOB_FILE)


def load(path: Path) -> PromoJob | None:
    try:
        return PromoJob.model_validate(json.loads((path / JOB_FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def list_all(data_dir: Path) -> list[PromoJob]:
    jobs = [job for p in root_dir(data_dir).iterdir() if p.is_dir() and (job := load(p))]
    return sorted(jobs, key=lambda j: j.created_at, reverse=True)


def clip_path(path: Path, index: int) -> Path:
    return path / f"shot-{index + 1}.mp4"


def remove(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
