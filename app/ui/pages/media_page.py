"""Media library page — import, browse, preview, validate and link video/audio to products."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from loguru import logger

from app.services.activity_log_service import log_activity
from app.services.media_service import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, MediaService
from app.services.product_service import ProductService
from app.ui.dialogs.link_media_dialog import LinkMediaToProductsDialog
from app.ui.styles.dark_theme import COLORS


class MediaDetailPanel(QFrame):
    """Panel showing detailed metadata for a selected media file."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("cardFrame")
        self.setMinimumWidth(300)
        self.setMaximumWidth(380)
        self._service = MediaService()
        self._product_service = ProductService()
        self._media = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("📋 Chi tiết Media")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        info_layout = QGridLayout()
        info_layout.setSpacing(6)
        info_layout.setColumnStretch(1, 1)
        labels = ["Tên file:", "Loại:", "Thời lượng:", "Độ phân giải:", "Codec:", "Dung lượng:", "Bitrate:", "Định dạng:", "Đường dẫn:", "Trạng thái:"]
        self._info_values: list[QLabel] = []
        for i, label_text in enumerate(labels):
            label = QLabel(label_text)
            label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
            label.setAlignment(Qt.AlignmentFlag.AlignTop)
            info_layout.addWidget(label, i, 0)
            value = QLabel("—")
            value.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 12px;")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            info_layout.addWidget(value, i, 1)
            self._info_values.append(value)
        layout.addLayout(info_layout)

        self._products_label = QLabel("📦 Sản phẩm liên kết: —")
        self._products_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
        self._products_label.setWordWrap(True)
        layout.addWidget(self._products_label)

        layout.addStretch()

        self.preview_btn = QPushButton("▶️ Xem trước (trình phát hệ thống)")
        self.preview_btn.clicked.connect(self._preview)
        layout.addWidget(self.preview_btn)
        self.folder_btn = QPushButton("📂 Mở thư mục chứa file")
        self.folder_btn.clicked.connect(self._open_folder)
        layout.addWidget(self.folder_btn)
        self.link_btn = QPushButton("🔗 Gắn với sản phẩm...")
        self.link_btn.setObjectName("primaryButton")
        self.link_btn.clicked.connect(self._link)
        layout.addWidget(self.link_btn)
        self.revalidate_btn = QPushButton("🔁 Kiểm tra lại file")
        self.revalidate_btn.clicked.connect(self._revalidate)
        layout.addWidget(self.revalidate_btn)
        self._set_buttons(False)

        self.on_changed = None  # callback set by page

    def _set_buttons(self, enabled: bool) -> None:
        for b in (self.preview_btn, self.folder_btn, self.link_btn, self.revalidate_btn):
            b.setEnabled(enabled)

    def show_media(self, media) -> None:
        self._media = media
        if not media:
            self.clear()
            return
        svc = self._service
        self._info_values[0].setText(media.filename)
        self._info_values[1].setText({"video": "🎬 Video", "audio": "🎵 Audio", "image": "🖼️ Ảnh"}.get(media.media_type, media.media_type))
        self._info_values[2].setText(svc.format_duration(media.duration_seconds))
        self._info_values[3].setText(svc.format_resolution(media.width, media.height))
        self._info_values[4].setText(media.codec or "—")
        self._info_values[5].setText(svc.format_file_size(media.file_size_bytes))
        self._info_values[6].setText(f"{media.bitrate // 1000} kbps" if media.bitrate else "—")
        self._info_values[7].setText(media.format_name or "—")
        self._info_values[8].setText(media.file_path)
        exists = Path(media.file_path).exists()
        if not exists:
            status = "❌ File không tồn tại trên ổ đĩa"
        elif media.is_valid:
            status = "✅ Hợp lệ"
        else:
            status = f"⚠️ {media.error_message or 'Không đọc được'}"
        self._info_values[9].setText(status)

        names = []
        for pid in svc.get_product_ids_for_media(media.id):
            p = self._product_service.get_product(pid)
            if p:
                names.append(p.name)
        self._products_label.setText("📦 Sản phẩm liên kết: " + (", ".join(names) if names else "chưa gắn — bấm 'Gắn với sản phẩm'"))
        self._set_buttons(True)
        self.preview_btn.setEnabled(exists)
        self.folder_btn.setEnabled(exists)

    def clear(self) -> None:
        self._media = None
        for v in self._info_values:
            v.setText("—")
        self._products_label.setText("📦 Sản phẩm liên kết: —")
        self._set_buttons(False)

    def _preview(self) -> None:
        if self._media and Path(self._media.file_path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._media.file_path))

    def _open_folder(self) -> None:
        if self._media:
            folder = str(Path(self._media.file_path).parent)
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _link(self) -> None:
        if not self._media:
            return
        dlg = LinkMediaToProductsDialog(self._media.id, self._media.filename, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.show_media(self._service.get_media(self._media.id))
            if self.on_changed:
                self.on_changed()

    def _revalidate(self) -> None:
        if not self._media:
            return
        ok = self._service.revalidate(self._media.id)
        self.show_media(self._service.get_media(self._media.id))
        if self.on_changed:
            self.on_changed()
        if not ok:
            QMessageBox.warning(self, "Kiểm tra file", "File vẫn không đọc được hoặc không tồn tại.")


class MediaPage(QWidget):
    """Media library page for managing imported video and audio files."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = MediaService()
        self._current_search = ""
        self._current_type = ""
        self._media_list: list = []
        self._setup_ui()
        self.refresh_data()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        header_row = QHBoxLayout()
        title = QLabel("Thư viện Media")
        title.setObjectName("headerLabel")
        header_row.addWidget(title)
        header_row.addStretch()
        self._count_label = QLabel("0 file")
        self._count_label.setObjectName("subHeaderLabel")
        header_row.addWidget(self._count_label)
        layout.addLayout(header_row)

        self._ffmpeg_banner = QLabel("")
        self._ffmpeg_banner.setObjectName("warningBanner")
        self._ffmpeg_banner.setWordWrap(True)
        self._ffmpeg_banner.setVisible(False)
        layout.addWidget(self._ffmpeg_banner)

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
        self._type_filter.addItem("🖼️ Ảnh", "image")
        self._type_filter.currentIndexChanged.connect(self._on_type_changed)
        toolbar.addWidget(self._type_filter)
        toolbar.addStretch()

        tip = QLabel("Mẹo: với LIVE kiểu banner + giọng AI (như demo), chỉ cần import ẢNH sản phẩm/ảnh người mẫu cầm sản phẩm và gắn với sản phẩm; "
                     "LiveFlow sẽ hiển thị ảnh trong OBS kèm tên + giá khi đến lượt sản phẩm đó.")
        tip.setObjectName("hintLabel")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        check_btn = QPushButton("🩺 Kiểm tra file thiếu")
        check_btn.setToolTip("Kiểm tra file bị thiếu hoặc không đọc được")
        check_btn.clicked.connect(self._on_check_files)
        toolbar.addWidget(check_btn)

        import_folder_btn = QPushButton("📁 Import thư mục")
        import_folder_btn.clicked.connect(self._on_import_folder)
        toolbar.addWidget(import_folder_btn)

        import_btn = QPushButton("📥 Import file")
        import_btn.setObjectName("primaryButton")
        import_btn.clicked.connect(self._on_import)
        toolbar.addWidget(import_btn)
        layout.addLayout(toolbar)

        content_layout = QHBoxLayout()
        content_layout.setSpacing(16)

        self._table = QTableWidget()
        self._table.setColumnCount(8)
        self._table.setHorizontalHeaderLabels(["ID", "Tên file", "Loại", "Thời lượng", "Độ phân giải", "Dung lượng", "Sản phẩm", "Thao tác"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setColumnWidth(0, 50)
        self._table.setColumnWidth(2, 80)
        self._table.setColumnWidth(3, 90)
        self._table.setColumnWidth(4, 100)
        self._table.setColumnWidth(5, 100)
        self._table.setColumnWidth(7, 130)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.currentCellChanged.connect(self._on_selection_changed)
        self._table.doubleClicked.connect(lambda _: self._detail_panel._preview())
        content_layout.addWidget(self._table, 1)

        self._detail_panel = MediaDetailPanel()
        self._detail_panel.on_changed = self.refresh_data
        content_layout.addWidget(self._detail_panel)
        layout.addLayout(content_layout, 1)

        self._status_label = QLabel("Sẵn sàng")
        self._status_label.setObjectName("mutedLabel")
        layout.addWidget(self._status_label)

    # --------------------------------------------------------------- data
    def refresh_data(self) -> None:
        try:
            self._ffmpeg_banner.setVisible(not self._service.ffprobe_available)
            if not self._service.ffprobe_available:
                self._ffmpeg_banner.setText(
                    "⚠️ Chưa tìm thấy FFmpeg/ffprobe — vẫn import được nhưng không đọc được thời lượng/độ phân giải. "
                    "Chạy scripts\\download_ffmpeg.ps1 hoặc cài FFmpeg rồi chọn đường dẫn trong Cài đặt."
                )
            media_files, total = self._service.get_all_media(search=self._current_search, media_type=self._current_type, limit=1000)
            self._populate_table(media_files)
            self._count_label.setText(f"{total} file")
            self._status_label.setText(f"Đã tải {len(media_files)} file")
        except Exception as e:  # noqa: BLE001
            logger.exception("Failed to refresh media")
            self._status_label.setText(f"Lỗi: {e}")

    def _populate_table(self, media_files: list) -> None:
        self._table.setRowCount(len(media_files))
        self._media_list = media_files
        product_service = ProductService()
        for row, media in enumerate(media_files):
            id_item = QTableWidgetItem(str(media.id))
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            id_item.setData(Qt.ItemDataRole.UserRole, media.id)
            self._table.setItem(row, 0, id_item)

            icon = {"video": "🎬", "audio": "🎵", "image": "🖼️"}.get(media.media_type, "📄")
            missing = not Path(media.file_path).exists()
            name_item = QTableWidgetItem(f"{icon} {media.filename}" + (" ⚠️" if missing or not media.is_valid else ""))
            if missing:
                name_item.setForeground(Qt.GlobalColor.red)
            elif not media.is_valid:
                name_item.setForeground(Qt.GlobalColor.yellow)
            self._table.setItem(row, 1, name_item)

            type_item = QTableWidgetItem({"video": "Video", "audio": "Audio", "image": "Ảnh"}.get(media.media_type, media.media_type))
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 2, type_item)

            dur_item = QTableWidgetItem(self._service.format_duration(media.duration_seconds))
            dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 3, dur_item)

            res_item = QTableWidgetItem(self._service.format_resolution(media.width, media.height))
            res_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 4, res_item)

            size_item = QTableWidgetItem(self._service.format_file_size(media.file_size_bytes))
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 5, size_item)

            pids = self._service.get_product_ids_for_media(media.id)
            names = []
            for pid in pids[:2]:
                p = product_service.get_product(pid)
                if p:
                    names.append(p.name[:22])
            extra = f" +{len(pids) - 2}" if len(pids) > 2 else ""
            self._table.setItem(row, 6, QTableWidgetItem((", ".join(names) + extra) if names else "—"))

            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(4, 2, 4, 2)
            action_layout.setSpacing(4)
            link_btn = QPushButton("🔗")
            link_btn.setFixedSize(32, 28)
            link_btn.setToolTip("Gắn với sản phẩm")
            link_btn.clicked.connect(lambda _, mid=media.id, fn=media.filename: self._link_media(mid, fn))
            action_layout.addWidget(link_btn)
            play_btn = QPushButton("▶️")
            play_btn.setFixedSize(32, 28)
            play_btn.setToolTip("Xem trước")
            play_btn.setEnabled(not missing)
            play_btn.clicked.connect(lambda _, p=media.file_path: QDesktopServices.openUrl(QUrl.fromLocalFile(p)))
            action_layout.addWidget(play_btn)
            del_btn = QPushButton("🗑️")
            del_btn.setFixedSize(32, 28)
            del_btn.setToolTip("Xóa khỏi thư viện (không xóa file gốc)")
            del_btn.setObjectName("dangerButton")
            del_btn.clicked.connect(lambda _, mid=media.id, fn=media.filename: self._delete_media(mid, fn))
            action_layout.addWidget(del_btn)
            action_layout.addStretch()
            self._table.setCellWidget(row, 7, action_widget)

    # ------------------------------------------------------------- events
    def _on_selection_changed(self, row: int, col: int, prev_row: int, prev_col: int) -> None:
        if 0 <= row < len(self._media_list):
            media = self._media_list[row]
            self._detail_panel.show_media(self._service.get_media(media.id) or media)
        else:
            self._detail_panel.clear()

    def _on_search(self, text: str) -> None:
        self._current_search = text.strip()
        self.refresh_data()

    def _on_type_changed(self, index: int) -> None:
        self._current_type = self._type_filter.currentData() or ""
        self.refresh_data()

    def _import_paths(self, file_paths: list[str]) -> None:
        if not file_paths:
            return
        success, errors = self._service.import_files(file_paths)
        msg = f"Đã import {success}/{len(file_paths)} file."
        if errors:
            msg += "\n\nLỗi:\n" + "\n".join(errors[:10])
        log_activity("media", f"Import {success} file media", "; ".join(errors[:3]) if errors else "", level="WARNING" if errors else "INFO")
        QMessageBox.information(self, "Import Media", msg)
        self.refresh_data()

    def _on_import(self) -> None:
        ext_video = " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))
        ext_audio = " ".join(f"*{e}" for e in sorted(AUDIO_EXTENSIONS))
        ext_image = " ".join(f"*{e}" for e in sorted(IMAGE_EXTENSIONS))
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "Import Media Files", "",
            f"Media Files ({ext_video} {ext_audio} {ext_image});;Video ({ext_video});;Ảnh ({ext_image});;Audio ({ext_audio});;All Files (*)",
        )
        self._import_paths(file_paths)

    def _on_import_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Chọn thư mục chứa video/audio")
        if not folder:
            return
        paths = [str(p) for p in sorted(Path(folder).iterdir())
                 if p.is_file() and p.suffix.lower() in (VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | IMAGE_EXTENSIONS)]
        if not paths:
            QMessageBox.information(self, "Import thư mục", "Không tìm thấy file video/audio nào trong thư mục.")
            return
        self._import_paths(paths)

    def _on_check_files(self) -> None:
        missing = self._service.check_missing_files()
        if not missing:
            QMessageBox.information(self, "Kiểm tra file", "✅ Tất cả file đều tồn tại trên ổ đĩa.")
        else:
            msg = f"⚠️ Phát hiện {len(missing)} file bị thiếu:\n\n" + "\n".join(f"  • {m['filename']}\n    {m['path']}" for m in missing[:10])
            QMessageBox.warning(self, "Kiểm tra file", msg)
        self.refresh_data()

    def _link_media(self, media_id: int, filename: str) -> None:
        dlg = LinkMediaToProductsDialog(media_id, filename, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh_data()

    def _delete_media(self, media_id: int, filename: str) -> None:
        reply = QMessageBox.question(
            self, "Xác nhận xóa",
            f"Xóa \"{filename}\" khỏi thư viện?\n\n(File gốc trên ổ đĩa sẽ KHÔNG bị xóa)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._service.delete_media(media_id)
            self._status_label.setText(f"✅ Đã xóa {filename}")
            self._detail_panel.clear()
            self.refresh_data()
