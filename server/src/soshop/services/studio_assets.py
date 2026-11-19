"""Local file storage for the live studio: uploads and motion-avatar assets.

Everything lives under ``settings.DATA_DIR`` (git-ignored):

* ``uploads/``            images uploaded from the studio, served at ``/media/uploads``
* ``avatars/<model_id>/`` motion-avatar assets built by ``soshop.agent.lipsync.build``
* ``models/``             model weights the avatar builder downloads on first use

Uploaded images are referenced by a relative URL (``/media/uploads/<name>``)
so the same value works from the browser (prefixed with the API base URL) and
from the agent worker (resolved back to a file with :func:`resolve_media_path`).
"""

from __future__ import annotations

import importlib.util
import io
import json
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel

from soshop_server.core.config import settings

logger = logging.getLogger("soshop.services.studio_assets")

MEDIA_UPLOADS_PREFIX = "/media/uploads/"
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
_EXT_BY_FORMAT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
# A build that has not touched status.json for this long is considered dead
# (worker killed, API restarted mid-build). The builder reports progress far
# more often than this.
_BUILD_STALE_AFTER_S = 300.0

AvatarStatusName = Literal["none", "processing", "ready", "failed"]


class UploadRejectedError(ValueError):
    """The uploaded bytes are not an acceptable image."""


class AvatarBuilderUnavailableError(RuntimeError):
    """The lip-sync avatar package is not installed in this environment."""


class AvatarStatus(BaseModel):
    status: AvatarStatusName = "none"
    progress: float | None = None
    error: str | None = None


def data_dir() -> Path:
    return Path(settings.DATA_DIR).resolve()


def uploads_dir() -> Path:
    path = data_dir() / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def avatar_dir(model_id: str) -> Path:
    return data_dir() / "avatars" / model_id


def save_image_upload(data: bytes) -> str:
    """Validate and store an uploaded image. Returns its relative media URL."""
    if not data:
        raise UploadRejectedError("Tệp ảnh trống.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadRejectedError("Ảnh vượt quá 8 MB.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
            image_format = image.format or ""
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise UploadRejectedError("Tệp không phải ảnh hợp lệ (JPG, PNG, WEBP).") from exc
    ext = _EXT_BY_FORMAT.get(image_format)
    if ext is None:
        raise UploadRejectedError("Chỉ hỗ trợ ảnh JPG, PNG hoặc WEBP.")
    name = f"{uuid4().hex}.{ext}"
    (uploads_dir() / name).write_bytes(data)
    return f"{MEDIA_UPLOADS_PREFIX}{name}"


def resolve_media_path(url: str | None) -> Path | None:
    """Map a relative ``/media/uploads/<name>`` URL back to its file, if any."""
    if not url or not url.startswith(MEDIA_UPLOADS_PREFIX):
        return None
    name = url[len(MEDIA_UPLOADS_PREFIX) :]
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    path = uploads_dir() / name
    return path if path.is_file() else None


def image_source_for_agent(url: str | None) -> str | None:
    """Return something the agent can load: a local file path or an http URL."""
    local = resolve_media_path(url)
    if local is not None:
        return str(local)
    if url and url.startswith(("http://", "https://")):
        return url
    return None


def remove_model_files(model_id: str, image_url: str | None) -> None:
    """Delete a model's avatar assets and its uploaded portrait. Best-effort."""
    shutil.rmtree(avatar_dir(model_id), ignore_errors=True)
    portrait = resolve_media_path(image_url)
    if portrait is not None:
        portrait.unlink(missing_ok=True)


# ------------------------------------------------------------ motion avatar


def avatar_builder_available() -> bool:
    try:
        return importlib.util.find_spec("soshop.agent.lipsync.build") is not None
    except (ImportError, ValueError):
        return False


def read_avatar_status(model_id: str) -> AvatarStatus:
    status_path = avatar_dir(model_id) / "status.json"
    if not status_path.is_file():
        return AvatarStatus()
    try:
        status = AvatarStatus.model_validate(json.loads(status_path.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        return AvatarStatus(status="failed", error="Không đọc được trạng thái dựng avatar.")
    if status.status == "processing":
        age_s = time.time() - status_path.stat().st_mtime
        if age_s > _BUILD_STALE_AFTER_S:
            return AvatarStatus(
                status="failed", error="Quá trình dựng avatar bị gián đoạn. Hãy dựng lại."
            )
    return status


def start_avatar_build(model_id: str, image_url: str | None) -> AvatarStatus:
    """Spawn the motion-avatar builder for a model. Returns the initial status.

    The builder is a separate process: it is CPU-heavy for minutes and must
    not block the API event loop or die with a request.
    """
    if not avatar_builder_available():
        raise AvatarBuilderUnavailableError("lip-sync avatar package is not installed")
    source = image_source_for_agent(image_url)
    if source is None:
        raise UploadRejectedError("Người mẫu chưa có ảnh chân dung hợp lệ.")
    if read_avatar_status(model_id).status == "processing":
        return read_avatar_status(model_id)

    out_dir = avatar_dir(model_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    initial = AvatarStatus(status="processing", progress=0.0)
    (out_dir / "status.json").write_text(initial.model_dump_json(), encoding="utf-8")
    log_path = out_dir / "build.log"
    with log_path.open("ab") as log_file:
        subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [
                sys.executable,
                "-m",
                "soshop.agent.lipsync.build",
                "--image",
                source,
                "--out",
                str(out_dir),
                "--models",
                str(models_dir()),
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
    logger.info("avatar build started", extra={"model_id": model_id})
    return initial
