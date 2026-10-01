"""Media repository — database operations for media files."""

from __future__ import annotations

from typing import Optional

from loguru import logger
from sqlalchemy import or_, func
from sqlalchemy.orm import Session, joinedload

from app.database.models.media import MediaFile, ProductMedia
from app.database.session import get_db


class MediaRepository:
    """Data access layer for MediaFile model."""

    @staticmethod
    def create(data: dict) -> MediaFile:
        """Create a new media file record."""
        with get_db() as db:
            media = MediaFile(**data)
            db.add(media)
            db.flush()
            db.refresh(media)
            logger.info("Media created: id={}, filename='{}'", media.id, media.filename)
            return media

    @staticmethod
    def get_by_id(media_id: int) -> Optional[MediaFile]:
        """Get a media file by ID."""
        with get_db() as db:
            return (
                db.query(MediaFile)
                .options(joinedload(MediaFile.product_links))
                .filter(MediaFile.id == media_id)
                .first()
            )

    @staticmethod
    def get_by_path(file_path: str) -> Optional[MediaFile]:
        """Get a media file by its file path."""
        with get_db() as db:
            return db.query(MediaFile).filter(MediaFile.file_path == file_path).first()

    @staticmethod
    def get_all(
        search: str = "",
        media_type: str = "",
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[MediaFile], int]:
        """Get all media files with filtering."""
        with get_db() as db:
            query = db.query(MediaFile)

            if search:
                query = query.filter(MediaFile.filename.ilike(f"%{search}%"))

            if media_type:
                query = query.filter(MediaFile.media_type == media_type)

            total = query.count()
            media_files = (
                query.order_by(MediaFile.created_at.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return media_files, total

    @staticmethod
    def update(media_id: int, data: dict) -> Optional[MediaFile]:
        """Update a media file record."""
        with get_db() as db:
            media = db.query(MediaFile).filter(MediaFile.id == media_id).first()
            if not media:
                return None
            for key, value in data.items():
                if hasattr(media, key) and key not in ("id", "created_at"):
                    setattr(media, key, value)
            db.flush()
            db.refresh(media)
            return media

    @staticmethod
    def delete(media_id: int) -> bool:
        """Delete a media file record (does NOT delete the actual file)."""
        with get_db() as db:
            media = db.query(MediaFile).filter(MediaFile.id == media_id).first()
            if not media:
                return False
            db.delete(media)
            logger.info("Media deleted: id={}", media_id)
            return True

    @staticmethod
    def count(media_type: str = "") -> int:
        """Count media files."""
        with get_db() as db:
            query = db.query(func.count(MediaFile.id))
            if media_type:
                query = query.filter(MediaFile.media_type == media_type)
            return query.scalar() or 0

    @staticmethod
    def mark_invalid(media_id: int, error_message: str) -> None:
        """Mark a media file as invalid."""
        with get_db() as db:
            media = db.query(MediaFile).filter(MediaFile.id == media_id).first()
            if media:
                media.is_valid = False
                media.error_message = error_message

    @staticmethod
    def get_invalid() -> list[MediaFile]:
        """Get all invalid/missing media files."""
        with get_db() as db:
            return db.query(MediaFile).filter(MediaFile.is_valid == False).all()


class ProductMediaRepository:
    """Data access layer for ProductMedia junction table."""

    @staticmethod
    def link(product_id: int, media_id: int, is_primary: bool = False) -> ProductMedia:
        """Link a media file to a product."""
        with get_db() as db:
            # Check if already linked
            existing = (
                db.query(ProductMedia)
                .filter(
                    ProductMedia.product_id == product_id,
                    ProductMedia.media_id == media_id,
                )
                .first()
            )
            if existing:
                return existing

            # Get next sort order for this product
            max_order = (
                db.query(func.max(ProductMedia.sort_order))
                .filter(ProductMedia.product_id == product_id)
                .scalar()
            ) or 0

            link = ProductMedia(
                product_id=product_id,
                media_id=media_id,
                is_primary=is_primary,
                sort_order=max_order + 1,
            )
            db.add(link)
            db.flush()
            db.refresh(link)
            logger.info("Media {} linked to product {}", media_id, product_id)
            return link

    @staticmethod
    def unlink(product_id: int, media_id: int) -> bool:
        """Remove the link between a product and a media file."""
        with get_db() as db:
            link = (
                db.query(ProductMedia)
                .filter(
                    ProductMedia.product_id == product_id,
                    ProductMedia.media_id == media_id,
                )
                .first()
            )
            if not link:
                return False
            db.delete(link)
            return True

    @staticmethod
    def get_media_for_product(product_id: int) -> list[MediaFile]:
        """Get all media files linked to a product."""
        with get_db() as db:
            links = (
                db.query(ProductMedia)
                .options(joinedload(ProductMedia.media))
                .filter(ProductMedia.product_id == product_id)
                .order_by(ProductMedia.sort_order)
                .all()
            )
            return [link.media for link in links if link.media]

    @staticmethod
    def get_products_for_media(media_id: int) -> list[int]:
        """Get all product IDs linked to a media file."""
        with get_db() as db:
            links = (
                db.query(ProductMedia.product_id)
                .filter(ProductMedia.media_id == media_id)
                .all()
            )
            return [link[0] for link in links]
