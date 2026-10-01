"""Product and ProductVariant models."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, IDMixin, TimestampMixin


class Product(Base, IDMixin, TimestampMixin):
    """Represents a product for livestream presentation.

    Stores all product information that AI uses to generate scripts.
    AI must only use verified data from this model.
    """

    __tablename__ = "products"

    name: Mapped[str] = mapped_column(String(500), nullable=False)
    sku: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, unique=True)
    category: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    promo_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verified_features: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ingredients: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sizes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_audience: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    usage_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    warnings: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    product_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    image_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    video_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    variants: Mapped[list[ProductVariant]] = relationship(
        "ProductVariant", back_populates="product", cascade="all, delete-orphan"
    )
    media_links: Mapped[list["ProductMedia"]] = relationship(
        "ProductMedia", back_populates="product", cascade="all, delete-orphan"
    )
    scripts: Mapped[list["Script"]] = relationship(
        "Script", back_populates="product", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_products_category", "category"),
        Index("idx_products_active", "is_active"),
        Index("idx_products_sort", "sort_order"),
    )

    def __repr__(self) -> str:
        return f"<Product(id={self.id}, name='{self.name}', sku='{self.sku}')>"


class ProductVariant(Base, IDMixin, TimestampMixin):
    """Product variants (sizes, colors, etc.)."""

    __tablename__ = "product_variants"

    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    variant_name: Mapped[str] = mapped_column(String(200), nullable=False)
    variant_value: Mapped[str] = mapped_column(String(500), nullable=False)
    price_adjustment: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    product: Mapped[Product] = relationship("Product", back_populates="variants")

    __table_args__ = (
        Index("idx_variants_product", "product_id"),
    )

    def __repr__(self) -> str:
        return f"<ProductVariant(id={self.id}, name='{self.variant_name}={self.variant_value}')>"
