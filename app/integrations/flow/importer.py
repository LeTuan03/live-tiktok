"""Nạp video tải từ Flow vào LiveFlow: kiểm tra → chuẩn hoá → đặt đúng chỗ.

Flow xuất 24fps, có thể là 720p và có tiếng. LiveFlow/OBS chạy mượt nhất với
1080x1920 @30fps h264 yuv420p, nên mọi clip đều đi qua một bước chuẩn hoá.

Module này dùng chung cho cả giao diện (Flow Studio) và dòng lệnh
(``scripts/import_flow_clips.py``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

from app.integrations.flow.pack import (
    KIND_AVATAR_BASE,
    KIND_AVATAR_CUE,
    KIND_PORTRAIT,
    KIND_PRODUCT_SCENE,
    FlowJob,
    FlowPack,
)

TARGET_W, TARGET_H, TARGET_FPS = 1080, 1920, 30
VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".mkv")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")

# Ngưỡng đánh giá nền xanh (tỉ lệ pixel xanh ở khung giữa clip)
GREEN_MIN_OK = 0.30
GREEN_MIN_WARN = 0.12


class FFmpegMissingError(RuntimeError):
    """Không tìm thấy ffmpeg/ffprobe."""


def find_tools() -> Tuple[str, str]:
    """Trả về (ffmpeg, ffprobe), ưu tiên cấu hình của app rồi mới tới PATH."""
    ffmpeg = ffprobe = None
    try:
        from app.utils.ffmpeg_locator import find_ffmpeg, find_ffprobe

        ffmpeg = find_ffmpeg() or shutil.which("ffmpeg")
        ffprobe = find_ffprobe() or shutil.which("ffprobe")
    except Exception:
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")

    if not ffmpeg or not ffprobe:
        raise FFmpegMissingError(
            "Không tìm thấy ffmpeg/ffprobe. Chạy INSTALL.bat và chọn tải FFmpeg, "
            "hoặc đặt LIVEFLOW_FFMPEG_PATH trong .env"
        )
    return str(ffmpeg), str(ffprobe)


def _run(cmd: List[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ---------------------------------------------------------------------------
# Kiểm tra clip
# ---------------------------------------------------------------------------
@dataclass
class ClipInspection:
    path: Path
    ok: bool = True
    width: int = 0
    height: int = 0
    duration: float = 0.0
    fps: float = 0.0
    has_audio: bool = False
    green_ratio: Optional[float] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def is_vertical(self) -> bool:
        return self.height > self.width

    @property
    def frame_label(self) -> str:
        return f"{self.width}x{self.height}" if self.ok else "-"


def probe(ffprobe: str, path: Path) -> Optional[dict]:
    try:
        out = _run(
            [ffprobe, "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", str(path)],
            timeout=60,
        )
        if out.returncode != 0:
            return None
        return json.loads(out.stdout)
    except Exception:
        return None


def measure_green(ffmpeg: str, path: Path, tmp_dir: Optional[Path] = None) -> Optional[float]:
    """Tỉ lệ pixel nền xanh ở khung giữa clip (0.0-1.0). None = không đo được."""
    try:
        import numpy as np
        from PIL import Image
    except Exception:
        return None

    # Dùng thư mục tạm của hệ thống — không rắc file rác vào thư mục tải về của người dùng.
    tmp_dir = tmp_dir or Path(tempfile.gettempdir()) / "liveflow_flow_probe"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    frame = tmp_dir / f"_probe_{Path(path).stem}.png"
    try:
        _run([ffmpeg, "-v", "error", "-ss", "1", "-i", str(path),
              "-frames:v", "1", "-y", str(frame)], timeout=90)
        if not frame.exists():
            return None
        arr = np.array(Image.open(frame).convert("RGB")).astype("int16")
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        green = (g > 90) & (g - r > 40) & (g - b > 40)
        return float(green.mean())
    except Exception:
        return None
    finally:
        frame.unlink(missing_ok=True)


def inspect_clip(
    path: Path | str,
    *,
    ffmpeg: str = "",
    ffprobe: str = "",
    expect_green: bool = True,
    expect_silent: bool = False,
    max_duration: float = 0.0,
) -> ClipInspection:
    """Đo clip và trả về cảnh báo bằng tiếng Việt trước khi nạp."""
    if not ffmpeg or not ffprobe:
        ffmpeg, ffprobe = find_tools()
    p = Path(path)
    rec = ClipInspection(path=p)

    info = probe(ffprobe, p)
    if not info:
        rec.ok = False
        rec.warnings.append("Không đọc được file (hỏng hoặc không phải video)")
        return rec

    vstreams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    astreams = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if not vstreams:
        rec.ok = False
        rec.warnings.append("File không có luồng video")
        return rec

    v = vstreams[0]
    rec.width = int(v.get("width", 0) or 0)
    rec.height = int(v.get("height", 0) or 0)
    rec.has_audio = bool(astreams)
    try:
        num, _, den = str(v.get("avg_frame_rate", "0/1")).partition("/")
        rec.fps = round(float(num) / float(den or 1), 2) if float(den or 1) else 0.0
    except Exception:
        rec.fps = 0.0
    try:
        rec.duration = float(info.get("format", {}).get("duration", 0) or 0)
    except Exception:
        rec.duration = 0.0

    if rec.height <= rec.width:
        rec.warnings.append(
            f"Khung NGANG {rec.frame_label} — cần khung DỌC 9:16. "
            "Đổi 'Aspect ratio' trong Flow sang 9:16 rồi tạo lại, nếu không clip sẽ bị thêm viền."
        )
    elif abs((rec.width / max(rec.height, 1)) - (9 / 16)) > 0.02:
        rec.warnings.append(f"Tỉ lệ {rec.frame_label} không chuẩn 9:16 — sẽ bị thêm viền")

    if expect_green:
        rec.green_ratio = measure_green(ffmpeg, p)
        if rec.green_ratio is None:
            rec.warnings.append("Không đo được nền xanh (thiếu Pillow/numpy)")
        elif rec.green_ratio < GREEN_MIN_WARN:
            rec.warnings.append(
                f"Nền xanh chỉ {rec.green_ratio:.0%} — quá ít, OBS sẽ không tách được người mẫu. "
                "Tạo lại với yêu cầu 'flat uniform #00FF00 green screen, no shadows'."
            )
        elif rec.green_ratio < GREEN_MIN_OK:
            rec.warnings.append(f"Nền xanh {rec.green_ratio:.0%} — hơi ít, kiểm tra lại trên OBS")

    if expect_silent and rec.has_audio:
        rec.warnings.append("Clip có tiếng — clip cử chỉ nên câm, LiveFlow sẽ bỏ phần tiếng này")
    if not expect_silent and not rec.has_audio:
        rec.warnings.append("Clip không có tiếng — LiveFlow sẽ ghép giọng Edge-TTS vào")

    if max_duration and rec.duration > max_duration + 1.5:
        rec.warnings.append(f"Clip dài {rec.duration:.1f}s — dài hơn mức đặt ({max_duration:.0f}s)")

    return rec


# ---------------------------------------------------------------------------
# Chuẩn hoá
# ---------------------------------------------------------------------------
def normalize(
    src: Path | str,
    dest: Path | str,
    *,
    ffmpeg: str = "",
    pad_color: str = "0x00FF00",
    keep_audio: bool = True,
) -> bool:
    """Scale/pad về 1080x1920 @30fps h264 yuv420p — định dạng OBS đọc mượt nhất."""
    if not ffmpeg:
        ffmpeg, _ = find_tools()
    src, dest = Path(src), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    vf = (
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
        f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2:color={pad_color},"
        f"fps={TARGET_FPS}"
    )
    cmd = [ffmpeg, "-v", "error", "-i", str(src), "-vf", vf,
           "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p"]
    if keep_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += ["-an"]
    cmd += ["-movflags", "+faststart", "-y", str(dest)]

    try:
        r = _run(cmd)
        if r.returncode != 0:
            logger.error("ffmpeg chuẩn hoá lỗi cho {}: {}", src.name, (r.stderr or "")[-400:])
            return False
        return dest.exists() and dest.stat().st_size > 1000
    except Exception as e:
        logger.error("Không chuẩn hoá được {}: {}", src, e)
        return False


def make_poster(video: Path | str, dest_jpg: Path | str, *, ffmpeg: str = "") -> bool:
    """Trích 1 khung làm ảnh đại diện người mẫu."""
    if not ffmpeg:
        ffmpeg, _ = find_tools()
    dest_jpg = Path(dest_jpg)
    dest_jpg.parent.mkdir(parents=True, exist_ok=True)
    try:
        _run([ffmpeg, "-v", "error", "-ss", "1", "-i", str(video),
              "-frames:v", "1", "-q:v", "2", "-y", str(dest_jpg)], timeout=90)
        return dest_jpg.exists()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Nạp một việc trong gói
# ---------------------------------------------------------------------------
@dataclass
class ImportResult:
    ok: bool
    job_id: str
    dest_path: str = ""
    message: str = ""
    inspection: Optional[ClipInspection] = None
    registered_cue: str = ""
    poster_path: str = ""

    @property
    def warnings(self) -> List[str]:
        return self.inspection.warnings if self.inspection else []


def _avatar_output_dir() -> Path:
    from app.config.settings import get_settings

    return get_settings().effective_avatar_dir


def _veo_output_dir() -> Path:
    from app.config.settings import get_settings

    return get_settings().effective_veo_dir


def import_job(
    pack: FlowPack,
    job: FlowJob,
    source: Path | str,
    *,
    avatar_id: Optional[int] = None,
    register_cue: bool = True,
    update_avatar_record: bool = True,
    ffmpeg: str = "",
    ffprobe: str = "",
) -> ImportResult:
    """Chuẩn hoá `source` và nạp vào đúng chỗ theo loại việc, rồi đánh dấu job xong.

    Clip cử chỉ được đăng ký ở trạng thái **chưa duyệt** — người vận hành vẫn phải
    tự xem lại rồi bấm Duyệt trong app (ràng buộc §4.2).
    """
    src = Path(source)
    if not src.exists():
        return ImportResult(False, job.job_id, message=f"Không thấy file: {src}")

    if not ffmpeg or not ffprobe:
        try:
            ffmpeg, ffprobe = find_tools()
        except FFmpegMissingError as e:
            return ImportResult(False, job.job_id, message=str(e))

    green = bool(pack.meta.get("green_screen", True))

    # --- Ảnh chân dung: chỉ copy, không qua ffmpeg ---
    if job.kind == KIND_PORTRAIT:
        if src.suffix.lower() not in IMAGE_SUFFIXES:
            return ImportResult(False, job.job_id, message=f"Cần file ảnh, nhận được {src.suffix}")
        dest = _avatar_output_dir() / job.target_filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        job.reference_image = str(dest)
        # Mọi clip sau dùng chung ảnh này làm Ingredients
        for other in pack.jobs:
            if other.kind in (KIND_AVATAR_BASE, KIND_AVATAR_CUE) and not other.reference_image:
                other.reference_image = str(dest)
        _mark_done(pack, job, dest)
        return ImportResult(True, job.job_id, str(dest),
                            message="Đã lưu ảnh chân dung — kéo ảnh này vào Ingredients trong Flow")

    if src.suffix.lower() not in VIDEO_SUFFIXES:
        return ImportResult(False, job.job_id, message=f"Cần file video, nhận được {src.suffix}")

    expect_green = green and job.kind in (KIND_AVATAR_BASE, KIND_AVATAR_CUE)
    expect_silent = job.kind == KIND_AVATAR_CUE or (
        job.kind == KIND_AVATAR_BASE and not job.speech_text
    )
    inspection = inspect_clip(
        src,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        expect_green=expect_green,
        expect_silent=expect_silent,
        max_duration=float(job.duration or 0),
    )
    if not inspection.ok:
        return ImportResult(False, job.job_id, message="; ".join(inspection.warnings),
                            inspection=inspection)

    if job.kind == KIND_PRODUCT_SCENE:
        dest = _veo_output_dir() / job.target_filename
        pad = "black"
        keep_audio = True
    else:
        dest = _avatar_output_dir() / job.target_filename
        pad = "0x00FF00" if green else "black"
        keep_audio = not expect_silent

    if not normalize(src, dest, ffmpeg=ffmpeg, pad_color=pad, keep_audio=keep_audio):
        return ImportResult(False, job.job_id,
                            message="Chuẩn hoá thất bại (xem log để biết lỗi ffmpeg)",
                            inspection=inspection)

    result = ImportResult(True, job.job_id, str(dest), inspection=inspection)

    # --- Clip nền người mẫu ---
    if job.kind == KIND_AVATAR_BASE:
        poster = dest.with_suffix(".jpg")
        if make_poster(dest, poster, ffmpeg=ffmpeg):
            result.poster_path = str(poster)
        if avatar_id and update_avatar_record:
            _update_avatar_record(avatar_id, dest, poster if result.poster_path else None, green)
        # Clip nền cũng chính là cue IDLE_TALK — đăng ký luôn để khỏi phải tạo clip thứ hai.
        if avatar_id and register_cue:
            _register_cue(avatar_id, job.cue or "IDLE_TALK", dest, inspection.duration, pack.model, result)
        result.message = "Đã nạp clip nền (loop) cho người mẫu"

    # --- Clip cử chỉ ---
    elif job.kind == KIND_AVATAR_CUE and job.cue:
        if avatar_id and register_cue:
            _register_cue(avatar_id, job.cue, dest, inspection.duration, pack.model, result)
        else:
            result.message = "Đã chuẩn hoá (chưa gán người mẫu nên không đăng ký cue)"

    else:
        result.message = f"Đã nạp cảnh {job.scene_index}"

    _mark_done(pack, job, dest)
    return result


def _register_cue(
    avatar_id: int, cue: str, dest: Path, duration: float, model: str, result: ImportResult
) -> None:
    """Đăng ký clip vào thư viện cử chỉ, luôn ở trạng thái CHƯA duyệt (§4.2)."""
    try:
        from app.presenter.clip_library import PresenterClipLibrary

        PresenterClipLibrary().register_clip(
            avatar_id=avatar_id,
            cue=cue,
            file_path=str(dest),
            duration=duration,
            is_approved=False,  # buộc người vận hành xem lại rồi mới duyệt
            notes=f"Tạo bằng Google Flow ({model})",
        )
        result.registered_cue = cue
        result.message = f"Đã đăng ký cue {cue} (CHƯA duyệt)"
    except Exception as e:
        result.message = f"Đã chuẩn hoá nhưng không đăng ký được cue: {e}"


def _mark_done(pack: FlowPack, job: FlowJob, dest: Path) -> None:
    job.status = "IMPORTED"
    job.imported_path = str(dest)
    job.imported_at = time.time()
    try:
        pack.save()
    except Exception as e:  # pragma: no cover
        logger.warning("Không ghi được manifest gói Flow: {}", e)


def _update_avatar_record(
    avatar_id: int, video: Path, poster: Optional[Path], green: bool
) -> None:
    """Trỏ người mẫu trong database sang clip vừa nạp."""
    try:
        from app.database.repositories.avatar_repo import AvatarRepository
        from app.database.session import get_db

        data: Dict[str, object] = {
            "media_path": str(video),
            "avatar_type": "video_loop",
            "transparent_bg": bool(green),
        }
        if poster:
            data["preview_image_path"] = str(poster)
        with get_db() as db:
            AvatarRepository(db).update(avatar_id, **data)
        logger.info("Đã trỏ người mẫu id={} sang clip Flow {}", avatar_id, video.name)
    except Exception as e:
        logger.warning("Không cập nhật được người mẫu id={}: {}", avatar_id, e)
