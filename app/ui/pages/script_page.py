"""AI Script Studio UI Page for generating, customizing, and editing product scripts."""

from __future__ import annotations

import asyncio
from typing import Optional, List, Dict, Any
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from loguru import logger
from app.database.models.product import Product
from app.database.models.script import Script
from app.database.repositories.product_repo import ProductRepository
from app.database.repositories.script_repo import ScriptRepository
from app.database.repositories.voice_repo import VoiceRepository
from app.database.session import get_session
from app.integrations.llm.base import BaseLLMProvider
from app.integrations.llm.factory import get_llm_provider
from app.render.compositor import VideoCompositor
from app.render.queue import RenderQueue
from app.render.worker import RenderWorker
from app.services.product_extractor_service import ProductExtractorService
from app.services.product_service import ProductService
from app.services.script_segment_service import (
    VALID_CUES,
    VALID_SHOTS,
    ScriptSegment,
    StructuredScript,
    parse_and_validate_script_json,
)
from app.services.script_service import ScriptService
from app.services.settings_service import SettingsService
from app.services.voice_service import VoiceService
from app.sets.library import SceneSetLibrary
from app.ui.styles.dark_theme import COLORS


class ExtractUrlWorker(QThread):
    """Worker thread for scraping and AI extracting product specs from URL."""

    finished = Signal(dict, str)  # (extracted_data, error_message)

    def __init__(
        self,
        extractor: ProductExtractorService,
        provider: BaseLLMProvider,
        url: str,
    ) -> None:
        super().__init__()
        self.extractor = extractor
        self.provider = provider
        self.url = url

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            data = loop.run_until_complete(
                self.extractor.extract_product_from_url(self.url, self.provider)
            )
            loop.close()
            self.finished.emit(data, "")
        except Exception as e:
            logger.exception("Lỗi khi trích xuất thông tin sản phẩm từ URL")
            self.finished.emit({}, str(e))


class ScriptWorker(QThread):
    """Worker thread for non-blocking AI script generation."""

    finished = Signal(object, str)  # (Script, error_message)

    def __init__(
        self,
        script_service: ScriptService,
        llm_provider: BaseLLMProvider,
        product: Optional[Product] = None,
        product_name: str = "",
        price: Optional[float] = None,
        promo_price: Optional[float] = None,
        description: str = "",
        verified_features: str = "",
        ingredients: str = "",
        script_type: str = "full_live",
        style: str = "energetic",
        target_audience: str = "Tất cả khách hàng",
        speaking_speed: str = "normal",
        honorific: str = "Anh/Chị",
        promotions_deal: Optional[str] = None,
        key_highlights: Optional[str] = None,
        cta_text: Optional[str] = None,
        custom_instructions: Optional[str] = None,
        duration_minutes: float = 2.0,
    ) -> None:
        super().__init__()
        self.script_service = script_service
        self.llm_provider = llm_provider
        self.product = product
        self.product_name = product_name
        self.price = price
        self.promo_price = promo_price
        self.description = description
        self.verified_features = verified_features
        self.ingredients = ingredients
        self.script_type = script_type
        self.style = style
        self.target_audience = target_audience
        self.speaking_speed = speaking_speed
        self.honorific = honorific
        self.promotions_deal = promotions_deal
        self.key_highlights = key_highlights
        self.cta_text = cta_text
        self.custom_instructions = custom_instructions
        self.duration_minutes = duration_minutes

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            script = loop.run_until_complete(
                self.script_service.generate_script_with_ai(
                    product=self.product,
                    provider=self.llm_provider,
                    product_name=self.product_name,
                    price=self.price,
                    promo_price=self.promo_price,
                    description=self.description,
                    verified_features=self.verified_features,
                    ingredients=self.ingredients,
                    script_type=self.script_type,
                    style=self.style,
                    target_audience=self.target_audience,
                    speaking_speed=self.speaking_speed,
                    honorific=self.honorific,
                    promotions_deal=self.promotions_deal,
                    key_highlights=self.key_highlights,
                    cta_text=self.cta_text,
                    custom_instructions=self.custom_instructions,
                    duration_minutes=self.duration_minutes,
                )
            )
            loop.close()
            self.finished.emit(script, "")
        except Exception as e:
            logger.exception("Lỗi khi phát sinh kịch bản AI")
            self.finished.emit(None, str(e))


class SuggestConfigWorker(QThread):
    """Worker thread for AI to suggest optimal script configuration."""

    finished = Signal(dict, str)  # (result_dict, error_message)

    def __init__(
        self,
        script_service: ScriptService,
        llm_provider: BaseLLMProvider,
        product_info: dict,
    ) -> None:
        super().__init__()
        self.script_service = script_service
        self.llm_provider = llm_provider
        self.product_info = product_info

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            data = loop.run_until_complete(
                self.script_service.suggest_script_config(
                    product_info=self.product_info,
                    provider=self.llm_provider,
                )
            )
            loop.close()
            self.finished.emit(data, "")
        except Exception as e:
            logger.exception("Lỗi khi AI gợi ý cấu hình")
            self.finished.emit({}, str(e))


class ImproveScriptWorker(QThread):
    """Worker thread for improving / rewriting existing script with AI."""

    finished = Signal(str, str)  # (new_content, error_message)

    def __init__(
        self,
        script_service: ScriptService,
        llm_provider: BaseLLMProvider,
        content: str,
        action: str,
        custom_note: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.script_service = script_service
        self.llm_provider = llm_provider
        self.content = content
        self.action = action
        self.custom_note = custom_note

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            new_text = loop.run_until_complete(
                self.script_service.improve_script_with_ai(
                    current_content=self.content,
                    action=self.action,
                    provider=self.llm_provider,
                    custom_note=self.custom_note,
                )
            )
            loop.close()
            self.finished.emit(new_text, "")
        except Exception as e:
            logger.exception("Lỗi khi cải thiện kịch bản")
            self.finished.emit("", str(e))


class TTSWorker(QThread):
    """Worker thread for generating TTS audio from script."""

    finished = Signal(object, object, str)  # (GeneratedAudio, Optional[StructuredScript], error_message)

    def __init__(
        self,
        voice_service: VoiceService,
        script_id: int,
        text: str,
        structured_script: Optional[StructuredScript] = None,
    ) -> None:
        super().__init__()
        self.voice_service = voice_service
        self.script_id = script_id
        self.text = text
        self.structured_script = structured_script

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            if self.structured_script and self.structured_script.segments:
                audio, updated = loop.run_until_complete(
                    self.voice_service.synthesize_structured_script(
                        script_id=self.script_id,
                        structured_script=self.structured_script,
                        force_regenerate=True,
                    )
                )
            else:
                audio = loop.run_until_complete(
                    self.voice_service.generate_speech(
                        script_id=self.script_id,
                        text=self.text,
                        force_regenerate=True,
                    )
                )
                updated = None
            loop.close()
            self.finished.emit(audio, updated, "")
        except Exception as e:
            logger.exception("Lỗi khi tạo file TTS audio")
            self.finished.emit(None, None, str(e))



class ScriptPage(QWidget):
    """AI Script Studio UI Page with URL Auto-Extraction, Full AI Generation & Custom Modes."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.products: List[Product] = []
        self.current_product: Optional[Product] = None
        self.current_script: Optional[Script] = None
        self.extracted_product_data: Optional[Dict[str, Any]] = None

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
        self.settings_service = SettingsService(session)
        self.extractor_service = ProductExtractorService()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(14)

        # Header
        header_layout = QHBoxLayout()
        title_label = QLabel("📝 AI Script Studio — Trung Tâm Kịch Bản Livestream")
        title_label.setObjectName("headerLabel")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {COLORS['accent_secondary']}; font-weight: bold;")
        header_layout.addWidget(self.status_label)

        main_layout.addLayout(header_layout)

        # Splitter main content
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # =========================================================================
        # LEFT PANEL — NGUỒN DỮ LIỆU & CẤU HÌNH KỊCH BẢN
        # =========================================================================
        left_card = QFrame()
        left_card.setObjectName("cardFrame")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(12)

        source_label = QLabel("1️⃣ Nguồn Dữ Liệu Sản Phẩm")
        source_label.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {COLORS['accent_primary_hover']};")
        left_layout.addWidget(source_label)

        self.source_tabs = QTabWidget()
        self.source_tabs.setStyleSheet(
            f"QTabBar::tab {{ font-size: 12px; font-weight: bold; padding: 6px 12px; }} "
            f"QTabBar::tab:selected {{ color: {COLORS['accent_secondary']}; }}"
        )

        # ---------------------------------------------------------------------
        # TAB 1: DÁN LINK SẢN PHẨM (TỰ ĐỘNG 100%)
        # ---------------------------------------------------------------------
        tab_link = QWidget()
        tab_link_layout = QVBoxLayout(tab_link)
        tab_link_layout.setContentsMargins(8, 10, 8, 8)
        tab_link_layout.setSpacing(8)

        link_input_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Dán link (TikTok Shop, Shopee, Lazada, Tiki, Website...)")
        link_input_row.addWidget(self.url_input, 1)

        self.paste_url_btn = QPushButton("📋 Dán")
        self.paste_url_btn.setToolTip("Dán nhanh liên kết từ Clipboard")
        self.paste_url_btn.setFixedWidth(50)
        self.paste_url_btn.clicked.connect(self._paste_clipboard_to_url)
        link_input_row.addWidget(self.paste_url_btn)
        tab_link_layout.addLayout(link_input_row)

        # Quick Actions from URL
        url_btns_row = QHBoxLayout()
        self.extract_and_gen_btn = QPushButton("⚡ 1-Click: Trích Xuất & Tự Sinh Kịch Bản")
        self.extract_and_gen_btn.setObjectName("primaryButton")
        self.extract_and_gen_btn.setStyleSheet(
            f"font-size: 13px; font-weight: bold; padding: 8px 12px; background-color: {COLORS['accent_primary']};"
        )
        self.extract_and_gen_btn.setToolTip("Tự động quét link -> bóc tách thông số -> sinh kịch bản livestream hoàn chỉnh ngay lập tức!")
        self.extract_and_gen_btn.clicked.connect(lambda: self._start_url_extraction(auto_generate=True))
        url_btns_row.addWidget(self.extract_and_gen_btn, 1)

        self.extract_only_btn = QPushButton("📥 Trích Xuất Để Tùy Chỉnh")
        self.extract_only_btn.setObjectName("accentButton")
        self.extract_only_btn.setStyleSheet("font-size: 12px; padding: 8px 10px;")
        self.extract_only_btn.setToolTip("Quét link và điền toàn bộ thông số vào form bên dưới để bạn tùy chỉnh trước khi tạo kịch bản")
        self.extract_only_btn.clicked.connect(lambda: self._start_url_extraction(auto_generate=False))
        url_btns_row.addWidget(self.extract_only_btn)
        tab_link_layout.addLayout(url_btns_row)

        # Extracted Preview Box
        self.url_preview_box = QFrame()
        self.url_preview_box.setStyleSheet(f"background-color: {COLORS['bg_darkest']}; border-radius: 6px; padding: 8px;")
        url_prev_layout = QVBoxLayout(self.url_preview_box)
        url_prev_layout.setSpacing(4)

        self.url_preview_title = QLabel("Chưa dán liên kết sản phẩm")
        self.url_preview_title.setStyleSheet(f"font-weight: bold; color: {COLORS['text_accent']}; font-size: 13px;")
        self.url_preview_title.setWordWrap(True)
        url_prev_layout.addWidget(self.url_preview_title)

        self.url_preview_details = QLabel("Dán link và bấm nút phía trên để hệ thống tự động trích xuất thông số, giá và tính năng.")
        self.url_preview_details.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        self.url_preview_details.setWordWrap(True)
        url_prev_layout.addWidget(self.url_preview_details)

        self.auto_save_product_cb = QCheckBox("Tự động lưu sản phẩm này vào kho sau khi trích xuất")
        self.auto_save_product_cb.setChecked(True)
        self.auto_save_product_cb.setStyleSheet("font-size: 11px; color: #a29bfe;")
        url_prev_layout.addWidget(self.auto_save_product_cb)

        tab_link_layout.addWidget(self.url_preview_box)
        self.source_tabs.addTab(tab_link, "🔗 Dán Link (Tự Động)")

        # ---------------------------------------------------------------------
        # TAB 2: CHỌN TỪ KHO SẢN PHẨM
        # ---------------------------------------------------------------------
        tab_existing = QWidget()
        tab_existing_layout = QVBoxLayout(tab_existing)
        tab_existing_layout.setContentsMargins(8, 10, 8, 8)
        tab_existing_layout.setSpacing(8)

        combo_row = QHBoxLayout()
        self.product_combo = QComboBox()
        self.product_combo.currentIndexChanged.connect(self._on_product_changed)
        combo_row.addWidget(self.product_combo, 1)

        self.refresh_prods_btn = QPushButton("🔄")
        self.refresh_prods_btn.setToolTip("Tải lại danh sách sản phẩm")
        self.refresh_prods_btn.setFixedWidth(36)
        self.refresh_prods_btn.clicked.connect(self.refresh_products)
        combo_row.addWidget(self.refresh_prods_btn)
        tab_existing_layout.addLayout(combo_row)

        self.prod_quick_info = QLabel("Chưa chọn sản phẩm")
        self.prod_quick_info.setWordWrap(True)
        self.prod_quick_info.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px; padding: 4px;")
        tab_existing_layout.addWidget(self.prod_quick_info)

        self.source_tabs.addTab(tab_existing, "📦 Kho Sản Phẩm")

        # ---------------------------------------------------------------------
        # TAB 3: NHẬP SẢN PHẨM TÙY CHỈNH THỦ CÔNG
        # ---------------------------------------------------------------------
        tab_custom = QWidget()
        tab_custom_layout = QFormLayout(tab_custom)
        tab_custom_layout.setContentsMargins(8, 10, 8, 8)
        tab_custom_layout.setSpacing(8)

        self.custom_name_input = QLineEdit()
        self.custom_name_input.setPlaceholderText("VD: Nồi chiên không dầu 5L, Son môi Black Rouge...")
        tab_custom_layout.addRow("Tên SP *:", self.custom_name_input)

        price_box = QHBoxLayout()
        self.custom_price_spin = QDoubleSpinBox()
        self.custom_price_spin.setRange(0, 999999999)
        self.custom_price_spin.setDecimals(0)
        self.custom_price_spin.setSuffix(" đ")
        self.custom_price_spin.setValue(199000)
        price_box.addWidget(self.custom_price_spin)

        price_box.addWidget(QLabel("KM:"))
        self.custom_promo_spin = QDoubleSpinBox()
        self.custom_promo_spin.setRange(0, 999999999)
        self.custom_promo_spin.setDecimals(0)
        self.custom_promo_spin.setSuffix(" đ")
        self.custom_promo_spin.setValue(149000)
        price_box.addWidget(self.custom_promo_spin)
        tab_custom_layout.addRow("Giá bán:", price_box)

        self.custom_desc_input = QTextEdit()
        self.custom_desc_input.setPlaceholderText("Mô tả ngắn, tính năng hoặc chất liệu sản phẩm...")
        self.custom_desc_input.setMaximumHeight(60)
        tab_custom_layout.addRow("Mô tả:", self.custom_desc_input)

        self.source_tabs.addTab(tab_custom, "✏️ Nhập Thủ Công")
        left_layout.addWidget(self.source_tabs)

        # ---------------------------------------------------------------------
        # 2️⃣ PHƯƠNG THỨC TẠO KỊCH BẢN (AUTO VS CUSTOM)
        # ---------------------------------------------------------------------
        method_label = QLabel("2️⃣ Thiết Lập Kịch Bản")
        method_label.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {COLORS['accent_primary_hover']};")
        left_layout.addWidget(method_label)

        # Nút One-Click Auto Generate
        self.auto_gen_btn = QPushButton("✨ AI Tự Động Sinh Kịch Bản (One-Click)")
        self.auto_gen_btn.setObjectName("primaryButton")
        self.auto_gen_btn.setStyleSheet(
            f"font-size: 14px; font-weight: bold; padding: 10px; background-color: {COLORS['accent_primary']};"
        )
        self.auto_gen_btn.setToolTip("AI tự động phân tích sản phẩm đã chọn, tạo deal hấp dẫn và viết toàn bộ kịch bản từ A-Z")
        self.auto_gen_btn.clicked.connect(self._generate_one_click_ai)
        left_layout.addWidget(self.auto_gen_btn)

        # Khu vực Form tùy chỉnh thông tin (Custom Configuration)
        scroll_custom = QScrollArea()
        scroll_custom.setWidgetResizable(True)
        scroll_custom.setFrameShape(QFrame.Shape.NoFrame)

        custom_container = QWidget()
        custom_layout = QVBoxLayout(custom_container)
        custom_layout.setContentsMargins(0, 4, 4, 4)
        custom_layout.setSpacing(10)

        config_title_row = QHBoxLayout()
        config_title = QLabel("⚙️ Tự Tùy Chỉnh Thông Tin Chi Tiết:")
        config_title.setStyleSheet(f"font-weight: bold; color: {COLORS['text_accent']};")
        config_title_row.addWidget(config_title)
        config_title_row.addStretch()

        self.suggest_btn = QPushButton("💡 AI Gợi Ý Điền Nhanh")
        self.suggest_btn.setToolTip("Để AI tự động phân tích và điền gợi ý điểm nhấn, deal sốc, CTA vào các ô bên dưới")
        self.suggest_btn.setStyleSheet(
            f"color: {COLORS['accent_secondary']}; border: 1px solid {COLORS['accent_secondary']}; "
            "padding: 4px 8px; border-radius: 4px; font-size: 11px;"
        )
        self.suggest_btn.clicked.connect(self._suggest_configuration)
        config_title_row.addWidget(self.suggest_btn)
        custom_layout.addLayout(config_title_row)

        form_layout = QFormLayout()
        form_layout.setSpacing(8)

        # Loại kịch bản
        self.type_combo = QComboBox()
        self.type_combo.addItem("🎯 Kịch bản Bán Hàng Toàn Diện (Full)", "full_live")
        self.type_combo.addItem("⚡ Flash Sale Chớp Nhoáng (60-90s)", "flash_sale")
        self.type_combo.addItem("📖 Kể Chuyện / Review Thực Tế", "storytelling")
        self.type_combo.addItem("🛡️ Bẻ Gãy Lời Từ Chối & Nghi Ngại", "objection_handling")
        self.type_combo.addItem("🎁 Minigame / Kéo Tương Tác Giữ Mắt", "giveaway_hook")
        form_layout.addRow("Loại kịch bản:", self.type_combo)

        # Phong cách nói
        self.style_combo = QComboBox()
        self.style_combo.addItem("🔥 Năng lượng bùng nổ, dồn dập (Energetic)", "energetic")
        self.style_combo.addItem("🎓 Chuyên gia tư vấn uy tín (Consultant)", "consultant")
        self.style_combo.addItem("💖 Thân thiện, tâm tình gần gũi (Friendly)", "friendly")
        self.style_combo.addItem("😂 Hài hước dí dỏm, bắt trend (Humorous)", "humorous")
        self.style_combo.addItem("👑 Sang trọng cao cấp (Luxury)", "luxury")
        self.style_combo.addItem("⚡ Ngắn gọn chốt nhanh (Concise)", "concise")
        form_layout.addRow("Phong cách:", self.style_combo)

        # Xưng hô
        self.honorific_input = QLineEdit("Em - Các tình yêu")
        self.honorific_input.setPlaceholderText("VD: Em - Các tình yêu, Mình - Các bạn...")
        form_layout.addRow("Xưng hô:", self.honorific_input)

        # Khách mục tiêu
        self.audience_input = QLineEdit("Tất cả khách hàng xem livestream")
        self.audience_input.setPlaceholderText("VD: Học sinh sinh viên, Mẹ bỉm sữa, Dân văn phòng...")
        form_layout.addRow("Khách mục tiêu:", self.audience_input)

        # Điểm nhấn USP cần xoáy sâu
        self.highlights_input = QLineEdit()
        self.highlights_input.setPlaceholderText("VD: Chống nước IPX8, bản mới 2026, tặng kèm quà...")
        form_layout.addRow("Điểm nhấn (USP):", self.highlights_input)

        # Ưu đãi & Deal sốc hôm nay
        self.deal_input = QLineEdit()
        self.deal_input.setPlaceholderText("VD: Mua 1 tặng 1, Giảm 50% cho 10 người đầu tiên, Freeship...")
        form_layout.addRow("Deal / Ưu đãi:", self.deal_input)

        # Kêu gọi hành động (CTA)
        self.cta_input = QLineEdit("Bấm vào giỏ hàng góc trái màn hình nhận voucher ngay!")
        form_layout.addRow("Kêu gọi (CTA):", self.cta_input)

        # Thời lượng
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.5, 10.0)
        self.duration_spin.setSingleStep(0.5)
        self.duration_spin.setValue(2.0)
        self.duration_spin.setSuffix(" phút")
        form_layout.addRow("Thời lượng:", self.duration_spin)

        # Prompt tùy biến thêm
        self.custom_prompt_input = QTextEdit()
        self.custom_prompt_input.setPlaceholderText("Yêu cầu bổ sung cho AI (VD: Nói giọng miền Nam, nhấn mạnh đếm ngược 10 giây...)")
        self.custom_prompt_input.setMaximumHeight(50)
        form_layout.addRow("Gợi ý thêm:", self.custom_prompt_input)

        custom_layout.addLayout(form_layout)

        # Nút tạo theo cấu hình
        self.custom_gen_btn = QPushButton("🎯 Tạo Kịch Bản Theo Cấu Hình Tùy Chỉnh")
        self.custom_gen_btn.setObjectName("accentButton")
        self.custom_gen_btn.clicked.connect(self._generate_custom_ai)
        custom_layout.addWidget(self.custom_gen_btn)

        scroll_custom.setWidget(custom_container)
        left_layout.addWidget(scroll_custom, 1)

        # History List
        version_title = QLabel("📜 Phiên Bản Kịch Bản Đã Lưu")
        version_title.setStyleSheet(f"font-weight: bold; color: {COLORS['text_muted']};")
        left_layout.addWidget(version_title)

        self.version_list = QListWidget()
        self.version_list.setMaximumHeight(100)
        self.version_list.itemClicked.connect(self._on_version_selected)
        left_layout.addWidget(self.version_list)

        splitter.addWidget(left_card)

        # =========================================================================
        # RIGHT PANEL — SCRIPT EDITOR & AI QUICK TOOLS
        # =========================================================================
        right_card = QFrame()
        right_card.setObjectName("cardFrame")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(10)

        # Top Bar: Title & Primary Actions
        editor_top = QHBoxLayout()
        self.script_title_edit = QLineEdit("Kịch bản Livestream mới")
        self.script_title_edit.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {COLORS['text_primary']}; "
            "background: transparent; border: none; border-bottom: 1px solid #333;"
        )
        editor_top.addWidget(self.script_title_edit, 1)

        self.new_script_btn = QPushButton("➕ Viết mới")
        self.new_script_btn.setToolTip("Xóa trắng editor để tự viết kịch bản tay từ đầu")
        self.new_script_btn.clicked.connect(self._new_manual_script)
        editor_top.addWidget(self.new_script_btn)

        self.copy_btn = QPushButton("📋 Sao chép")
        self.copy_btn.setToolTip("Sao chép toàn bộ kịch bản vào bộ nhớ tạm")
        self.copy_btn.clicked.connect(self._copy_to_clipboard)
        editor_top.addWidget(self.copy_btn)

        self.save_btn = QPushButton("💾 Lưu kịch bản")
        self.save_btn.setObjectName("primaryButton")
        self.save_btn.clicked.connect(self._save_script)
        editor_top.addWidget(self.save_btn)

        self.tts_btn = QPushButton("🔊 Tạo âm thanh TTS")
        self.tts_btn.setObjectName("accentButton")
        self.tts_btn.clicked.connect(self._generate_tts_audio)
        editor_top.addWidget(self.tts_btn)

        self.render_btn = QPushButton("🎬 Dựng Video (P3)")
        self.render_btn.setObjectName("primaryButton")
        self.render_btn.setStyleSheet(f"background-color: {COLORS['accent_primary']}; font-weight: bold;")
        self.render_btn.setToolTip("Dựng video review 1080×1920 hoàn chỉnh kết hợp bối cảnh studio, sản phẩm và banner giá")
        self.render_btn.clicked.connect(self._start_video_render)
        editor_top.addWidget(self.render_btn)

        right_layout.addLayout(editor_top)

        # AI Quick Rewrite Toolbar
        ai_tools_bar = QHBoxLayout()
        ai_tools_bar.setSpacing(8)

        ai_tools_label = QLabel("✨ AI Tinh Chỉnh Nhanh:")
        ai_tools_label.setStyleSheet(f"color: {COLORS['accent_secondary']}; font-weight: bold; font-size: 12px;")
        ai_tools_bar.addWidget(ai_tools_label)

        self.tool_energetic_btn = QPushButton("🔥 Tăng Năng Lượng / FOMO")
        self.tool_energetic_btn.setStyleSheet("font-size: 11px; padding: 4px 10px;")
        self.tool_energetic_btn.clicked.connect(lambda: self._improve_script("energetic"))
        ai_tools_bar.addWidget(self.tool_energetic_btn)

        self.tool_shorten_btn = QPushButton("⚡ Rút Ngắn Súc Tích")
        self.tool_shorten_btn.setStyleSheet("font-size: 11px; padding: 4px 10px;")
        self.tool_shorten_btn.clicked.connect(lambda: self._improve_script("shorten"))
        ai_tools_bar.addWidget(self.tool_shorten_btn)

        self.tool_interactive_btn = QPushButton("💬 Thêm Tương Tác / Comment")
        self.tool_interactive_btn.setStyleSheet("font-size: 11px; padding: 4px 10px;")
        self.tool_interactive_btn.clicked.connect(lambda: self._improve_script("interactive"))
        ai_tools_bar.addWidget(self.tool_interactive_btn)

        self.tool_custom_btn = QPushButton("✏️ Yêu Cầu Riêng...")
        self.tool_custom_btn.setStyleSheet("font-size: 11px; padding: 4px 10px;")
        self.tool_custom_btn.clicked.connect(self._improve_script_custom)
        ai_tools_bar.addWidget(self.tool_custom_btn)

        ai_tools_bar.addStretch()
        right_layout.addLayout(ai_tools_bar)

        # Segmented Editor with Tabs
        self.editor_tabs = QTabWidget()

        # Tab 1: Segment Table (Phân đoạn Review)
        tab_segments = QWidget()
        tab_seg_layout = QVBoxLayout(tab_segments)
        tab_seg_layout.setContentsMargins(4, 8, 4, 4)
        tab_seg_layout.setSpacing(6)

        table_toolbar = QHBoxLayout()
        table_hint = QLabel("💡 Mỗi đoạn kịch bản tương ứng 1 góc máy (shot) và 1 cử chỉ người mẫu (cue).")
        table_hint.setObjectName("hintLabel")
        table_toolbar.addWidget(table_hint)
        table_toolbar.addStretch()

        self.add_seg_btn = QPushButton("➕ Thêm đoạn")
        self.add_seg_btn.clicked.connect(self._add_segment_row)
        table_toolbar.addWidget(self.add_seg_btn)

        self.del_seg_btn = QPushButton("➖ Xóa đoạn")
        self.del_seg_btn.clicked.connect(self._remove_selected_segment_row)
        table_toolbar.addWidget(self.del_seg_btn)

        tab_seg_layout.addLayout(table_toolbar)

        self.segment_table = QTableWidget()
        self.segment_table.setColumnCount(5)
        self.segment_table.setHorizontalHeaderLabels([
            "#",
            "Lời thoại streamer (TTS)",
            "Cử chỉ mẫu (Cue)",
            "Góc máy quay (Shot)",
            "Giây",
        ])
        self.segment_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.segment_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.segment_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.segment_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.segment_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        tab_seg_layout.addWidget(self.segment_table, 1)

        self.editor_tabs.addTab(tab_segments, "📋 Phân Đoạn Review (Cue & Shot)")

        # Tab 2: Plain Text Editor
        self.script_edit = QTextEdit()
        self.script_edit.setPlaceholderText(
            "Nội dung kịch bản livestream sẽ hiển thị tại đây...\n"
            "Các cách tạo kịch bản siêu nhanh:\n"
            "1. Dán đường link sản phẩm ở cột bên trái và bấm '⚡ 1-Click: Trích Xuất & Tự Sinh Kịch Bản Ngay'\n"
            "2. Hoặc chọn sản phẩm trong kho và bấm '✨ AI Tự Động Sinh Kịch Bản (One-Click)'\n"
            "3. Hoặc bấm '📥 Trích Xuất Để Tùy Chỉnh' để xem và chỉnh sửa thông số theo ý mình trước khi tạo!"
        )
        self.script_edit.setStyleSheet(
            f"font-size: 14px; line-height: 1.6; background-color: {COLORS['bg_darkest']}; "
            f"color: {COLORS['text_primary']}; border: 1px solid {COLORS['border']}; border-radius: 6px; padding: 10px;"
        )
        self.script_edit.textChanged.connect(self._update_script_stats)
        self.editor_tabs.addTab(self.script_edit, "📝 Toàn Bộ Lời Thoại")

        self.editor_tabs.currentChanged.connect(self._on_editor_tab_changed)
        right_layout.addWidget(self.editor_tabs, 1)

        # Footer Status & Stats
        footer_layout = QHBoxLayout()
        self.stats_label = QLabel("📊 0 từ (~ 0.0 phút nói)")
        self.stats_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px; font-weight: bold;")
        footer_layout.addWidget(self.stats_label)
        footer_layout.addStretch()

        self.audio_status_label = QLabel("Trạng thái âm thanh: Chưa có audio được tạo.")
        self.audio_status_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px;")
        footer_layout.addWidget(self.audio_status_label)

        right_layout.addLayout(footer_layout)

        splitter.addWidget(right_card)
        splitter.setSizes([430, 670])

        main_layout.addWidget(splitter)

    # -------------------------------------------------------------------------
    # PRODUCT HANDLING & LINK EXTRACTION
    # -------------------------------------------------------------------------
    def _paste_clipboard_to_url(self) -> None:
        text = QGuiApplication.clipboard().text().strip()
        if text:
            self.url_input.setText(text)

    def _start_url_extraction(self, auto_generate: bool) -> None:
        """Extract product parameters from URL, then either auto-generate script or fill custom form."""
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng dán đường link sản phẩm trước!")
            self.url_input.setFocus()
            return

        try:
            provider = get_llm_provider(self.settings_service)
        except Exception as e:
            QMessageBox.warning(self, "Lỗi Cấu Hình AI", str(e))
            return

        self._set_generating_ui_state(True, "⏳ Đang quét link & trích xuất thông số sản phẩm...")

        self.extract_worker = ExtractUrlWorker(
            extractor=self.extractor_service,
            provider=provider,
            url=url,
        )
        self.extract_worker.finished.connect(
            lambda data, error: self._on_url_extracted(data, error, auto_generate)
        )
        self.extract_worker.start()

    def _on_url_extracted(self, data: dict, error: str, auto_generate: bool) -> None:
        if error or not data or not data.get("name"):
            self._set_generating_ui_state(False, "")
            QMessageBox.warning(self, "Lỗi Trích Xuất Link", f"Không thể lấy thông tin từ link: {error}")
            return

        self.extracted_product_data = data

        # Update preview box in Tab 0
        name = data.get("name", "Sản phẩm")
        price = data.get("price", 0)
        promo = data.get("promo_price", 0)
        category = data.get("category", "")
        features = data.get("verified_features", "")

        price_str = f"Giá: {price:,.0f} đ"
        if promo and promo < price:
            price_str += f" -> <b>KM: {promo:,.0f} đ</b>"

        self.url_preview_title.setText(f"✅ {name}")
        self.url_preview_details.setText(
            f"<b>Danh mục:</b> {category} | <b>{price_str}</b><br>"
            f"<b>Điểm nổi bật:</b> {features[:120]}..."
        )

        # Auto-save to product repository if checkbox checked
        if self.auto_save_product_cb.isChecked():
            try:
                saved_prod = self.product_repo.create(data)
                self.current_product = saved_prod
                self.refresh_products()
            except Exception as e:
                logger.warning(f"Không thể tự động lưu sản phẩm vào kho: {e}")

        # Pre-fill custom form inputs for convenience
        if features:
            self.highlights_input.setText(features.replace("\n", "; ")[:150])
        if promo and promo > 0:
            self.deal_input.setText(f"Giá sốc chỉ {promo:,.0f} đ (Giá gốc {price:,.0f} đ)")
        if data.get("target_audience"):
            self.audience_input.setText(data["target_audience"])
        if data.get("description"):
            self.custom_prompt_input.setPlainText(data["description"][:200])

        if auto_generate:
            # Immediately continue to auto generate script
            self.status_label.setText("⏳ Đã lấy xong thông số! AI đang viết kịch bản livestream...")
            self._generate_one_click_ai()
        else:
            self._set_generating_ui_state(False, "")
            QMessageBox.information(
                self,
                "Trích Xuất Thành Công",
                f"🎉 Đã lấy xong thông số của '{name}'!\n"
                "Các thông tin đã được tự động điền vào cấu hình. Bạn có thể tùy chỉnh thêm rồi bấm 'Tạo Kịch Bản'."
            )

    def refresh_products(self) -> None:
        """Reload products list from database."""
        try:
            self.script_repo.session.expire_all()
        except Exception:  # noqa: BLE001
            pass
        self.product_combo.blockSignals(True)
        self.product_combo.clear()
        self.products = list(self.product_service.list_products())

        if self.products:
            for p in self.products:
                price_str = f"{p.price:,.0f}đ" if p.price else "Chưa có giá"
                self.product_combo.addItem(f"{p.name} ({price_str})", p.id)
            current_id = self.current_product.id if self.current_product else None
            match = next((p for p in self.products if p.id == current_id), None)
            self.current_product = match or self.products[0]
            idx = self.product_combo.findData(self.current_product.id)
            if idx >= 0:
                self.product_combo.setCurrentIndex(idx)
            self._update_prod_quick_info(self.current_product)
            self._load_product_scripts(self.current_product.id)
        else:
            self.current_product = None
            self.prod_quick_info.setText("Kho sản phẩm đang trống. Bạn có thể sang tab '🔗 Dán Link' để tự động lấy từ web!")

        self.product_combo.blockSignals(False)

    def _on_product_changed(self, index: int) -> None:
        if 0 <= index < len(self.products):
            self.current_product = self.products[index]
            self._update_prod_quick_info(self.current_product)
            self._load_product_scripts(self.current_product.id)

    def _update_prod_quick_info(self, p: Product) -> None:
        desc = (p.description or "Không có mô tả")[:90]
        self.prod_quick_info.setText(
            f"<b>Mã:</b> {p.sku or 'N/A'} | <b>Giá:</b> {p.price or 0:,.0f}đ (KM: {p.promo_price or 0:,.0f}đ)<br>"
            f"<b>Mô tả:</b> {desc}..."
        )

    def _load_product_scripts(self, product_id: int) -> None:
        self.version_list.clear()
        scripts = self.script_service.get_scripts_by_product(product_id)
        if scripts:
            self.current_script = scripts[0]
            self._display_script_content(self.current_script.content)
            self.script_title_edit.setText(self.current_script.title)

            for s in scripts:
                date_str = s.created_at.strftime('%H:%M %d/%m') if s.created_at else ''
                item = QListWidgetItem(f"v{s.version} - {s.title[:30]} ({date_str})")
                item.setData(Qt.ItemDataRole.UserRole, s)
                self.version_list.addItem(item)
        else:
            self.current_script = None
            self.script_edit.clear()
            self.script_title_edit.setText(f"Kịch bản Livestream - {self.current_product.name if self.current_product else ''}")

    def _on_version_selected(self, item: QListWidgetItem) -> None:
        script = item.data(Qt.ItemDataRole.UserRole)
        if script:
            self.current_script = script
            self._display_script_content(script.content)
            self.script_title_edit.setText(script.title)

    def _display_script_content(self, raw_content: str) -> None:
        """Parse structured script and populate both segment table and plain text view."""
        structured = parse_and_validate_script_json(raw_content)
        self._populate_segment_table(structured)
        self.script_edit.blockSignals(True)
        self.script_edit.setText(structured.total_text)
        self.script_edit.blockSignals(False)
        self._update_script_stats()

    def _populate_segment_table(self, structured: StructuredScript) -> None:
        self.segment_table.setRowCount(0)
        cues_list = sorted(list(VALID_CUES))
        shots_list = sorted(list(VALID_SHOTS))

        for row_idx, seg in enumerate(structured.segments):
            self.segment_table.insertRow(row_idx)

            # Col 0: #
            num_item = QTableWidgetItem(str(row_idx + 1))
            num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            num_item.setFlags(num_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.segment_table.setItem(row_idx, 0, num_item)

            # Col 1: Text
            text_item = QTableWidgetItem(seg.text)
            self.segment_table.setItem(row_idx, 1, text_item)

            # Col 2: Cue
            cue_combo = QComboBox()
            cue_combo.addItems(cues_list)
            c_idx = cue_combo.findText(seg.cue)
            if c_idx >= 0:
                cue_combo.setCurrentIndex(c_idx)
            self.segment_table.setCellWidget(row_idx, 2, cue_combo)

            # Col 3: Shot
            shot_combo = QComboBox()
            shot_combo.addItems(shots_list)
            s_idx = shot_combo.findText(seg.shot)
            if s_idx >= 0:
                shot_combo.setCurrentIndex(s_idx)
            self.segment_table.setCellWidget(row_idx, 3, shot_combo)

            # Col 4: Duration
            dur_item = QTableWidgetItem(f"{seg.duration_seconds:.1f}s" if seg.duration_seconds else "—")
            dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.segment_table.setItem(row_idx, 4, dur_item)

    def _extract_segments_from_table(self) -> StructuredScript:
        segments: list[ScriptSegment] = []
        for r in range(self.segment_table.rowCount()):
            text_item = self.segment_table.item(r, 1)
            text = text_item.text().strip() if text_item else ""
            if not text:
                continue

            cue_widget = self.segment_table.cellWidget(r, 2)
            cue = cue_widget.currentText() if isinstance(cue_widget, QComboBox) else "IDLE_TALK"

            shot_widget = self.segment_table.cellWidget(r, 3)
            shot = shot_widget.currentText() if isinstance(shot_widget, QComboBox) else "MEDIUM_MODEL"

            segments.append(
                ScriptSegment(
                    text=text,
                    cue=cue,
                    shot=shot,
                )
            )
        return StructuredScript(segments=segments)

    def _add_segment_row(self) -> None:
        r = self.segment_table.rowCount()
        self.segment_table.insertRow(r)
        num_item = QTableWidgetItem(str(r + 1))
        num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        num_item.setFlags(num_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.segment_table.setItem(r, 0, num_item)

        self.segment_table.setItem(r, 1, QTableWidgetItem("Nội dung đoạn giới thiệu mới..."))

        cue_combo = QComboBox()
        cue_combo.addItems(sorted(list(VALID_CUES)))
        self.segment_table.setCellWidget(r, 2, cue_combo)

        shot_combo = QComboBox()
        shot_combo.addItems(sorted(list(VALID_SHOTS)))
        self.segment_table.setCellWidget(r, 3, shot_combo)

        dur_item = QTableWidgetItem("—")
        dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.segment_table.setItem(r, 4, dur_item)

    def _remove_selected_segment_row(self) -> None:
        row = self.segment_table.currentRow()
        if row >= 0:
            self.segment_table.removeRow(row)

    def _on_editor_tab_changed(self, index: int) -> None:
        if index == 1:
            # Switched to plain text: sync from table
            structured = self._extract_segments_from_table()
            if structured.segments:
                self.script_edit.blockSignals(True)
                self.script_edit.setText(structured.total_text)
                self.script_edit.blockSignals(False)
                self._update_script_stats()
        elif index == 0:
            # Switched to table: if table is empty but plain text exists, populate table
            if self.segment_table.rowCount() == 0 and self.script_edit.toPlainText().strip():
                structured = parse_and_validate_script_json(self.script_edit.toPlainText().strip())
                self._populate_segment_table(structured)

    def _update_script_stats(self) -> None:
        text = self.script_edit.toPlainText().strip()
        words = len(text.split()) if text else 0
        minutes = words / 150.0
        self.stats_label.setText(f"📊 {words} từ (~ {minutes:.1f} phút nói)")

    # -------------------------------------------------------------------------
    # GENERATION LOGIC: AUTO (ONE-CLICK) & CUSTOM
    # -------------------------------------------------------------------------
    def _get_active_product_data(self) -> tuple[Optional[Product], dict]:
        """Return (Product or None, product_dict_info) based on the active tab."""
        tab_idx = self.source_tabs.currentIndex()

        # Tab 0: Link URL
        if tab_idx == 0:
            if self.extracted_product_data:
                return self.current_product, self.extracted_product_data
            url = self.url_input.text().strip()
            if url:
                return None, {"name": f"Sản phẩm từ Link", "product_url": url}
            return None, {}

        # Tab 1: Kho sản phẩm
        elif tab_idx == 1:
            if not self.current_product:
                return None, {}
            return self.current_product, {
                "name": self.current_product.name,
                "price": self.current_product.price or 0,
                "promo_price": self.current_product.promo_price or 0,
                "description": self.current_product.description or "",
                "verified_features": self.current_product.verified_features or "",
                "ingredients": self.current_product.ingredients or "",
            }

        # Tab 2: Nhập thủ công
        else:
            name = self.custom_name_input.text().strip()
            if not name:
                return None, {}
            return None, {
                "name": name,
                "price": self.custom_price_spin.value(),
                "promo_price": self.custom_promo_spin.value(),
                "description": self.custom_desc_input.toPlainText().strip(),
                "verified_features": "",
                "ingredients": "",
            }

    def _generate_one_click_ai(self) -> None:
        """One-click full AI generation without requiring manual parameter setup."""
        product, info = self._get_active_product_data()
        if not info.get("name"):
            QMessageBox.warning(
                self,
                "Cảnh báo",
                "Vui lòng dán Link sản phẩm, hoặc chọn sản phẩm trong kho trước khi tạo kịch bản!"
            )
            return

        try:
            provider = get_llm_provider(self.settings_service)
        except Exception as e:
            QMessageBox.warning(self, "Lỗi Cấu Hình AI", str(e))
            return

        self._set_generating_ui_state(True, "⏳ AI đang tự động phân tích và viết kịch bản livestream...")

        self.worker = ScriptWorker(
            script_service=self.script_service,
            llm_provider=provider,
            product=product,
            product_name=info.get("name", ""),
            price=info.get("price"),
            promo_price=info.get("promo_price"),
            description=info.get("description", ""),
            verified_features=info.get("verified_features", ""),
            ingredients=info.get("ingredients", ""),
            script_type="full_live",
            style="energetic",
            target_audience="Tất cả khách hàng livestream",
            speaking_speed="normal",
            honorific=self.honorific_input.text().strip() or "Anh/Chị",
            promotions_deal="",
            key_highlights="",
            cta_text="",
            custom_instructions="Tự động phân tích sản phẩm, tạo deal sốc hấp dẫn và chốt đơn mạnh mẽ.",
            duration_minutes=2.0,
        )
        self.worker.finished.connect(self._on_script_generated)
        self.worker.start()

    def _generate_custom_ai(self) -> None:
        """Generate AI script using user-configured custom parameters."""
        product, info = self._get_active_product_data()
        if not info.get("name"):
            QMessageBox.warning(
                self,
                "Cảnh báo",
                "Vui lòng dán Link sản phẩm, hoặc chọn sản phẩm trong kho trước khi tạo kịch bản!"
            )
            return

        try:
            provider = get_llm_provider(self.settings_service)
        except Exception as e:
            QMessageBox.warning(self, "Lỗi Cấu Hình AI", str(e))
            return

        self._set_generating_ui_state(True, "⏳ AI đang viết kịch bản theo cấu hình tùy chỉnh của bạn...")

        self.worker = ScriptWorker(
            script_service=self.script_service,
            llm_provider=provider,
            product=product,
            product_name=info.get("name", ""),
            price=info.get("price"),
            promo_price=info.get("promo_price"),
            description=info.get("description", ""),
            verified_features=info.get("verified_features", ""),
            ingredients=info.get("ingredients", ""),
            script_type=self.type_combo.currentData(),
            style=self.style_combo.currentData(),
            target_audience=self.audience_input.text().strip(),
            speaking_speed="normal",
            honorific=self.honorific_input.text().strip() or "Anh/Chị",
            promotions_deal=self.deal_input.text().strip(),
            key_highlights=self.highlights_input.text().strip(),
            cta_text=self.cta_input.text().strip(),
            custom_instructions=self.custom_prompt_input.toPlainText().strip(),
            duration_minutes=self.duration_spin.value(),
        )
        self.worker.finished.connect(self._on_script_generated)
        self.worker.start()

    def _suggest_configuration(self) -> None:
        """Ask AI to suggest custom configuration fields."""
        _, info = self._get_active_product_data()
        if not info.get("name"):
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn hoặc nhập link/tên sản phẩm trước khi gợi ý!")
            return

        try:
            provider = get_llm_provider(self.settings_service)
        except Exception as e:
            QMessageBox.warning(self, "Lỗi Cấu Hình AI", str(e))
            return

        self.suggest_btn.setEnabled(False)
        self.status_label.setText("⏳ AI đang phân tích sản phẩm để gợi ý cấu hình...")

        self.suggest_worker = SuggestConfigWorker(
            script_service=self.script_service,
            llm_provider=provider,
            product_info=info,
        )
        self.suggest_worker.finished.connect(self._on_config_suggested)
        self.suggest_worker.start()

    def _on_config_suggested(self, data: dict, error: str) -> None:
        self.suggest_btn.setEnabled(True)
        self.status_label.setText("")

        if error or not data:
            QMessageBox.warning(self, "Thông báo", f"Không thể lấy gợi ý từ AI: {error}")
            return

        if data.get("target_audience"):
            self.audience_input.setText(data["target_audience"])
        if data.get("key_highlights"):
            self.highlights_input.setText(data["key_highlights"])
        if data.get("promotions_deal"):
            self.deal_input.setText(data["promotions_deal"])
        if data.get("cta_text"):
            self.cta_input.setText(data["cta_text"])

        rec_style = data.get("recommended_style", "")
        for i in range(self.style_combo.count()):
            if self.style_combo.itemData(i) == rec_style:
                self.style_combo.setCurrentIndex(i)
                break

        rec_type = data.get("recommended_type", "")
        for i in range(self.type_combo.count()):
            if self.type_combo.itemData(i) == rec_type:
                self.type_combo.setCurrentIndex(i)
                break

        QMessageBox.information(
            self,
            "Gợi Ý Thành Công",
            "✨ AI đã tự động điền gợi ý điểm nhấn USP, deal khuyến mãi và CTA!\n"
            "Bạn có thể chỉnh sửa lại theo ý muốn rồi bấm 'Tạo Kịch Bản Theo Cấu Hình'."
        )

    def _on_script_generated(self, script: Optional[Script], error: str) -> None:
        self._set_generating_ui_state(False, "")

        if error or not script:
            QMessageBox.warning(self, "Lỗi Tạo Kịch Bản", f"Lỗi tạo kịch bản AI: {error}")
            return

        self.current_script = script
        self.script_title_edit.setText(script.title)
        self._display_script_content(script.content)

        self.refresh_products()
        if script.product_id:
            self._load_product_scripts(script.product_id)

        QMessageBox.information(self, "Thành công", "🎉 Đã tạo kịch bản livestream thành công!")

    def _set_generating_ui_state(self, is_busy: bool, message: str) -> None:
        self.auto_gen_btn.setEnabled(not is_busy)
        self.custom_gen_btn.setEnabled(not is_busy)
        self.extract_and_gen_btn.setEnabled(not is_busy)
        self.extract_only_btn.setEnabled(not is_busy)
        self.suggest_btn.setEnabled(not is_busy)
        self.status_label.setText(message if is_busy else "")

    # -------------------------------------------------------------------------
    # AI QUICK REWRITE / POLISH
    # -------------------------------------------------------------------------
    def _improve_script(self, action: str, custom_note: Optional[str] = None) -> None:
        content = self.script_edit.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "Cảnh báo", "Editor đang trống, không có kịch bản để cải thiện!")
            return

        try:
            provider = get_llm_provider(self.settings_service)
        except Exception as e:
            QMessageBox.warning(self, "Lỗi Cấu Hình AI", str(e))
            return

        self.status_label.setText("⏳ AI đang viết lại / cải thiện kịch bản...")
        self.script_edit.setReadOnly(True)

        self.improve_worker = ImproveScriptWorker(
            script_service=self.script_service,
            llm_provider=provider,
            content=content,
            action=action,
            custom_note=custom_note,
        )
        self.improve_worker.finished.connect(self._on_script_improved)
        self.improve_worker.start()

    def _improve_script_custom(self) -> None:
        note, ok = QInputDialog.getText(
            self,
            "Yêu Cầu Tinh Chỉnh AI",
            "Nhập yêu cầu bạn muốn AI sửa đổi trong kịch bản (VD: chèn thêm câu đùa hài hước, nhấn mạnh giảm giá 50k...):",
        )
        if ok and note.strip():
            self._improve_script("custom", custom_note=note.strip())

    def _on_script_improved(self, new_text: str, error: str) -> None:
        self.script_edit.setReadOnly(False)
        self.status_label.setText("")

        if error or not new_text:
            QMessageBox.warning(self, "Thông báo", f"Lỗi khi viết lại kịch bản: {error}")
            return

        self._display_script_content(new_text)
        QMessageBox.information(self, "Thành công", "✨ Đã tinh chỉnh và viết lại kịch bản thành công!")

    # -------------------------------------------------------------------------
    # SAVE & AUDIO & CLIPBOARD
    # -------------------------------------------------------------------------
    def _new_manual_script(self) -> None:
        self.current_script = None
        self.script_title_edit.setText("Kịch bản mới (Tự viết tay)")
        self.segment_table.setRowCount(0)
        self.script_edit.clear()
        self._add_segment_row()
        self.script_edit.setFocus()

    def _copy_to_clipboard(self) -> None:
        content = self.script_edit.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "Thông báo", "Không có nội dung để sao chép!")
            return
        QGuiApplication.clipboard().setText(content)
        QMessageBox.information(self, "Đã sao chép", "Đã sao chép kịch bản vào bộ nhớ tạm (Clipboard)!")

    def _save_script(self) -> None:
        if self.segment_table.rowCount() > 0:
            structured = self._extract_segments_from_table()
            content = structured.to_json()
        else:
            content = self.script_edit.toPlainText().strip()

        if not content:
            QMessageBox.warning(self, "Cảnh báo", "Nội dung kịch bản không được để trống!")
            return

        title = self.script_title_edit.text().strip() or "Kịch bản Livestream"

        if self.current_script:
            updated = self.script_service.update_script(
                script_id=self.current_script.id,
                content=content,
                title=title,
                note="Chỉnh sửa và lưu bởi người dùng",
            )
            if updated:
                self.current_script = updated
                if updated.product_id:
                    self._load_product_scripts(updated.product_id)
                QMessageBox.information(self, "Thành công", f"Đã lưu kịch bản (Phiên bản v{updated.version})!")
        elif self.current_product:
            new_script = self.script_repo.create(
                product_id=self.current_product.id,
                title=title,
                content=content,
            )
            self.current_script = new_script
            self._load_product_scripts(self.current_product.id)
            QMessageBox.information(self, "Thành công", "Đã tạo kịch bản mới trong cơ sở dữ liệu!")
        else:
            _, info = self._get_active_product_data()
            name = info.get("name") or "Sản phẩm tùy chỉnh"
            from app.database.repositories.product_repo import ProductRepository
            p_repo = ProductRepository(self.script_repo.session)
            new_p = p_repo.create({
                "name": name,
                "price": info.get("price", 0),
                "promo_price": info.get("promo_price", 0),
                "description": info.get("description", ""),
                "category": info.get("category", "Kịch bản tùy chỉnh"),
                "product_url": info.get("product_url", ""),
            })
            new_script = self.script_repo.create(
                product_id=new_p.id,
                title=title,
                content=content,
            )
            self.current_script = new_script
            self.refresh_products()
            QMessageBox.information(self, "Thành công", "Đã lưu sản phẩm và kịch bản mới thành công!")

    def _generate_tts_audio(self) -> None:
        if not self.current_script:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn hoặc lưu kịch bản trước khi tạo audio!")
            return

        structured = self._extract_segments_from_table() if self.segment_table.rowCount() > 0 else None
        content = self.script_edit.toPlainText().strip()
        if not content and (not structured or not structured.segments):
            QMessageBox.warning(self, "Cảnh báo", "Nội dung kịch bản không được để trống!")
            return

        self.tts_btn.setEnabled(False)
        self.audio_status_label.setText("⏳ Đang tạo file âm thanh TTS...")

        self.tts_worker = TTSWorker(
            voice_service=self.voice_service,
            script_id=self.current_script.id,
            text=content,
            structured_script=structured,
        )
        self.tts_worker.finished.connect(self._on_tts_generated)
        self.tts_worker.start()

    def _on_tts_generated(self, audio: Optional[object], updated_struct: Optional[StructuredScript], error: str) -> None:
        self.tts_btn.setEnabled(True)
        if error or not audio:
            self.audio_status_label.setText(f"❌ Lỗi tạo audio: {error}")
            QMessageBox.warning(self, "Lỗi TTS", f"Không thể tạo âm thanh: {error}")
        else:
            if updated_struct:
                self._populate_segment_table(updated_struct)
                try:
                    self.script_service.update_script(
                        script_id=self.current_script.id,
                        content=updated_struct.to_json(),
                        title=self.script_title_edit.text().strip() or self.current_script.title,
                        note="Cập nhật thời lượng âm thanh thật từng segment",
                    )
                except Exception as e:
                    logger.debug("Lỗi lưu lại kịch bản sau khi TTS: {}", e)

            path = getattr(audio, "file_path", "")
            dur = getattr(audio, "duration_seconds", None)
            self.audio_status_label.setText(f"✅ Audio sẵn sàng ({dur:.1f}s): {path}" if dur else f"✅ Audio sẵn sàng: {path}")
            from app.services.activity_log_service import log_activity

            log_activity("tts", "Tạo audio TTS cho kịch bản", self.script_title_edit.text()[:100])
            QMessageBox.information(self, "Thành công", f"Đã tạo audio TTS. Sản phẩm này đã sẵn sàng để đưa vào playlist.\n\n{path}")

    # -------------------------------------------------------------------------
    # VIDEO COMPOSITING (P3)
    # -------------------------------------------------------------------------
    def _start_video_render(self) -> None:
        if not self.current_script:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn hoặc lưu kịch bản trước khi dựng video!")
            return

        latest_audio = self.voice_service.get_latest_audio_for_script(self.current_script.id)
        if not latest_audio or not latest_audio.file_path or not os.path.exists(latest_audio.file_path):
            QMessageBox.warning(
                self,
                "Chưa có file âm thanh",
                "Kịch bản này chưa có file âm thanh TTS.\n\nVui lòng bấm '🔊 Tạo âm thanh TTS' trước khi dựng video!",
            )
            return

        product = self.current_product
        structured = parse_and_validate_script_json(self.current_script.content)
        scene_set = SceneSetLibrary().get_set_or_default(structured.set_id)

        # Output video path
        output_dir = Path("storage/rendered_videos")
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_title = "".join(c for c in (self.current_script.title or "review") if c.isalnum() or c in ("-", "_")).strip()
        out_mp4 = output_dir / f"review_prod_{product.id if product else 'custom'}_{safe_title[:20]}.mp4"

        # Generate banner overlay if product has price
        banner_path = None
        if product and (product.price or product.promo_price):
            from app.render.banner import BannerGenerator
            banner_gen = BannerGenerator()
            b_path = output_dir / f"banner_{product.id}.png"
            try:
                banner_gen.generate_product_banner(
                    output_path=b_path,
                    title=product.name,
                    price=product.price or 0,
                    promo_price=product.promo_price,
                )
                banner_path = str(b_path)
            except Exception as e:
                logger.debug("Không thể tạo banner: {}", e)

        # Plan review rhythm cuts
        from app.render.shot_planner import ShotPlanner
        has_closeup = bool(product and product.video_path and os.path.exists(product.video_path))
        shot_plan = ShotPlanner().plan_shots(structured, has_closeup_asset=has_closeup)

        # Find approved presenter clip if avatar assigned
        presenter_clip = None
        if product and product.avatar_id:
            from app.presenter.clip_library import PresenterClipLibrary
            p_clip = PresenterClipLibrary().get_clip_for_cue(product.avatar_id, "IDLE_TALK", product_id=product.id, require_approved=False)
            if p_clip and Path(p_clip.file_path).exists():
                presenter_clip = p_clip.file_path

        queue = RenderQueue.get_instance()
        job = queue.create_job(
            output_mp4=out_mp4,
            scene_set=scene_set,
            audio_path=latest_audio.file_path,
            product_id=product.id if product else None,
            product_name=product.name if product else "Sản phẩm",
            product_image=product.image_path if (product and product.image_path) else None,
            closeup_media=product.video_path if (product and product.video_path) else None,
            banner_path=banner_path,
            presenter_clip=presenter_clip,
            shot_plan=shot_plan,
            duration_seconds=latest_audio.duration_seconds,
        )

        self.render_btn.setEnabled(False)
        self.audio_status_label.setText("⏳ Đang render video review 1080×1920: 10% (Chuẩn bị bối cảnh...)")

        self.render_worker = RenderWorker(job)
        self.render_worker.progress_updated.connect(self._on_render_progress)
        self.render_worker.job_finished.connect(self._on_render_finished)
        self.render_worker.start()

    def _on_render_progress(self, job_id: str, percent: int, msg: str) -> None:
        self.audio_status_label.setText(f"⏳ Đang render video review: {percent}% ({msg})")

    def _on_render_finished(self, job: Any, success: bool, error: str) -> None:
        self.render_btn.setEnabled(True)
        if success:
            out_file = job.output_mp4
            self.audio_status_label.setText(f"🎬 Video review đã render xong: {out_file}")
            if self.current_product and not self.current_product.video_path:
                try:
                    self.product_repo.update(self.current_product.id, {"video_path": str(out_file)})
                    self.current_product.video_path = str(out_file)
                except Exception as e:
                    logger.debug("Không thể tự gán video_path vào sản phẩm: {}", e)

            from app.services.activity_log_service import log_activity
            log_activity("render", "Render video review sản phẩm 1080x1920", getattr(job, "product_name", ""))
            QMessageBox.information(
                self,
                "Dựng Video Thành Công",
                f"🎉 Đã render xong video review chuẩn 1080×1920 30fps!\n\n"
                f"Bối cảnh: {job.scene_set.name if job.scene_set else 'Studio'}\n"
                f"Đường dẫn: {out_file}\n\n"
                "Video đã sẵn sàng để phát sóng trên TikTok/Shopee LIVE!",
            )
        else:
            self.audio_status_label.setText(f"❌ Lỗi render video: {error}")
            QMessageBox.warning(self, "Lỗi Render Video", f"Không thể render video: {error}")

