"""Settings UI Page for application configuration and integration parameters."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.database.session import get_session
from app.services.settings_service import SettingsService
from app.ui.styles.dark_theme import COLORS


class SettingsPage(QWidget):
    """Settings UI Page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_services()
        self._init_ui()
        self._load_settings()

    def _init_services(self) -> None:
        session = get_session()
        self.settings_service = SettingsService(session)

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        # Header
        header_layout = QHBoxLayout()
        title_label = QLabel("⚙️ Cài Đặt Hệ Thống & Tích Hợp")
        title_label.setObjectName("headerLabel")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        self.save_btn = QPushButton("💾 Lưu Cấu Hình")
        self.save_btn.setObjectName("primaryButton")
        self.save_btn.clicked.connect(self._save_settings)
        header_layout.addWidget(self.save_btn)

        main_layout.addLayout(header_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setSpacing(20)

        # AI Provider Settings Card
        ai_card = QFrame()
        ai_card.setObjectName("cardFrame")
        ai_layout = QVBoxLayout(ai_card)
        ai_layout.setContentsMargins(16, 16, 16, 16)

        ai_title = QLabel("🤖 Cấu Hình AI Provider (LLM)")
        ai_title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {COLORS['accent']};")
        ai_layout.addWidget(ai_title)

        ai_form = QFormLayout()
        ai_form.setSpacing(12)

        self.openai_key_input = QLineEdit()
        self.openai_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_key_input.setPlaceholderText("sk-...")
        ai_form.addRow("OpenAI API Key:", self.openai_key_input)

        self.openai_base_input = QLineEdit()
        self.openai_base_input.setPlaceholderText("Để trống nếu dùng OpenAI mặc định, hoặc http://localhost:11434/v1")
        ai_form.addRow("Base URL (Custom/Ollama):", self.openai_base_input)

        self.model_name_input = QLineEdit("gpt-4o-mini")
        ai_form.addRow("Tên Model:", self.model_name_input)

        ai_layout.addLayout(ai_form)
        content_layout.addWidget(ai_card)

        # OBS Settings Card
        obs_card = QFrame()
        obs_card.setObjectName("cardFrame")
        obs_layout = QVBoxLayout(obs_card)
        obs_layout.setContentsMargins(16, 16, 16, 16)

        obs_title = QLabel("🎥 Cấu Hình OBS Studio WebSocket")
        obs_title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {COLORS['accent']};")
        obs_layout.addWidget(obs_title)

        obs_form = QFormLayout()
        obs_form.setSpacing(12)

        self.obs_host_input = QLineEdit("localhost")
        obs_form.addRow("Host:", self.obs_host_input)

        self.obs_port_input = QLineEdit("4455")
        obs_form.addRow("Port:", self.obs_port_input)

        self.obs_pass_input = QLineEdit()
        self.obs_pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        obs_form.addRow("Password:", self.obs_pass_input)

        obs_layout.addLayout(obs_form)
        content_layout.addWidget(obs_card)

        # TikTok Capability Matrix Card
        cap_card = QFrame()
        cap_card.setObjectName("cardFrame")
        cap_layout = QVBoxLayout(cap_card)
        cap_layout.setContentsMargins(16, 16, 16, 16)

        cap_title = QLabel("📊 Bảng Khả Năng Tích Hợp TikTok Live API")
        cap_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        cap_layout.addWidget(cap_title)

        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Tính Năng", "Trạng Thái API Phản Hồi", "Phương Án Thay Thế (Fallback)"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        matrix = [
            ("Tạo & Quản Lý Kịch Bản AI", "✅ Hoạt động 100%", "Offline Studio mode"),
            ("Chuyển Văn Bản Cung Cấp Thành Audio (TTS)", "✅ Edge TTS / OpenAI", "Local SAPI5 TTS"),
            ("Điều Khiển Livestream OBS", "✅ OBS WebSocket 5.x", "Thủ công switch scenes"),
            ("TikTok Live Chat Real-time Stream", "⚠️ API Giới Hạn / Webhook", "Giả lập & Thủ công nhấp reply"),
            ("Tự Động Đẩy Video Lên TikTok Studio", "⚠️ Cần TikTok App Approved", "Manual upload qua OBS Virtual Cam"),
        ]

        table.setRowCount(len(matrix))
        for row, (feat, status, fallback) in enumerate(matrix):
            table.setItem(row, 0, QTableWidgetItem(feat))
            table.setItem(row, 1, QTableWidgetItem(status))
            table.setItem(row, 2, QTableWidgetItem(fallback))

        cap_layout.addWidget(table)
        content_layout.addWidget(cap_card)

        scroll.setWidget(content_widget)
        main_layout.addWidget(scroll)

    def _load_settings(self) -> None:
        self.openai_key_input.setText(self.settings_service.get_setting("openai_api_key"))
        self.openai_base_input.setText(self.settings_service.get_setting("openai_base_url"))
        self.model_name_input.setText(self.settings_service.get_setting("openai_model", "gpt-4o-mini"))
        self.obs_host_input.setText(self.settings_service.get_setting("obs_host", "localhost"))
        self.obs_port_input.setText(self.settings_service.get_setting("obs_port", "4455"))
        self.obs_pass_input.setText(self.settings_service.get_setting("obs_password", ""))

    def _save_settings(self) -> None:
        self.settings_service.set_setting("openai_api_key", self.openai_key_input.text().strip(), is_secret=True)
        self.settings_service.set_setting("openai_base_url", self.openai_base_input.text().strip())
        self.settings_service.set_setting("openai_model", self.model_name_input.text().strip())
        self.settings_service.set_setting("obs_host", self.obs_host_input.text().strip())
        self.settings_service.set_setting("obs_port", self.obs_port_input.text().strip())
        self.settings_service.set_setting("obs_password", self.obs_pass_input.text().strip(), is_secret=True)

        QMessageBox.information(self, "Thành công", "Đã lưu toàn bộ cấu hình cài đặt!")
