"""Voice Service for managing Voice Profiles and TTS Audio Generation."""

from __future__ import annotations

import hashlib
import os
from typing import Optional, Sequence
from loguru import logger
from app.database.models.voice import GeneratedAudio, VoiceProfile
from app.database.repositories.voice_repo import VoiceRepository
from app.integrations.tts.edge_tts_provider import EdgeTTSProvider


class VoiceService:
    """Service layer for TTS generation and voice profile management."""

    def __init__(self, voice_repo: VoiceRepository, storage_dir: str = "storage/audio") -> None:
        self.voice_repo = voice_repo
        self.storage_dir = storage_dir
        os.makedirs(self.storage_dir, exist_ok=True)
        self._ensure_default_profiles()

    def _ensure_default_profiles(self) -> None:
        """Seed default voice profiles if none exist."""
        profiles = self.voice_repo.list_profiles()
        if not profiles:
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

    def compute_audio_hash(self, text: str, voice_id: str, speed: float) -> str:
        """Compute SHA256 content hash for caching generated audio."""
        raw_key = f"{text}||{voice_id}||{speed}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    async def generate_speech(
        self,
        script_id: int,
        text: str,
        voice_profile: Optional[VoiceProfile] = None,
        force_regenerate: bool = False,
    ) -> GeneratedAudio:
        """Generate audio from script text or return cached audio."""
        if not voice_profile:
            voice_profile = self.voice_repo.get_default_profile()
            if not voice_profile:
                profiles = self.voice_repo.list_profiles()
                voice_profile = profiles[0] if profiles else None

        voice_id = voice_profile.voice_id if voice_profile else "vi-VN-HoaiMyNeural"
        speed = voice_profile.speed if voice_profile else 1.0
        provider_type = voice_profile.provider if voice_profile else "edge_tts"

        content_hash = self.compute_audio_hash(text, voice_id, speed)

        # Check cache if not forcing regeneration
        if not force_regenerate:
            cached = self.voice_repo.get_cached_audio(content_hash)
            if cached and os.path.exists(cached.file_path):
                logger.info(f"Sử dụng file âm thanh cached: {cached.file_path}")
                return cached

        # Rate format string (+0%, +10%, -10%, etc.)
        speed_percent = int((speed - 1.0) * 100)
        rate_str = f"{'+' if speed_percent >= 0 else ''}{speed_percent}%"

        filename = f"audio_script_{script_id}_{content_hash[:10]}.mp3"
        output_path = os.path.join(self.storage_dir, filename)

        if provider_type == "edge_tts":
            provider = EdgeTTSProvider()
            await provider.generate_speech(
                text=text,
                output_path=output_path,
                voice_id=voice_id,
                rate=rate_str,
            )
        else:
            # Fallback to Edge TTS if unknown provider
            provider = EdgeTTSProvider()
            await provider.generate_speech(
                text=text,
                output_path=output_path,
                voice_id="vi-VN-HoaiMyNeural",
                rate="+0%",
            )

        file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0

        audio = self.voice_repo.create_generated_audio(
            script_id=script_id,
            file_path=output_path,
            content_hash=content_hash,
            voice_profile_id=voice_profile.id if voice_profile else None,
            duration_seconds=None,
            file_size_bytes=file_size,
        )
        return audio

    def get_profiles(self) -> Sequence[VoiceProfile]:
        return self.voice_repo.list_profiles()

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

    def delete_profile(self, profile_id: int) -> bool:
        return self.voice_repo.delete_profile(profile_id)
