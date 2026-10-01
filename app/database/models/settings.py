"""Application settings, integration settings, and activity log models."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, IDMixin, TimestampMixin


class IntegrationSetting(Base, IDMixin, TimestampMixin):
    """Settings for external integrations (OBS, TikTok, AI providers, etc.)."""

    __tablename__ = "integration_settings"

    integration_name: Mapped[str] = mapped_column(
        String(100), nullable=False
    )  # obs, tiktok, openai, edge_tts, etc.
    setting_key: Mapped[str] = mapped_column(String(200), nullable=False)
    setting_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_sensitive: Mapped[bool] = mapped_column(default=False)

    __table_args__ = (
        Index(
            "idx_integration_unique",
            "integration_name",
            "setting_key",
            unique=True,
        ),
    )

    def __repr__(self) -> str:
        return f"<IntegrationSetting({self.integration_name}.{self.setting_key})>"


class AppSetting(Base, IDMixin, TimestampMixin):
    """General application key-value settings stored in DB."""

    __tablename__ = "app_settings"

    setting_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    setting_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        Index("idx_app_settings_category", "category"),
    )


class ActivityLog(Base, IDMixin, TimestampMixin):
    """Application activity log for audit and debugging."""

    __tablename__ = "activity_logs"

    level: Mapped[str] = mapped_column(
        String(10), nullable=False, default="INFO"
    )  # INFO, WARNING, ERROR
    category: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # product, media, script, tts, obs, session, comment, system
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("idx_logs_level", "level"),
        Index("idx_logs_category", "category"),
        Index("idx_logs_created", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<ActivityLog(level='{self.level}', action='{self.action}')>"
