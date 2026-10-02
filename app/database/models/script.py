"""Script and ScriptVersion models."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, IDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.database.models.product import Product
    from app.database.models.voice import GeneratedAudio


class Script(Base, IDMixin, TimestampMixin):
    """AI-generated or user-written product presentation script."""

    __tablename__ = "scripts"

    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    style: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # friendly, consultant, concise, energetic
    target_audience: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="vi")
    speaking_speed: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    honorific: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    cta_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(default=True)

    # Relationships
    product: Mapped["Product"] = relationship("Product", back_populates="scripts")
    versions: Mapped[list[ScriptVersion]] = relationship(
        "ScriptVersion", back_populates="script", cascade="all, delete-orphan"
    )
    audio_files: Mapped[list["GeneratedAudio"]] = relationship(
        "GeneratedAudio", back_populates="script"
    )

    __table_args__ = (
        Index("idx_scripts_product", "product_id"),
        Index("idx_scripts_current", "is_current"),
    )

    def __repr__(self) -> str:
        return f"<Script(id={self.id}, title='{self.title}', v{self.version})>"


class ScriptVersion(Base, IDMixin, TimestampMixin):
    """Historical version of a script for version tracking."""

    __tablename__ = "script_versions"

    script_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    change_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    script: Mapped[Script] = relationship("Script", back_populates="versions")

    __table_args__ = (
        Index("idx_script_versions_script", "script_id"),
    )
