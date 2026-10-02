"""Settings page — AI provider, OBS, platform/RTMP, shop policies, FFmpeg, data management."""

from __future__ import annotations

import asyncio
import shutil
from typing import Optional

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from loguru import logger

from app.config.settings import get_settings
from app.core import secrets
from app.database.session import get_session
from app.integrations.llm.base import BaseLLMProvider
from app.integrations.llm.gemini_provider import GeminiLLMProvider
from app.integrations.llm.openai_provider import OpenAILLMProvider
from app.integrations.obs.client import OBSClient
from app.integrations.tiktok.capabilities import Platform, get_capabilities
from app.services.activity_log_service import log_activity
from app.services.settings_service import (
    KEY_CLOSE_TO_TRAY,
    KEY_FFMPEG_DIR,
    KEY_GEMINI_API_KEY,
    KEY_GEMINI_MODEL,
    KEY_LLM_PROVIDER,
    KEY_OBS_AUDIO_SOURCE,
    KEY_OBS_AUTO_SWITCH_SCENE,
    KEY_OBS_HOST,
    KEY_OBS_IMAGE_SOURCE,
    KEY_OBS_SHOW_TEXT,
    KEY_OBS_TEXT_SOURCE,
    KEY_OBS_PASSWORD,
    KEY_OBS_PORT,
    KEY_OBS_SCENE,
    KEY_OBS_VIDEO_SOURCE,
    KEY_OPENAI_API_KEY,
    KEY_OPENAI_BASE_URL,
    KEY_OPENAI_MODEL,
    KEY_PLATFORM,
    KEY_SHOP_NAME,
    KEY_SHOP_RETURN_POLICY,
    KEY_SHOP_SHIPPING_POLICY,
    KEY_SHOP_WARRANTY_POLICY,
    SettingsService,
)


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
                self.provider.generate_text(prompt="Trả lời đúng 4 chữ: Kết nối thành công.", max_tokens=30)
            )
            loop.close()
            self.finished.emit(True, res)
        except Exception as e:  # noqa: BLE001
            self.finished.emit(False, str(e))


class ListModelsWorker(QThread):
    finished = Signal(list, str)

    def __init__(self, provider) -> None:
        super().__init__()
        self.provider = provider

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            models = loop.run_until_complete(self.provider.list_models())
            loop.close()
            self.finished.emit(list(models), "")
        except Exception as e:  # noqa: BLE001
            self.finished.emit([], str(e))


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
        except Exception as e:  # noqa: BLE001
            self.finished.emit(False, str(e))


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("cardFrame")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)
    label = QLabel(title)
    label.setObjectName("sectionTitle")
    layout.addWidget(label)
    return card, layout


class SettingsPage(QWidget):
    """Settings UI Page."""

    settings_saved = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._workers: list[QThread] = []
        self._init_services()
        self._init_ui()
        self._load_settings()

    def _init_services(self) -> None:
        self.session = get_session()
        self.settings_service = SettingsService(self.session)

    # ------------------------------------------------------------------ UI
    def _init_ui(self) -> None:  # noqa: C901 - long declarative UI
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        header_layout = QHBoxLayout()
        title_label = QLabel("⚙️ Cài đặt hệ thống & tích hợp")
        title_label.setObjectName("headerLabel")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        self.save_btn = QPushButton("💾 Lưu cấu hình")
        self.save_btn.setObjectName("primaryButton")
        self.save_btn.clicked.connect(self._save_settings)
        header_layout.addWidget(self.save_btn)
        main_layout.addLayout(header_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 8, 0)
        content_layout.setSpacing(16)

        # ================= AI =================
        ai_card, ai_layout = _card("🤖 AI Provider (LLM) — tạo kịch bản & gợi ý trả lời")
        provider_form = QFormLayout()
        provider_form.setSpacing(8)
        self.provider_combo = QComboBox()
        self.provider_combo.addItem("✨ Google Gemini (miễn phí, khuyên dùng)", "gemini")
        self.provider_combo.addItem("🌐 OpenAI / tương thích (Ollama, LM Studio...)", "openai")
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        provider_form.addRow("AI Provider:", self.provider_combo)
        ai_layout.addLayout(provider_form)

        self.gemini_widget = QWidget()
        gemini_form = QFormLayout(self.gemini_widget)
        gemini_form.setContentsMargins(0, 0, 0, 0)
        gemini_form.setSpacing(8)
        key_row = QHBoxLayout()
        self.gemini_key_input = QLineEdit()
        self.gemini_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.gemini_key_input.setPlaceholderText("Dán Gemini API Key")
        key_row.addWidget(self.gemini_key_input, 1)
        self.gemini_show_btn = QPushButton("👁")
        self.gemini_show_btn.setFixedWidth(40)
        self.gemini_show_btn.setCheckable(True)
        self.gemini_show_btn.toggled.connect(lambda on: self.gemini_key_input.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        key_row.addWidget(self.gemini_show_btn)
        gemini_form.addRow("Gemini API Key:", key_row)

        model_row = QHBoxLayout()
        self.gemini_model_combo = QComboBox()
        self.gemini_model_combo.setEditable(True)
        for m in GeminiLLMProvider.FALLBACK_MODELS:
            self.gemini_model_combo.addItem(m, m)
        model_row.addWidget(self.gemini_model_combo, 1)
        self.gemini_models_btn = QPushButton("🔄 Tải danh sách model")
        self.gemini_models_btn.setToolTip("Hỏi Google xem API Key này dùng được model nào")
        self.gemini_models_btn.clicked.connect(self._load_gemini_models)
        model_row.addWidget(self.gemini_models_btn)
        gemini_form.addRow("Model Gemini:", model_row)

        gemini_hint = QLabel(
            "💡 Lấy API Key miễn phí tại <a href='https://aistudio.google.com/app/apikey' style='color:#38bdf8;'>aistudio.google.com/app/apikey</a>. "
            "Bấm nút Copy trên trang đó để lấy đủ key (không copy đoạn bị rút gọn dạng ...xxxx). "
            "Nếu model bị lỗi 404, bấm 'Tải danh sách model' và chọn model khác."
        )
        gemini_hint.setOpenExternalLinks(True)
        gemini_hint.setWordWrap(True)
        gemini_hint.setObjectName("hintLabel")
        gemini_form.addRow("", gemini_hint)
        ai_layout.addWidget(self.gemini_widget)

        self.openai_widget = QWidget()
        openai_form = QFormLayout(self.openai_widget)
        openai_form.setContentsMargins(0, 0, 0, 0)
        openai_form.setSpacing(8)
        self.openai_key_input = QLineEdit()
        self.openai_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_key_input.setPlaceholderText("sk-... (bỏ trống nếu dùng Ollama/LM Studio)")
        openai_form.addRow("API Key:", self.openai_key_input)
        self.openai_base_input = QLineEdit()
        self.openai_base_input.setPlaceholderText("Để trống = api.openai.com; Ollama: http://localhost:11434/v1")
        openai_form.addRow("Base URL:", self.openai_base_input)
        self.openai_model_input = QLineEdit("gpt-4o-mini")
        openai_form.addRow("Tên model:", self.openai_model_input)
        ai_layout.addWidget(self.openai_widget)

        btn_box = QHBoxLayout()
        self.test_ai_btn = QPushButton("🧪 Kiểm tra kết nối AI")
        self.test_ai_btn.clicked.connect(self._test_ai_connection)
        self.test_ai_status = QLabel("")
        btn_box.addWidget(self.test_ai_btn)
        btn_box.addWidget(self.test_ai_status)
        btn_box.addStretch()
        ai_layout.addLayout(btn_box)
        content_layout.addWidget(ai_card)

        # ================= OBS =================
        obs_card, obs_layout = _card("🎥 OBS Studio WebSocket")
        obs_form = QFormLayout()
        obs_form.setSpacing(8)
        self.obs_host_input = QLineEdit("localhost")
        obs_form.addRow("Host:", self.obs_host_input)
        self.obs_port_input = QLineEdit("4455")
        obs_form.addRow("Port:", self.obs_port_input)
        self.obs_pass_input = QLineEdit()
        self.obs_pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.obs_pass_input.setPlaceholderText("Password (để trống nếu OBS không bật xác thực)")
        obs_form.addRow("Password:", self.obs_pass_input)
        self.obs_scene_input = QLineEdit()
        self.obs_scene_input.setPlaceholderText("LiveFlow AI")
        obs_form.addRow("Scene LiveFlow điều khiển:", self.obs_scene_input)
        self.obs_video_input = QLineEdit()
        self.obs_video_input.setPlaceholderText("LiveFlow Video")
        obs_form.addRow("Media source video:", self.obs_video_input)
        self.obs_audio_input = QLineEdit()
        self.obs_audio_input.setPlaceholderText("LiveFlow Audio")
        obs_form.addRow("Media source audio:", self.obs_audio_input)
        self.obs_image_input = QLineEdit()
        self.obs_image_input.setPlaceholderText("LiveFlow Image")
        obs_form.addRow("Image source (ảnh sản phẩm):", self.obs_image_input)
        self.obs_text_input = QLineEdit()
        self.obs_text_input.setPlaceholderText("LiveFlow Text")
        obs_form.addRow("Text overlay (tên + giá):", self.obs_text_input)
        self.obs_show_text_cb = QCheckBox("Hiển thị tên + giá sản phẩm bằng text overlay khi điều phối")
        self.obs_show_text_cb.setChecked(True)
        obs_form.addRow("", self.obs_show_text_cb)
        self.obs_auto_scene_cb = QCheckBox("Tự chuyển sang scene LiveFlow khi bắt đầu điều phối")
        self.obs_auto_scene_cb.setChecked(True)
        obs_form.addRow("", self.obs_auto_scene_cb)
        obs_hint = QLabel(
            "💡 Bật WebSocket: OBS → <b>Tools (Công cụ)</b> → <b>WebSocket Server Settings</b> → tích <i>Enable WebSocket server</i> "
            "(port mặc định 4455). Scene/nguồn ở trên sẽ được tạo tự động bằng nút 'Tạo scene & nguồn LiveFlow' trong Live Studio; "
            "LiveFlow chỉ điều khiển các nguồn này, không đụng vào scene khác."
        )
        obs_hint.setWordWrap(True)
        obs_hint.setObjectName("hintLabel")
        obs_form.addRow("", obs_hint)
        obs_layout.addLayout(obs_form)

        obs_btn_box = QHBoxLayout()
        self.test_obs_btn = QPushButton("🧪 Kiểm tra kết nối OBS")
        self.test_obs_btn.clicked.connect(self._test_obs_connection)
        self.test_obs_status = QLabel("")
        obs_btn_box.addWidget(self.test_obs_btn)
        obs_btn_box.addWidget(self.test_obs_status)
        obs_btn_box.addStretch()
        obs_layout.addLayout(obs_btn_box)
        content_layout.addWidget(obs_card)

        # ================= Shop info =================
        shop_card, shop_layout = _card("🏪 Thông tin shop — dùng cho trả lời bình luận có căn cứ")
        shop_hint = QLabel(
            "AI chỉ được trả lời khách bằng thông tin trong hồ sơ sản phẩm và các chính sách bạn khai báo ở đây. "
            "Bỏ trống mục nào thì AI sẽ trả lời 'em kiểm tra lại' thay vì bịa."
        )
        shop_hint.setWordWrap(True)
        shop_hint.setObjectName("hintLabel")
        shop_layout.addWidget(shop_hint)
        shop_form = QFormLayout()
        shop_form.setSpacing(8)
        self.shop_name_input = QLineEdit()
        self.shop_name_input.setPlaceholderText("Tên shop hiển thị")
        shop_form.addRow("Tên shop:", self.shop_name_input)
        self.shop_shipping_input = QTextEdit()
        self.shop_shipping_input.setMaximumHeight(60)
        self.shop_shipping_input.setPlaceholderText("VD: Giao hàng toàn quốc 2–4 ngày, phí theo nền tảng; miễn phí cho đơn từ 300k (nếu đúng)")
        shop_form.addRow("Chính sách vận chuyển:", self.shop_shipping_input)
        self.shop_return_input = QTextEdit()
        self.shop_return_input.setMaximumHeight(60)
        self.shop_return_input.setPlaceholderText("VD: Đổi trả trong 7 ngày nếu lỗi do nhà sản xuất, còn tem mác")
        shop_form.addRow("Chính sách đổi trả:", self.shop_return_input)
        self.shop_warranty_input = QTextEdit()
        self.shop_warranty_input.setMaximumHeight(60)
        self.shop_warranty_input.setPlaceholderText("VD: Bảo hành 12 tháng tại shop")
        shop_form.addRow("Chính sách bảo hành:", self.shop_warranty_input)
        shop_layout.addLayout(shop_form)
        content_layout.addWidget(shop_card)

        # ================= Platform =================
        cap_card, cap_layout = _card("📊 Nền tảng LIVE & khả năng tích hợp")
        plat_row = QHBoxLayout()
        plat_row.addWidget(QLabel("Nền tảng mặc định:"))
        self.platform_combo = QComboBox()
        self.platform_combo.addItem("TikTok LIVE", "tiktok")
        self.platform_combo.addItem("Shopee Live", "shopee")
        self.platform_combo.currentIndexChanged.connect(self._render_capabilities)
        plat_row.addWidget(self.platform_combo)
        plat_row.addStretch()
        cap_layout.addLayout(plat_row)
        self.cap_table = QTableWidget()
        self.cap_table.setColumnCount(3)
        self.cap_table.setHorizontalHeaderLabels(["Tính năng", "Trạng thái", "Cách thực hiện"])
        self.cap_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.cap_table.setColumnWidth(0, 230)
        self.cap_table.setColumnWidth(1, 170)
        self.cap_table.verticalHeader().setVisible(False)
        self.cap_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.cap_table.setWordWrap(True)
        self.cap_table.setMinimumHeight(260)
        cap_layout.addWidget(self.cap_table)
        cap_note = QLabel(
            "LiveFlow không scrape API riêng tư, không vượt CAPTCHA/OTP, không lưu mật khẩu TikTok/Shopee và không giả lập thành công. "
            "Những gì nền tảng chưa mở API sẽ ở chế độ thủ công có hướng dẫn."
        )
        cap_note.setWordWrap(True)
        cap_note.setObjectName("mutedLabel")
        cap_layout.addWidget(cap_note)
        content_layout.addWidget(cap_card)

        # ================= FFmpeg & app =================
        sys_card, sys_layout = _card("🧰 FFmpeg, dữ liệu & ứng dụng")
        ff_row = QHBoxLayout()
        self.ffmpeg_dir_input = QLineEdit()
        self.ffmpeg_dir_input.setPlaceholderText("Thư mục chứa ffmpeg.exe/ffprobe.exe (để trống = tự tìm)")
        ff_row.addWidget(self.ffmpeg_dir_input, 1)
        browse = QPushButton("📂 Chọn...")
        browse.clicked.connect(self._browse_ffmpeg)
        ff_row.addWidget(browse)
        sys_layout.addLayout(ff_row)
        self.ffmpeg_status = QLabel("")
        self.ffmpeg_status.setObjectName("hintLabel")
        self.ffmpeg_status.setWordWrap(True)
        sys_layout.addWidget(self.ffmpeg_status)

        self.close_to_tray_cb = QCheckBox("Khi bấm nút X: thu nhỏ xuống khay hệ thống thay vì thoát")
        sys_layout.addWidget(self.close_to_tray_cb)

        data_row = QHBoxLayout()
        self.data_dir_label = QLabel("")
        self.data_dir_label.setObjectName("mutedLabel")
        self.data_dir_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        data_row.addWidget(self.data_dir_label, 1)
        open_data = QPushButton("📂 Mở thư mục dữ liệu")
        open_data.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_settings().data_dir))))
        data_row.addWidget(open_data)
        open_logs = QPushButton("🧾 Mở thư mục log")
        open_logs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_settings().effective_log_dir))))
        data_row.addWidget(open_logs)
        sys_layout.addLayout(data_row)

        danger_row = QHBoxLayout()
        self.clear_cache_btn = QPushButton("🧹 Xóa cache TTS")
        self.clear_cache_btn.clicked.connect(self._clear_tts_cache)
        danger_row.addWidget(self.clear_cache_btn)
        self.wipe_btn = QPushButton("🗑️ Xóa toàn bộ dữ liệu người dùng")
        self.wipe_btn.setObjectName("dangerButton")
        self.wipe_btn.clicked.connect(self._wipe_data)
        danger_row.addWidget(self.wipe_btn)
        danger_row.addStretch()
        sys_layout.addLayout(danger_row)

        self.secret_status = QLabel("")
        self.secret_status.setObjectName("mutedLabel")
        sys_layout.addWidget(self.secret_status)
        content_layout.addWidget(sys_card)

        content_layout.addStretch()
        scroll.setWidget(content_widget)
        main_layout.addWidget(scroll, 1)

    # ---------------------------------------------------------- load/save
    def _on_provider_changed(self) -> None:
        provider = self.provider_combo.currentData()
        self.gemini_widget.setVisible(provider == "gemini")
        self.openai_widget.setVisible(provider != "gemini")

    def _render_capabilities(self) -> None:
        platform = Platform(self.platform_combo.currentData())
        caps = get_capabilities(platform)
        self.cap_table.setRowCount(len(caps))
        for row, (cap, info) in enumerate(caps.items()):
            self.cap_table.setItem(row, 0, QTableWidgetItem(cap.display_name))
            self.cap_table.setItem(row, 1, QTableWidgetItem(info.status.label))
            self.cap_table.setItem(row, 2, QTableWidgetItem(info.method))
        self.cap_table.resizeRowsToContents()

    def _load_settings(self) -> None:
        s = self.settings_service
        app = get_settings()

        provider = s.get_setting(KEY_LLM_PROVIDER, app.llm_provider)
        idx = self.provider_combo.findData(provider)
        self.provider_combo.setCurrentIndex(max(idx, 0))

        self.gemini_key_input.setText(s.get_setting(KEY_GEMINI_API_KEY, "") or app.gemini_api_key)
        gemini_model = s.get_setting(KEY_GEMINI_MODEL, "") or app.gemini_model
        idx = self.gemini_model_combo.findData(gemini_model)
        if idx >= 0:
            self.gemini_model_combo.setCurrentIndex(idx)
        else:
            self.gemini_model_combo.setEditText(gemini_model)

        self.openai_key_input.setText(s.get_setting(KEY_OPENAI_API_KEY, "") or app.openai_api_key)
        self.openai_base_input.setText(s.get_setting(KEY_OPENAI_BASE_URL, "") or app.openai_base_url)
        self.openai_model_input.setText(s.get_setting(KEY_OPENAI_MODEL, "") or app.openai_model)

        host, port, password = s.get_obs_connection()
        self.obs_host_input.setText(host)
        self.obs_port_input.setText(str(port))
        self.obs_pass_input.setText(password)
        scene, video, audio = s.get_obs_sources()
        self.obs_scene_input.setText(scene)
        self.obs_video_input.setText(video)
        self.obs_audio_input.setText(audio)
        self.obs_auto_scene_cb.setChecked(s.get_bool(KEY_OBS_AUTO_SWITCH_SCENE, True))
        image, text = s.get_obs_extra_sources()
        self.obs_image_input.setText(image)
        self.obs_text_input.setText(text)
        self.obs_show_text_cb.setChecked(s.get_bool(KEY_OBS_SHOW_TEXT, True))

        policies = s.get_shop_policies()
        self.shop_name_input.setText(policies["shop_name"])
        self.shop_shipping_input.setPlainText(policies["shipping"])
        self.shop_return_input.setPlainText(policies["return"])
        self.shop_warranty_input.setPlainText(policies["warranty"])

        idx = self.platform_combo.findData(s.get_platform())
        self.platform_combo.setCurrentIndex(max(idx, 0))
        self._render_capabilities()

        self.ffmpeg_dir_input.setText(s.get_setting(KEY_FFMPEG_DIR, ""))
        self._refresh_ffmpeg_status()
        self.close_to_tray_cb.setChecked(s.get_bool(KEY_CLOSE_TO_TRAY, False))
        self.data_dir_label.setText(f"Dữ liệu: {app.data_dir}")
        self.secret_status.setText(
            "🔐 API key / mật khẩu được lưu trong Windows Credential Manager."
            if secrets.keyring_available()
            else "⚠️ Không tìm thấy Credential Manager — khóa bí mật được lưu trong database cục bộ."
        )
        self._on_provider_changed()

    def _save_settings(self) -> None:
        s = self.settings_service
        s.set_setting(KEY_LLM_PROVIDER, self.provider_combo.currentData())
        s.set_setting(KEY_GEMINI_API_KEY, self.gemini_key_input.text().strip(), is_secret=True)
        model_data = self.gemini_model_combo.currentData()
        model_text = self.gemini_model_combo.currentText().strip()
        model = model_data if (model_data and self.gemini_model_combo.itemText(self.gemini_model_combo.currentIndex()) == model_text) else model_text
        s.set_setting(KEY_GEMINI_MODEL, model or GeminiLLMProvider.DEFAULT_MODEL)
        s.set_setting(KEY_OPENAI_API_KEY, self.openai_key_input.text().strip(), is_secret=True)
        s.set_setting(KEY_OPENAI_BASE_URL, self.openai_base_input.text().strip())
        s.set_setting(KEY_OPENAI_MODEL, self.openai_model_input.text().strip() or "gpt-4o-mini")

        try:
            port = int(self.obs_port_input.text().strip() or "4455")
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Port không hợp lệ", "Port OBS phải là số từ 1 đến 65535.")
            return
        s.set_setting(KEY_OBS_HOST, self.obs_host_input.text().strip() or "localhost")
        s.set_setting(KEY_OBS_PORT, str(port))
        s.set_setting(KEY_OBS_PASSWORD, self.obs_pass_input.text().strip(), is_secret=True)
        s.set_setting(KEY_OBS_SCENE, self.obs_scene_input.text().strip() or "LiveFlow AI")
        s.set_setting(KEY_OBS_VIDEO_SOURCE, self.obs_video_input.text().strip() or "LiveFlow Video")
        s.set_setting(KEY_OBS_AUDIO_SOURCE, self.obs_audio_input.text().strip() or "LiveFlow Audio")
        s.set_bool(KEY_OBS_AUTO_SWITCH_SCENE, self.obs_auto_scene_cb.isChecked())
        s.set_setting(KEY_OBS_IMAGE_SOURCE, self.obs_image_input.text().strip() or "LiveFlow Image")
        s.set_setting(KEY_OBS_TEXT_SOURCE, self.obs_text_input.text().strip() or "LiveFlow Text")
        s.set_bool(KEY_OBS_SHOW_TEXT, self.obs_show_text_cb.isChecked())

        s.set_setting(KEY_SHOP_NAME, self.shop_name_input.text().strip())
        s.set_setting(KEY_SHOP_SHIPPING_POLICY, self.shop_shipping_input.toPlainText().strip())
        s.set_setting(KEY_SHOP_RETURN_POLICY, self.shop_return_input.toPlainText().strip())
        s.set_setting(KEY_SHOP_WARRANTY_POLICY, self.shop_warranty_input.toPlainText().strip())

        s.set_setting(KEY_PLATFORM, self.platform_combo.currentData())
        s.set_setting(KEY_FFMPEG_DIR, self.ffmpeg_dir_input.text().strip())
        s.set_bool(KEY_CLOSE_TO_TRAY, self.close_to_tray_cb.isChecked())

        from app.utils.ffmpeg_locator import apply_to_settings

        apply_to_settings(self.ffmpeg_dir_input.text().strip())
        self._refresh_ffmpeg_status()
        log_activity("settings", "Lưu cấu hình")
        self.settings_saved.emit()
        QMessageBox.information(self, "Đã lưu", "Đã lưu toàn bộ cấu hình.")

    # ------------------------------------------------------------- AI test
    def _build_provider(self) -> Optional[BaseLLMProvider]:
        if self.provider_combo.currentData() == "gemini":
            key = self.gemini_key_input.text().strip()
            model = self.gemini_model_combo.currentText().strip() or GeminiLLMProvider.DEFAULT_MODEL
            if not key:
                QMessageBox.warning(self, "Thiếu API Key", "Vui lòng nhập Gemini API Key.")
                return None
            return GeminiLLMProvider(api_key=key, model_name=model)
        key = self.openai_key_input.text().strip()
        base = self.openai_base_input.text().strip()
        model = self.openai_model_input.text().strip() or "gpt-4o-mini"
        if not key and not base:
            QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập OpenAI API Key hoặc Base URL.")
            return None
        return OpenAILLMProvider(api_key=key or None, base_url=base or None, model_name=model)

    def _test_ai_connection(self) -> None:
        provider = self._build_provider()
        if provider is None:
            return
        self.test_ai_btn.setEnabled(False)
        self.test_ai_status.setText("⏳ Đang kiểm tra...")
        worker = TestAIWorker(provider)
        worker.finished.connect(self._on_test_finished)
        self._workers.append(worker)
        worker.start()

    def _on_test_finished(self, success: bool, message: str) -> None:
        self.test_ai_btn.setEnabled(True)
        if success:
            self.test_ai_status.setText("✅ Kết nối thành công")
            QMessageBox.information(self, "Kết nối thành công", f"Phản hồi từ AI:\n\n{message}")
        else:
            self.test_ai_status.setText("❌ Kết nối thất bại")
            QMessageBox.critical(self, "Lỗi kết nối AI", f"Không thể kết nối với AI Provider:\n\n{message}")

    def _load_gemini_models(self) -> None:
        key = self.gemini_key_input.text().strip()
        if not key:
            QMessageBox.warning(self, "Thiếu API Key", "Nhập Gemini API Key trước khi tải danh sách model.")
            return
        self.gemini_models_btn.setEnabled(False)
        worker = ListModelsWorker(GeminiLLMProvider(api_key=key))
        worker.finished.connect(self._on_models_loaded)
        self._workers.append(worker)
        worker.start()

    def _on_models_loaded(self, models: list, error: str) -> None:
        self.gemini_models_btn.setEnabled(True)
        if error or not models:
            QMessageBox.warning(self, "Không tải được model", error or "Danh sách rỗng.")
            return
        current = self.gemini_model_combo.currentText().strip()
        self.gemini_model_combo.clear()
        for m in models:
            self.gemini_model_combo.addItem(m, m)
        idx = self.gemini_model_combo.findData(current)
        self.gemini_model_combo.setCurrentIndex(idx if idx >= 0 else 0)
        QMessageBox.information(self, "Danh sách model", f"API Key này dùng được {len(models)} model. Đã chọn: {self.gemini_model_combo.currentText()}")

    # ------------------------------------------------------------ OBS test
    def _test_obs_connection(self) -> None:
        host = self.obs_host_input.text().strip() or "localhost"
        try:
            port = int(self.obs_port_input.text().strip() or "4455")
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Port không hợp lệ", "Port phải là số nguyên từ 1 đến 65535.")
            return
        self.test_obs_btn.setEnabled(False)
        self.test_obs_status.setText("⏳ Đang kiểm tra OBS...")
        worker = TestOBSWorker(host=host, port=port, password=self.obs_pass_input.text().strip())
        worker.finished.connect(self._on_test_obs_finished)
        self._workers.append(worker)
        worker.start()

    def _on_test_obs_finished(self, success: bool, message: str) -> None:
        self.test_obs_btn.setEnabled(True)
        if success:
            self.test_obs_status.setText("✅ Kết nối thành công")
            QMessageBox.information(self, "Kết nối OBS thành công", message)
        else:
            self.test_obs_status.setText("❌ Kết nối thất bại")
            QMessageBox.critical(self, "Lỗi kết nối OBS", message)

    # ------------------------------------------------------------- FFmpeg
    def _browse_ffmpeg(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Chọn thư mục chứa ffmpeg.exe / ffprobe.exe")
        if folder:
            self.ffmpeg_dir_input.setText(folder)
            from app.utils.ffmpeg_locator import apply_to_settings

            apply_to_settings(folder)
            self._refresh_ffmpeg_status()

    def _refresh_ffmpeg_status(self) -> None:
        from app.utils.ffmpeg_locator import find_ffmpeg, find_ffprobe

        extra = self.ffmpeg_dir_input.text().strip()
        ffmpeg = find_ffmpeg(extra)
        ffprobe = find_ffprobe(extra)
        if ffmpeg and ffprobe:
            self.ffmpeg_status.setText(f"✅ ffprobe: {ffprobe}")
        else:
            self.ffmpeg_status.setText(
                "⚠️ Chưa tìm thấy FFmpeg. Chạy scripts\\download_ffmpeg.ps1 (tự tải về thư mục tools\\ffmpeg) "
                "hoặc cài bằng 'winget install Gyan.FFmpeg' rồi chọn thư mục ở trên."
            )

    # --------------------------------------------------------------- data
    def _clear_tts_cache(self) -> None:
        cache_dir = get_settings().effective_tts_cache_dir
        removed = 0
        for p in cache_dir.rglob("*.mp3"):
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
        QMessageBox.information(self, "Cache TTS", f"Đã xóa {removed} file nghe thử. Audio của kịch bản (thư mục audio) được giữ lại.")

    def _wipe_data(self) -> None:
        reply = QMessageBox.question(
            self, "Xóa toàn bộ dữ liệu",
            "Thao tác này xóa TẤT CẢ sản phẩm, kịch bản, playlist, bình luận, audio đã tạo và cấu hình trong ứng dụng "
            "(file video gốc của bạn KHÔNG bị xóa).\n\nỨng dụng sẽ đóng sau khi xóa. Bạn chắc chắn?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        confirm, ok = __import__("PySide6.QtWidgets", fromlist=["QInputDialog"]).QInputDialog.getText(
            self, "Xác nhận lần cuối", "Gõ XOA để xác nhận:"
        )
        if not ok or confirm.strip().upper() != "XOA":
            return
        from app.database.session import close_database
        from PySide6.QtWidgets import QApplication

        for key in ("gemini_api_key", "openai_api_key", "obs_password", "stream_key_tiktok", "stream_key_shopee"):
            secrets.delete_secret(key)
        data_dir = get_settings().data_dir
        close_database()
        errors = []
        for child in data_dir.iterdir():
            try:
                if child.is_dir():
                    if child.name == "logs":
                        continue
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink()
            except OSError as e:
                errors.append(f"{child.name}: {e}")
        logger.warning("Người dùng đã xóa toàn bộ dữ liệu ứng dụng.")
        QMessageBox.information(self, "Đã xóa", "Đã xóa dữ liệu. Ứng dụng sẽ đóng; mở lại để bắt đầu mới." + ("\n" + "\n".join(errors) if errors else ""))
        QApplication.quit()
