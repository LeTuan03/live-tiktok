"""Product repository — database operations for products and variants."""

from __future__ import annotations

from typing import Optional

from loguru import logger
from sqlalchemy import or_, func
from sqlalchemy.orm import Session, joinedload

from app.database.models.product import Product, ProductVariant
from app.database.session import get_db


class ProductRepository:
    """Data access layer for Product and ProductVariant models."""

    # --- Product CRUD ---

    @staticmethod
    def create(data: dict) -> Product:
        """Create a new product.

        Args:
            data: Dictionary of product fields.

        Returns:
            The created Product instance.
        """
        with get_db() as db:
            # Get next sort order
            max_order = db.query(func.max(Product.sort_order)).scalar() or 0
            product = Product(**data)
            if product.sort_order == 0:
                product.sort_order = max_order + 1
            db.add(product)
            db.flush()
            db.refresh(product)
            logger.info("Product created: id={}, name='{}'", product.id, product.name)
            return product

    @staticmethod
    def get_by_id(product_id: int) -> Optional[Product]:
        """Get a product by ID with relationships loaded."""
        with get_db() as db:
            product = (
                db.query(Product)
                .options(
                    joinedload(Product.variants),
                    joinedload(Product.media_links),
                )
                .filter(Product.id == product_id)
                .first()
            )
            return product

    @staticmethod
    def get_all(
        search: str = "",
        category: str = "",
        is_active: Optional[bool] = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[Product], int]:
        """Get all products with optional filtering.

        Args:
            search: Search term for name, SKU, or description.
            category: Filter by category.
            is_active: Filter by active status.
            offset: Pagination offset.
            limit: Maximum number of results.

        Returns:
            Tuple of (products list, total count).
        """
        with get_db() as db:
            query = db.query(Product)

            if search:
                search_pattern = f"%{search}%"
                query = query.filter(
                    or_(
                        Product.name.ilike(search_pattern),
                        Product.sku.ilike(search_pattern),
                        Product.description.ilike(search_pattern),
                        Product.category.ilike(search_pattern),
                    )
                )

            if category:
                query = query.filter(Product.category == category)

            if is_active is not None:
                query = query.filter(Product.is_active == is_active)

            total = query.count()
            products = (
                query
                .order_by(Product.sort_order.asc(), Product.id.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )

            return products, total

    @staticmethod
    def update(product_id: int, data: dict) -> Optional[Product]:
        """Update a product by ID.

        Args:
            product_id: The product ID.
            data: Dictionary of fields to update.

        Returns:
            The updated Product or None if not found.
        """
        with get_db() as db:
            product = db.query(Product).filter(Product.id == product_id).first()
            if not product:
                return None

            for key, value in data.items():
                if hasattr(product, key) and key not in ("id", "created_at"):
                    setattr(product, key, value)

            db.flush()
            db.refresh(product)
            logger.info("Product updated: id={}", product_id)
            return product

    @staticmethod
    def delete(product_id: int) -> bool:
        """Delete a product by ID.

        Args:
            product_id: The product ID.

        Returns:
            True if deleted, False if not found.
        """
        with get_db() as db:
            product = db.query(Product).filter(Product.id == product_id).first()
            if not product:
                return False
            db.delete(product)
            logger.info("Product deleted: id={}", product_id)
            return True

    @staticmethod
    def toggle_active(product_id: int) -> Optional[bool]:
        """Toggle the active status of a product.

        Returns:
            The new is_active value, or None if not found.
        """
        with get_db() as db:
            product = db.query(Product).filter(Product.id == product_id).first()
            if not product:
                return None
            product.is_active = not product.is_active
            db.flush()
            return product.is_active

    @staticmethod
    def update_sort_order(product_id: int, new_order: int) -> bool:
        """Update the sort order of a product."""
        with get_db() as db:
            product = db.query(Product).filter(Product.id == product_id).first()
            if not product:
                return False
            product.sort_order = new_order
            return True

    @staticmethod
    def get_categories() -> list[str]:
        """Get all distinct categories."""
        with get_db() as db:
            results = (
                db.query(Product.category)
                .filter(Product.category.isnot(None), Product.category != "")
                .distinct()
                .order_by(Product.category)
                .all()
            )
            return [r[0] for r in results]

    @staticmethod
    def count(is_active: Optional[bool] = None) -> int:
        """Count products, optionally filtered by active status."""
        with get_db() as db:
            query = db.query(func.count(Product.id))
            if is_active is not None:
                query = query.filter(Product.is_active == is_active)
            return query.scalar() or 0

    @staticmethod
    def bulk_create(products_data: list[dict]) -> int:
        """Bulk create products from a list of dictionaries.

        Returns:
            Number of products created.
        """
        with get_db() as db:
            max_order = db.query(func.max(Product.sort_order)).scalar() or 0
            created = 0
            for i, data in enumerate(products_data):
                product = Product(**data)
                if product.sort_order == 0:
                    product.sort_order = max_order + i + 1
                db.add(product)
                created += 1
            db.flush()
            logger.info("Bulk created {} products", created)
            return created


class ProductVariantRepository:
    """Data access layer for ProductVariant model."""

    @staticmethod
    def create(product_id: int, data: dict) -> ProductVariant:
        with get_db() as db:
            variant = ProductVariant(product_id=product_id, **data)
            db.add(variant)
            db.flush()
            db.refresh(variant)
            return variant

    @staticmethod
    def get_by_product(product_id: int) -> list[ProductVariant]:
        with get_db() as db:
            return (
                db.query(ProductVariant)
                .filter(ProductVariant.product_id == product_id)
                .all()
            )

    @staticmethod
    def delete(variant_id: int) -> bool:
        with get_db() as db:
            variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).first()
            if not variant:
                return False
            db.delete(variant)
            return True
