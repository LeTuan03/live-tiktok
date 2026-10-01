"""Voice Profile and Generated Audio Repository."""

from __future__ import annotations

from typing import Optional, Sequence
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.database.models.voice import GeneratedAudio, VoiceProfile


class VoiceRepository:
    """Repository for managing VoiceProfile and GeneratedAudio entities."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # --- Voice Profiles ---

    def create_profile(
        self,
        name: str,
        provider: str,
        voice_id: str,
        language: str = "vi",
        speed: float = 1.0,
        volume: float = 1.0,
        pitch: Optional[str] = None,
        is_default: bool = False,
        extra_config: Optional[str] = None,
    ) -> VoiceProfile:
        if is_default:
            self._unset_default_profiles()

        profile = VoiceProfile(
            name=name,
            provider=provider,
            voice_id=voice_id,
            language=language,
            speed=speed,
            volume=volume,
            pitch=pitch,
            is_default=is_default,
            extra_config=extra_config,
        )
        self.session.add(profile)
        self.session.commit()
        self.session.refresh(profile)
        return profile

    def update_profile(
        self,
        profile_id: int,
        **kwargs,
    ) -> Optional[VoiceProfile]:
        profile = self.get_profile_by_id(profile_id)
        if not profile:
            return None

        if kwargs.get("is_default"):
            self._unset_default_profiles()

        for key, value in kwargs.items():
            if hasattr(profile, key):
                setattr(profile, key, value)

        self.session.commit()
        self.session.refresh(profile)
        return profile

    def get_profile_by_id(self, profile_id: int) -> Optional[VoiceProfile]:
        return self.session.get(VoiceProfile, profile_id)

    def list_profiles(self) -> Sequence[VoiceProfile]:
        return self.session.scalars(
            select(VoiceProfile).order_by(desc(VoiceProfile.is_default), VoiceProfile.name)
        ).all()

    def get_default_profile() -> Optional[VoiceProfile]:
        return self.session.scalars(
            select(VoiceProfile).where(VoiceProfile.is_default == True)
        ).first()

    def delete_profile(self, profile_id: int) -> bool:
        profile = self.get_profile_by_id(profile_id)
        if not profile:
            return False
        self.session.delete(profile)
        self.session.commit()
        return True

    def _unset_default_profiles(self) -> None:
        defaults = self.session.scalars(
            select(VoiceProfile).where(VoiceProfile.is_default == True)
        ).all()
        for p in defaults:
            p.is_default = False

    # --- Generated Audio ---

    def create_generated_audio(
        self,
        script_id: int,
        file_path: str,
        content_hash: str,
        voice_profile_id: Optional[int] = None,
        duration_seconds: Optional[float] = None,
        file_size_bytes: Optional[int] = None,
    ) -> GeneratedAudio:
        audio = GeneratedAudio(
            script_id=script_id,
            voice_profile_id=voice_profile_id,
            file_path=file_path,
            duration_seconds=duration_seconds,
            file_size_bytes=file_size_bytes,
            content_hash=content_hash,
            is_cached=True,
        )
        self.session.add(audio)
        self.session.commit()
        self.session.refresh(audio)
        return audio

    def get_cached_audio(self, content_hash: str) -> Optional[GeneratedAudio]:
        return self.session.scalars(
            select(GeneratedAudio).where(GeneratedAudio.content_hash == content_hash)
        ).first()

    def get_audio_by_script(self, script_id: int) -> Sequence[GeneratedAudio]:
        return self.session.scalars(
            select(GeneratedAudio)
            .where(GeneratedAudio.script_id == script_id)
            .order_by(desc(GeneratedAudio.created_at))
        ).all()
