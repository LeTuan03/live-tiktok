"""Product management page — full CRUD with table, dialogs, search, and CSV import/export."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from loguru import logger

from app.services.product_service import ProductService
from app.ui.styles.dark_theme import COLORS


class ProductFormDialog(QDialog):
    """Dialog for creating or editing a product."""

    saved = Signal(dict)

    def __init__(
        self,
        parent: QWidget | None = None,
        product_data: dict | None = None,
        categories: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._product_data = product_data or {}
        self._is_edit = bool(product_data and product_data.get("id"))
        self.setWindowTitle("Sửa sản phẩm" if self._is_edit else "Thêm sản phẩm mới")
        self.setMinimumSize(650, 700)
        self.resize(700, 780)
        self._categories = categories or []
        self._setup_ui()
        self._load_data()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Title
        title = QLabel("Sửa sản phẩm" if self._is_edit else "Thêm sản phẩm mới")
        title.setObjectName("headerLabel")
        layout.addWidget(title)

        # Scrollable form
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setSpacing(12)
        form.setContentsMargins(0, 0, 16, 0)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

        # --- Fields ---
        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("Tên sản phẩm (bắt buộc)")
        form.addRow("Tên sản phẩm *:", self._name_input)

        self._sku_input = QLineEdit()
        self._sku_input.setPlaceholderText("Mã SKU / mã sản phẩm")
        form.addRow("Mã SKU:", self._sku_input)

        self._category_combo = QComboBox()
        self._category_combo.setEditable(True)
        self._category_combo.addItem("")
        for cat in self._categories:
            self._category_combo.addItem(cat)
        self._category_combo.setPlaceholderText("Chọn hoặc nhập danh mục")
        form.addRow("Danh mục:", self._category_combo)

        # Price row
        price_row = QHBoxLayout()
        self._price_input = QDoubleSpinBox()
        self._price_input.setRange(0, 999999999)
        self._price_input.setDecimals(0)
        self._price_input.setSuffix(" ₫")
        self._price_input.setSpecialValueText("—")
        price_row.addWidget(self._price_input)
        price_row.addWidget(QLabel("Giá KM:"))
        self._promo_price_input = QDoubleSpinBox()
        self._promo_price_input.setRange(0, 999999999)
        self._promo_price_input.setDecimals(0)
        self._promo_price_input.setSuffix(" ₫")
        self._promo_price_input.setSpecialValueText("—")
        price_row.addWidget(self._promo_price_input)
        form.addRow("Giá bán:", price_row)

        self._description_input = QTextEdit()
        self._description_input.setPlaceholderText("Mô tả chi tiết sản phẩm")
        self._description_input.setMaximumHeight(100)
        form.addRow("Mô tả:", self._description_input)

        self._features_input = QTextEdit()
        self._features_input.setPlaceholderText("Các tính năng đã được xác minh (mỗi dòng một tính năng)")
        self._features_input.setMaximumHeight(80)
        form.addRow("Tính năng:", self._features_input)

        self._ingredients_input = QLineEdit()
        self._ingredients_input.setPlaceholderText("Thành phần / chất liệu")
        form.addRow("Thành phần:", self._ingredients_input)

        self._sizes_input = QLineEdit()
        self._sizes_input.setPlaceholderText("Kích thước hoặc biến thể (VD: S, M, L, XL)")
        form.addRow("Kích thước:", self._sizes_input)

        self._audience_input = QLineEdit()
        self._audience_input.setPlaceholderText("Đối tượng sử dụng")
        form.addRow("Đối tượng:", self._audience_input)

        self._usage_input = QTextEdit()
        self._usage_input.setPlaceholderText("Hướng dẫn sử dụng")
        self._usage_input.setMaximumHeight(80)
        form.addRow("Hướng dẫn:", self._usage_input)

        self._warnings_input = QLineEdit()
        self._warnings_input.setPlaceholderText("Lưu ý / cảnh báo")
        form.addRow("Lưu ý:", self._warnings_input)

        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("https://...")
        form.addRow("URL sản phẩm:", self._url_input)

        self._active_checkbox = QCheckBox("Sản phẩm đang hoạt động")
        self._active_checkbox.setChecked(True)
        form.addRow("Trạng thái:", self._active_checkbox)

        scroll.setWidget(form_widget)
        layout.addWidget(scroll, 1)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Hủy")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("Lưu sản phẩm")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

    def _load_data(self) -> None:
        """Load existing product data into the form."""
        d = self._product_data
        if not d:
            return
        self._name_input.setText(d.get("name", ""))
        self._sku_input.setText(d.get("sku", "") or "")
        self._category_combo.setCurrentText(d.get("category", "") or "")
        self._price_input.setValue(d.get("price", 0) or 0)
        self._promo_price_input.setValue(d.get("promo_price", 0) or 0)
        self._description_input.setPlainText(d.get("description", "") or "")
        self._features_input.setPlainText(d.get("verified_features", "") or "")
        self._ingredients_input.setText(d.get("ingredients", "") or "")
        self._sizes_input.setText(d.get("sizes", "") or "")
        self._audience_input.setText(d.get("target_audience", "") or "")
        self._usage_input.setPlainText(d.get("usage_instructions", "") or "")
        self._warnings_input.setText(d.get("warnings", "") or "")
        self._url_input.setText(d.get("product_url", "") or "")
        self._active_checkbox.setChecked(d.get("is_active", True))

    def _on_save(self) -> None:
        name = self._name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập tên sản phẩm.")
            self._name_input.setFocus()
            return

        data = {
            "name": name,
            "sku": self._sku_input.text().strip() or None,
            "category": self._category_combo.currentText().strip() or None,
            "price": self._price_input.value() or None,
            "promo_price": self._promo_price_input.value() or None,
            "description": self._description_input.toPlainText().strip() or None,
            "verified_features": self._features_input.toPlainText().strip() or None,
            "ingredients": self._ingredients_input.text().strip() or None,
            "sizes": self._sizes_input.text().strip() or None,
            "target_audience": self._audience_input.text().strip() or None,
            "usage_instructions": self._usage_input.toPlainText().strip() or None,
            "warnings": self._warnings_input.text().strip() or None,
            "product_url": self._url_input.text().strip() or None,
            "is_active": self._active_checkbox.isChecked(),
        }

        if self._is_edit:
            data["id"] = self._product_data["id"]

        self.saved.emit(data)
        self.accept()


class ProductPage(QWidget):
    """Full product management page with table, search, filter, and CRUD."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = ProductService()
        self._current_search = ""
        self._current_category = ""
        self._setup_ui()
        self.refresh_data()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # --- Header ---
        header_row = QHBoxLayout()
        title = QLabel("Quản lý sản phẩm")
        title.setObjectName("headerLabel")
        header_row.addWidget(title)
        header_row.addStretch()

        self._count_label = QLabel("0 sản phẩm")
        self._count_label.setObjectName("subHeaderLabel")
        header_row.addWidget(self._count_label)
        layout.addLayout(header_row)

        # --- Toolbar ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        # Search
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("🔍 Tìm kiếm sản phẩm...")
        self._search_input.setMinimumWidth(250)
        self._search_input.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self._search_input)

        # Category filter
        self._category_filter = QComboBox()
        self._category_filter.addItem("Tất cả danh mục", "")
        self._category_filter.setMinimumWidth(160)
        self._category_filter.currentIndexChanged.connect(self._on_category_changed)
        toolbar.addWidget(self._category_filter)

        toolbar.addStretch()

        # Action buttons
        import_btn = QPushButton("📥 Import CSV")
        import_btn.clicked.connect(self._on_import_csv)
        toolbar.addWidget(import_btn)

        export_btn = QPushButton("📤 Export CSV")
        export_btn.clicked.connect(self._on_export_csv)
        toolbar.addWidget(export_btn)

        add_btn = QPushButton("➕ Thêm sản phẩm")
        add_btn.setObjectName("primaryButton")
        add_btn.clicked.connect(self._on_add_product)
        toolbar.addWidget(add_btn)

        layout.addLayout(toolbar)

        # --- Product Table ---
        self._table = QTableWidget()
        self._table.setColumnCount(8)
        self._table.setHorizontalHeaderLabels([
            "ID", "Tên sản phẩm", "SKU", "Danh mục", "Giá", "Giá KM", "Trạng thái", "Thao tác"
        ])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 50)
        self._table.setColumnWidth(2, 100)
        self._table.setColumnWidth(3, 120)
        self._table.setColumnWidth(4, 110)
        self._table.setColumnWidth(5, 110)
        self._table.setColumnWidth(6, 90)
        self._table.setColumnWidth(7, 200)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.doubleClicked.connect(self._on_edit_product)

        layout.addWidget(self._table, 1)

        # --- Status bar ---
        status_row = QHBoxLayout()
        self._status_label = QLabel("Sẵn sàng")
        self._status_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px;")
        status_row.addWidget(self._status_label)
        status_row.addStretch()

        refresh_btn = QPushButton("🔄 Làm mới")
        refresh_btn.clicked.connect(self.refresh_data)
        status_row.addWidget(refresh_btn)
        layout.addLayout(status_row)

    def refresh_data(self) -> None:
        """Reload product data from the database."""
        try:
            products, total = self._service.get_products(
                search=self._current_search,
                category=self._current_category,
            )
            self._populate_table(products)
            self._count_label.setText(f"{total} sản phẩm")
            self._refresh_categories()
            self._status_label.setText(f"Đã tải {len(products)} sản phẩm")
        except Exception as e:
            logger.exception("Failed to refresh products")
            self._status_label.setText(f"Lỗi: {e}")

    def _populate_table(self, products: list) -> None:
        """Fill the table with product data."""
        self._table.setRowCount(len(products))

        for row, product in enumerate(products):
            # ID
            id_item = QTableWidgetItem(str(product.id))
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            id_item.setData(Qt.ItemDataRole.UserRole, product.id)
            self._table.setItem(row, 0, id_item)

            # Name
            self._table.setItem(row, 1, QTableWidgetItem(product.name))

            # SKU
            self._table.setItem(row, 2, QTableWidgetItem(product.sku or "—"))

            # Category
            self._table.setItem(row, 3, QTableWidgetItem(product.category or "—"))

            # Price
            price_text = f"{product.price:,.0f}₫" if product.price else "—"
            price_item = QTableWidgetItem(price_text)
            price_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 4, price_item)

            # Promo price
            promo_text = f"{product.promo_price:,.0f}₫" if product.promo_price else "—"
            promo_item = QTableWidgetItem(promo_text)
            promo_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 5, promo_item)

            # Status
            status_text = "✅ Bật" if product.is_active else "⏸️ Tắt"
            status_item = QTableWidgetItem(status_text)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if not product.is_active:
                status_item.setForeground(Qt.GlobalColor.gray)
            self._table.setItem(row, 6, status_item)

            # Action buttons
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(4, 2, 4, 2)
            action_layout.setSpacing(4)

            edit_btn = QPushButton("✏️")
            edit_btn.setFixedSize(32, 28)
            edit_btn.setToolTip("Sửa sản phẩm")
            edit_btn.clicked.connect(lambda _, pid=product.id: self._edit_product(pid))
            action_layout.addWidget(edit_btn)

            toggle_btn = QPushButton("⏸️" if product.is_active else "▶️")
            toggle_btn.setFixedSize(32, 28)
            toggle_btn.setToolTip("Tắt" if product.is_active else "Bật")
            toggle_btn.clicked.connect(lambda _, pid=product.id: self._toggle_product(pid))
            action_layout.addWidget(toggle_btn)

            del_btn = QPushButton("🗑️")
            del_btn.setFixedSize(32, 28)
            del_btn.setToolTip("Xóa sản phẩm")
            del_btn.setObjectName("dangerButton")
            del_btn.clicked.connect(lambda _, pid=product.id, pname=product.name: self._delete_product(pid, pname))
            action_layout.addWidget(del_btn)

            action_layout.addStretch()
            self._table.setCellWidget(row, 7, action_widget)

        self._table.resizeRowsToContents()

    def _refresh_categories(self) -> None:
        """Update the category filter dropdown."""
        current = self._category_filter.currentData()
        self._category_filter.blockSignals(True)
        self._category_filter.clear()
        self._category_filter.addItem("Tất cả danh mục", "")
        for cat in self._service.get_categories():
            self._category_filter.addItem(cat, cat)
        # Restore selection
        idx = self._category_filter.findData(current)
        if idx >= 0:
            self._category_filter.setCurrentIndex(idx)
        self._category_filter.blockSignals(False)

    def _on_search_changed(self, text: str) -> None:
        self._current_search = text.strip()
        self.refresh_data()

    def _on_category_changed(self, index: int) -> None:
        self._current_category = self._category_filter.currentData() or ""
        self.refresh_data()

    def _on_add_product(self) -> None:
        categories = self._service.get_categories()
        dialog = ProductFormDialog(self, categories=categories)
        dialog.saved.connect(self._save_new_product)
        dialog.exec()

    def _save_new_product(self, data: dict) -> None:
        try:
            self._service.create_product(data)
            self._status_label.setText("✅ Đã thêm sản phẩm mới")
            self.refresh_data()
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Không thể tạo sản phẩm:\n{e}")

    def _on_edit_product(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        product_id = self._table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self._edit_product(product_id)

    def _edit_product(self, product_id: int) -> None:
        product = self._service.get_product(product_id)
        if not product:
            QMessageBox.warning(self, "Lỗi", "Không tìm thấy sản phẩm")
            return

        product_data = {
            "id": product.id,
            "name": product.name,
            "sku": product.sku,
            "category": product.category,
            "price": product.price,
            "promo_price": product.promo_price,
            "description": product.description,
            "verified_features": product.verified_features,
            "ingredients": product.ingredients,
            "sizes": product.sizes,
            "target_audience": product.target_audience,
            "usage_instructions": product.usage_instructions,
            "warnings": product.warnings,
            "product_url": product.product_url,
            "is_active": product.is_active,
        }
        categories = self._service.get_categories()
        dialog = ProductFormDialog(self, product_data=product_data, categories=categories)
        dialog.saved.connect(self._save_edited_product)
        dialog.exec()

    def _save_edited_product(self, data: dict) -> None:
        product_id = data.pop("id", None)
        if not product_id:
            return
        try:
            self._service.update_product(product_id, data)
            self._status_label.setText("✅ Đã cập nhật sản phẩm")
            self.refresh_data()
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Không thể cập nhật sản phẩm:\n{e}")

    def _toggle_product(self, product_id: int) -> None:
        new_state = self._service.toggle_active(product_id)
        if new_state is not None:
            state_text = "bật" if new_state else "tắt"
            self._status_label.setText(f"Đã {state_text} sản phẩm")
            self.refresh_data()

    def _delete_product(self, product_id: int, product_name: str) -> None:
        reply = QMessageBox.question(
            self,
            "Xác nhận xóa",
            f"Bạn có chắc muốn xóa sản phẩm:\n\n\"{product_name}\"?\n\nThao tác này không thể hoàn tác.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            if self._service.delete_product(product_id):
                self._status_label.setText("✅ Đã xóa sản phẩm")
                self.refresh_data()

    def _on_import_csv(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import sản phẩm từ CSV",
            "",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not file_path:
            return

        count, errors = self._service.import_csv(file_path)
        msg = f"Đã import {count} sản phẩm."
        if errors:
            msg += f"\n\nCảnh báo ({len(errors)}):\n" + "\n".join(errors[:10])
            if len(errors) > 10:
                msg += f"\n... và {len(errors) - 10} lỗi khác"

        QMessageBox.information(self, "Import CSV", msg)
        self.refresh_data()

    def _on_export_csv(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export sản phẩm ra CSV",
            "products.csv",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not file_path:
            return

        try:
            count = self._service.export_csv(file_path)
            QMessageBox.information(
                self, "Export CSV", f"Đã export {count} sản phẩm ra:\n{file_path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Không thể export:\n{e}")
