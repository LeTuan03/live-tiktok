"""Voice profile and generated audio models."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, IDMixin, TimestampMixin


class VoiceProfile(Base, IDMixin, TimestampMixin):
    """TTS voice configuration profile."""

    __tablename__ = "voice_profiles"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # edge_tts, openai, etc.
    voice_id: Mapped[str] = mapped_column(String(200), nullable=False)
    language: Mapped[str] = mapped_column(String(10), default="vi")
    speed: Mapped[float] = mapped_column(Float, default=1.0)
    volume: Mapped[float] = mapped_column(Float, default=1.0)
    pitch: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    extra_config: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON

    # Relationships
    audio_files: Mapped[list[GeneratedAudio]] = relationship(
        "GeneratedAudio", back_populates="voice_profile"
    )

    __table_args__ = (
        Index("idx_voice_provider", "provider"),
    )

    def __repr__(self) -> str:
        return f"<VoiceProfile(id={self.id}, name='{self.name}', voice='{self.voice_id}')>"


class GeneratedAudio(Base, IDMixin, TimestampMixin):
    """Audio file generated from a script via TTS."""

    __tablename__ = "generated_audio"

    script_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False
    )
    voice_profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("voice_profiles.id", ondelete="SET NULL"), nullable=True
    )
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # SHA-256 of script content + voice config
    is_cached: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    script: Mapped["Script"] = relationship("Script", back_populates="audio_files")
    voice_profile: Mapped[Optional[VoiceProfile]] = relationship(
        "VoiceProfile", back_populates="audio_files"
    )

    __table_args__ = (
        Index("idx_audio_script", "script_id"),
        Index("idx_audio_hash", "content_hash"),
    )

    def __repr__(self) -> str:
        return f"<GeneratedAudio(id={self.id}, script_id={self.script_id})>"
