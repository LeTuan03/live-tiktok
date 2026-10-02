"""AIAvatar model for AI digital presenters and livestream models."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, IDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.database.models.product import Product


class AIAvatar(Base, IDMixin, TimestampMixin):
    """Represents an AI Avatar / Virtual Presenter for livestreaming and video presentation."""

    __tablename__ = "ai_avatars"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    gender: Mapped[str] = mapped_column(String(20), default="female")  # female, male
    style: Mapped[str] = mapped_column(String(100), default="Thanh lịch")  # Năng động, Thanh lịch, Chuyên gia...
    avatar_type: Mapped[str] = mapped_column(String(50), default="video_loop")  # video_loop, photo, generated_video
    
    # Path to video loop, portrait image, or composite video
    media_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    preview_image_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    
    # Chroma key settings for green-screen transparency
    transparent_bg: Mapped[bool] = mapped_column(Boolean, default=True)
    chroma_key_color: Mapped[str] = mapped_column(String(20), default="#00FF00")
    
    # Default TTS voice recommended for this avatar
    voice_id: Mapped[str] = mapped_column(String(200), default="vi-VN-HoaiMyNeural")
    
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # OBS Overlay positioning: bottom_right, bottom_left, center, pip
    obs_position: Mapped[str] = mapped_column(String(50), default="bottom_right")
    obs_scale: Mapped[float] = mapped_column(Float, default=0.35)

    # Relationships
    products: Mapped[list["Product"]] = relationship(
        "Product", back_populates="avatar"
    )

    __table_args__ = (
        Index("idx_avatars_active", "is_active"),
        Index("idx_avatars_gender", "gender"),
    )
