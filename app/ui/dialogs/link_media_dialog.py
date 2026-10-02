"""Dialog: link a media file to one or more products (and vice versa)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services.media_service import MediaService
from app.services.product_service import ProductService


class LinkMediaToProductsDialog(QDialog):
    """Pick products for a given media file."""

    def __init__(self, media_id: int, media_name: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Gắn media với sản phẩm")
        self.setMinimumSize(520, 520)
        self._media_id = media_id
        self._media_service = MediaService()
        self._product_service = ProductService()
        self._products = self._product_service.list_products()
        linked_ids = {
            pid for pid in self._media_service.get_product_ids_for_media(media_id)
        }

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel(f"🎬 {media_name}")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        hint = QLabel("Tích chọn các sản phẩm sẽ dùng video/audio này khi lên LIVE.")
        hint.setObjectName("hintLabel")
        layout.addWidget(hint)

        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍 Lọc sản phẩm...")
        self._search.textChanged.connect(self._filter)
        layout.addWidget(self._search)

        self._list = QListWidget()
        for p in self._products:
            item = QListWidgetItem(f"{p.name}" + (f"  ·  {p.sku}" if p.sku else ""))
            item.setData(Qt.ItemDataRole.UserRole, p.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if p.id in linked_ids else Qt.CheckState.Unchecked)
            self._list.addItem(item)
        layout.addWidget(self._list, 1)

        if not self._products:
            empty = QLabel("Chưa có sản phẩm nào. Hãy thêm sản phẩm ở trang 'Sản phẩm' trước.")
            empty.setObjectName("warningBanner")
            layout.addWidget(empty)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("Hủy")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        save = QPushButton("💾 Lưu liên kết")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._save)
        btns.addWidget(save)
        layout.addLayout(btns)

    def _filter(self, text: str) -> None:
        text = text.strip().lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setHidden(bool(text) and text not in item.text().lower())

    def _save(self) -> None:
        wanted: set[int] = set()
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                wanted.add(int(item.data(Qt.ItemDataRole.UserRole)))
        current = set(self._media_service.get_product_ids_for_media(self._media_id))
        try:
            for pid in wanted - current:
                self._media_service.link_to_product(self._media_id, pid)
            for pid in current - wanted:
                self._media_service.unlink_from_product(self._media_id, pid)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Lỗi", f"Không thể lưu liên kết:\n{e}")
            return
        self.accept()


class LinkProductToMediaDialog(QDialog):
    """Pick media files (videos) for a given product."""

    def __init__(self, product_id: int, product_name: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Gắn video cho sản phẩm")
        self.setMinimumSize(560, 520)
        self._product_id = product_id
        self._media_service = MediaService()
        media_files, _ = self._media_service.get_all_media(limit=10000)
        self._media = media_files
        linked_ids = {m.id for m in self._media_service.get_product_media(product_id)}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel(f"📦 {product_name}")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        hint = QLabel("Chọn video hoặc ảnh trong Thư viện Media sẽ hiển thị trong OBS khi giới thiệu sản phẩm này. "
                      "Có video thì phát video; chỉ có ảnh thì hiển thị ảnh (kiểu banner + giọng AI).")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍 Lọc file...")
        self._search.textChanged.connect(self._filter)
        layout.addWidget(self._search)

        self._list = QListWidget()
        for m in self._media:
            icon = {"video": "🎬", "audio": "🎵", "image": "🖼️"}.get(m.media_type, "📄")
            dur = self._media_service.format_duration(m.duration_seconds)
            flag = "" if m.is_valid else "  ⚠️ lỗi file"
            item = QListWidgetItem(f"{icon} {m.filename}  ·  {dur}{flag}")
            item.setData(Qt.ItemDataRole.UserRole, m.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if m.id in linked_ids else Qt.CheckState.Unchecked)
            self._list.addItem(item)
        layout.addWidget(self._list, 1)

        if not self._media:
            empty = QLabel("Thư viện Media đang trống. Hãy import video ở trang 'Thư viện Media' trước.")
            empty.setObjectName("warningBanner")
            layout.addWidget(empty)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("Hủy")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        save = QPushButton("💾 Lưu liên kết")
        save.setObjectName("primaryButton")
        save.clicked.connect(self._save)
        btns.addWidget(save)
        layout.addLayout(btns)

    def _filter(self, text: str) -> None:
        text = text.strip().lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            item.setHidden(bool(text) and text not in item.text().lower())

    def _checked_ids(self) -> list[int]:
        ids = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                ids.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return ids

    def _save(self) -> None:
        wanted = self._checked_ids()
        current = {m.id for m in self._media_service.get_product_media(self._product_id)}
        try:
            for n, mid in enumerate(wanted):
                if mid not in current:
                    self._media_service.link_to_product(mid, self._product_id, is_primary=(n == 0))
            for mid in current - set(wanted):
                self._media_service.unlink_from_product(mid, self._product_id)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Lỗi", f"Không thể lưu liên kết:\n{e}")
            return
        self.accept()
