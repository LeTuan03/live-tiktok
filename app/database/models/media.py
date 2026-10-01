"""Media file models — MediaFile and ProductMedia junction."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, IDMixin, TimestampMixin


class MediaFile(Base, IDMixin, TimestampMixin):
    """Represents an imported media file (video or audio)."""

    __tablename__ = "media_files"

    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False, unique=True)
    media_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'video' | 'audio'
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    codec: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    format_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    bitrate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    product_links: Mapped[list["ProductMedia"]] = relationship(
        "ProductMedia", back_populates="media", cascade="all, delete-orphan"
    )
    playlist_items: Mapped[list["PlaylistItem"]] = relationship(
        "PlaylistItem", back_populates="media"
    )

    __table_args__ = (
        Index("idx_media_type", "media_type"),
        Index("idx_media_valid", "is_valid"),
    )

    def __repr__(self) -> str:
        return f"<MediaFile(id={self.id}, filename='{self.filename}', type='{self.media_type}')>"


class ProductMedia(Base, IDMixin, TimestampMixin):
    """Junction table linking products to media files."""

    __tablename__ = "product_media"

    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    media_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    product: Mapped["Product"] = relationship("Product", back_populates="media_links")
    media: Mapped[MediaFile] = relationship("MediaFile", back_populates="product_links")

    __table_args__ = (
        Index("idx_product_media_product", "product_id"),
        Index("idx_product_media_media", "media_id"),
    )
