"""The videos a broadcast can play: finished promo videos and uploaded files.

:func:`resolve_promo` and :func:`resolve_upload` turn an item's source into
what the player needs (a URL, the video's own length, a thumbnail) or the
reason it cannot play right now. :func:`save_upload` checks an uploaded video
with ffmpeg in scratch space, then puts it in the media store under
``uploads/`` next to a thumbnail and a small JSON sidecar with what ffmpeg
measured. Both local ``/media`` and an S3 bucket answer HTTP range requests,
which the browser needs to seek.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from soshop.promo import jobs
from soshop.promo.assemble import AssembleError, ffmpeg
from soshop.promo.script import PromoProduct
from soshop.services.studio_assets import UPLOADS_PREFIX, upload_key
from soshop_server import storage

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
_CHUNK = 1024 * 1024
_BROWSER_CODECS = {"h264", "av1", "vp8", "vp9"}
"""Video codecs every current browser decodes (HEVC and ProRes are not safe)."""
_WEBM_CODECS = {"vp8", "vp9"}

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_RE = re.compile(r"Stream #[^\n]*?Video: (\w+)[^\n]*?\b(\d{2,5})x(\d{2,5})\b")
_INPUT_RE = re.compile(r"Input #0, ([\w,]+)")


class UploadRejectedError(ValueError):
    """The uploaded file is not a video the player can use."""


@dataclass(frozen=True)
class Media:
    """What the player needs for one item, or why it cannot play."""

    video_url: str | None = None
    thumb_url: str | None = None
    captions_url: str | None = None
    video_s: float | None = None
    problem: str | None = None
    product: PromoProduct | None = None
    host_name: str | None = None

    @property
    def playable(self) -> bool:
        return self.problem is None and self.video_url is not None and bool(self.video_s)


@dataclass(frozen=True)
class VideoInfo:
    duration_s: float
    codec: str
    width: int
    height: int
    container: str
    has_audio: bool


@dataclass(frozen=True)
class SavedVideo:
    url: str
    thumb_url: str | None
    duration_s: float
    width: int
    height: int
    name: str


# ----------------------------------------------------------------- resolving


def resolve_promo(job: jobs.PromoJob | None) -> Media:
    """``job`` is the promo video an item points at, ``None`` once it was deleted."""
    if job is None:
        return Media(problem="Video đã bị xóa.")
    store = storage.get_store()
    # Video and thumbnail come straight from the store (the CDN on S3); a
    # re-render writes new keys, so no cache busting is needed for them.
    thumb = store.url(job.thumb_key) if job.thumb_key else None
    if job.status != "ready" or not job.video_key:
        problem = "Video đang được tạo lại." if job.busy else "Video chưa tạo xong."
        return Media(thumb_url=thumb, problem=problem, product=job.product, host_name=job.host.name)
    if not job.duration_s:
        return Media(
            thumb_url=thumb,
            problem="Không đọc được thời lượng video.",
            product=job.product,
            host_name=job.host.name,
        )
    # Captions go through the API (a <track> must be same-origin or CORS); the
    # stamp makes the browser fetch them afresh after a re-render.
    stamp = f"v={int(job.updated_at.timestamp())}"
    captions = f"/promo/videos/{job.id}/captions?{stamp}" if job.captions_key else None
    return Media(
        video_url=store.url(job.video_key),
        thumb_url=thumb,
        captions_url=captions,
        video_s=job.duration_s,
        product=job.product,
        host_name=job.host.name,
    )


def _sibling(key: str, ext: str) -> str:
    return f"{key.rsplit('.', 1)[0]}.{ext}"


def _present(store: storage.MediaStore, key: str) -> bool:
    # Asking a bucket would block every playlist read on a network round trip.
    # Uploaded videos are never deleted, so on S3 a stored key is trusted.
    return store.exists(key) if store.kind == "local" else True


def resolve_upload(url: str, duration_s: float | None) -> Media:
    key = upload_key(url)
    store = storage.get_store()
    if key is None or not _present(store, key):
        return Media(problem="Tệp video đã bị xóa.")
    thumb_key = _sibling(key, "jpg")
    thumb_url = store.url(thumb_key) if _present(store, thumb_key) else None
    if not duration_s:
        return Media(thumb_url=thumb_url, problem="Không đọc được thời lượng video.")
    return Media(video_url=store.url(key), thumb_url=thumb_url, video_s=duration_s)


def upload_info(url: str) -> dict[str, object] | None:
    """The sidecar written by :func:`save_upload`, if ``url`` is such an upload."""
    key = upload_key(url)
    if key is None:
        return None
    raw = storage.get_store().get_bytes(_sibling(key, "json"))
    if raw is None:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# ----------------------------------------------------------------- uploading


def _ffmpeg(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [ffmpeg(), "-hide_banner", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def parse_probe(banner: str) -> VideoInfo | None:
    """What ffmpeg's ``-i`` banner says about a file, or ``None`` if not a video."""
    duration = _DURATION_RE.search(banner)
    video = _VIDEO_RE.search(banner)
    if duration is None or video is None:
        return None
    hours, minutes, seconds = duration.groups()
    container = _INPUT_RE.search(banner)
    return VideoInfo(
        duration_s=int(hours) * 3600 + int(minutes) * 60 + float(seconds),
        codec=video.group(1).lower(),
        width=int(video.group(2)),
        height=int(video.group(3)),
        container=container.group(1) if container else "",
        has_audio="Audio:" in banner,
    )


def _copy_limited(src: BinaryIO, dst: Path) -> int:
    size = 0
    with dst.open("wb") as out:
        while chunk := src.read(_CHUNK):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise UploadRejectedError("Video vượt quá 500 MB.")
            out.write(chunk)
    return size


def _check(info: VideoInfo | None) -> VideoInfo:
    if info is None:
        raise UploadRejectedError("Tệp không phải video hợp lệ.")
    if info.codec not in _BROWSER_CODECS:
        raise UploadRejectedError(
            f"Video mã hóa {info.codec.upper()} không phát được trên mọi trình duyệt. "
            "Hãy xuất lại thành MP4 (H.264)."
        )
    if info.duration_s < 1:
        raise UploadRejectedError("Video quá ngắn.")
    return info


def _prepare(incoming: Path, info: VideoInfo, out_dir: Path, stem: str) -> Path:
    """The file to store. MP4 gets its index up front for a quick start."""
    if info.codec in _WEBM_CODECS:
        if "webm" not in info.container:
            raise UploadRejectedError("Video VP8/VP9 cần ở dạng WebM. Hãy xuất lại thành MP4.")
        dst = out_dir / f"{stem}.webm"
        shutil.move(incoming, dst)
        return dst
    dst = out_dir / f"{stem}.mp4"
    args = ["-y", "-i", str(incoming), "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy"]
    args += ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dst)]
    proc = _ffmpeg(args)
    if proc.returncode != 0 or not dst.is_file():
        dst.unlink(missing_ok=True)
        raise UploadRejectedError("Không đọc được nội dung video. Hãy xuất lại thành MP4 (H.264).")
    return dst


def save_upload(src: BinaryIO, filename: str) -> SavedVideo:
    """Validate and store an uploaded video. Blocking: run it in a thread."""
    ffmpeg()  # fail with AssembleError before reading hundreds of MB
    work = storage.work_dir("uploads", uuid4().hex)
    try:
        return _save_upload(src, filename, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _save_upload(src: BinaryIO, filename: str, work: Path) -> SavedVideo:
    incoming = work / "incoming"
    if _copy_limited(src, incoming) == 0:
        raise UploadRejectedError("Tệp video trống.")
    info = _check(parse_probe(_ffmpeg(["-i", str(incoming)]).stderr))
    stem = uuid4().hex
    video = _prepare(incoming, info, work, stem)
    thumb = video.with_suffix(".jpg")
    shot = _ffmpeg(
        ["-y", "-ss", f"{min(1.0, info.duration_s / 2):.2f}", "-i", str(video)]
        + ["-frames:v", "1", "-q:v", "3", str(thumb)]
    )
    has_thumb = shot.returncode == 0 and thumb.is_file()
    name = Path(filename).name[:200] or video.name
    sidecar = {
        "duration_s": round(info.duration_s, 3),
        "width": info.width,
        "height": info.height,
        "codec": info.codec,
        "name": name,
    }
    store = storage.get_store()
    key = f"{UPLOADS_PREFIX}{video.name}"
    store.put_file(key, video, storage.content_type_for(video.name))
    if has_thumb:
        store.put_file(_sibling(key, "jpg"), thumb, "image/jpeg")
    store.put_bytes(_sibling(key, "json"), json.dumps(sidecar).encode("utf-8"), "application/json")
    return SavedVideo(
        url=store.url(key),
        thumb_url=store.url(_sibling(key, "jpg")) if has_thumb else None,
        duration_s=round(info.duration_s, 3),
        width=info.width,
        height=info.height,
        name=name,
    )


__all__ = [
    "MAX_UPLOAD_BYTES",
    "AssembleError",
    "Media",
    "SavedVideo",
    "UploadRejectedError",
    "VideoInfo",
    "parse_probe",
    "resolve_promo",
    "resolve_upload",
    "save_upload",
    "upload_info",
]
