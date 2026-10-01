"""Live session and scheduler job models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, IDMixin, TimestampMixin


class LiveSession(Base, IDMixin, TimestampMixin):
    """Represents a single livestream session."""

    __tablename__ = "live_sessions"

    name: Mapped[str] = mapped_column(String(500), nullable=False)
    playlist_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("playlists.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="created"
    )  # created, running, paused, stopped, completed, error
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    total_duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    products_presented: Mapped[int] = mapped_column(Integer, default=0)
    comments_processed: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    obs_scene: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Relationships
    playlist: Mapped[Optional["Playlist"]] = relationship("Playlist")
    scheduler_jobs: Mapped[list[SchedulerJob]] = relationship(
        "SchedulerJob", back_populates="session", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_sessions_status", "status"),
        Index("idx_sessions_started", "started_at"),
    )

    def __repr__(self) -> str:
        return f"<LiveSession(id={self.id}, name='{self.name}', status='{self.status}')>"


class SchedulerJob(Base, IDMixin, TimestampMixin):
    """Tracks individual scheduled product presentation jobs within a session."""

    __tablename__ = "scheduler_jobs"

    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("live_sessions.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    playlist_item_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("playlist_items.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending, running, completed, failed, skipped
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    session: Mapped[LiveSession] = relationship("LiveSession", back_populates="scheduler_jobs")
    product: Mapped[Optional["Product"]] = relationship("Product")

    __table_args__ = (
        Index("idx_jobs_session", "session_id"),
        Index("idx_jobs_status", "status"),
    )
