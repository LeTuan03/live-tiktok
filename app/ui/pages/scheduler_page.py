"""Scheduler & Playlist UI Page for automated live product rotation."""

from __future__ import annotations

from typing import List, Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.database.models.playlist import Playlist
from app.database.repositories.playlist_repo import PlaylistRepository
from app.database.repositories.product_repo import ProductRepository
from app.database.session import get_session
from app.services.product_service import ProductService
from app.ui.styles.dark_theme import COLORS


class SchedulerPage(QWidget):
    """Scheduler & Playlist UI Page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.playlists: List[Playlist] = []
        self.current_playlist: Optional[Playlist] = None

        self._init_services()
        self._init_ui()
        self.refresh_playlists()

    def _init_services(self) -> None:
        session = get_session()
        self.playlist_repo = PlaylistRepository(session)
        self.product_repo = ProductRepository(session)
        self.product_service = ProductService(self.product_repo)

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        # Header
        header_layout = QHBoxLayout()
        title_label = QLabel("📅 Lịch Trình & Playlist Livestream")
        title_label.setObjectName("headerLabel")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        # Content Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left Panel — Playlists List
        left_card = QFrame()
        left_card.setObjectName("cardFrame")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(16, 16, 16, 16)

        list_title = QLabel("📜 Danh Sách Playlists")
        list_title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {COLORS['accent']};")
        left_layout.addWidget(list_title)

        self.playlist_combo = QComboBox()
        self.playlist_combo.currentIndexChanged.connect(self._on_playlist_changed)
        left_layout.addWidget(self.playlist_combo)

        left_layout.addSpacing(16)

        new_title = QLabel("➕ Tạo Playlist Mới")
        new_title.setStyleSheet("font-weight: bold;")
        left_layout.addWidget(new_title)

        form = QFormLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("VD: Live Khuyến Mãi Cuối Tuần")
        form.addRow("Tên Playlist:", self.name_input)

        self.loop_cb = QCheckBox("Lặp lại liên tục (Loop)")
        self.loop_cb.setChecked(True)
        form.addRow("", self.loop_cb)

        self.gap_spin = QDoubleSpinBox()
        self.gap_spin.setRange(0, 60)
        self.gap_spin.setValue(5.0)
        self.gap_spin.setSuffix(" giây")
        form.addRow("Thời gian nghỉ giữa SP:", self.gap_spin)

        left_layout.addLayout(form)

        self.create_btn = QPushButton("➕ Tạo Playlist")
        self.create_btn.setObjectName("primaryButton")
        self.create_btn.clicked.connect(self._create_playlist)
        left_layout.addWidget(self.create_btn)

        left_layout.addStretch()
        splitter.addWidget(left_card)

        # Right Panel — Playlist Items Manager
        right_card = QFrame()
        right_card.setObjectName("cardFrame")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(16, 16, 16, 16)

        items_header = QHBoxLayout()
        self.playlist_name_label = QLabel("Chi Tiết Playlist")
        self.playlist_name_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        items_header.addWidget(self.playlist_name_label)
        items_header.addStretch()

        self.add_item_btn = QPushButton("➕ Thêm Sản Phẩm Vào Queue")
        self.add_item_btn.setObjectName("accentButton")
        self.add_item_btn.clicked.connect(self._add_item_to_playlist)
        items_header.addWidget(self.add_item_btn)

        right_layout.addLayout(items_header)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["STT", "Sản Phẩm", "Thời Lượng Dự Kiến", "Trạng Thái"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        right_layout.addWidget(self.table)

        splitter.addWidget(right_card)
        splitter.setSizes([350, 650])

        main_layout.addWidget(splitter)

    def refresh_playlists(self) -> None:
        self.playlist_combo.clear()
        self.playlists = self.playlist_repo.list_playlists()
        if not self.playlists:
            # Seed initial default playlist
            p = self.playlist_repo.create_playlist(
                name="Playlist Mặc Định - Hot Products",
                description="Danh sách sản phẩm giới thiệu mặc định",
            )
            self.playlists = [p]

        for p in self.playlists:
            self.playlist_combo.addItem(p.name, p.id)

        if self.playlists:
            self.current_playlist = self.playlists[0]
            self._load_playlist_items(self.current_playlist)

    def _on_playlist_changed(self, index: int) -> None:
        if index >= 0 and index < len(self.playlists):
            self.current_playlist = self.playlists[index]
            self._load_playlist_items(self.current_playlist)

    def _load_playlist_items(self, playlist: Playlist) -> None:
        self.playlist_name_label.setText(f"Chi Tiết Playlist: {playlist.name}")
        items = playlist.items
        self.table.setRowCount(len(items))

        for row, item in enumerate(items):
            prod_name = item.product.name if item.product else "N/A"
            duration = f"{item.duration_seconds or 120:.0f}s"

            self.table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
            self.table.setItem(row, 1, QTableWidgetItem(prod_name))
            self.table.setItem(row, 2, QTableWidgetItem(duration))
            self.table.setItem(row, 3, QTableWidgetItem("✅ Sẵn sàng" if item.is_enabled else "⏸️ Tạm dừng"))

    def _create_playlist(self) -> None:
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng nhập tên Playlist!")
            return

        p = self.playlist_repo.create_playlist(
            name=name,
            is_loop=self.loop_cb.isChecked(),
            gap_seconds=self.gap_spin.value(),
        )
        self.name_input.clear()
        self.refresh_playlists()
        QMessageBox.information(self, "Thành công", f"Đã tạo Playlist '{name}'!")

    def _add_item_to_playlist(self) -> None:
        if not self.current_playlist:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn Playlist trước!")
            return

        products = self.product_service.list_products()
        if not products:
            QMessageBox.warning(self, "Cảnh báo", "Chưa có sản phẩm nào trong kho để thêm vào Playlist!")
            return

        # Add first available product
        p = products[0]
        self.playlist_repo.add_item(
            playlist_id=self.current_playlist.id,
            product_id=p.id,
            duration_seconds=120.0,
        )
        # Reload current playlist
        updated_playlist = self.playlist_repo.get_by_id(self.current_playlist.id)
        if updated_playlist:
            self.current_playlist = updated_playlist
            self._load_playlist_items(self.current_playlist)
            QMessageBox.information(self, "Thành công", f"Đã thêm sản phẩm '{p.name}' vào Playlist!")
