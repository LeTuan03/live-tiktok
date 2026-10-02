"""Live Studio — OBS connection, RTMP setup for TikTok/Shopee and playlist orchestration."""

from __future__ import annotations

import os
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from loguru import logger

from app.database.repositories.playlist_repo import PlaylistRepository
from app.database.session import get_session
from app.integrations.obs.client import OBSClient
from app.integrations.tiktok.capabilities import Platform, get_capability, Capability
from app.services.activity_log_service import log_activity
from app.services.scheduler_service import (
    LiveOrchestrator,
    OrchestratorConfig,
    RunState,
    build_entries_from_playlist,
)
from app.services.settings_service import (
    KEY_OBS_AUTO_SWITCH_SCENE,
    KEY_OBS_AVATAR_SOURCE,
    KEY_STREAM_KEY_SHOPEE,
    KEY_STREAM_KEY_TIKTOK,
    KEY_STREAM_SERVER_SHOPEE,
    KEY_STREAM_SERVER_TIKTOK,
    SettingsService,
)
from app.ui.dialogs.product_showcase_dialog import ProductShowcaseDialog
from app.ui.pages.settings_page import TestOBSWorker
from app.ui.styles.dark_theme import COLORS


class LocalAudioPlayer:
    """Thin wrapper around QMediaPlayer with graceful fallback."""

    def __init__(self, parent: QWidget, on_finished) -> None:
        self._player = None
        self._output = None
        self._on_finished = on_finished
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

            self._player = QMediaPlayer(parent)
            self._output = QAudioOutput(parent)
            self._player.setAudioOutput(self._output)
            self._player.mediaStatusChanged.connect(self._on_status)
            self._player.errorOccurred.connect(lambda err, msg: logger.warning("QMediaPlayer: {}", msg))
        except Exception as e:  # pragma: no cover - multimedia backend missing
            logger.warning("QtMultimedia không khả dụng, dùng trình phát hệ thống: {}", e)

    def _on_status(self, status) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._on_finished()

    def play(self, path: str, volume: float = 1.0) -> None:
        if self._player is None:
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            return
        self._player.stop()
        self._output.setVolume(max(0.0, min(1.0, volume)))
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()

    def stop(self) -> None:
        if self._player is not None:
            self._player.stop()

    @property
    def available(self) -> bool:
        return self._player is not None


class LiveStudioPage(QWidget):
    """Live Studio UI Page."""

    obs_status_changed = Signal(bool)
    live_state_changed = Signal(str, bool)  # label, running

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.obs_client = OBSClient()
        self._test_worker: Optional[TestOBSWorker] = None
        self._orchestrator: Optional[LiveOrchestrator] = None
        self._entries = []
        self._session = get_session()
        self.settings_service = SettingsService(self._session)
        self.playlist_repo = PlaylistRepository(self._session)
        self._audio = LocalAudioPlayer(self, self._on_local_audio_finished)

        self._init_ui()
        self.reload_settings()
        self.refresh_playlists()

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(5000)
        self._status_timer.timeout.connect(self._poll_obs_status)
        self._status_timer.start()

    # ------------------------------------------------------------------ UI
    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(12)

        header_layout = QHBoxLayout()
        title_label = QLabel("🔴 Live Studio")
        title_label.setObjectName("headerLabel")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        self.stream_badge = QLabel("OBS: CHƯA KẾT NỐI")
        self._set_badge("offline")
        header_layout.addWidget(self.stream_badge)
        main_layout.addLayout(header_layout)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ============ LEFT: OBS + RTMP ============
        left_card = QFrame()
        left_card.setObjectName("cardFrame")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(10)

        obs_title = QLabel("🎥 1. Kết nối OBS Studio")
        obs_title.setObjectName("sectionTitle")
        left_layout.addWidget(obs_title)

        form = QFormLayout()
        form.setSpacing(8)
        self.host_input = QLineEdit("localhost")
        form.addRow("Host:", self.host_input)
        self.port_input = QLineEdit("4455")
        form.addRow("Port:", self.port_input)
        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_input.setPlaceholderText("Mật khẩu WebSocket (nếu có)")
        form.addRow("Password:", self.pass_input)
        left_layout.addLayout(form)

        btn_box = QHBoxLayout()
        self.connect_btn = QPushButton("🔌 Kết nối OBS")
        self.connect_btn.setObjectName("primaryButton")
        self.connect_btn.clicked.connect(self._toggle_obs_connection)
        btn_box.addWidget(self.connect_btn)
        self.test_obs_btn = QPushButton("🧪 Kiểm tra")
        self.test_obs_btn.clicked.connect(self._test_obs_connection)
        btn_box.addWidget(self.test_obs_btn)
        left_layout.addLayout(btn_box)

        self.setup_obs_btn = QPushButton("🛠️ Tạo scene & nguồn 'LiveFlow' trong OBS")
        self.setup_obs_btn.setToolTip(
            "Tạo scene riêng + 2 media source (video, audio) để LiveFlow điều khiển. "
            "Không đụng tới các scene/nguồn khác của bạn."
        )
        self.setup_obs_btn.clicked.connect(self._setup_obs_scene)
        left_layout.addWidget(self.setup_obs_btn)

        scene_row = QHBoxLayout()
        scene_row.addWidget(QLabel("Scene đang phát:"))
        self.scene_combo = QComboBox()
        self.scene_combo.setToolTip("Chuyển cảnh OBS thủ công")
        self.scene_combo.activated.connect(self._on_scene_activated)
        scene_row.addWidget(self.scene_combo, 1)
        left_layout.addLayout(scene_row)

        self.obs_info_label = QLabel("")
        self.obs_info_label.setObjectName("mutedLabel")
        self.obs_info_label.setWordWrap(True)
        left_layout.addWidget(self.obs_info_label)

        # ---- RTMP
        rtmp_title = QLabel("📡 2. Nền tảng & RTMP (TikTok / Shopee)")
        rtmp_title.setObjectName("sectionTitle")
        left_layout.addWidget(rtmp_title)

        rtmp_form = QFormLayout()
        rtmp_form.setSpacing(8)
        self.platform_combo = QComboBox()
        self.platform_combo.addItem("TikTok LIVE", "tiktok")
        self.platform_combo.addItem("Shopee Live", "shopee")
        self.platform_combo.currentIndexChanged.connect(self._on_platform_changed)
        rtmp_form.addRow("Nền tảng:", self.platform_combo)
        self.rtmp_server_input = QLineEdit()
        self.rtmp_server_input.setPlaceholderText("rtmp://... (Server URL lấy từ TikTok/Shopee)")
        rtmp_form.addRow("Server URL:", self.rtmp_server_input)
        self.rtmp_key_input = QLineEdit()
        self.rtmp_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.rtmp_key_input.setPlaceholderText("Stream Key (bí mật — lưu trong Credential Manager)")
        rtmp_form.addRow("Stream Key:", self.rtmp_key_input)
        left_layout.addLayout(rtmp_form)

        rtmp_btns = QHBoxLayout()
        self.apply_rtmp_btn = QPushButton("📤 Gửi cấu hình RTMP vào OBS")
        self.apply_rtmp_btn.setToolTip("Lưu Server/Key và đặt vào OBS → Settings → Stream (Custom)")
        self.apply_rtmp_btn.clicked.connect(self._apply_rtmp)
        rtmp_btns.addWidget(self.apply_rtmp_btn)
        self.guide_btn = QPushButton("❓ Lấy Stream Key ở đâu?")
        self.guide_btn.clicked.connect(self._show_platform_guide)
        rtmp_btns.addWidget(self.guide_btn)
        left_layout.addLayout(rtmp_btns)

        stream_btns = QHBoxLayout()
        self.start_stream_btn = QPushButton("🔴 Bắt đầu LIVE (OBS Start Streaming)")
        self.start_stream_btn.setObjectName("dangerButton")
        self.start_stream_btn.clicked.connect(self._start_streaming)
        stream_btns.addWidget(self.start_stream_btn)
        self.stop_stream_btn = QPushButton("⏹️ Kết thúc LIVE")
        self.stop_stream_btn.clicked.connect(self._stop_streaming)
        stream_btns.addWidget(self.stop_stream_btn)
        left_layout.addLayout(stream_btns)

        left_layout.addStretch()
        splitter.addWidget(left_card)

        # ============ RIGHT: Orchestration ============
        right_card = QFrame()
        right_card.setObjectName("cardFrame")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(10)

        monitor_title = QLabel("🤖 3. Điều phối nội dung tự động (AI Auto-Live)")
        monitor_title.setObjectName("sectionTitle")
        right_layout.addWidget(monitor_title)

        pl_row = QHBoxLayout()
        pl_row.addWidget(QLabel("Playlist:"))
        self.playlist_combo = QComboBox()
        self.playlist_combo.currentIndexChanged.connect(self._on_playlist_selected)
        pl_row.addWidget(self.playlist_combo, 1)
        refresh_pl = QPushButton("🔄")
        refresh_pl.setFixedWidth(40)
        refresh_pl.setToolTip("Tải lại playlist")
        refresh_pl.clicked.connect(self.refresh_playlists)
        pl_row.addWidget(refresh_pl)
        right_layout.addLayout(pl_row)

        audio_row = QHBoxLayout()
        audio_row.addWidget(QLabel("Âm thanh TTS phát qua:"))
        self.audio_mode_combo = QComboBox()
        self.audio_mode_combo.addItem("Máy tính này (OBS thu 'Desktop Audio')", "local")
        self.audio_mode_combo.addItem("Media source trong OBS (khuyên dùng khi LIVE)", "obs")
        audio_row.addWidget(self.audio_mode_combo, 1)
        right_layout.addLayout(audio_row)

        ctrl_layout = QHBoxLayout()
        self.start_btn = QPushButton("▶️ Bắt đầu điều phối")
        self.start_btn.setObjectName("successButton")
        self.start_btn.clicked.connect(self._start_orchestration)
        ctrl_layout.addWidget(self.start_btn)
        self.pause_btn = QPushButton("⏸️ Tạm dừng")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._toggle_pause)
        ctrl_layout.addWidget(self.pause_btn)
        self.skip_btn = QPushButton("⏭️ Bỏ qua")
        self.skip_btn.setEnabled(False)
        self.skip_btn.clicked.connect(self._skip_item)
        ctrl_layout.addWidget(self.skip_btn)
        self.stop_btn = QPushButton("⏹️ Dừng")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setObjectName("dangerButton")
        self.stop_btn.clicked.connect(self._stop_orchestration)
        ctrl_layout.addWidget(self.stop_btn)

        self.showcase_btn = QPushButton("💃 Người Mẫu Giới Thiệu")
        self.showcase_btn.setToolTip("Mở phòng Người Mẫu AI giới thiệu sản phẩm ngay lập tức lên OBS Live")
        self.showcase_btn.setStyleSheet(
            f"color: {COLORS['accent_secondary']}; border: 1px solid {COLORS['accent_secondary']}; "
            "padding: 6px 12px; border-radius: 6px; font-weight: bold;"
        )
        self.showcase_btn.clicked.connect(self._open_showcase_modal)
        ctrl_layout.addWidget(self.showcase_btn)
        right_layout.addLayout(ctrl_layout)

        # Now-playing box
        active_box = QFrame()
        active_box.setObjectName("innerBox")
        box_layout = QVBoxLayout(active_box)
        box_layout.setContentsMargins(14, 12, 14, 12)
        box_layout.setSpacing(6)
        self.state_label = QLabel("Trạng thái: Chưa chạy")
        self.state_label.setStyleSheet(f"color: {COLORS['text_accent']}; font-weight: 600;")
        box_layout.addWidget(self.state_label)
        self.current_prod_label = QLabel("Sản phẩm hiện tại: —")
        self.current_prod_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        self.current_prod_label.setWordWrap(True)
        box_layout.addWidget(self.current_prod_label)
        self.current_video_label = QLabel("Video: —")
        self.current_video_label.setObjectName("hintLabel")
        box_layout.addWidget(self.current_video_label)
        self.current_script_label = QLabel("Kịch bản: —")
        self.current_script_label.setObjectName("hintLabel")
        self.current_script_label.setWordWrap(True)
        box_layout.addWidget(self.current_script_label)
        self.timer_label = QLabel("00:00 / 00:00")
        self.timer_label.setStyleSheet(f"color: {COLORS['accent']}; font-size: 14px; font-weight: bold;")
        box_layout.addWidget(self.timer_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        box_layout.addWidget(self.progress_bar)
        right_layout.addWidget(active_box)

        queue_row = QHBoxLayout()
        queue_col = QVBoxLayout()
        queue_title = QLabel("📋 Hàng đợi sản phẩm")
        queue_title.setStyleSheet("font-weight: 600;")
        queue_col.addWidget(queue_title)
        self.queue_list = QListWidget()
        queue_col.addWidget(self.queue_list, 1)
        queue_row.addLayout(queue_col, 1)

        log_col = QVBoxLayout()
        log_title = QLabel("🧾 Nhật ký LIVE")
        log_title.setStyleSheet("font-weight: 600;")
        log_col.addWidget(log_title)
        self.log_list = QListWidget()
        log_col.addWidget(self.log_list, 1)
        queue_row.addLayout(log_col, 1)
        right_layout.addLayout(queue_row, 1)

        splitter.addWidget(right_card)
        splitter.setSizes([420, 760])
        main_layout.addWidget(splitter, 1)

    # ------------------------------------------------------------ settings
    def reload_settings(self) -> None:
        """Pull OBS/RTMP settings from the database (called after Settings is saved)."""
        self._session.expire_all()
        host, port, password = self.settings_service.get_obs_connection()
        self.host_input.setText(host)
        self.port_input.setText(str(port))
        self.pass_input.setText(password)
        platform = self.settings_service.get_platform()
        idx = self.platform_combo.findData(platform)
        self.platform_combo.blockSignals(True)
        self.platform_combo.setCurrentIndex(max(idx, 0))
        self.platform_combo.blockSignals(False)
        self._load_rtmp_fields()

    def _load_rtmp_fields(self) -> None:
        platform = self.platform_combo.currentData()
        if platform == "shopee":
            self.rtmp_server_input.setText(self.settings_service.get_setting(KEY_STREAM_SERVER_SHOPEE, ""))
            self.rtmp_key_input.setText(self.settings_service.get_setting(KEY_STREAM_KEY_SHOPEE, ""))
        else:
            self.rtmp_server_input.setText(self.settings_service.get_setting(KEY_STREAM_SERVER_TIKTOK, ""))
            self.rtmp_key_input.setText(self.settings_service.get_setting(KEY_STREAM_KEY_TIKTOK, ""))

    def _on_platform_changed(self) -> None:
        self.settings_service.set_setting("default_platform", self.platform_combo.currentData())
        self._load_rtmp_fields()

    # ----------------------------------------------------------------- OBS
    def _set_badge(self, mode: str) -> None:
        styles = {
            "offline": (COLORS["bg_lighter"], COLORS["text_muted"], "OBS: CHƯA KẾT NỐI"),
            "connected": (COLORS["accent_info"], "white", "OBS: ĐÃ KẾT NỐI"),
            "streaming": (COLORS["accent_danger"], "white", "● ĐANG LIVE"),
        }
        bg, fg, text = styles[mode]
        self.stream_badge.setText(text)
        self.stream_badge.setStyleSheet(
            f"background-color: {bg}; color: {fg}; padding: 6px 16px; border-radius: 12px; font-weight: bold; font-size: 13px;"
        )

    def _read_connection_fields(self) -> Optional[tuple[str, int, str]]:
        host = self.host_input.text().strip() or "localhost"
        try:
            port = int(self.port_input.text().strip() or "4455")
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Port không hợp lệ", "Cổng kết nối (Port) phải là số nguyên từ 1 đến 65535.")
            return None
        return host, port, self.pass_input.text().strip()

    def _toggle_obs_connection(self) -> None:
        if self.obs_client.is_connected:
            self.obs_client.disconnect()
            self._after_obs_disconnected("ℹ️ Đã ngắt kết nối OBS Studio.")
            return

        fields = self._read_connection_fields()
        if not fields:
            return
        host, port, password = fields
        self.obs_client.configure(host, port, password)
        self.connect_btn.setEnabled(False)
        self.connect_btn.setText("⏳ Đang kết nối...")
        try:
            ok = self.obs_client.connect()
        finally:
            self.connect_btn.setEnabled(True)

        if ok:
            # Persist what worked so other pages/the orchestrator use the same values.
            self.settings_service.set_setting("obs_host", host)
            self.settings_service.set_setting("obs_port", str(port))
            self.settings_service.set_setting("obs_password", password, is_secret=True)
            self.connect_btn.setText("🔌 Ngắt kết nối OBS")
            self._add_log("✅ Đã kết nối OBS Studio qua WebSocket.")
            log_activity("obs", "Kết nối OBS thành công", f"{host}:{port}")
            self.obs_status_changed.emit(True)
            self._poll_obs_status()
        else:
            self.connect_btn.setText("🔌 Kết nối OBS")
            self._add_log("❌ Không kết nối được OBS Studio.")
            log_activity("obs", "Kết nối OBS thất bại", self.obs_client.last_error[:200], level="WARNING")
            QMessageBox.critical(self, "Lỗi kết nối OBS", self.obs_client.last_error or "Không thể kết nối tới OBS.")
            self.obs_status_changed.emit(False)

    def _after_obs_disconnected(self, message: str) -> None:
        self.connect_btn.setText("🔌 Kết nối OBS")
        self.scene_combo.clear()
        self.obs_info_label.setText("")
        self._set_badge("offline")
        self._add_log(message)
        self.obs_status_changed.emit(False)

    def _test_obs_connection(self) -> None:
        fields = self._read_connection_fields()
        if not fields:
            return
        host, port, password = fields
        self._add_log(f"⏳ Đang kiểm tra kết nối tới OBS ({host}:{port})...")
        self.test_obs_btn.setEnabled(False)
        self._test_worker = TestOBSWorker(host=host, port=port, password=password)
        self._test_worker.finished.connect(self._on_test_obs_finished)
        self._test_worker.start()

    def _on_test_obs_finished(self, success: bool, message: str) -> None:
        self.test_obs_btn.setEnabled(True)
        if success:
            self._add_log("✅ " + message.splitlines()[0])
            QMessageBox.information(self, "Kết nối OBS thành công", message)
        else:
            self._add_log("❌ Kiểm tra kết nối OBS thất bại.")
            QMessageBox.critical(self, "Lỗi kết nối OBS", message)

    def _poll_obs_status(self) -> None:
        if not self.obs_client.is_connected:
            if self.connect_btn.text().startswith("🔌 Ngắt"):
                self._after_obs_disconnected("⚠️ Mất kết nối OBS Studio.")
            return
        status = self.obs_client.get_status()
        if not status.connected:
            self._after_obs_disconnected(f"⚠️ Mất kết nối OBS: {status.error}")
            return
        self._set_badge("streaming" if status.streaming else "connected")
        info = f"OBS {status.obs_version} · WebSocket v{status.ws_version}"
        if status.streaming:
            info += f" · Đang LIVE {status.stream_timecode[:8]}"
        self.obs_info_label.setText(info)
        current = self.scene_combo.currentText()
        if [self.scene_combo.itemText(i) for i in range(self.scene_combo.count())] != status.scenes:
            self.scene_combo.blockSignals(True)
            self.scene_combo.clear()
            self.scene_combo.addItems(status.scenes)
            self.scene_combo.blockSignals(False)
        target = status.current_scene or current
        idx = self.scene_combo.findText(target)
        if idx >= 0 and self.scene_combo.currentIndex() != idx:
            self.scene_combo.blockSignals(True)
            self.scene_combo.setCurrentIndex(idx)
            self.scene_combo.blockSignals(False)

    def _on_scene_activated(self, index: int) -> None:
        scene = self.scene_combo.itemText(index)
        if not scene:
            return
        if self.obs_client.set_current_scene(scene):
            self._add_log(f"🎬 Đã chuyển cảnh OBS sang: {scene}")
        else:
            self._add_log(f"❌ Không chuyển được cảnh '{scene}'.")

    def _setup_obs_scene(self) -> None:
        if not self.obs_client.is_connected:
            QMessageBox.warning(self, "Chưa kết nối", "Hãy kết nối OBS trước.")
            return
        scene, video, audio = self.settings_service.get_obs_sources()
        image, text = self.settings_service.get_obs_extra_sources()
        avatar = self.settings_service.get(KEY_OBS_AVATAR_SOURCE) or "LiveFlow Avatar"
        reply = QMessageBox.question(
            self,
            "Tạo cấu hình LiveFlow trong OBS",
            f"LiveFlow sẽ tạo (nếu chưa có) trong OBS:\n\n"
            f"• Scene: {scene}\n"
            f"• Image source (ảnh sản phẩm): {image}\n"
            f"• Media source video: {video}\n"
            f"• Media source Người Mẫu AI: {avatar}\n"
            f"• Media source audio: {audio}\n"
            f"• Text overlay (tên + giá): {text}\n\n"
            "Gợi ý: đặt canvas OBS 1080×1920 (dọc) cho TikTok/Shopee và thêm ảnh nền/banner của bạn phía dưới các nguồn này.\n"
            "Các scene/nguồn khác của bạn KHÔNG bị thay đổi. Tiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok, msg = self.obs_client.ensure_liveflow_setup(
            scene_name=scene,
            video_source=video,
            audio_source=audio,
            image_source=image,
            text_source=text,
            avatar_source=avatar,
        )
        self._add_log(("✅ " if ok else "❌ ") + msg)
        (QMessageBox.information if ok else QMessageBox.critical)(self, "Cấu hình OBS", msg)
        self._poll_obs_status()

    def _open_showcase_modal(self) -> None:
        """Open the AI Presenter Showcase Modal for currently playing or selected product."""
        from app.services.product_service import ProductService
        from PySide6.QtWidgets import QInputDialog

        prod_svc = ProductService()
        products, _ = prod_svc.get_products(limit=100)
        if not products:
            QMessageBox.information(
                self, "Thông báo", "Chưa có sản phẩm nào trong hệ thống. Vui lòng thêm sản phẩm trước."
            )
            return

        # If an item is currently playing, find its product ID
        target_pid = None
        if self._orchestrator and self._orchestrator.isRunning() and self._orchestrator.current_index >= 0:
            if self._orchestrator.current_index < len(self._entries):
                entry = self._entries[self._orchestrator.current_index]
                target_pid = entry.product_id

        if not target_pid:
            # Let user choose product from list
            items = [f"{p.id} — {p.name}" for p in products]
            selected, ok = QInputDialog.getItem(
                self,
                "Chọn sản phẩm để Người Mẫu AI giới thiệu",
                "Chọn sản phẩm bạn muốn Người Mẫu AI thuyết trình ngay:",
                items,
                0,
                False,
            )
            if not ok or not selected:
                return
            target_pid = int(selected.split(" — ")[0])

        dlg = ProductShowcaseDialog(target_pid, self, obs_client=self.obs_client)
        dlg.exec()

    # ---------------------------------------------------------------- RTMP
    def _apply_rtmp(self) -> None:
        server = self.rtmp_server_input.text().strip()
        key = self.rtmp_key_input.text().strip()
        platform = self.platform_combo.currentData()
        if not server or not key:
            QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập cả Server URL và Stream Key.")
            return
        if not server.lower().startswith(("rtmp://", "rtmps://")):
            QMessageBox.warning(self, "Server URL không hợp lệ", "Server URL phải bắt đầu bằng rtmp:// hoặc rtmps://")
            return
        if platform == "shopee":
            self.settings_service.set_setting(KEY_STREAM_SERVER_SHOPEE, server)
            self.settings_service.set_setting(KEY_STREAM_KEY_SHOPEE, key, is_secret=True)
        else:
            self.settings_service.set_setting(KEY_STREAM_SERVER_TIKTOK, server)
            self.settings_service.set_setting(KEY_STREAM_KEY_TIKTOK, key, is_secret=True)

        if not self.obs_client.is_connected:
            QMessageBox.information(
                self, "Đã lưu",
                "Đã lưu Server/Stream Key an toàn. Kết nối OBS rồi bấm lại nút này để gửi cấu hình vào OBS, "
                "hoặc tự dán vào OBS → Settings → Stream → Service: Custom.",
            )
            return
        if self.obs_client.set_stream_service(server, key):
            self._add_log(f"📤 Đã đặt RTMP {self.platform_combo.currentText()} vào OBS.")
            log_activity("obs", "Cập nhật RTMP cho OBS", self.platform_combo.currentText())
            QMessageBox.information(self, "Thành công", "OBS đã nhận Server URL + Stream Key. Bạn có thể bấm 'Bắt đầu LIVE'.")
        else:
            QMessageBox.critical(self, "Lỗi", "OBS không nhận cấu hình stream. Kiểm tra OBS không đang stream và thử lại.")

    def _show_platform_guide(self) -> None:
        platform = Platform(self.platform_combo.currentData())
        info = get_capability(platform, Capability.STREAM_VIDEO)
        steps = "\n".join(f"{i}. {s}" for i, s in enumerate(info.manual_steps, 1))
        QMessageBox.information(
            self,
            f"Lấy Stream Key — {platform.display_name}",
            f"{info.method}\n\nCác bước:\n{steps}\n\n"
            "Lưu ý: điều kiện mở LIVE qua OBS do nền tảng quy định và có thể thay đổi; "
            "LiveFlow không vượt qua các giới hạn này.",
        )

    def _start_streaming(self) -> None:
        if not self.obs_client.is_connected:
            QMessageBox.warning(self, "Chưa kết nối OBS", "Hãy kết nối OBS trước khi bắt đầu LIVE.")
            return
        platform = self.platform_combo.currentText()
        reply = QMessageBox.question(
            self, "Bắt đầu LIVE",
            f"OBS sẽ bắt đầu phát trực tiếp lên {platform} với RTMP đã cấu hình.\n"
            "Hãy chắc chắn bạn đã mở phòng LIVE trên ứng dụng chính thức. Tiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok, msg = self.obs_client.start_stream()
        self._add_log(("🔴 " if ok else "❌ ") + msg)
        log_activity("obs", "Bắt đầu stream" if ok else "Bắt đầu stream thất bại", msg, level="INFO" if ok else "ERROR")
        (QMessageBox.information if ok else QMessageBox.critical)(self, "Bắt đầu LIVE", msg)
        self._poll_obs_status()

    def _stop_streaming(self) -> None:
        if not self.obs_client.is_connected:
            return
        reply = QMessageBox.question(
            self, "Kết thúc LIVE", "Dừng phát trực tiếp trong OBS?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ok, msg = self.obs_client.stop_stream()
        self._add_log(("⏹️ " if ok else "❌ ") + msg)
        log_activity("obs", "Dừng stream", msg)
        self._poll_obs_status()

    # ----------------------------------------------------------- playlists
    def refresh_playlists(self) -> None:
        current = self.playlist_combo.currentData()
        self.playlist_combo.blockSignals(True)
        self.playlist_combo.clear()
        for p in self.playlist_repo.list_playlists():
            enabled = sum(1 for i in p.items if i.is_enabled)
            self.playlist_combo.addItem(f"{p.name}  ({enabled} mục bật)", p.id)
        idx = self.playlist_combo.findData(current)
        self.playlist_combo.setCurrentIndex(max(idx, 0))
        self.playlist_combo.blockSignals(False)
        self._on_playlist_selected()

    def _on_playlist_selected(self) -> None:
        if self._orchestrator and self._orchestrator.isRunning():
            return
        pid = self.playlist_combo.currentData()
        self.queue_list.clear()
        self._entries = []
        if pid is None:
            return
        playlist = self.playlist_repo.get_by_id(pid, fresh=True)
        if not playlist:
            return
        self._entries = build_entries_from_playlist(playlist)
        self._render_queue(-1)

    def _render_queue(self, current_index: int) -> None:
        self.queue_list.clear()
        enabled = [e for e in self._entries if e.enabled]
        for i, e in enumerate(enabled):
            flags = []
            if e.video_path and os.path.exists(e.video_path):
                flags.append("🎬")
            elif e.image_path and os.path.exists(e.image_path):
                flags.append("🖼️")
            else:
                flags.append("▫️")
            flags.append("🔊" if e.audio_path and os.path.exists(e.audio_path) else "▫️")
            prefix = "▶️ " if i == current_index else f"{i + 1}. "
            dur = e.duration_seconds or e.audio_duration or 0
            dur_text = f" · {dur:.0f}s" if dur else ""
            self.queue_list.addItem(f"{prefix}{e.product_name}  {' '.join(flags)}{dur_text}")
        if current_index >= 0 and current_index < self.queue_list.count():
            self.queue_list.setCurrentRow(current_index)

    # -------------------------------------------------------- orchestrator
    def _start_orchestration(self) -> None:
        if self._orchestrator and self._orchestrator.isRunning():
            return
        pid = self.playlist_combo.currentData()
        if pid is None:
            QMessageBox.warning(self, "Chưa có playlist", "Hãy tạo playlist ở trang 'Lịch trình' trước.")
            return
        playlist = self.playlist_repo.get_by_id(pid, fresh=True)
        if not playlist:
            return
        entries = build_entries_from_playlist(playlist)
        enabled = [e for e in entries if e.enabled]
        if not enabled:
            QMessageBox.warning(self, "Playlist trống", "Playlist chưa có mục nào được bật.")
            return

        missing_audio = [e.product_name for e in enabled if not e.audio_path]
        missing_video = [e.product_name for e in enabled if not e.video_path and not e.image_path]
        warnings: List[str] = []
        if missing_audio:
            warnings.append(f"• {len(missing_audio)} sản phẩm chưa có audio TTS: {', '.join(missing_audio[:3])}{'...' if len(missing_audio) > 3 else ''}")
        if missing_video:
            warnings.append(f"• {len(missing_video)} sản phẩm chưa có video/ảnh: {', '.join(missing_video[:3])}{'...' if len(missing_video) > 3 else ''}")
        if not self.obs_client.is_connected:
            warnings.append("• OBS chưa kết nối — video sẽ không được đổi trong OBS, chỉ phát âm thanh/đếm giờ.")
        if warnings:
            reply = QMessageBox.question(
                self, "Kiểm tra trước khi chạy",
                "Một số mục chưa sẵn sàng:\n\n" + "\n".join(warnings) + "\n\nVẫn bắt đầu điều phối?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        scene, video_src, audio_src = self.settings_service.get_obs_sources()
        image_src, text_src = self.settings_service.get_obs_extra_sources()
        audio_mode = self.audio_mode_combo.currentData()
        if audio_mode == "obs" and not self.obs_client.is_connected:
            audio_mode = "local"
        from app.config.settings import get_settings

        app_settings = get_settings()
        config = OrchestratorConfig(
            playlist_id=playlist.id,
            playlist_name=playlist.name,
            entries=entries,
            loop=bool(playlist.is_loop),
            loop_count=max(1, playlist.loop_count or 1),
            gap_seconds=float(playlist.gap_seconds or 0),
            default_item_seconds=float(app_settings.scheduler_default_item_seconds),
            platform=self.platform_combo.currentData(),
            use_obs=self.obs_client.is_connected,
            obs_scene=scene,
            obs_video_source=video_src,
            obs_audio_source=audio_src,
            obs_image_source=image_src,
            obs_text_source=text_src,
            show_overlay_text=self.settings_service.get_bool("obs_show_text", True),
            auto_switch_scene=self.settings_service.get_bool(KEY_OBS_AUTO_SWITCH_SCENE, True),
            audio_mode=audio_mode,
            max_consecutive_errors=app_settings.scheduler_max_consecutive_errors,
            session_name=f"LIVE {self.platform_combo.currentText()} — {playlist.name}",
        )
        self._entries = entries
        self._orchestrator = LiveOrchestrator(config, obs_client=self.obs_client, parent=self)
        o = self._orchestrator
        o.state_changed.connect(self._on_state_changed)
        o.item_started.connect(self._on_item_started)
        o.item_progress.connect(self._on_item_progress)
        o.item_finished.connect(self._on_item_finished)
        o.play_audio_requested.connect(self._on_play_audio)
        o.stop_audio_requested.connect(self._audio.stop)
        o.log_message.connect(lambda level, msg: self._add_log(msg))
        o.finished_run.connect(self._on_run_finished)
        o.start()

        self.start_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.skip_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.playlist_combo.setEnabled(False)
        self.audio_mode_combo.setEnabled(False)

    def _toggle_pause(self) -> None:
        if not self._orchestrator:
            return
        if self._orchestrator.state == RunState.RUNNING:
            self._orchestrator.pause()
            self.pause_btn.setText("▶️ Tiếp tục")
        elif self._orchestrator.state == RunState.PAUSED:
            self._orchestrator.resume()
            self.pause_btn.setText("⏸️ Tạm dừng")

    def _skip_item(self) -> None:
        if self._orchestrator:
            self._orchestrator.skip_current()

    def _stop_orchestration(self) -> None:
        if self._orchestrator:
            self._orchestrator.stop()
            self.stop_btn.setEnabled(False)

    def interject_reply_audio(self, path: str, duration: Optional[float] = None) -> bool:
        """Called by the Comment page: speak an approved reply during the live."""
        if self._orchestrator and self._orchestrator.isRunning():
            self._orchestrator.interject_audio(path, duration)
            return True
        # Not orchestrating → just play it now.
        self._audio.play(path)
        self._add_log("🎙️ Phát câu trả lời bình luận bằng giọng nói.")
        return True

    def shutdown(self) -> None:
        """Stop background work before the app exits."""
        self._status_timer.stop()
        if self._orchestrator and self._orchestrator.isRunning():
            self._orchestrator.stop()
            self._orchestrator.wait(5000)
        self._audio.stop()
        # Never kill OBS — only close our socket.
        self.obs_client.disconnect(quiet=True)

    # ---------------------------------------------------- orchestrator slots
    def _on_state_changed(self, state_value: str) -> None:
        state = RunState(state_value)
        self.state_label.setText(f"Trạng thái: {state.label}")
        running = state in (RunState.RUNNING, RunState.PAUSED)
        self.live_state_changed.emit(state.label, running)

    def _on_item_started(self, index: int, entry) -> None:
        self.current_prod_label.setText(f"Sản phẩm hiện tại: {entry.product_name}")
        visual = entry.video_path or entry.image_path
        self.current_video_label.setText(
            ("Video: " if entry.video_path else "Ảnh: ") + (os.path.basename(visual) if visual else "— (chưa gắn)")
            + (f"   ·   {entry.price_text}" if entry.price_text else "")
        )
        excerpt = entry.script_excerpt or "— (chưa có kịch bản)"
        self.current_script_label.setText(f"Kịch bản: {entry.script_title or ''} {excerpt}".strip())
        self.progress_bar.setValue(0)
        self._render_queue(index)

    def _on_item_progress(self, index: int, elapsed: float, total: float) -> None:
        total = max(total, 0.1)
        self.progress_bar.setValue(int(min(elapsed / total, 1.0) * 1000))
        self.timer_label.setText(f"{self._fmt(elapsed)} / {self._fmt(total)}")

    def _on_item_finished(self, index: int, status: str) -> None:
        if status == "failed":
            self._add_log("❌ Mục thất bại.")

    def _on_play_audio(self, path: str) -> None:
        self._audio.play(path)

    def _on_local_audio_finished(self) -> None:
        if self._orchestrator:
            self._orchestrator.notify_local_audio_finished()

    def _on_run_finished(self, state_value: str) -> None:
        self.start_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText("⏸️ Tạm dừng")
        self.skip_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.playlist_combo.setEnabled(True)
        self.audio_mode_combo.setEnabled(True)
        self.progress_bar.setValue(0)
        self.current_prod_label.setText("Sản phẩm hiện tại: —")
        self._render_queue(-1)
        self._audio.stop()
        state = RunState(state_value)
        self.live_state_changed.emit(state.label, False)
        if state == RunState.ERROR:
            QMessageBox.warning(self, "Điều phối dừng do lỗi", "Điều phối đã dừng vì gặp nhiều lỗi liên tiếp. Xem Nhật ký LIVE để biết chi tiết.")

    # -------------------------------------------------------------- utils
    @staticmethod
    def _fmt(seconds: float) -> str:
        seconds = int(max(seconds, 0))
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def _add_log(self, text: str) -> None:
        from datetime import datetime

        self.log_list.addItem(f"[{datetime.now().strftime('%H:%M:%S')}] {text}")
        if self.log_list.count() > 500:
            self.log_list.takeItem(0)
        self.log_list.scrollToBottom()
