"""Live Studio UI Page for controlling OBS and active livestream sessions."""

from __future__ import annotations

from typing import List, Optional
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from loguru import logger
from app.database.session import get_session
from app.integrations.obs.client import OBSClient
from app.services.settings_service import SettingsService
from app.ui.pages.settings_page import TestOBSWorker
from app.ui.styles.dark_theme import COLORS


class LiveStudioPage(QWidget):
    """Live Studio UI Page."""

    obs_status_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.obs_client = OBSClient()
        self.is_streaming: bool = False
        self.stream_seconds: int = 0
        self._test_worker: Optional[TestOBSWorker] = None

        # Load persisted settings if available
        try:
            session = get_session()
            settings_service = SettingsService(session)
            self._default_host = settings_service.get_setting("obs_host", "localhost")
            self._default_port = settings_service.get_setting("obs_port", "4455")
            self._default_pass = settings_service.get_setting("obs_password", "")
        except Exception:
            self._default_host = "localhost"
            self._default_port = "4455"
            self._default_pass = ""

        self._init_ui()

        # Stream Timer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_timer)

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        # Header
        header_layout = QHBoxLayout()
        title_label = QLabel("🔴 Live Studio")
        title_label.setObjectName("headerLabel")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        self.stream_badge = QLabel("OFFLINE")
        self.stream_badge.setStyleSheet(
            f"background-color: {COLORS['bg_card']}; color: {COLORS['text_muted']}; "
            "padding: 6px 16px; border-radius: 12px; font-weight: bold; font-size: 13px;"
        )
        header_layout.addWidget(self.stream_badge)

        main_layout.addLayout(header_layout)

        # Content Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left Panel — OBS Control & Scene Switcher
        left_card = QFrame()
        left_card.setObjectName("cardFrame")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(16, 16, 16, 16)

        obs_title = QLabel("🎥 Kết Nối OBS Studio")
        obs_title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {COLORS['accent']};")
        left_layout.addWidget(obs_title)

        form = QFormLayout()
        form.setSpacing(10)

        self.host_input = QLineEdit(self._default_host)
        form.addRow("Host:", self.host_input)

        self.port_input = QLineEdit(str(self._default_port))
        form.addRow("Port:", self.port_input)

        self.pass_input = QLineEdit(self._default_pass)
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_input.setPlaceholderText("Password (nếu có)")
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

        left_layout.addSpacing(16)

        scene_title = QLabel("🎬 Đổi Cảnh (Scene Switcher)")
        scene_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        left_layout.addWidget(scene_title)

        self.scene_combo = QComboBox()
        self.scene_combo.currentIndexChanged.connect(self._on_scene_changed)
        left_layout.addWidget(self.scene_combo)

        left_layout.addStretch()
        splitter.addWidget(left_card)

        # Right Panel — Live Automation Monitor & Controls
        right_card = QFrame()
        right_card.setObjectName("cardFrame")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(16, 16, 16, 16)

        monitor_title = QLabel("📡 Điều Phối Livestream Tự Động")
        monitor_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        right_layout.addWidget(monitor_title)

        # Stream Control Buttons
        ctrl_layout = QHBoxLayout()

        self.start_stream_btn = QPushButton("▶️ Bắt Đầu Stream")
        self.start_stream_btn.setObjectName("accentButton")
        self.start_stream_btn.setStyleSheet("font-size: 15px; padding: 12px 24px;")
        self.start_stream_btn.clicked.connect(self._start_stream)
        ctrl_layout.addWidget(self.start_stream_btn)

        self.stop_stream_btn = QPushButton("⏹️ Dừng Stream")
        self.stop_stream_btn.setEnabled(False)
        self.stop_stream_btn.setStyleSheet("font-size: 15px; padding: 12px 24px;")
        self.stop_stream_btn.clicked.connect(self._stop_stream)
        ctrl_layout.addWidget(self.stop_stream_btn)

        right_layout.addLayout(ctrl_layout)

        right_layout.addSpacing(12)

        # Active Product Display Card
        active_box = QFrame()
        active_box.setStyleSheet(f"background-color: {COLORS['bg_darkest']}; border-radius: 8px; padding: 12px;")
        box_layout = QVBoxLayout(active_box)

        self.current_prod_label = QLabel("Sản phẩm đang giới thiệu: Chưa có")
        self.current_prod_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        box_layout.addWidget(self.current_prod_label)

        self.timer_label = QLabel("Thời gian phát: 00:00:00")
        self.timer_label.setStyleSheet(f"color: {COLORS['accent']}; font-size: 14px; font-weight: bold;")
        box_layout.addWidget(self.timer_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        box_layout.addWidget(self.progress_bar)

        right_layout.addWidget(active_box)

        right_layout.addSpacing(12)

        log_title = QLabel("📋 Nhật Ký Hoạt Động Live")
        log_title.setStyleSheet(f"color: {COLORS['text_muted']}; font-weight: bold;")
        right_layout.addWidget(log_title)

        self.log_list = QListWidget()
        right_layout.addWidget(self.log_list)

        splitter.addWidget(right_card)
        splitter.setSizes([350, 650])

        main_layout.addWidget(splitter)
        self._load_scenes()

    def _toggle_obs_connection(self) -> None:
        if not self.obs_client.is_connected:
            self.obs_client.host = self.host_input.text().strip()
            self.obs_client.port = int(self.port_input.text().strip() or "4455")
            self.obs_client.password = self.pass_input.text().strip()

            success = self.obs_client.connect()
            if success:
                self.connect_btn.setText("🔌 Ngắt kết nối OBS")
                self.connect_btn.setStyleSheet(f"background-color: {COLORS['status_offline']};")
                self._load_scenes()
                self._add_log("✅ Đã kết nối thành công tới OBS Studio WebSocket.")
                self.obs_status_changed.emit(True)
            else:
                self._add_log("⚠️ Không thể kết nối OBS Studio. Sử dụng chế độ Simulation mode.")
                self._load_scenes()
                self.obs_status_changed.emit(False)
        else:
            self.obs_client.disconnect()
            self.connect_btn.setText("🔌 Kết nối OBS")
            self.connect_btn.setStyleSheet("")
            self._add_log("ℹ️ Đã ngắt kết nối OBS Studio.")
            self.obs_status_changed.emit(False)

    def _test_obs_connection(self) -> None:
        host = self.host_input.text().strip() or "localhost"
        try:
            port = int(self.port_input.text().strip() or "4455")
            if not (1 <= port <= 65535):
                raise ValueError()
        except ValueError:
            QMessageBox.warning(self, "Port Không Hợp Lệ", "Cổng kết nối (Port) phải là một số nguyên từ 1 đến 65535.")
            return
        password = self.pass_input.text().strip()

        self._add_log(f"⏳ Đang kiểm tra kết nối tới OBS Studio ({host}:{port})...")
        self.test_obs_btn.setEnabled(False)

        self._test_worker = TestOBSWorker(host=host, port=port, password=password)
        self._test_worker.finished.connect(self._on_test_obs_finished)
        self._test_worker.start()

    def _on_test_obs_finished(self, success: bool, message: str) -> None:
        self.test_obs_btn.setEnabled(True)
        if success:
            first_line = message.splitlines()[0]
            self._add_log(f"✅ {first_line}")
            QMessageBox.information(self, "Kết Nối OBS Thành Công", message)
        else:
            self._add_log("❌ Kiểm tra kết nối OBS thất bại.")
            QMessageBox.critical(self, "Lỗi Kết Nối OBS", message)

    def _load_scenes(self) -> None:
        self.scene_combo.clear()
        scenes = self.obs_client.get_scenes()
        for s in scenes:
            self.scene_combo.addItem(s)

    def _on_scene_changed(self, index: int) -> None:
        scene = self.scene_combo.currentText()
        if scene:
            self.obs_client.set_current_scene(scene)
            self._add_log(f"🎬 Đã chuyển cảnh OBS sang: {scene}")

    def _start_stream(self) -> None:
        self.is_streaming = True
        self.stream_seconds = 0
        self.start_stream_btn.setEnabled(False)
        self.stop_stream_btn.setEnabled(True)

        self.stream_badge.setText("● LIVE STREAMING")
        self.stream_badge.setStyleSheet(
            f"background-color: {COLORS['status_online']}; color: white; "
            "padding: 6px 16px; border-radius: 12px; font-weight: bold; font-size: 13px;"
        )
        self.timer.start(1000)
        self._add_log("🔴 BẮT ĐẦU Phiên Livestream Tự Động!")

    def _stop_stream(self) -> None:
        self.is_streaming = False
        self.timer.stop()
        self.start_stream_btn.setEnabled(True)
        self.stop_stream_btn.setEnabled(False)

        self.stream_badge.setText("OFFLINE")
        self.stream_badge.setStyleSheet(
            f"background-color: {COLORS['bg_card']}; color: {COLORS['text_muted']}; "
            "padding: 6px 16px; border-radius: 12px; font-weight: bold; font-size: 13px;"
        )
        self._add_log("⏹️ ĐÃ DỪNG Phiên Livestream.")

    def _update_timer(self) -> None:
        self.stream_seconds += 1
        hours = self.stream_seconds // 3600
        mins = (self.stream_seconds % 3600) // 60
        secs = self.stream_seconds % 60
        self.timer_label.setText(f"Thời gian phát: {hours:02d}:{mins:02d}:{secs:02d}")

    def _add_log(self, text: str) -> None:
        self.log_list.addItem(text)
        self.log_list.scrollToBottom()
