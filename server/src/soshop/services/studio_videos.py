"""Pre-rendered review videos for the studio: one directory per job.

``data/videos/<job_id>/`` holds ``job.json`` (what was asked), ``request.json``
(the render request handed to ``python -m soshop.advideo``), and everything
the pipeline writes: ``status.json``, ``script.json``, ``video.mp4``,
``thumb.jpg``, ``render.log``.

A render takes 10 to 20 minutes and saturates the machine, so only one runs
at a time. Like the avatar build it is a separate process, so it neither
blocks the API nor dies with a request.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel

from soshop.services.studio import ModelRow, ProductRow, resolve_voice
from soshop.services.studio_assets import data_dir, image_source_for_agent, models_dir

logger = logging.getLogger("soshop.services.studio_videos")

VideoStatusName = Literal["processing", "ready", "failed"]

_JOB_FILE = "job.json"
_REQUEST_FILE = "request.json"
_STATUS_FILE = "status.json"
_VIDEO_FILE = "video.mp4"
_THUMB_FILE = "thumb.jpg"
_LOG_FILE = "render.log"
_STALE_AFTER_S = 600.0
"""A render writes progress every few seconds; this much silence means it died."""


class VideoBusyError(RuntimeError):
    """Another video is still rendering."""


class VideoUnavailableError(RuntimeError):
    """The renderer (agent dependency group) is not installed, or inputs are missing."""


class VideoJob(BaseModel):
    id: str
    model_id: str
    product_id: str
    title: str
    created_at: datetime
    status: VideoStatusName
    progress: float | None = None
    error: str | None = None
    has_video: bool = False
    has_thumb: bool = False


def videos_dir() -> Path:
    path = data_dir() / "videos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _job_dir(job_id: str) -> Path | None:
    """The job's directory, or ``None`` for an unknown or malformed id."""
    if not job_id or not all(ch.isalnum() or ch == "-" for ch in job_id):
        return None
    path = videos_dir() / job_id
    return path if (path / _JOB_FILE).is_file() else None


def renderer_available() -> bool:
    try:
        return importlib.util.find_spec("soshop.advideo.pipeline") is not None
    except (ImportError, ValueError):
        return False


def _read_status(path: Path) -> tuple[VideoStatusName, float | None, str | None]:
    try:
        raw = json.loads((path / _STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "failed", None, "Không đọc được trạng thái dựng video."
    status = raw.get("status")
    if status not in ("processing", "ready", "failed"):
        return "failed", None, "Trạng thái dựng video không hợp lệ."
    if status == "processing":
        age_s = time.time() - (path / _STATUS_FILE).stat().st_mtime
        if age_s > _STALE_AFTER_S:
            return "failed", None, "Quá trình dựng video bị gián đoạn. Hãy tạo lại."
    return status, raw.get("progress"), raw.get("error")


def _load(path: Path) -> VideoJob | None:
    try:
        job = json.loads((path / _JOB_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    status, progress, error = _read_status(path)
    return VideoJob(
        **job,
        status=status,
        progress=progress,
        error=error,
        has_video=status == "ready" and (path / _VIDEO_FILE).is_file(),
        has_thumb=(path / _THUMB_FILE).is_file(),
    )


def list_videos() -> list[VideoJob]:
    jobs = [job for path in videos_dir().iterdir() if (job := _load(path)) is not None]
    return sorted(jobs, key=lambda job: job.created_at, reverse=True)


def get_video(job_id: str) -> VideoJob | None:
    path = _job_dir(job_id)
    return _load(path) if path is not None else None


def video_file(job_id: str) -> Path | None:
    path = _job_dir(job_id)
    file = path / _VIDEO_FILE if path is not None else None
    return file if file is not None and file.is_file() else None


def thumb_file(job_id: str) -> Path | None:
    path = _job_dir(job_id)
    file = path / _THUMB_FILE if path is not None else None
    return file if file is not None and file.is_file() else None


def delete_video(job_id: str) -> bool:
    """Remove a finished or failed job. A running render cannot be deleted."""
    path = _job_dir(job_id)
    if path is None:
        return False
    if _read_status(path)[0] == "processing":
        raise VideoBusyError("Video đang được dựng, chưa xóa được.")
    shutil.rmtree(path, ignore_errors=True)
    return True


def build_request(model: ModelRow, product: ProductRow) -> dict[str, object]:
    """The render request for ``python -m soshop.advideo --request``."""
    portrait = image_source_for_agent(model.image_url)
    if portrait is None:
        raise VideoUnavailableError("Người mẫu chưa có ảnh chân dung hợp lệ.")
    return {
        "portrait": portrait,
        "voice": resolve_voice(model.voice, model.language),
        "language": model.language,
        "product": {
            "name": product.name,
            "price": float(product.price) if product.price is not None else None,
            "currency": product.currency,
            "description": product.description,
            "highlights": product.highlights,
            "image": image_source_for_agent(product.image_url),
        },
    }


def start_video(model: ModelRow, product: ProductRow) -> VideoJob:
    """Spawn a render for ``product`` hosted by ``model``. One at a time."""
    if not renderer_available():
        raise VideoUnavailableError("Bộ dựng video chưa được cài trên server.")
    if any(job.status == "processing" for job in list_videos()):
        raise VideoBusyError("Đang dựng một video khác. Hãy đợi video đó xong.")
    request = build_request(model, product)

    job_id = uuid4().hex
    out_dir = videos_dir() / job_id
    out_dir.mkdir(parents=True)
    job = {
        "id": job_id,
        "model_id": model.id,
        "product_id": product.id,
        "title": product.name,
        "created_at": datetime.now(UTC).isoformat(),
    }
    (out_dir / _JOB_FILE).write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    (out_dir / _REQUEST_FILE).write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    status = {"status": "processing", "progress": 0.0, "error": None}
    (out_dir / _STATUS_FILE).write_text(json.dumps(status), encoding="utf-8")
    with (out_dir / _LOG_FILE).open("ab") as log_file:
        subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [
                sys.executable,
                "-m",
                "soshop.advideo",
                "--request",
                str(out_dir / _REQUEST_FILE),
                "--models",
                str(models_dir()),
                "--out",
                str(out_dir),
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
    logger.info("video render started", extra={"job_id": job_id, "product_id": product.id})
    loaded = _load(out_dir)
    if loaded is None:
        raise RuntimeError("video job vanished right after it was created")
    return loaded
