"""Studio media: uploaded images, plus the local motion-avatar workspace.

Uploaded images go to the media store (:mod:`soshop_server.storage`) under
``uploads/<uuid>.<ext>`` and are referenced by the URL the store hands out:
``/media/uploads/<name>`` on local storage (the browser prefixes the API base
URL), the public bucket URL on S3. The agent worker and the renderers turn
such a URL back into a local file with :func:`resolve_media_path`, which
downloads it when the store is remote.

Still on the local disk under ``settings.DATA_DIR`` (git-ignored):

* ``avatars/<model_id>/`` motion-avatar assets built by ``soshop.agent.lipsync.build``
* ``models/``             model weights the avatar builder downloads on first use
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

from soshop_server import storage
from soshop_server.core.config import settings

logger = logging.getLogger("soshop.services.studio_assets")

MEDIA_UPLOADS_PREFIX = "/media/uploads/"
"""URL prefix of an upload on local storage."""
UPLOADS_PREFIX = "uploads/"
"""Store key prefix of every upload."""
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


def models_dir() -> Path:
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def avatar_dir(model_id: str) -> Path:
    return data_dir() / "avatars" / model_id


def save_image_upload(data: bytes) -> str:
    """Validate and store an uploaded image. Returns its media URL.

    Blocking (it may upload to S3): call it through ``asyncio.to_thread``.
    """
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
    key = f"{UPLOADS_PREFIX}{uuid4().hex}.{ext}"
    store = storage.get_store()
    store.put_bytes(key, data, storage.content_type_for(key))
    return store.url(key)


def upload_key(url: str | None) -> str | None:
    """The store key (``uploads/<name>``) behind an uploaded file's URL, if it is one."""
    if not url:
        return None
    key = storage.get_store().key_for_url(url)
    if key is None or not key.startswith(UPLOADS_PREFIX) or key.count("/") != 1:
        return None
    return key


def resolve_media_path(url: str | None) -> Path | None:
    """A local file for an uploaded file's URL, downloaded from S3 if needed."""
    key = upload_key(url)
    return storage.local_file(key) if key is not None else None


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
    key = upload_key(image_url)
    if key is None:
        return
    try:
        storage.get_store().delete(key)
    except Exception:
        logger.warning("could not delete portrait %s", key, exc_info=True)


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
