"""Settings UI Page for application configuration and integration parameters."""

from __future__ import annotations

import asyncio
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
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
from app.integrations.llm.base import BaseLLMProvider
from app.integrations.llm.gemini_provider import GeminiLLMProvider
from app.integrations.llm.openai_provider import OpenAILLMProvider
from app.integrations.obs.client import OBSClient
from app.services.settings_service import SettingsService
from app.ui.styles.dark_theme import COLORS


class TestAIWorker(QThread):
    """Worker thread for testing AI connection without freezing UI."""

    finished = Signal(bool, str)

    def __init__(self, provider: BaseLLMProvider) -> None:
        super().__init__()
        self.provider = provider

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            res = loop.run_until_complete(
                self.provider.generate_text(
                    prompt="Trả lời ngắn gọn đúng 5 chữ: Kết nối Gemini thành công.",
                    max_tokens=30,
                )
            )
            loop.close()
            self.finished.emit(True, res)
        except Exception as e:
            self.finished.emit(False, str(e))


class TestOBSWorker(QThread):
    """Worker thread for testing OBS connection without freezing UI."""

    finished = Signal(bool, str)

    def __init__(self, host: str, port: int, password: str = "") -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.password = password

    def run(self) -> None:
        try:
            client = OBSClient(host=self.host, port=self.port, password=self.password)
            success, msg, _ = client.test_connection()
            self.finished.emit(success, msg)
        except Exception as e:
            self.finished.emit(False, str(e))


class SettingsPage(QWidget):
    """Settings UI Page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._test_worker: Optional[TestAIWorker] = None
        self._obs_worker: Optional[TestOBSWorker] = None
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

        # --- AI Provider Settings Card ---
        ai_card = QFrame()
        ai_card.setObjectName("cardFrame")
        ai_layout = QVBoxLayout(ai_card)
        ai_layout.setContentsMargins(16, 16, 16, 16)
        ai_layout.setSpacing(12)

        ai_title = QLabel("🤖 Cấu Hình AI Provider (LLM)")
        ai_title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {COLORS['accent']};")
        ai_layout.addWidget(ai_title)

        provider_form = QFormLayout()
        provider_form.setSpacing(10)

        self.provider_combo = QComboBox()
        self.provider_combo.addItem("✨ Google Gemini (Khuyên dùng - Nhanh & Ổn định)", "gemini")
        self.provider_combo.addItem("🌐 OpenAI (ChatGPT / Tùy chỉnh / Ollama)", "openai")
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_form.addRow("Chọn AI Provider:", self.provider_combo)
        ai_layout.addLayout(provider_form)

        # Gemini Container
        self.gemini_widget = QWidget()
        gemini_form = QFormLayout(self.gemini_widget)
        gemini_form.setContentsMargins(0, 0, 0, 0)
        gemini_form.setSpacing(10)

        self.gemini_key_input = QLineEdit()
        self.gemini_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_key_input.setPlaceholderText("AIzaSy...")
        gemini_form.addRow("Gemini API Key:", self.gemini_key_input)

        self.gemini_model_combo = QComboBox()
        self.gemini_model_combo.setEditable(True)
        self.gemini_model_combo.addItem("gemini-3.5-flash (Khuyên dùng - Ổn định)", "gemini-3.5-flash")
        self.gemini_model_combo.addItem("gemini-flash-latest (Bản mới nhất)", "gemini-flash-latest")
        self.gemini_model_combo.addItem("gemini-3.1-flash-lite (Siêu nhẹ)", "gemini-3.1-flash-lite")
        self.gemini_model_combo.addItem("gemini-3.8-flash (Cao cấp)", "gemini-3.8-flash")
        gemini_form.addRow("Model Gemini:", self.gemini_model_combo)

        gemini_hint = QLabel(
            "💡 Lấy API Key miễn phí tại: <a href='https://aistudio.google.com/app/apikey' style='color:#38bdf8;'>"
            "https://aistudio.google.com/app/apikey</a><br>"
            "<i>(Lưu ý: Bấm nút biểu tượng Copy để sao chép đầy đủ mã bắt đầu bằng <b>AIzaSy...</b>, tránh copy text bị rút gọn dạng ...xxxx)</i>"
        )
        gemini_hint.setOpenExternalLinks(True)
        gemini_hint.setStyleSheet("color: #94a3b8; font-size: 12px;")
        gemini_form.addRow("", gemini_hint)

        ai_layout.addWidget(self.gemini_widget)

        # OpenAI Container
        self.openai_widget = QWidget()
        openai_form = QFormLayout(self.openai_widget)
        openai_form.setContentsMargins(0, 0, 0, 0)
        openai_form.setSpacing(10)

        self.openai_key_input = QLineEdit()
        self.openai_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_key_input.setPlaceholderText("sk-...")
        openai_form.addRow("OpenAI API Key:", self.openai_key_input)

        self.openai_base_input = QLineEdit()
        self.openai_base_input.setPlaceholderText("Để trống nếu dùng OpenAI mặc định, hoặc http://localhost:11434/v1")
        openai_form.addRow("Base URL (Custom/Ollama):", self.openai_base_input)

        self.openai_model_input = QLineEdit("gpt-4o-mini")
        openai_form.addRow("Tên Model:", self.openai_model_input)

        ai_layout.addWidget(self.openai_widget)

        # Test Connection Button
        btn_box = QHBoxLayout()
        self.test_ai_btn = QPushButton("🧪 Thử Nghiệm Kết Nối AI")
        self.test_ai_btn.clicked.connect(self._test_ai_connection)
        self.test_ai_status = QLabel("")
        btn_box.addWidget(self.test_ai_btn)
        btn_box.addWidget(self.test_ai_status)
        btn_box.addStretch()
        ai_layout.addLayout(btn_box)

        content_layout.addWidget(ai_card)

        # --- OBS Settings Card ---
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
        self.obs_pass_input.setPlaceholderText("Password (để trống nếu không có)")
        obs_form.addRow("Password:", self.obs_pass_input)

        obs_hint = QLabel(
            "💡 Hướng dẫn bật WebSocket trên OBS: Mở OBS Studio ➜ menu <b>Công cụ (Tools)</b> ➜ "
            "<b>Cài đặt máy chủ WebSocket (WebSocket Server Settings)</b> ➜ Tích chọn <i>Bật máy chủ WebSocket</i> (Port mặc định: 4455)."
        )
        obs_hint.setStyleSheet("color: #94a3b8; font-size: 12px;")
        obs_form.addRow("", obs_hint)

        obs_layout.addLayout(obs_form)

        # Test OBS Connection Button
        obs_btn_box = QHBoxLayout()
        self.test_obs_btn = QPushButton("🧪 Thử Nghiệm Kết Nối OBS")
        self.test_obs_btn.clicked.connect(self._test_obs_connection)
        self.test_obs_status = QLabel("")
        obs_btn_box.addWidget(self.test_obs_btn)
        obs_btn_box.addWidget(self.test_obs_status)
        obs_btn_box.addStretch()
        obs_layout.addLayout(obs_btn_box)

        content_layout.addWidget(obs_card)

        # --- TikTok Capability Matrix Card ---
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
            ("Tạo & Quản Lý Kịch Bản AI", "✅ Hoạt động 100% (Google Gemini / OpenAI)", "Offline Studio mode"),
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

    def _on_provider_changed(self) -> None:
        provider = self.provider_combo.currentData()
        if provider == "gemini":
            self.gemini_widget.setVisible(True)
            self.openai_widget.setVisible(False)
        else:
            self.gemini_widget.setVisible(False)
            self.openai_widget.setVisible(True)

    def _load_settings(self) -> None:
        provider = self.settings_service.get_setting("llm_provider", "gemini")
        idx = self.provider_combo.findData(provider)
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        else:
            self.provider_combo.setCurrentIndex(0)

        # Gemini
        self.gemini_key_input.setText(self.settings_service.get_setting("gemini_api_key", ""))
        gemini_model = self.settings_service.get_setting("gemini_model", "gemini-1.5-flash")
        model_idx = self.gemini_model_combo.findData(gemini_model)
        if model_idx >= 0:
            self.gemini_model_combo.setCurrentIndex(model_idx)
        else:
            self.gemini_model_combo.setEditText(gemini_model)

        # OpenAI
        self.openai_key_input.setText(self.settings_service.get_setting("openai_api_key", ""))
        self.openai_base_input.setText(self.settings_service.get_setting("openai_base_url", ""))
        self.openai_model_input.setText(self.settings_service.get_setting("openai_model", "gpt-4o-mini"))

        # OBS
        self.obs_host_input.setText(self.settings_service.get_setting("obs_host", "localhost"))
        self.obs_port_input.setText(self.settings_service.get_setting("obs_port", "4455"))
        self.obs_pass_input.setText(self.settings_service.get_setting("obs_password", ""))

        self._on_provider_changed()

    def _save_settings(self) -> None:
        provider = self.provider_combo.currentData()
        self.settings_service.set_setting("llm_provider", provider)

        # Gemini
        self.settings_service.set_setting("gemini_api_key", self.gemini_key_input.text().strip(), is_secret=True)
        self.settings_service.set_setting("gemini_model", self.gemini_model_combo.currentText().strip())

        # OpenAI
        self.settings_service.set_setting("openai_api_key", self.openai_key_input.text().strip(), is_secret=True)
        self.settings_service.set_setting("openai_base_url", self.openai_base_input.text().strip())
        self.settings_service.set_setting("openai_model", self.openai_model_input.text().strip())

        # OBS
        self.settings_service.set_setting("obs_host", self.obs_host_input.text().strip())
        self.settings_service.set_setting("obs_port", self.obs_port_input.text().strip())
        self.settings_service.set_setting("obs_password", self.obs_pass_input.text().strip(), is_secret=True)

        QMessageBox.information(self, "Thành công", "Đã lưu toàn bộ cấu hình cài đặt!")

    def _test_ai_connection(self) -> None:
        provider_name = self.provider_combo.currentData()
        if provider_name == "gemini":
            key = self.gemini_key_input.text().strip()
            model = self.gemini_model_combo.currentText().strip() or "gemini-1.5-flash"
            if not key:
                QMessageBox.warning(self, "Thiếu API Key", "Vui lòng nhập Gemini API Key trước khi thử nghiệm!")
                return
            if key.startswith("...") or "..." in key or len(key) < 20:
                QMessageBox.warning(
                    self,
                    "API Key Không Hợp Lệ",
                    "Gemini API Key đang bị rút gọn (dạng '...xxxx').\n\n"
                    "Vui lòng truy cập Google AI Studio (https://aistudio.google.com/app/apikey) "
                    "và bấm nút Copy để sao chép toàn bộ mã bắt đầu bằng 'AIzaSy...'.",
                )
                return
            provider = GeminiLLMProvider(api_key=key, model_name=model)
        else:
            key = self.openai_key_input.text().strip()
            base = self.openai_base_input.text().strip()
            model = self.openai_model_input.text().strip() or "gpt-4o-mini"
            if not key and not base:
                QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập OpenAI API Key hoặc Base URL!")
                return
            provider = OpenAILLMProvider(api_key=key or None, base_url=base or None, model_name=model)

        self.test_ai_btn.setEnabled(False)
        self.test_ai_status.setText("⏳ Đang kiểm tra kết nối...")

        self._test_worker = TestAIWorker(provider)
        self._test_worker.finished.connect(self._on_test_finished)
        self._test_worker.start()

    def _on_test_finished(self, success: bool, message: str) -> None:
        self.test_ai_btn.setEnabled(True)
        if success:
            self.test_ai_status.setText("✅ Kết nối thành công!")
            QMessageBox.information(self, "Kết Nối Thành Công", f"Phản hồi từ AI:\n\n{message}")
        else:
            self.test_ai_status.setText("❌ Kết nối thất bại")
            QMessageBox.critical(self, "Lỗi Kết Nối AI", f"Không thể kết nối với AI Provider:\n\n{message}")

    def _test_obs_connection(self) -> None:
        host = self.obs_host_input.text().strip() or "localhost"
        port_str = self.obs_port_input.text().strip() or "4455"
        password = self.obs_pass_input.text().strip()

        try:
            port = int(port_str)
            if not (1 <= port <= 65535):
                raise ValueError()
        except ValueError:
            QMessageBox.warning(self, "Port Không Hợp Lệ", "Cổng kết nối (Port) phải là một số nguyên từ 1 đến 65535.")
            return

        self.test_obs_btn.setEnabled(False)
        self.test_obs_status.setText("⏳ Đang kiểm tra kết nối OBS...")
        self.test_obs_status.setStyleSheet("color: #e2e8f0; font-size: 13px;")

        self._obs_worker = TestOBSWorker(host=host, port=port, password=password)
        self._obs_worker.finished.connect(self._on_test_obs_finished)
        self._obs_worker.start()

    def _on_test_obs_finished(self, success: bool, message: str) -> None:
        self.test_obs_btn.setEnabled(True)
        if success:
            self.test_obs_status.setText("✅ Kết nối thành công!")
            self.test_obs_status.setStyleSheet(f"color: {COLORS['status_online']}; font-weight: bold;")
            QMessageBox.information(self, "Kết Nối OBS Thành Công", message)
        else:
            self.test_obs_status.setText("❌ Kết nối thất bại")
            self.test_obs_status.setStyleSheet(f"color: {COLORS['status_offline']}; font-weight: bold;")
            QMessageBox.critical(self, "Lỗi Kết Nối OBS", message)
