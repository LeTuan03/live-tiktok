"""Voice Service — voice profiles and cached TTS audio generation."""

from __future__ import annotations

import hashlib
import os
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


def estimate_mp3_duration(file_path: str | Path) -> Optional[float]:
    """Best-effort duration: ffprobe when available, else bitrate-based estimate."""
    try:
        from app.integrations.ffmpeg.ffprobe_service import FFProbeService

        d = FFProbeService().get_duration(file_path)
        if d:
            return float(d)
    except Exception:
        pass
    try:
        size = os.path.getsize(file_path)
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
    def compute_audio_hash(self, text: str, voice_id: str, speed: float, volume: float = 1.0) -> str:
        """SHA-256 of content + voice configuration (cache key)."""
        raw_key = f"{text.strip()}||{voice_id}||{speed:.2f}||{volume:.2f}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def get_cached(self, text: str, voice_profile: Optional[VoiceProfile] = None) -> Optional[GeneratedAudio]:
        profile = voice_profile or self.get_default_profile()
        voice_id = profile.voice_id if profile else get_settings().tts_default_voice
        speed = profile.speed if profile else 1.0
        volume = profile.volume if profile else 1.0
        cached = self.voice_repo.get_cached_audio(self.compute_audio_hash(text, voice_id, speed, volume))
        if cached and os.path.exists(cached.file_path):
            return cached
        return None

    # ------------------------------------------------------------ generate
    async def _synthesize(self, text: str, output_path: Path, voice_id: str, speed: float, volume: float) -> None:
        provider = EdgeTTSProvider()
        volume_percent = int(round((volume - 1.0) * 100))
        volume_str = f"{'+' if volume_percent >= 0 else ''}{volume_percent}%"
        try:
            await provider.generate_speech(
                text=text,
                output_path=str(output_path),
                voice_id=voice_id,
                rate=speed_to_rate(speed),
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
