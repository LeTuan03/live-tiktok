"""Product service — business logic for product management."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from app.core.events import AppEvent, event_bus
from app.database.repositories.product_repo import ProductRepository, ProductVariantRepository


# CSV column mapping: CSV header → model field
CSV_FIELD_MAP = {
    "Tên sản phẩm": "name",
    "Mã SKU": "sku",
    "Danh mục": "category",
    "Giá bán": "price",
    "Giá khuyến mãi": "promo_price",
    "Mô tả": "description",
    "Tính năng": "verified_features",
    "Thành phần": "ingredients",
    "Kích thước": "sizes",
    "Đối tượng": "target_audience",
    "Hướng dẫn sử dụng": "usage_instructions",
    "Lưu ý": "warnings",
    "URL sản phẩm": "product_url",
    "Đường dẫn ảnh": "image_path",
    "Đường dẫn video": "video_path",
    "Trạng thái": "is_active",
}

# Reverse mapping for export
FIELD_CSV_MAP = {v: k for k, v in CSV_FIELD_MAP.items()}

EXPORT_FIELDS = [
    "name", "sku", "category", "price", "promo_price",
    "description", "verified_features", "ingredients", "sizes",
    "target_audience", "usage_instructions", "warnings",
    "product_url", "image_path", "video_path", "is_active",
]


class ProductService:
    """Business logic layer for product operations."""

    def __init__(self) -> None:
        self._repo = ProductRepository()
        self._variant_repo = ProductVariantRepository()

    def create_product(self, data: dict) -> Any:
        """Create a new product and emit event."""
        product = self._repo.create(data)
        event_bus.emit(AppEvent.PRODUCT_CREATED, product_id=product.id)
        return product

    def get_product(self, product_id: int) -> Any:
        """Get a product by ID."""
        return self._repo.get_by_id(product_id)

    def get_products(
        self,
        search: str = "",
        category: str = "",
        is_active: Optional[bool] = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list, int]:
        """Get products with filtering and pagination."""
        return self._repo.get_all(search, category, is_active, offset, limit)

    def update_product(self, product_id: int, data: dict) -> Any:
        """Update a product and emit event."""
        product = self._repo.update(product_id, data)
        if product:
            event_bus.emit(AppEvent.PRODUCT_UPDATED, product_id=product_id)
        return product

    def delete_product(self, product_id: int) -> bool:
        """Delete a product and emit event."""
        result = self._repo.delete(product_id)
        if result:
            event_bus.emit(AppEvent.PRODUCT_DELETED, product_id=product_id)
        return result

    def toggle_active(self, product_id: int) -> Optional[bool]:
        """Toggle product active status."""
        return self._repo.toggle_active(product_id)

    def get_categories(self) -> list[str]:
        """Get all product categories."""
        return self._repo.get_categories()

    def count_products(self, is_active: Optional[bool] = None) -> int:
        """Count products."""
        return self._repo.count(is_active)

    # --- CSV Import ---

    def import_csv(self, file_path: str | Path) -> tuple[int, list[str]]:
        """Import products from a CSV file.

        Args:
            file_path: Path to the CSV file.

        Returns:
            Tuple of (number imported, list of error messages).
        """
        file_path = Path(file_path)
        errors: list[str] = []

        if not file_path.exists():
            return 0, [f"File không tồn tại: {file_path}"]

        if file_path.stat().st_size > 10 * 1024 * 1024:  # 10MB limit
            return 0, ["File CSV quá lớn (tối đa 10MB)"]

        try:
            with open(file_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                products_data = []

                for row_num, row in enumerate(reader, start=2):
                    try:
                        product_data = self._parse_csv_row(row, row_num)
                        if product_data:
                            products_data.append(product_data)
                    except ValueError as e:
                        errors.append(str(e))

                if products_data:
                    count = self._repo.bulk_create(products_data)
                    event_bus.emit(AppEvent.PRODUCTS_IMPORTED, count=count)
                    logger.info("Imported {} products from CSV", count)
                    return count, errors
                else:
                    return 0, errors or ["Không tìm thấy dữ liệu hợp lệ trong file CSV"]

        except UnicodeDecodeError:
            return 0, ["File CSV không phải UTF-8. Vui lòng lưu file dạng UTF-8."]
        except Exception as e:
            logger.exception("CSV import error")
            return 0, [f"Lỗi đọc file CSV: {e}"]

    def _parse_csv_row(self, row: dict, row_num: int) -> Optional[dict]:
        """Parse a single CSV row into product data."""
        data = {}

        for csv_header, field_name in CSV_FIELD_MAP.items():
            value = row.get(csv_header, "").strip()
            if not value:
                continue

            if field_name in ("price", "promo_price"):
                try:
                    value = float(value.replace(",", "").replace("₫", "").strip())
                except ValueError:
                    raise ValueError(f"Dòng {row_num}: Giá '{value}' không hợp lệ")
            elif field_name == "is_active":
                value = value.lower() in ("1", "true", "có", "yes", "on", "bật")
            data[field_name] = value

        # Name is required
        if "name" not in data or not data["name"]:
            raise ValueError(f"Dòng {row_num}: Thiếu tên sản phẩm")

        return data

    # --- CSV Export ---

    def export_csv(self, file_path: str | Path, product_ids: list[int] | None = None) -> int:
        """Export products to a CSV file.

        Args:
            file_path: Output file path.
            product_ids: Optional list of specific product IDs. Exports all if None.

        Returns:
            Number of products exported.
        """
        file_path = Path(file_path)
        products, _ = self._repo.get_all(limit=10000)

        if product_ids:
            products = [p for p in products if p.id in product_ids]

        headers = [FIELD_CSV_MAP.get(f, f) for f in EXPORT_FIELDS]

        with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)

            for product in products:
                row = []
                for field in EXPORT_FIELDS:
                    value = getattr(product, field, "")
                    if field == "is_active":
                        value = "Có" if value else "Không"
                    elif value is None:
                        value = ""
                    row.append(str(value))
                writer.writerow(row)

        logger.info("Exported {} products to CSV: {}", len(products), file_path)
        return len(products)

    def export_csv_string(self, product_ids: list[int] | None = None) -> str:
        """Export products to a CSV string (for clipboard)."""
        output = io.StringIO()
        products, _ = self._repo.get_all(limit=10000)

        if product_ids:
            products = [p for p in products if p.id in product_ids]

        headers = [FIELD_CSV_MAP.get(f, f) for f in EXPORT_FIELDS]
        writer = csv.writer(output)
        writer.writerow(headers)

        for product in products:
            row = []
            for field in EXPORT_FIELDS:
                value = getattr(product, field, "")
                if field == "is_active":
                    value = "Có" if value else "Không"
                elif value is None:
                    value = ""
                row.append(str(value))
            writer.writerow(row)

        return output.getvalue()
