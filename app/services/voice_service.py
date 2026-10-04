"""Voice Service — voice profiles and cached TTS audio generation."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Optional, Sequence

from loguru import logger

from app.config.settings import get_settings
from app.core.exceptions import TTSProviderError
from app.database.models.voice import GeneratedAudio, VoiceProfile
from app.database.repositories.voice_repo import VoiceRepository
from app.integrations.tts.edge_tts_provider import EdgeTTSProvider

# Edge TTS output: 24 kHz mono MP3 at 48 kbps → ~6000 bytes per second.
_EDGE_TTS_BYTES_PER_SECOND = 6000

# ---------------------------------------------------------------------------
# Stage direction stripping — safety net for bug #1
# Removes cue/gesture annotations that must NEVER be spoken aloud:
#   (giơ sản phẩm lại gần camera)  [cười tươi]  *nhấn mạnh*
# ---------------------------------------------------------------------------
_STAGE_DIR_RE = re.compile(
    r"""
    \(  [^)]*  \)       # (anything in parentheses)
    | \[  [^\]]*  \]    # [anything in square brackets]
    | \*  [^*]+  \*     # *anything between single asterisks*
    """,
    re.VERBOSE,
)


def strip_stage_directions(text: str) -> str:
    """Remove all stage-direction annotations from text before TTS.

    Examples
    --------
    >>> strip_stage_directions("Chào cả nhà (giơ sản phẩm lại gần camera), giá chỉ còn 199k!")
    'Chào cả nhà , giá chỉ còn 199k!'
    >>> strip_stage_directions("[cười tươi] Hôm nay có deal sốc *nhấn mạnh* nha!")
    ' Hôm nay có deal sốc  nha!'
    """
    cleaned = _STAGE_DIR_RE.sub("", text)
    # Collapse multiple spaces but preserve newlines
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return cleaned.strip()


def estimate_mp3_duration(file_path: str | Path) -> Optional[float]:
    """Get accurate duration from ffprobe. Falls back to bitrate-based estimate with warning."""
    try:
        from app.integrations.ffmpeg.ffprobe_service import FFProbeService

        d = FFProbeService().get_duration(file_path)
        if d:
            return float(d)
    except Exception:
        pass
    try:
        size = os.path.getsize(file_path)
        logger.warning("ffprobe không khả dụng; dùng ước tính bitrate (filesize/6000) cho {}", file_path)
        return round(size / _EDGE_TTS_BYTES_PER_SECOND, 1)
    except OSError:
        return None



def speed_to_rate(speed: float) -> str:
    """Convert a 1.0-based multiplier into the Edge TTS rate string (+10%, -20%, ...)."""
    percent = int(round((speed - 1.0) * 100))
    return f"{'+' if percent >= 0 else ''}{percent}%"


class VoiceService:
    """Service layer for TTS generation and voice profile management."""

    def __init__(self, voice_repo: VoiceRepository, storage_dir: str | Path | None = None) -> None:
        self.voice_repo = voice_repo
        settings = get_settings()
        self.storage_dir = Path(storage_dir) if storage_dir else settings.effective_audio_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_default_profiles()

    # ------------------------------------------------------------ profiles
    def _ensure_default_profiles(self) -> None:
        """Seed default voice profiles if none exist."""
        try:
            profiles = self.voice_repo.list_profiles()
        except Exception:
            self.voice_repo.session.rollback()
            profiles = []
        if profiles:
            return
        logger.info("Khởi tạo danh sách Voice Profile mặc định...")
        self.voice_repo.create_profile(
            name="Hoài My (Nữ Việt Nam)",
            provider="edge_tts",
            voice_id="vi-VN-HoaiMyNeural",
            language="vi",
            speed=1.0,
            is_default=True,
        )
        self.voice_repo.create_profile(
            name="Nam Minh (Nam Việt Nam)",
            provider="edge_tts",
            voice_id="vi-VN-NamMinhNeural",
            language="vi",
            speed=1.0,
            is_default=False,
        )

    def get_profiles(self) -> Sequence[VoiceProfile]:
        return self.voice_repo.list_profiles()

    def get_default_profile(self) -> Optional[VoiceProfile]:
        profile = self.voice_repo.get_default_profile()
        if profile:
            return profile
        profiles = self.voice_repo.list_profiles()
        return profiles[0] if profiles else None

    def create_profile(
        self,
        name: str,
        voice_id: str,
        provider: str = "edge_tts",
        language: str = "vi",
        speed: float = 1.0,
        is_default: bool = False,
    ) -> VoiceProfile:
        return self.voice_repo.create_profile(
            name=name,
            provider=provider,
            voice_id=voice_id,
            language=language,
            speed=speed,
            is_default=is_default,
        )

    def update_profile(self, profile_id: int, **fields) -> Optional[VoiceProfile]:
        return self.voice_repo.update_profile(profile_id, **fields)

    def delete_profile(self, profile_id: int) -> bool:
        return self.voice_repo.delete_profile(profile_id)

    # --------------------------------------------------------------- cache
    def compute_audio_hash(self, text: str, voice_id: str, speed: float = 1.0, volume: float = 1.0) -> str:
        """SHA-256 of content + voice configuration (cache key)."""
        spd = speed if speed is not None else 1.0
        vol = volume if volume is not None else 1.0
        raw_key = f"{text.strip()}||{voice_id}||{spd:.2f}||{vol:.2f}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def get_cached(self, text: str, voice_profile: Optional[VoiceProfile] = None) -> Optional[GeneratedAudio]:
        profile = voice_profile or self.get_default_profile()
        voice_id = profile.voice_id if profile else get_settings().tts_default_voice
        speed = profile.speed if profile and profile.speed is not None else 1.0
        volume = profile.volume if profile and profile.volume is not None else 1.0
        cached = self.voice_repo.get_cached_audio(self.compute_audio_hash(text, voice_id, speed, volume))
        if cached and os.path.exists(cached.file_path):
            return cached
        return None

    # ------------------------------------------------------------ generate
    async def _synthesize(self, text: str, output_path: Path, voice_id: str, speed: float, volume: float) -> None:
        # Safety net: NEVER let stage directions reach the TTS engine (bug #1)
        text = strip_stage_directions(text)
        if not text.strip():
            raise TTSProviderError("Nội dung kịch bản trống sau khi lọc ghi chú cử chỉ.")
        provider = EdgeTTSProvider()
        vol = volume if volume is not None else 1.0
        volume_percent = int(round((vol - 1.0) * 100))
        volume_str = f"{'+' if volume_percent >= 0 else ''}{volume_percent}%"
        try:
            await provider.generate_speech(
                text=text,
                output_path=str(output_path),
                voice_id=voice_id,
                rate=speed_to_rate(speed if speed is not None else 1.0),
                volume=volume_str,
            )
        except Exception as e:
            msg = str(e)
            hint = ""
            if any(k in msg.lower() for k in ("getaddrinfo", "connect", "timeout", "ssl", "403", "401")):
                hint = " Kiểm tra kết nối Internet — Edge TTS cần mạng để tổng hợp giọng."
            raise TTSProviderError(f"Edge TTS lỗi: {msg}.{hint}") from e
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise TTSProviderError("Edge TTS không tạo được file âm thanh (file rỗng).")

    async def synthesize_structured_script(
        self,
        script_id: int,
        structured_script: Any,
        voice_profile: Optional[VoiceProfile] = None,
        force_regenerate: bool = False,
    ) -> tuple[GeneratedAudio, Any]:
        """Synthesize TTS audio for each segment, determine exact segment durations with ffprobe,
        and concatenate into a synchronized master audio track.
        """
        if not structured_script.segments:
            raise TTSProviderError("Kịch bản không có phân đoạn nào để đọc.")

        profile = voice_profile or self.get_default_profile()
        voice_id = profile.voice_id if profile else get_settings().tts_default_voice
        speed = profile.speed if profile and profile.speed is not None else 1.0
        volume = profile.volume if profile and profile.volume is not None else 1.0

        seg_files: list[Path] = []
        for i, seg in enumerate(structured_script.segments):
            clean_text = seg.clean_text_for_tts()
            if not clean_text:
                continue

            seg_hash = self.compute_audio_hash(clean_text, voice_id, speed, volume)
            seg_file = self.storage_dir / f"seg_audio_{script_id}_{i}_{seg_hash[:8]}.mp3"

            if force_regenerate or not seg_file.exists() or seg_file.stat().st_size == 0:
                await self._synthesize(clean_text, seg_file, voice_id, speed, volume)

            dur = self.get_audio_duration(seg_file)
            seg.duration_seconds = dur
            seg.audio_path = str(seg_file)
            seg_files.append(seg_file)

        if not seg_files:
            raise TTSProviderError("Không có đoạn kịch bản hợp lệ nào sau khi loại bỏ cử chỉ.")

        # Master combined audio
        content_hash = self.compute_audio_hash(structured_script.total_text, voice_id, speed, volume)
        master_file = self.storage_dir / f"audio_script_{script_id}_{content_hash[:10]}.mp3"

        if len(seg_files) == 1:
            import shutil
            shutil.copyfile(str(seg_files[0]), str(master_file))
        else:
            from app.integrations.ffmpeg.ffprobe_service import FFProbeService
            import subprocess, tempfile

            ffmpeg_exe = FFProbeService()._ffmpeg_path
            concat_ok = False
            if ffmpeg_exe and Path(ffmpeg_exe).exists():
                with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
                    for sf in seg_files:
                        escaped = str(sf).replace("\\", "/")
                        f.write(f"file '{escaped}'\n")
                    list_path = f.name
                try:
                    cmd = [ffmpeg_exe, "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", str(master_file)]
                    res = subprocess.run(cmd, capture_output=True, timeout=30)
                    if res.returncode == 0 and master_file.exists() and master_file.stat().st_size > 0:
                        concat_ok = True
                except Exception as e:
                    logger.debug("Lỗi ffmpeg concat, chuyển sang binary merge: {}", e)
                finally:
                    if os.path.exists(list_path):
                        try:
                            os.unlink(list_path)
                        except OSError:
                            pass

            if not concat_ok:
                with open(master_file, "wb") as outfile:
                    for sf in seg_files:
                        with open(sf, "rb") as infile:
                            outfile.write(infile.read())

        master_dur = self.get_audio_duration(master_file)
        file_size = master_file.stat().st_size

        audio = self.voice_repo.create_generated_audio(
            script_id=script_id,
            file_path=str(master_file),
            content_hash=content_hash,
            voice_profile_id=profile.id if profile else None,
            duration_seconds=master_dur,
            file_size_bytes=file_size,
            voice_id=voice_id,
        )
        logger.info(
            "Đã tạo master audio cho kịch bản phân đoạn: {} segments, tổng {:.1f}s",
            len(seg_files),
            master_dur,
        )
        return audio, structured_script

    async def generate_speech(
        self,
        script_id: int,
        text: str,
        voice_profile: Optional[VoiceProfile] = None,
        force_regenerate: bool = False,
    ) -> GeneratedAudio:
        """Generate audio from script text, or return cached audio when unchanged."""
        text = text.strip()
        if not text:
            raise TTSProviderError("Nội dung kịch bản trống, không thể tạo giọng đọc.")

        # Check if text is structured JSON with segments
        if text.startswith("{") and "segments" in text:
            try:
                from app.services.script_segment_service import parse_and_validate_script_json
                structured = parse_and_validate_script_json(text)
                if structured.segments:
                    audio, _ = await self.synthesize_structured_script(
                        script_id=script_id,
                        structured_script=structured,
                        voice_profile=voice_profile,
                        force_regenerate=force_regenerate,
                    )
                    return audio
            except Exception as e:
                logger.warning("Không thể tổng hợp theo phân đoạn JSON, chuyển sang đọc toàn văn: {}", e)

        profile = voice_profile or self.get_default_profile()
        voice_id = profile.voice_id if profile else get_settings().tts_default_voice
        speed = profile.speed if profile else 1.0
        volume = profile.volume if profile else 1.0

        content_hash = self.compute_audio_hash(text, voice_id, speed, volume)

        if not force_regenerate:
            cached = self.voice_repo.get_cached_audio(content_hash)
            if cached and os.path.exists(cached.file_path):
                logger.info("Sử dụng file âm thanh cached: {}", cached.file_path)
                return cached

        filename = f"audio_script_{script_id}_{content_hash[:10]}.mp3"
        output_path = self.storage_dir / filename

        await self._synthesize(text, output_path, voice_id, speed, volume)

        file_size = output_path.stat().st_size
        duration = estimate_mp3_duration(output_path)

        audio = self.voice_repo.create_generated_audio(
            script_id=script_id,
            file_path=str(output_path),
            content_hash=content_hash,
            voice_profile_id=profile.id if profile else None,
            duration_seconds=duration,
            file_size_bytes=file_size,
            voice_id=voice_id,
        )
        logger.info("Đã tạo audio TTS: {} ({:.1f}s)", output_path.name, duration or 0)
        return audio


    async def generate_preview(
        self, text: str, voice_id: str, speed: float = 1.0, volume: float = 1.0
    ) -> Path:
        """Synthesize a throw-away preview clip (not stored in the database)."""
        text = text.strip()
        if not text:
            raise TTSProviderError("Vui lòng nhập văn bản để đọc thử.")
        preview_dir = get_settings().effective_tts_cache_dir / "preview"
        preview_dir.mkdir(parents=True, exist_ok=True)
        digest = self.compute_audio_hash(text, voice_id, speed, volume)[:12]
        output_path = preview_dir / f"preview_{digest}.mp3"
        if output_path.exists() and output_path.stat().st_size > 0:
            return output_path
        await self._synthesize(text, output_path, voice_id, speed, volume)
        return output_path

    async def generate_adhoc(self, text: str, voice_profile: Optional[VoiceProfile] = None) -> Path:
        """Synthesize speech for text that is not a saved script (e.g. a comment reply)."""
        profile = voice_profile or self.get_default_profile()
        voice_id = profile.voice_id if profile else get_settings().tts_default_voice
        speed = profile.speed if profile else 1.0
        volume = profile.volume if profile else 1.0
        return await self.generate_preview(text, voice_id, speed, volume)

    async def synthesize_speech(
        self,
        text: str,
        voice_id: Optional[str] = None,
        speed: float = 1.0,
        volume: float = 1.0,
        output_path: Optional[str | Path] = None,
    ) -> str:
        """Synthesize speech directly to an output file and return the file path as string."""
        text = text.strip()
        if not text:
            raise TTSProviderError("Nội dung kịch bản trống, không thể tạo giọng đọc.")

        target_voice_id = voice_id or get_settings().tts_default_voice
        if output_path:
            out_file = Path(output_path)
            out_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            digest = self.compute_audio_hash(text, target_voice_id, speed, volume)[:12]
            out_file = self.storage_dir / f"tts_{digest}.mp3"

        await self._synthesize(text, out_file, target_voice_id, speed, volume)
        return str(out_file)

    def get_audio_duration(self, file_path: str | Path) -> float:
        """Get duration of audio file in seconds, or 0.0 if unable to determine."""
        duration = estimate_mp3_duration(file_path)
        return float(duration) if duration is not None else 0.0

    # ------------------------------------------------------------- lookups
    def get_audio_for_script(self, script_id: int) -> Sequence[GeneratedAudio]:
        return self.voice_repo.get_audio_by_script(script_id)

    def get_latest_audio_for_script(self, script_id: int) -> Optional[GeneratedAudio]:
        for a in self.voice_repo.get_audio_by_script(script_id):
            if os.path.exists(a.file_path):
                return a
        return None
