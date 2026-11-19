"""The videos a broadcast can play: finished promo videos and uploaded files.

:func:`resolve_promo` and :func:`resolve_upload` turn an item's source into
what the player needs (a URL, the video's own length, a thumbnail) or the
reason it cannot play right now. :func:`save_upload` stores an uploaded video
in the studio uploads directory, so the existing ``/media/uploads`` mount
serves it with HTTP range requests (the browser needs them to seek), next to
a thumbnail and a small JSON sidecar with what ffmpeg measured.
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
from soshop.services.studio_assets import (
    MEDIA_UPLOADS_PREFIX,
    data_dir,
    resolve_media_path,
    uploads_dir,
)

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


def resolve_promo(job_id: str) -> Media:
    path = jobs.job_dir(data_dir(), job_id)
    job = jobs.load(path) if path is not None else None
    if path is None or job is None:
        return Media(problem="Video đã bị xóa.")
    base = f"/promo/videos/{job.id}"
    # The version stamp makes the browser fetch a re-rendered video afresh.
    stamp = f"v={int(job.updated_at.timestamp())}"
    thumb = f"{base}/thumb?{stamp}" if (path / jobs.THUMB_FILE).is_file() else None
    if job.status != "ready" or not (path / jobs.VIDEO_FILE).is_file():
        problem = "Video đang được tạo lại." if job.busy else "Video chưa tạo xong."
        return Media(thumb_url=thumb, problem=problem, product=job.product, host_name=job.host.name)
    if not job.duration_s:
        return Media(
            thumb_url=thumb,
            problem="Không đọc được thời lượng video.",
            product=job.product,
            host_name=job.host.name,
        )
    captions = f"{base}/captions?{stamp}" if (path / jobs.CAPTIONS_FILE).is_file() else None
    return Media(
        video_url=f"{base}/file?{stamp}",
        thumb_url=thumb,
        captions_url=captions,
        video_s=job.duration_s,
        product=job.product,
        host_name=job.host.name,
    )


def resolve_upload(url: str, duration_s: float | None) -> Media:
    path = resolve_media_path(url)
    if path is None:
        return Media(problem="Tệp video đã bị xóa.")
    thumb = path.with_suffix(".jpg")
    thumb_url = f"{MEDIA_UPLOADS_PREFIX}{thumb.name}" if thumb.is_file() else None
    if not duration_s:
        return Media(thumb_url=thumb_url, problem="Không đọc được thời lượng video.")
    return Media(video_url=url, thumb_url=thumb_url, video_s=duration_s)


def upload_info(url: str) -> dict[str, object] | None:
    """The sidecar written by :func:`save_upload`, if ``url`` is such an upload."""
    path = resolve_media_path(url)
    if path is None:
        return None
    try:
        data = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
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


def _store(incoming: Path, info: VideoInfo, stem: str) -> Path:
    """Move the upload into place. MP4 gets its index up front for a quick start."""
    if info.codec in _WEBM_CODECS:
        if "webm" not in info.container:
            raise UploadRejectedError("Video VP8/VP9 cần ở dạng WebM. Hãy xuất lại thành MP4.")
        dst = uploads_dir() / f"{stem}.webm"
        shutil.move(incoming, dst)
        return dst
    dst = uploads_dir() / f"{stem}.mp4"
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
    incoming = uploads_dir() / f".incoming-{uuid4().hex}"
    try:
        if _copy_limited(src, incoming) == 0:
            raise UploadRejectedError("Tệp video trống.")
        info = _check(parse_probe(_ffmpeg(["-i", str(incoming)]).stderr))
        stem = uuid4().hex
        video = _store(incoming, info, stem)
    finally:
        incoming.unlink(missing_ok=True)
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
    video.with_suffix(".json").write_text(json.dumps(sidecar), encoding="utf-8")
    return SavedVideo(
        url=f"{MEDIA_UPLOADS_PREFIX}{video.name}",
        thumb_url=f"{MEDIA_UPLOADS_PREFIX}{thumb.name}" if has_thumb else None,
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
