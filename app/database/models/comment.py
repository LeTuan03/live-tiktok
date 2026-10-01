"""Comment event and AI suggestion models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, IDMixin, TimestampMixin


class CommentEvent(Base, IDMixin, TimestampMixin):
    """A comment received during a live session."""

    __tablename__ = "comment_events"

    session_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("live_sessions.id", ondelete="SET NULL"), nullable=True
    )
    username: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # price, feature, shipping, variant, spam, inappropriate, other
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_spam: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relationships
    suggestions: Mapped[list[AISuggestion]] = relationship(
        "AISuggestion", back_populates="comment", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_comments_session", "session_id"),
        Index("idx_comments_category", "category"),
        Index("idx_comments_processed", "is_processed"),
    )

    def __repr__(self) -> str:
        return f"<CommentEvent(id={self.id}, user='{self.username}', cat='{self.category}')>"


class AISuggestion(Base, IDMixin, TimestampMixin):
    """AI-generated reply suggestion for a comment."""

    __tablename__ = "ai_suggestions"

    comment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("comment_events.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    suggested_reply: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending, approved, rejected, sent, failed
    approved_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    send_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_auto_reply: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relationships
    comment: Mapped[CommentEvent] = relationship("CommentEvent", back_populates="suggestions")

    __table_args__ = (
        Index("idx_suggestions_comment", "comment_id"),
        Index("idx_suggestions_status", "status"),
    )
