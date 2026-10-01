"""Media library page — import, browse, and manage video/audio files."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from loguru import logger

from app.services.media_service import MediaService, VIDEO_EXTENSIONS, AUDIO_EXTENSIONS
from app.ui.styles.dark_theme import COLORS


class MediaDetailPanel(QFrame):
    """Panel showing detailed metadata for a selected media file."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setMinimumWidth(280)
        self.setMaximumWidth(350)
        self._service = MediaService()
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("📋 Chi tiết Media")
        title.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {COLORS['text_primary']};")
        layout.addWidget(title)

        # Info grid
        info_layout = QGridLayout()
        info_layout.setSpacing(8)
        info_layout.setColumnStretch(1, 1)

        labels = [
            "Tên file:", "Loại:", "Thời lượng:", "Độ phân giải:",
            "Codec:", "Dung lượng:", "Bitrate:", "Định dạng:",
            "Đường dẫn:", "Trạng thái:",
        ]

        self._info_values: list[QLabel] = []
        for i, label_text in enumerate(labels):
            label = QLabel(label_text)
            label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
            info_layout.addWidget(label, i, 0)

            value = QLabel("—")
            value.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 12px;")
            value.setWordWrap(True)
            info_layout.addWidget(value, i, 1)
            self._info_values.append(value)

        layout.addLayout(info_layout)
        layout.addStretch()

        # Linked products section
        self._products_label = QLabel("📦 Sản phẩm liên kết: —")
        self._products_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
        self._products_label.setWordWrap(True)
        layout.addWidget(self._products_label)

    def show_media(self, media) -> None:
        """Display metadata for a media file."""
        if not media:
            for v in self._info_values:
                v.setText("—")
            return

        svc = self._service
        self._info_values[0].setText(media.filename)
        self._info_values[1].setText("🎬 Video" if media.media_type == "video" else "🎵 Audio")
        self._info_values[2].setText(svc.format_duration(media.duration_seconds))
        self._info_values[3].setText(svc.format_resolution(media.width, media.height))
        self._info_values[4].setText(media.codec or "—")
        self._info_values[5].setText(svc.format_file_size(media.file_size_bytes))
        self._info_values[6].setText(f"{media.bitrate // 1000}kbps" if media.bitrate else "—")
        self._info_values[7].setText(media.format_name or "—")
        self._info_values[8].setText(media.file_path)
        self._info_values[9].setText("✅ Hợp lệ" if media.is_valid else f"❌ {media.error_message or 'Lỗi'}")

    def clear(self) -> None:
        for v in self._info_values:
            v.setText("—")
        self._products_label.setText("📦 Sản phẩm liên kết: —")


class MediaPage(QWidget):
    """Media library page for managing imported video and audio files."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = MediaService()
        self._current_search = ""
        self._current_type = ""
        self._setup_ui()
        self.refresh_data()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # --- Header ---
        header_row = QHBoxLayout()
        title = QLabel("Thư viện Media")
        title.setObjectName("headerLabel")
        header_row.addWidget(title)
        header_row.addStretch()

        self._count_label = QLabel("0 file")
        self._count_label.setObjectName("subHeaderLabel")
        header_row.addWidget(self._count_label)
        layout.addLayout(header_row)

        # --- Toolbar ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("🔍 Tìm kiếm file...")
        self._search_input.setMinimumWidth(250)
        self._search_input.textChanged.connect(self._on_search)
        toolbar.addWidget(self._search_input)

        self._type_filter = QComboBox()
        self._type_filter.addItem("Tất cả", "")
        self._type_filter.addItem("🎬 Video", "video")
        self._type_filter.addItem("🎵 Audio", "audio")
        self._type_filter.currentIndexChanged.connect(self._on_type_changed)
        toolbar.addWidget(self._type_filter)

        toolbar.addStretch()

        check_btn = QPushButton("🔍 Kiểm tra file")
        check_btn.setToolTip("Kiểm tra file bị thiếu hoặc không đọc được")
        check_btn.clicked.connect(self._on_check_files)
        toolbar.addWidget(check_btn)

        import_btn = QPushButton("📥 Import Media")
        import_btn.setObjectName("primaryButton")
        import_btn.clicked.connect(self._on_import)
        toolbar.addWidget(import_btn)

        layout.addLayout(toolbar)

        # --- Content: Table + Detail Panel ---
        content_layout = QHBoxLayout()
        content_layout.setSpacing(16)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            "ID", "Tên file", "Loại", "Thời lượng", "Độ phân giải", "Dung lượng", "Thao tác"
        ])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(0, 50)
        self._table.setColumnWidth(2, 80)
        self._table.setColumnWidth(3, 90)
        self._table.setColumnWidth(4, 100)
        self._table.setColumnWidth(5, 100)
        self._table.setColumnWidth(6, 100)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.currentCellChanged.connect(self._on_selection_changed)

        content_layout.addWidget(self._table, 1)

        # Detail panel
        self._detail_panel = MediaDetailPanel()
        content_layout.addWidget(self._detail_panel)

        layout.addLayout(content_layout, 1)

        # --- Status ---
        self._status_label = QLabel("Sẵn sàng")
        self._status_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px;")
        layout.addWidget(self._status_label)

    def refresh_data(self) -> None:
        """Reload media data from database."""
        try:
            media_files, total = self._service.get_all_media(
                search=self._current_search,
                media_type=self._current_type,
            )
            self._populate_table(media_files)
            self._count_label.setText(f"{total} file")
            self._status_label.setText(f"Đã tải {len(media_files)} file")
        except Exception as e:
            logger.exception("Failed to refresh media")
            self._status_label.setText(f"Lỗi: {e}")

    def _populate_table(self, media_files: list) -> None:
        self._table.setRowCount(len(media_files))
        self._media_list = media_files

        for row, media in enumerate(media_files):
            # ID
            id_item = QTableWidgetItem(str(media.id))
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            id_item.setData(Qt.ItemDataRole.UserRole, media.id)
            self._table.setItem(row, 0, id_item)

            # Filename
            icon = "🎬" if media.media_type == "video" else "🎵"
            name_item = QTableWidgetItem(f"{icon} {media.filename}")
            if not media.is_valid:
                name_item.setForeground(Qt.GlobalColor.red)
            self._table.setItem(row, 1, name_item)

            # Type
            type_item = QTableWidgetItem("Video" if media.media_type == "video" else "Audio")
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 2, type_item)

            # Duration
            dur_text = self._service.format_duration(media.duration_seconds)
            dur_item = QTableWidgetItem(dur_text)
            dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 3, dur_item)

            # Resolution
            res_text = self._service.format_resolution(media.width, media.height)
            res_item = QTableWidgetItem(res_text)
            res_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 4, res_item)

            # File size
            size_text = self._service.format_file_size(media.file_size_bytes)
            size_item = QTableWidgetItem(size_text)
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 5, size_item)

            # Actions
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(4, 2, 4, 2)
            action_layout.setSpacing(4)

            del_btn = QPushButton("🗑️")
            del_btn.setFixedSize(32, 28)
            del_btn.setToolTip("Xóa khỏi thư viện")
            del_btn.clicked.connect(lambda _, mid=media.id, fn=media.filename: self._delete_media(mid, fn))
            action_layout.addWidget(del_btn)
            action_layout.addStretch()

            self._table.setCellWidget(row, 6, action_widget)

    def _on_selection_changed(self, row: int, col: int, prev_row: int, prev_col: int) -> None:
        if 0 <= row < len(self._media_list):
            media = self._media_list[row]
            # Re-fetch to get fresh data
            full_media = self._service.get_media(media.id)
            self._detail_panel.show_media(full_media or media)
        else:
            self._detail_panel.clear()

    def _on_search(self, text: str) -> None:
        self._current_search = text.strip()
        self.refresh_data()

    def _on_type_changed(self, index: int) -> None:
        self._current_type = self._type_filter.currentData() or ""
        self.refresh_data()

    def _on_import(self) -> None:
        ext_video = " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))
        ext_audio = " ".join(f"*{e}" for e in sorted(AUDIO_EXTENSIONS))
        ext_all = f"{ext_video} {ext_audio}"

        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import Media Files",
            "",
            f"Media Files ({ext_all});;Video ({ext_video});;Audio ({ext_audio});;All Files (*)",
        )
        if not file_paths:
            return

        success, errors = self._service.import_files(file_paths)

        msg = f"Đã import {success}/{len(file_paths)} file."
        if errors:
            msg += f"\n\nLỗi:\n" + "\n".join(errors[:10])

        QMessageBox.information(self, "Import Media", msg)
        self.refresh_data()

    def _on_check_files(self) -> None:
        missing = self._service.check_missing_files()
        if not missing:
            QMessageBox.information(self, "Kiểm tra file", "✅ Tất cả file đều hợp lệ!")
        else:
            msg = f"⚠️ Phát hiện {len(missing)} file bị thiếu:\n\n"
            for m in missing[:10]:
                msg += f"  • {m['filename']}\n    {m['path']}\n"
            QMessageBox.warning(self, "Kiểm tra file", msg)
        self.refresh_data()

    def _delete_media(self, media_id: int, filename: str) -> None:
        reply = QMessageBox.question(
            self,
            "Xác nhận xóa",
            f"Xóa \"{filename}\" khỏi thư viện?\n\n(File gốc trên ổ đĩa sẽ KHÔNG bị xóa)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._service.delete_media(media_id)
            self._status_label.setText(f"✅ Đã xóa {filename}")
            self._detail_panel.clear()
            self.refresh_data()
