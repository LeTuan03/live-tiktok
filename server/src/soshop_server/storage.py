"""Where media files live: local disk in development, an S3 bucket in production.

Studio uploads (images, videos) and promo video files are addressed by an
object *key* such as ``uploads/<uuid>.jpg`` or ``promo/<job>/video-<rev>.mp4``.
:func:`get_store` returns the backend picked by ``settings.MEDIA_STORAGE``:

* ``local`` (the default): files under ``settings.DATA_DIR``, served by the API
  at ``/media/<key>`` (see :mod:`soshop_server.api.routes.media`).
* ``s3``: objects in ``settings.BUCKET_NAME``, read by browsers straight from
  ``settings.S3_PUBLIC_URL`` (a CDN in front of the bucket). ``/media/<key>``
  then redirects there, so URLs stored before the switch keep working.

Keys are never reused for different bytes: uploads get a fresh uuid and a
re-rendered promo file gets a new revision. That is what lets the CDN cache
every object for a day without serving a stale video.

The interface is synchronous on purpose. Callers either run inside
``asyncio.to_thread`` (uploads, ffmpeg work) or move small objects; it keeps
the sync broadcast resolver and the ffmpeg pipeline free of async plumbing.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from soshop_server.core.config import settings

logger = logging.getLogger("soshop_server.storage")

MEDIA_URL_PREFIX = "/media/"
PREFIXES = frozenset({"uploads", "promo"})
"""Top-level key folders. Nothing else under ``DATA_DIR`` is ever served."""
CACHE_CONTROL = "public, max-age=86400"
_SEGMENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,199}")
_MISSING_TTL_S = 30.0
"""How long an S3 "does not exist" answer is trusted before asking again."""


class StorageError(RuntimeError):
    """The storage backend failed or is misconfigured."""


def valid_key(key: str) -> bool:
    """True for ``<prefix>/<name>[/<name>...]`` with plain, non-hidden segments."""
    parts = key.split("/")
    return (
        len(parts) >= 2
        and parts[0] in PREFIXES
        and all(_SEGMENT.fullmatch(part) and ".." not in part for part in parts[1:])
    )


def _require_key(key: str) -> str:
    if not valid_key(key):
        raise StorageError(f"invalid media key: {key!r}")
    return key


def new_name(stem: str, ext: str) -> str:
    """A fresh file name like ``video-1a2b3c4d.mp4`` for a re-rendered output."""
    return f"{stem}-{uuid4().hex[:12]}.{ext.lstrip('.')}"


class MediaStore(Protocol):
    kind: Literal["local", "s3"]

    def url(self, key: str) -> str: ...
    def key_for_url(self, url: str) -> str | None: ...
    def put_file(self, key: str, src: Path, content_type: str) -> None: ...
    def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...
    def get_bytes(self, key: str) -> bytes | None: ...
    def download(self, key: str, dst: Path) -> bool: ...
    def exists(self, key: str) -> bool: ...
    def local_path(self, key: str) -> Path | None: ...
    def delete(self, key: str) -> None: ...
    def delete_prefix(self, prefix: str) -> None: ...


def _key_from_media_path(url: str) -> str | None:
    """``/media/<key>`` (any query string dropped) to ``<key>``."""
    path = urlsplit(url).path
    if not path.startswith(MEDIA_URL_PREFIX):
        return None
    key = path[len(MEDIA_URL_PREFIX) :]
    return key if valid_key(key) else None


class LocalStore:
    """Files under ``root`` (``settings.DATA_DIR``), one file per key."""

    kind: Literal["local", "s3"] = "local"

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root.joinpath(*_require_key(key).split("/"))

    def url(self, key: str) -> str:
        return f"{MEDIA_URL_PREFIX}{_require_key(key)}"

    def key_for_url(self, url: str) -> str | None:
        return _key_from_media_path(url)

    def put_file(self, key: str, src: Path, content_type: str) -> None:
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.resolve() != dst.resolve():
            shutil.copyfile(src, dst)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.{uuid4().hex[:8]}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dst)

    def get_bytes(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            return path.read_bytes()
        except OSError:
            return None

    def download(self, key: str, dst: Path) -> bool:
        path = self._path(key)
        if not path.is_file():
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        if path.resolve() != dst.resolve():
            shutil.copyfile(path, dst)
        return True

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def local_path(self, key: str) -> Path | None:
        path = self._path(key)
        return path if path.is_file() else None

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> None:
        shutil.rmtree(self._path(prefix.rstrip("/")), ignore_errors=True)


class S3Store:
    """An S3-compatible bucket (Tigris, AWS S3, MinIO) with a public read URL."""

    kind: Literal["local", "s3"] = "s3"

    def __init__(self, client: Any, bucket: str, public_base: str) -> None:
        self._s3 = client
        self.bucket = bucket
        self.public_base = public_base.rstrip("/")
        self._seen: set[str] = set()
        self._missing: dict[str, float] = {}

    def url(self, key: str) -> str:
        return f"{self.public_base}/{_require_key(key)}"

    def key_for_url(self, url: str) -> str | None:
        if url.startswith(f"{self.public_base}/"):
            key = urlsplit(url[len(self.public_base) + 1 :]).path
            return key if valid_key(key) else None
        return _key_from_media_path(url)

    def _extra(self, content_type: str) -> dict[str, str]:
        return {"ContentType": content_type, "CacheControl": CACHE_CONTROL}

    def _stored(self, key: str) -> None:
        self._seen.add(key)
        self._missing.pop(key, None)

    def put_file(self, key: str, src: Path, content_type: str) -> None:
        self._s3.upload_file(
            str(src), self.bucket, _require_key(key), ExtraArgs=self._extra(content_type)
        )
        self._stored(key)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self._s3.put_object(
            Bucket=self.bucket, Key=_require_key(key), Body=data, **self._extra(content_type)
        )
        self._stored(key)

    def _not_found(self, exc: Exception) -> bool:
        code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
        return code in ("404", "NoSuchKey", "NotFound")

    def get_bytes(self, key: str) -> bytes | None:
        try:
            obj = self._s3.get_object(Bucket=self.bucket, Key=_require_key(key))
        except Exception as exc:
            if self._not_found(exc):
                return None
            raise StorageError(f"could not read {key}: {exc}") from exc
        data: bytes = obj["Body"].read()
        return data

    def download(self, key: str, dst: Path) -> bool:
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.{uuid4().hex[:8]}.part")
        try:
            self._s3.download_file(self.bucket, _require_key(key), str(tmp))
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            if self._not_found(exc):
                return False
            raise StorageError(f"could not download {key}: {exc}") from exc
        os.replace(tmp, dst)
        return True

    def exists(self, key: str) -> bool:
        """HEAD the object. Keys are immutable, so a hit is remembered for good."""
        _require_key(key)
        if key in self._seen:
            return True
        checked = self._missing.get(key)
        if checked is not None and time.monotonic() - checked < _MISSING_TTL_S:
            return False
        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if not self._not_found(exc):
                logger.warning("media HEAD %s failed: %s", key, exc)
                return False
            self._missing[key] = time.monotonic()
            return False
        self._stored(key)
        return True

    def local_path(self, key: str) -> Path | None:
        return None

    def _forget(self, key: str) -> None:
        self._seen.discard(key)
        self._missing.pop(key, None)

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self.bucket, Key=_require_key(key))
        self._forget(key)

    def delete_prefix(self, prefix: str) -> None:
        prefix = _require_key(prefix.rstrip("/")) + "/"
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [obj["Key"] for obj in page.get("Contents", [])]
            if not keys:
                continue
            self._s3.delete_objects(
                Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in keys]}
            )
            for key in keys:
                self._forget(key)


@lru_cache(maxsize=4)
def _s3_store(endpoint: str | None, bucket: str, public_base: str, region: str) -> S3Store:
    import boto3
    from botocore.config import Config as BotoConfig

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 4}),
    )
    return S3Store(client, bucket, public_base)


def get_store() -> MediaStore:
    """The configured backend. Cheap to call; the S3 client is built once."""
    if settings.MEDIA_STORAGE == "s3":
        if not settings.BUCKET_NAME or not settings.S3_PUBLIC_URL:
            raise StorageError("MEDIA_STORAGE=s3 needs BUCKET_NAME and S3_PUBLIC_URL")
        return _s3_store(
            settings.AWS_ENDPOINT_URL_S3,
            settings.BUCKET_NAME,
            settings.S3_PUBLIC_URL,
            settings.S3_REGION,
        )
    return LocalStore(data_root())


def data_root() -> Path:
    return Path(settings.DATA_DIR).resolve()


def work_dir(*parts: str) -> Path:
    """Scratch space on this machine (ffmpeg input and output, Flow downloads)."""
    path = data_root().joinpath("work", *parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def local_file(key: str) -> Path | None:
    """A file on this machine holding ``key``'s bytes, downloading it if needed.

    Local storage returns the stored file itself. S3 keeps a copy under
    ``DATA_DIR/cache``; keys never change content, so the copy never goes stale.
    """
    store = get_store()
    if not valid_key(key):
        return None
    direct = store.local_path(key)
    if direct is not None or store.kind == "local":
        return direct
    cached = data_root().joinpath("cache", *key.split("/"))
    if cached.is_file():
        return cached
    with contextlib.suppress(StorageError):
        if store.download(key, cached):
            return cached
    return None


def content_type_for(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower()
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "mp4": "video/mp4",
        "webm": "video/webm",
        "vtt": "text/vtt",
        "json": "application/json",
        "txt": "text/plain; charset=utf-8",
    }.get(ext, "application/octet-stream")


__all__ = [
    "CACHE_CONTROL",
    "MEDIA_URL_PREFIX",
    "PREFIXES",
    "LocalStore",
    "MediaStore",
    "S3Store",
    "StorageError",
    "content_type_for",
    "data_root",
    "get_store",
    "local_file",
    "new_name",
    "valid_key",
    "work_dir",
]
