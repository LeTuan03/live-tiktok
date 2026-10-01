"""AI Script Studio UI Page for generating and editing product scripts."""

from __future__ import annotations

import asyncio
from typing import Optional, List
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from loguru import logger
from app.database.models.product import Product
from app.database.models.script import Script, ScriptVersion
from app.database.repositories.product_repo import ProductRepository
from app.database.repositories.script_repo import ScriptRepository
from app.database.repositories.voice_repo import VoiceRepository
from app.database.session import get_session
from app.integrations.llm.openai_provider import OpenAILLMProvider
from app.services.product_service import ProductService
from app.services.script_service import ScriptService
from app.services.voice_service import VoiceService
from app.ui.styles.dark_theme import COLORS


class ScriptWorker(QThread):
    """Worker thread for non-blocking AI script generation."""

    finished = Signal(object, str)  # (Script, error_message)

    def __init__(
        self,
        script_service: ScriptService,
        product: Product,
        llm_provider: OpenAILLMProvider,
        style: str,
        target_audience: str,
        speaking_speed: str,
        honorific: str,
        cta_text: str,
        duration_minutes: float,
    ) -> None:
        super().__init__()
        self.script_service = script_service
        self.product = product
        self.llm_provider = llm_provider
        self.style = style
        self.target_audience = target_audience
        self.speaking_speed = speaking_speed
        self.honorific = honorific
        self.cta_text = cta_text
        self.duration_minutes = duration_minutes

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            script = loop.run_until_complete(
                self.script_service.generate_script_with_ai(
                    product=self.product,
                    provider=self.llm_provider,
                    style=self.style,
                    target_audience=self.target_audience,
                    speaking_speed=self.speaking_speed,
                    honorific=self.honorific,
                    cta_text=self.cta_text,
                    duration_minutes=self.duration_minutes,
                )
            )
            loop.close()
            self.finished.emit(script, "")
        except Exception as e:
            logger.exception("Lỗi khi phát sinh kịch bản AI")
            self.finished.emit(None, str(e))


class TTSWorker(QThread):
    """Worker thread for generating TTS audio from script."""

    finished = Signal(object, str)  # (GeneratedAudio, error_message)

    def __init__(self, voice_service: VoiceService, script_id: int, text: str) -> None:
        super().__init__()
        self.voice_service = voice_service
        self.script_id = script_id
        self.text = text

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            audio = loop.run_until_complete(
                self.voice_service.generate_speech(
                    script_id=self.script_id,
                    text=self.text,
                )
            )
            loop.close()
            self.finished.emit(audio, "")
        except Exception as e:
            logger.exception("Lỗi khi tạo file TTS audio")
            self.finished.emit(None, str(e))


class ScriptPage(QWidget):
    """AI Script Studio UI Page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.products: List[Product] = []
        self.current_product: Optional[Product] = None
        self.current_script: Optional[Script] = None

        self._init_services()
        self._init_ui()
        self.refresh_products()

    def _init_services(self) -> None:
        session = get_session()
        self.product_repo = ProductRepository(session)
        self.script_repo = ScriptRepository(session)
        self.voice_repo = VoiceRepository(session)
        self.product_service = ProductService(self.product_repo)
        self.script_service = ScriptService(self.script_repo)
        self.voice_service = VoiceService(self.voice_repo)

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        # Header
        header_layout = QHBoxLayout()
        title_label = QLabel("📝 AI Script Studio")
        title_label.setObjectName("headerLabel")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {COLORS['accent']}; font_weight: bold;")
        header_layout.addWidget(self.status_label)

        main_layout.addLayout(header_layout)

        # Splitter main content
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left Panel — Parameters & Controls
        left_card = QFrame()
        left_card.setObjectName("cardFrame")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(16, 16, 16, 16)

        left_title = QLabel("⚙️ Cấu hình Kịch bản")
        left_title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {COLORS['accent']};")
        left_layout.addWidget(left_title)

        form_layout = QFormLayout()
        form_layout.setSpacing(12)

        # Product Combo
        self.product_combo = QComboBox()
        self.product_combo.currentIndexChanged.connect(self._on_product_changed)
        form_layout.addRow("Sản phẩm:", self.product_combo)

        # Style Combo
        self.style_combo = QComboBox()
        self.style_combo.addItem("Chuyên gia tư vấn (Consultant)", "consultant")
        self.style_combo.addItem("Thân thiện gần gũi (Friendly)", "friendly")
        self.style_combo.addItem("Năng lượng bùng nổ (Energetic)", "energetic")
        self.style_combo.addItem("Ngắn gọn chốt nhanh (Concise)", "concise")
        form_layout.addRow("Phong cách:", self.style_combo)

        # Honorific
        self.honorific_input = QLineEdit("Anh/Chị")
        form_layout.addRow("Xưng hô:", self.honorific_input)

        # Target audience
        self.audience_input = QLineEdit("Tất cả khách hàng")
        form_layout.addRow("Khách mục tiêu:", self.audience_input)

        # CTA
        self.cta_input = QLineEdit("Bấm vào giỏ hàng góc trái")
        form_layout.addRow("Kêu gọi (CTA):", self.cta_input)

        # Duration
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.5, 10.0)
        self.duration_spin.setSingleStep(0.5)
        self.duration_spin.setValue(2.0)
        self.duration_spin.setSuffix(" phút")
        form_layout.addRow("Thời lượng:", self.duration_spin)

        left_layout.addLayout(form_layout)

        # Generate Button
        self.generate_btn = QPushButton("✨ Tạo kịch bản bằng AI")
        self.generate_btn.setObjectName("primaryButton")
        self.generate_btn.clicked.connect(self._generate_ai_script)
        left_layout.addWidget(self.generate_btn)

        left_layout.addSpacing(16)

        # History List
        version_title = QLabel("📜 Lịch sử Kịch bản")
        version_title.setStyleSheet(f"font-weight: bold; color: {COLORS['text_muted']};")
        left_layout.addWidget(version_title)

        self.version_list = QListWidget()
        self.version_list.itemClicked.connect(self._on_version_selected)
        left_layout.addWidget(self.version_list)

        splitter.addWidget(left_card)

        # Right Panel — Script Editor & Audio preview
        right_card = QFrame()
        right_card.setObjectName("cardFrame")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(16, 16, 16, 16)

        editor_header = QHBoxLayout()
        self.script_title_label = QLabel("Nội dung kịch bản")
        self.script_title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        editor_header.addWidget(self.script_title_label)
        editor_header.addStretch()

        self.save_btn = QPushButton("💾 Lưu kịch bản")
        self.save_btn.clicked.connect(self._save_script)
        editor_header.addWidget(self.save_btn)

        self.tts_btn = QPushButton("🔊 Tạo âm thanh TTS")
        self.tts_btn.setObjectName("accentButton")
        self.tts_btn.clicked.connect(self._generate_tts_audio)
        editor_header.addWidget(self.tts_btn)

        right_layout.addLayout(editor_header)

        self.script_edit = QTextEdit()
        self.script_edit.setPlaceholderText("Nội dung kịch bản livestream sẽ hiển thị tại đây...")
        self.script_edit.setStyleSheet("font-size: 14px; line-height: 1.5;")
        right_layout.addWidget(self.script_edit)

        # Audio Output Status Bar
        self.audio_status_label = QLabel("Trạng thái âm thanh: Chưa có audio được tạo.")
        self.audio_status_label.setStyleSheet(f"color: {COLORS['text_muted']};")
        right_layout.addWidget(self.audio_status_label)

        splitter.addWidget(right_card)
        splitter.setSizes([350, 650])

        main_layout.addWidget(splitter)

    def refresh_products(self) -> None:
        self.product_combo.clear()
        self.products = self.product_service.list_products()
        for p in self.products:
            self.product_combo.addItem(f"{p.name} ({p.sku or 'No SKU'})", p.id)

        if self.products:
            self.current_product = self.products[0]
            self._load_product_scripts(self.current_product.id)

    def _on_product_changed(self, index: int) -> None:
        if index >= 0 and index < len(self.products):
            self.current_product = self.products[index]
            self._load_product_scripts(self.current_product.id)

    def _load_product_scripts(self, product_id: int) -> None:
        self.version_list.clear()
        scripts = self.script_service.get_scripts_by_product(product_id)
        if scripts:
            self.current_script = scripts[0]
            self.script_edit.setText(self.current_script.content)
            self.script_title_label.setText(f"Nội dung: {self.current_script.title}")

            for s in scripts:
                item = QListWidgetItem(f"v{s.version} - {s.created_at.strftime('%H:%M %d/%m') if s.created_at else ''}")
                item.setData(Qt.ItemDataRole.UserRole, s)
                self.version_list.addItem(item)
        else:
            self.current_script = None
            self.script_edit.clear()
            self.script_title_label.setText("Nội dung kịch bản (Chưa có)")

    def _on_version_selected(self, item: QListWidgetItem) -> None:
        script = item.data(Qt.ItemDataRole.UserRole)
        if script:
            self.current_script = script
            self.script_edit.setText(script.content)
            self.script_title_label.setText(f"Nội dung: {script.title} (v{script.version})")

    def _generate_ai_script(self) -> None:
        if not self.current_product:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn sản phẩm trước!")
            return

        self.generate_btn.setEnabled(False)
        self.status_label.setText("⏳ Đang tạo kịch bản AI...")

        provider = OpenAILLMProvider()  # Uses settings or fallback

        self.worker = ScriptWorker(
            script_service=self.script_service,
            product=self.current_product,
            llm_provider=provider,
            style=self.style_combo.currentData(),
            target_audience=self.audience_input.text(),
            speaking_speed="normal",
            honorific=self.honorific_input.text(),
            cta_text=self.cta_input.text(),
            duration_minutes=self.duration_spin.value(),
        )
        self.worker.finished.connect(self._on_script_generated)
        self.worker.start()

    def _on_script_generated(self, script: Optional[Script], error: str) -> None:
        self.generate_btn.setEnabled(True)
        self.status_label.setText("")

        if error or not script:
            QMessageBox.warning(self, "Thông báo", f"Lỗi tạo kịch bản AI: {error}")
            return

        self.current_script = script
        self.script_edit.setText(script.content)
        if self.current_product:
            self._load_product_scripts(self.current_product.id)
        QMessageBox.information(self, "Thành công", "Đã tạo kịch bản AI mới thành công!")

    def _save_script(self) -> None:
        content = self.script_edit.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "Cảnh báo", "Nội dung kịch bản không được để trống!")
            return

        if self.current_script:
            updated = self.script_service.update_script(
                script_id=self.current_script.id,
                content=content,
                note="Chỉnh sửa thủ công bởi người dùng",
            )
            if updated:
                self.current_script = updated
                if self.current_product:
                    self._load_product_scripts(self.current_product.id)
                QMessageBox.information(self, "Thành công", "Đã lưu kịch bản!")
        elif self.current_product:
            new_script = self.script_repo.create(
                product_id=self.current_product.id,
                title=f"Kịch bản Livestream - {self.current_product.name}",
                content=content,
            )
            self.current_script = new_script
            self._load_product_scripts(self.current_product.id)
            QMessageBox.information(self, "Thành công", "Đã tạo kịch bản mới!")

    def _generate_tts_audio(self) -> None:
        content = self.script_edit.toPlainText().strip()
        if not content or not self.current_script:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn hoặc lưu kịch bản trước khi tạo audio!")
            return

        self.tts_btn.setEnabled(False)
        self.audio_status_label.setText("⏳ Đang tạo file âm thanh TTS...")

        self.tts_worker = TTSWorker(
            voice_service=self.voice_service,
            script_id=self.current_script.id,
            text=content,
        )
        self.tts_worker.finished.connect(self._on_tts_generated)
        self.tts_worker.start()

    def _on_tts_generated(self, audio: Optional[object], error: str) -> None:
        self.tts_btn.setEnabled(True)
        if error or not audio:
            self.audio_status_label.setText(f"❌ Lỗi tạo audio: {error}")
            QMessageBox.warning(self, "Lỗi TTS", f"Không thể tạo âm thanh: {error}")
        else:
            path = getattr(audio, "file_path", "")
            self.audio_status_label.setText(f"✅ Đã tạo file audio: {path}")
            QMessageBox.information(self, "Thành công", f"Đã tạo audio TTS tại:\n{path}")
