"""Playlist and PlaylistItem models."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, IDMixin, TimestampMixin


class Playlist(Base, IDMixin, TimestampMixin):
    """A playlist of product presentations for scheduling."""

    __tablename__ = "playlists"

    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_loop: Mapped[bool] = mapped_column(Boolean, default=False)
    loop_count: Mapped[int] = mapped_column(Integer, default=1)
    gap_seconds: Mapped[float] = mapped_column(Float, default=5.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    items: Mapped[list[PlaylistItem]] = relationship(
        "PlaylistItem",
        back_populates="playlist",
        cascade="all, delete-orphan",
        order_by="PlaylistItem.sort_order",
    )

    def __repr__(self) -> str:
        return f"<Playlist(id={self.id}, name='{self.name}', items={len(self.items)})>"


class PlaylistItem(Base, IDMixin, TimestampMixin):
    """An item in a playlist, linking a product + media + script + audio."""

    __tablename__ = "playlist_items"

    playlist_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("playlists.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    media_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("media_files.id", ondelete="SET NULL"), nullable=True
    )
    script_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("scripts.id", ondelete="SET NULL"), nullable=True
    )
    audio_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("generated_audio.id", ondelete="SET NULL"), nullable=True
    )
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    playlist: Mapped[Playlist] = relationship("Playlist", back_populates="items")
    product: Mapped[Optional["Product"]] = relationship("Product")
    media: Mapped[Optional["MediaFile"]] = relationship("MediaFile", back_populates="playlist_items")
    script: Mapped[Optional["Script"]] = relationship("Script")
    audio: Mapped[Optional["GeneratedAudio"]] = relationship("GeneratedAudio")

    __table_args__ = (
        Index("idx_playlist_items_playlist", "playlist_id"),
        Index("idx_playlist_items_order", "playlist_id", "sort_order"),
    )
