"""Scheduler & Playlist page — build the ordered list of products to present on LIVE."""

from __future__ import annotations

import os
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


from app.database.models.playlist import Playlist
from app.database.repositories.playlist_repo import PlaylistRepository
from app.database.repositories.product_repo import ProductRepository
from app.database.repositories.script_repo import ScriptRepository
from app.database.repositories.voice_repo import VoiceRepository
from app.database.session import get_session
from app.services.media_service import MediaService
from app.services.product_service import ProductService


class PlaylistItemDialog(QDialog):
    """Add/edit one playlist item: product + video + script/audio + duration."""

    def __init__(self, session, item=None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Mục playlist" if item is None else "Sửa mục playlist")
        self.setMinimumWidth(620)
        self._session = session
        self._item = item
        self._product_service = ProductService(ProductRepository(session))
        self._media_service = MediaService()
        self._script_repo = ScriptRepository(session)
        self._voice_repo = VoiceRepository(session)
        self.result_data: dict = {}
        self._build()
        if item is not None:
            self._load(item)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self.product_combo = QComboBox()
        self.product_combo.addItem("— Chọn sản phẩm —", None)
        for p in self._product_service.list_products(is_active=True):
            price = f" · {p.price:,.0f}đ" if p.price else ""
            self.product_combo.addItem(f"{p.name}{price}", p.id)
        self.product_combo.currentIndexChanged.connect(self._on_product_changed)
        form.addRow("Sản phẩm *:", self.product_combo)

        self.media_combo = QComboBox()
        form.addRow("Video phát trên OBS:", self.media_combo)

        self.script_combo = QComboBox()
        self.script_combo.currentIndexChanged.connect(self._on_script_changed)
        form.addRow("Kịch bản:", self.script_combo)

        self.audio_combo = QComboBox()
        form.addRow("Audio TTS:", self.audio_combo)

        dur_row = QHBoxLayout()
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0, 3600)
        self.duration_spin.setDecimals(0)
        self.duration_spin.setSuffix(" giây")
        self.duration_spin.setSpecialValueText("Tự động theo audio")
        self.duration_spin.setValue(0)
        dur_row.addWidget(self.duration_spin)
        dur_hint = QLabel("0 = theo thời lượng audio (hoặc 90s nếu chưa có audio)")
        dur_hint.setObjectName("mutedLabel")
        dur_row.addWidget(dur_hint, 1)
        form.addRow("Thời lượng:", dur_row)

        self.notes_input = QLineEdit()
        self.notes_input.setPlaceholderText("Ghi chú cho người vận hành (VD: nhắc ghim sản phẩm, deal 10 phút)...")
        form.addRow("Ghi chú:", self.notes_input)

        self.enabled_cb = QCheckBox("Bật mục này trong playlist")
        self.enabled_cb.setChecked(True)
        form.addRow("", self.enabled_cb)

        layout.addLayout(form)

        self.readiness = QLabel("")
        self.readiness.setObjectName("hintLabel")
        self.readiness.setWordWrap(True)
        layout.addWidget(self.readiness)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("Hủy")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        ok = QPushButton("💾 Lưu")
        ok.setObjectName("primaryButton")
        ok.clicked.connect(self._save)
        btns.addWidget(ok)
        layout.addLayout(btns)

        self._on_product_changed()

    def _on_product_changed(self) -> None:
        pid = self.product_combo.currentData()
        self.media_combo.clear()
        self.script_combo.clear()
        self.media_combo.addItem("— Tự động (video/ảnh đã gắn với sản phẩm) —", None)
        self.script_combo.addItem("— Tự động (kịch bản hiện tại của sản phẩm) —", None)
        if pid is None:
            self._on_script_changed()
            return
        linked = self._media_service.get_product_media(pid)
        icons = {"video": "🎬", "image": "🖼️"}
        for m in linked:
            if m.media_type in icons:
                self.media_combo.addItem(f"{icons[m.media_type]} {m.filename}", m.id)
        all_media, _ = self._media_service.get_all_media(limit=500)
        linked_ids = {m.id for m in linked}
        for m in all_media:
            if m.id not in linked_ids and m.media_type in icons:
                self.media_combo.addItem(f"{icons[m.media_type]} {m.filename} (chưa gắn)", m.id)
        for s in self._script_repo.get_by_product(pid):
            self.script_combo.addItem(f"v{s.version} · {s.title[:50]}", s.id)
        self._on_script_changed()

    def _on_script_changed(self) -> None:
        self.audio_combo.clear()
        self.audio_combo.addItem("— Tự động (audio mới nhất của kịch bản) —", None)
        sid = self.script_combo.currentData()
        pid = self.product_combo.currentData()
        scripts = []
        if sid is not None:
            s = self._script_repo.get_by_id(sid)
            scripts = [s] if s else []
        elif pid is not None:
            scripts = list(self._script_repo.get_by_product(pid))
        audio_count = 0
        for s in scripts:
            for a in self._voice_repo.get_audio_by_script(s.id):
                exists = os.path.exists(a.file_path)
                dur = f"{a.duration_seconds:.0f}s" if a.duration_seconds else "?"
                label = f"🔊 {os.path.basename(a.file_path)} · {dur}" + ("" if exists else " ⚠️ thiếu file")
                self.audio_combo.addItem(label, a.id)
                audio_count += 1
        self._update_readiness(bool(scripts), audio_count > 0)

    def _update_readiness(self, has_script: bool, has_audio: bool) -> None:
        pid = self.product_combo.currentData()
        if pid is None:
            self.readiness.setText("")
            return
        parts = []
        has_video = any(m.media_type in ("video", "image") for m in self._media_service.get_product_media(pid))
        parts.append("🎬 video/ảnh: " + ("có" if has_video else "chưa gắn — OBS sẽ không có hình cho mục này"))
        parts.append("📝 kịch bản: " + ("có" if has_script else "chưa có — tạo ở Script Studio"))
        parts.append("🔊 audio: " + ("có" if has_audio else "chưa có — bấm 'Tạo âm thanh TTS' ở Script Studio"))
        self.readiness.setText("Sẵn sàng: " + " · ".join(parts))

    def _load(self, item) -> None:
        idx = self.product_combo.findData(item.product_id)
        if idx >= 0:
            self.product_combo.setCurrentIndex(idx)
        idx = self.media_combo.findData(item.media_id)
        if idx >= 0:
            self.media_combo.setCurrentIndex(idx)
        idx = self.script_combo.findData(item.script_id)
        if idx >= 0:
            self.script_combo.setCurrentIndex(idx)
        idx = self.audio_combo.findData(item.audio_id)
        if idx >= 0:
            self.audio_combo.setCurrentIndex(idx)
        self.duration_spin.setValue(item.duration_seconds or 0)
        self.notes_input.setText(item.notes or "")
        self.enabled_cb.setChecked(bool(item.is_enabled))

    def _save(self) -> None:
        pid = self.product_combo.currentData()
        if pid is None:
            QMessageBox.warning(self, "Thiếu sản phẩm", "Vui lòng chọn sản phẩm cho mục này.")
            return
        self.result_data = {
            "product_id": pid,
            "media_id": self.media_combo.currentData(),
            "script_id": self.script_combo.currentData(),
            "audio_id": self.audio_combo.currentData(),
            "duration_seconds": self.duration_spin.value() or None,
            "notes": self.notes_input.text().strip() or None,
            "is_enabled": self.enabled_cb.isChecked(),
        }
        self.accept()


class SchedulerPage(QWidget):
    """Scheduler & Playlist UI Page."""

    playlists_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.playlists: List[Playlist] = []
        self.current_playlist: Optional[Playlist] = None
        self._init_services()
        self._init_ui()
        self.refresh_playlists()

    def _init_services(self) -> None:
        self.session = get_session()
        self.playlist_repo = PlaylistRepository(self.session)
        self.product_repo = ProductRepository(self.session)
        self.product_service = ProductService(self.product_repo)
        self.media_service = MediaService()

    # ------------------------------------------------------------------ UI
    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        header_layout = QHBoxLayout()
        title_label = QLabel("📅 Playlist & Lịch trình giới thiệu sản phẩm")
        title_label.setObjectName("headerLabel")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        intro = QLabel(
            "Playlist là thứ tự sản phẩm sẽ được tự động giới thiệu khi LIVE: với mỗi mục, OBS phát video, "
            "giọng đọc AI đọc kịch bản, hết thời lượng thì chuyển sang sản phẩm tiếp theo. "
            "Bắt đầu/tạm dừng/dừng ở trang Live Studio."
        )
        intro.setObjectName("infoBanner")
        intro.setWordWrap(True)
        main_layout.addWidget(intro)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---- Left: playlists
        left_card = QFrame()
        left_card.setObjectName("cardFrame")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(10)

        list_title = QLabel("📜 Playlist")
        list_title.setObjectName("sectionTitle")
        left_layout.addWidget(list_title)

        self.playlist_combo = QComboBox()
        self.playlist_combo.currentIndexChanged.connect(self._on_playlist_changed)
        left_layout.addWidget(self.playlist_combo)

        settings_title = QLabel("⚙️ Thiết lập playlist đang chọn")
        settings_title.setStyleSheet("font-weight: 600; margin-top: 6px;")
        left_layout.addWidget(settings_title)

        form = QFormLayout()
        form.setSpacing(8)
        self.edit_name_input = QLineEdit()
        form.addRow("Tên:", self.edit_name_input)
        self.loop_cb = QCheckBox("Lặp lại liên tục cho đến khi dừng")
        self.loop_cb.setChecked(True)
        self.loop_cb.toggled.connect(lambda on: self.loop_count_spin.setEnabled(not on))
        form.addRow("", self.loop_cb)
        self.loop_count_spin = QSpinBox()
        self.loop_count_spin.setRange(1, 999)
        self.loop_count_spin.setValue(1)
        self.loop_count_spin.setEnabled(False)
        form.addRow("Số vòng (nếu không lặp):", self.loop_count_spin)
        self.gap_spin = QDoubleSpinBox()
        self.gap_spin.setRange(0, 300)
        self.gap_spin.setValue(5.0)
        self.gap_spin.setDecimals(0)
        self.gap_spin.setSuffix(" giây")
        form.addRow("Nghỉ giữa sản phẩm:", self.gap_spin)
        left_layout.addLayout(form)

        btn_row = QHBoxLayout()
        self.save_settings_btn = QPushButton("💾 Lưu thiết lập")
        self.save_settings_btn.clicked.connect(self._save_playlist_settings)
        btn_row.addWidget(self.save_settings_btn)
        self.delete_playlist_btn = QPushButton("🗑️ Xóa playlist")
        self.delete_playlist_btn.setObjectName("dangerButton")
        self.delete_playlist_btn.clicked.connect(self._delete_playlist)
        btn_row.addWidget(self.delete_playlist_btn)
        left_layout.addLayout(btn_row)

        left_layout.addSpacing(10)
        new_title = QLabel("➕ Tạo playlist mới")
        new_title.setStyleSheet("font-weight: 600;")
        left_layout.addWidget(new_title)
        new_row = QHBoxLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("VD: Live tối thứ 6 — Deal sốc")
        new_row.addWidget(self.name_input, 1)
        self.create_btn = QPushButton("Tạo")
        self.create_btn.setObjectName("primaryButton")
        self.create_btn.clicked.connect(self._create_playlist)
        new_row.addWidget(self.create_btn)
        left_layout.addLayout(new_row)

        self.quick_fill_btn = QPushButton("⚡ Thêm nhanh tất cả sản phẩm đang bật")
        self.quick_fill_btn.setObjectName("accentButton")
        self.quick_fill_btn.setToolTip("Thêm mọi sản phẩm đang bật vào playlist hiện tại theo thứ tự sắp xếp của trang Sản phẩm")
        self.quick_fill_btn.clicked.connect(self._quick_fill)
        left_layout.addWidget(self.quick_fill_btn)

        left_layout.addStretch()
        splitter.addWidget(left_card)

        # ---- Right: items
        right_card = QFrame()
        right_card.setObjectName("cardFrame")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(10)

        items_header = QHBoxLayout()
        self.playlist_name_label = QLabel("Chi tiết playlist")
        self.playlist_name_label.setObjectName("sectionTitle")
        items_header.addWidget(self.playlist_name_label)
        items_header.addStretch()
        self.add_item_btn = QPushButton("➕ Thêm sản phẩm")
        self.add_item_btn.setObjectName("primaryButton")
        self.add_item_btn.clicked.connect(self._add_item)
        items_header.addWidget(self.add_item_btn)
        right_layout.addLayout(items_header)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["#", "Sản phẩm", "Video", "Kịch bản / Audio", "Thời lượng", "Trạng thái", "Ghi chú"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(4, 90)
        self.table.setColumnWidth(5, 100)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(lambda _: self._edit_item())
        right_layout.addWidget(self.table, 1)

        item_btns = QHBoxLayout()
        self.up_btn = QPushButton("⬆️ Lên")
        self.up_btn.clicked.connect(lambda: self._move_item(-1))
        item_btns.addWidget(self.up_btn)
        self.down_btn = QPushButton("⬇️ Xuống")
        self.down_btn.clicked.connect(lambda: self._move_item(1))
        item_btns.addWidget(self.down_btn)
        self.edit_btn = QPushButton("✏️ Sửa")
        self.edit_btn.clicked.connect(self._edit_item)
        item_btns.addWidget(self.edit_btn)
        self.toggle_btn = QPushButton("⏯️ Bật/Tắt")
        self.toggle_btn.clicked.connect(self._toggle_item)
        item_btns.addWidget(self.toggle_btn)
        self.remove_btn = QPushButton("🗑️ Gỡ")
        self.remove_btn.setObjectName("dangerButton")
        self.remove_btn.clicked.connect(self._remove_item)
        item_btns.addWidget(self.remove_btn)
        item_btns.addStretch()
        right_layout.addLayout(item_btns)

        self.summary_label = QLabel("")
        self.summary_label.setObjectName("hintLabel")
        self.summary_label.setWordWrap(True)
        right_layout.addWidget(self.summary_label)

        splitter.addWidget(right_card)
        splitter.setSizes([380, 760])
        main_layout.addWidget(splitter, 1)

    # ---------------------------------------------------------- playlists
    def refresh_playlists(self) -> None:
        current_id = self.current_playlist.id if self.current_playlist else None
        self.playlist_combo.blockSignals(True)
        self.playlist_combo.clear()
        self.playlists = list(self.playlist_repo.list_playlists())
        for p in self.playlists:
            self.playlist_combo.addItem(f"{p.name}  ({len(p.items)} mục)", p.id)
        self.playlist_combo.blockSignals(False)

        if not self.playlists:
            self.current_playlist = None
            self.playlist_name_label.setText("Chưa có playlist — hãy tạo một playlist ở cột trái")
            self.table.setRowCount(0)
            self.summary_label.setText("")
            self._set_settings_enabled(False)
            self.playlists_changed.emit()
            return

        idx = self.playlist_combo.findData(current_id) if current_id else 0
        self.playlist_combo.setCurrentIndex(max(idx, 0))
        self._on_playlist_changed(self.playlist_combo.currentIndex())
        self.playlists_changed.emit()

    def _set_settings_enabled(self, enabled: bool) -> None:
        for w in (self.edit_name_input, self.loop_cb, self.loop_count_spin, self.gap_spin,
                  self.save_settings_btn, self.delete_playlist_btn, self.add_item_btn, self.quick_fill_btn):
            w.setEnabled(enabled)
        if enabled:
            self.loop_count_spin.setEnabled(not self.loop_cb.isChecked())

    def _on_playlist_changed(self, index: int) -> None:
        if index < 0 or index >= len(self.playlists):
            return
        self.current_playlist = self.playlist_repo.get_by_id(self.playlists[index].id, fresh=True)
        if not self.current_playlist:
            return
        p = self.current_playlist
        self._set_settings_enabled(True)
        self.edit_name_input.setText(p.name)
        self.loop_cb.setChecked(bool(p.is_loop))
        self.loop_count_spin.setValue(max(1, p.loop_count or 1))
        self.gap_spin.setValue(p.gap_seconds or 0)
        self._load_items(p)

    def _load_items(self, playlist: Playlist) -> None:
        self.playlist_name_label.setText(f"📋 {playlist.name}")
        items = sorted(playlist.items, key=lambda i: i.sort_order)
        self.table.setRowCount(len(items))
        total = 0.0
        ready = 0
        for row, item in enumerate(items):
            product = item.product
            prod_name = product.name if product else "⚠️ Sản phẩm đã bị xóa"

            video_name = "—"
            if item.media is not None:
                video_name = item.media.filename
            elif product is not None:
                primary = self.media_service.get_primary_video_for_product(product.id) or self.media_service.get_primary_image_for_product(product.id)
                video_name = f"{primary.filename} (tự động)" if primary else "⚠️ chưa gắn video/ảnh"

            script_label = "—"
            audio_dur = None
            script = item.script
            if script is None and product is not None:
                current = [s for s in product.scripts if s.is_current] or list(product.scripts)
                script = current[0] if current else None
            if script is not None:
                audios = [a for a in script.audio_files if os.path.exists(a.file_path)]
                audio_label = "🔊 có audio" if audios else "⚠️ chưa có audio"
                if audios:
                    audio_dur = audios[-1].duration_seconds
                script_label = f"v{script.version} · {audio_label}"
            else:
                script_label = "⚠️ chưa có kịch bản"

            duration = item.duration_seconds or audio_dur or 90.0
            total += duration + (playlist.gap_seconds or 0)
            is_ready = product is not None and script is not None and "có audio" in script_label
            if is_ready and item.is_enabled:
                ready += 1

            cells = [
                str(item.sort_order),
                prod_name,
                video_name,
                script_label,
                f"{duration:.0f}s" + ("" if item.duration_seconds else " (auto)"),
                "✅ Bật" if item.is_enabled else "⏸️ Tắt",
                item.notes or "",
            ]
            for col, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                if col in (0, 4, 5):
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, item.id)
                if not item.is_enabled:
                    cell.setForeground(Qt.GlobalColor.gray)
                self.table.setItem(row, col, cell)

        mins = total / 60
        self.summary_label.setText(
            f"Tổng {len(items)} mục · {ready} mục sẵn sàng (có kịch bản + audio) · "
            f"ước tính ~{mins:.0f} phút/vòng" + (" · lặp liên tục" if playlist.is_loop else f" · {playlist.loop_count} vòng")
        )

    def _create_playlist(self) -> None:
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Thiếu tên", "Vui lòng nhập tên playlist.")
            return
        p = self.playlist_repo.create_playlist(name=name, is_loop=True, gap_seconds=5.0)
        self.name_input.clear()
        self.current_playlist = p
        self.refresh_playlists()

    def _save_playlist_settings(self) -> None:
        if not self.current_playlist:
            return
        name = self.edit_name_input.text().strip() or self.current_playlist.name
        self.playlist_repo.update_playlist(
            self.current_playlist.id,
            name=name,
            is_loop=self.loop_cb.isChecked(),
            loop_count=self.loop_count_spin.value(),
            gap_seconds=float(self.gap_spin.value()),
        )
        self.refresh_playlists()

    def _delete_playlist(self) -> None:
        if not self.current_playlist:
            return
        reply = QMessageBox.question(
            self, "Xóa playlist",
            f"Xóa playlist \"{self.current_playlist.name}\" và toàn bộ mục trong đó?\n(Sản phẩm, video, kịch bản KHÔNG bị xóa.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.playlist_repo.delete_playlist(self.current_playlist.id)
            self.current_playlist = None
            self.refresh_playlists()

    # -------------------------------------------------------------- items
    def _selected_item_id(self) -> Optional[int]:
        row = self.table.currentRow()
        if row < 0:
            return None
        cell = self.table.item(row, 0)
        return int(cell.data(Qt.ItemDataRole.UserRole)) if cell else None

    def _reload_current(self) -> None:
        if self.current_playlist:
            self.current_playlist = self.playlist_repo.get_by_id(self.current_playlist.id, fresh=True)
            if self.current_playlist:
                self._load_items(self.current_playlist)
        # keep combo labels (item counts) fresh
        idx = self.playlist_combo.currentIndex()
        if idx >= 0 and self.current_playlist:
            self.playlist_combo.setItemText(idx, f"{self.current_playlist.name}  ({len(self.current_playlist.items)} mục)")
        self.playlists_changed.emit()

    def _add_item(self) -> None:
        if not self.current_playlist:
            QMessageBox.warning(self, "Chưa có playlist", "Hãy tạo playlist trước.")
            return
        if not self.product_service.list_products(is_active=True):
            QMessageBox.warning(self, "Chưa có sản phẩm", "Chưa có sản phẩm nào đang bật. Hãy thêm sản phẩm ở trang 'Sản phẩm'.")
            return
        dlg = PlaylistItemDialog(self.session, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_data:
            d = dlg.result_data
            item = self.playlist_repo.add_item(
                playlist_id=self.current_playlist.id,
                product_id=d["product_id"],
                media_id=d["media_id"],
                script_id=d["script_id"],
                audio_id=d["audio_id"],
                duration_seconds=d["duration_seconds"],
                notes=d["notes"],
            )
            if not d["is_enabled"]:
                self.playlist_repo.update_item(item.id, is_enabled=False)
            self._reload_current()

    def _quick_fill(self) -> None:
        if not self.current_playlist:
            return
        products = self.product_service.list_products(is_active=True)
        if not products:
            QMessageBox.warning(self, "Chưa có sản phẩm", "Không có sản phẩm nào đang bật.")
            return
        existing = {i.product_id for i in self.current_playlist.items}
        added = 0
        for p in products:
            if p.id in existing:
                continue
            self.playlist_repo.add_item(playlist_id=self.current_playlist.id, product_id=p.id)
            added += 1
        self._reload_current()
        QMessageBox.information(self, "Đã thêm", f"Đã thêm {added} sản phẩm vào playlist. Các mục dùng video/kịch bản/audio tự động của sản phẩm.")

    def _edit_item(self) -> None:
        item_id = self._selected_item_id()
        if item_id is None:
            QMessageBox.information(self, "Chọn mục", "Hãy chọn một dòng trong bảng để sửa.")
            return
        item = self.playlist_repo.get_item(item_id)
        if not item:
            return
        dlg = PlaylistItemDialog(self.session, item=item, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_data:
            self.playlist_repo.update_item(item_id, **dlg.result_data)
            self._reload_current()

    def _toggle_item(self) -> None:
        item_id = self._selected_item_id()
        if item_id is None:
            return
        item = self.playlist_repo.get_item(item_id)
        if item:
            self.playlist_repo.update_item(item_id, is_enabled=not item.is_enabled)
            self._reload_current()

    def _move_item(self, direction: int) -> None:
        item_id = self._selected_item_id()
        if item_id is None:
            return
        row = self.table.currentRow()
        if self.playlist_repo.move_item(item_id, direction):
            self._reload_current()
            new_row = min(max(row + direction, 0), self.table.rowCount() - 1)
            self.table.selectRow(new_row)

    def _remove_item(self) -> None:
        item_id = self._selected_item_id()
        if item_id is None:
            return
        if self.playlist_repo.remove_item(item_id):
            self._reload_current()
