"""Product AI Showcase & Presenter Dialog — interactive virtual model presentation for products."""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from loguru import logger

from app.database.models.avatar import AIAvatar
from app.database.models.product import Product
from app.database.models.script import Script
from app.database.session import get_session
from app.integrations.llm.factory import get_llm_provider
from app.integrations.obs.client import OBSClient
from app.services.avatar_service import AvatarService
from app.services.media_service import MediaService
from app.services.product_service import ProductService
from app.services.script_service import ScriptService
from app.services.settings_service import SettingsService
from app.services.voice_service import VoiceService
from app.ui.styles.dark_theme import COLORS


class ScriptGenerateWorker(QThread):
    """Worker thread for AI to write an engaging product pitch."""

    finished = Signal(str, str)

    def __init__(
        self,
        script_service: ScriptService,
        product: Product,
        style: str,
        script_type: str,
    ) -> None:
        super().__init__()
        self.script_service = script_service
        self.product = product
        self.style = style
        self.script_type = script_type

    def run(self) -> None:
        try:
            provider = get_llm_provider()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            sys_prompt, user_prompt = self.script_service.build_script_prompt(
                product=self.product,
                script_type=self.script_type,
                style=self.style,
                duration_minutes=1.0,
            )
            content = loop.run_until_complete(
                self.script_service.generate_script_text(
                    system_prompt=sys_prompt,
                    user_prompt=user_prompt,
                    provider=provider,
                )
            )
            loop.close()
            self.finished.emit(content, "")
        except Exception as e:
            logger.exception("Lỗi khi sinh kịch bản giới thiệu")
            self.finished.emit("", str(e))


class SpeechWorker(QThread):
    """Worker thread to generate TTS audio for the pitch."""

    finished = Signal(str, float, str)

    def __init__(
        self,
        avatar_service: AvatarService,
        text: str,
        voice_id: str,
        speed: float,
    ) -> None:
        super().__init__()
        self.avatar_service = avatar_service
        self.text = text
        self.voice_id = voice_id
        self.speed = speed

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            audio_path, duration = loop.run_until_complete(
                self.avatar_service.generate_speech_for_pitch(
                    text=self.text,
                    voice_id=self.voice_id,
                    speed=self.speed,
                )
            )
            loop.close()
            self.finished.emit(audio_path or "", duration, "")
        except Exception as e:
            logger.exception("Lỗi sinh âm thanh giọng đọc")
            self.finished.emit("", 0.0, str(e))


class VideoGenWorker(QThread):
    """Worker thread for generating presenter lip-sync video."""

    finished = Signal(str, str)

    def __init__(
        self,
        avatar_service: AvatarService,
        avatar: AIAvatar,
        audio_path: str,
        script_text: str,
    ) -> None:
        super().__init__()
        self.avatar_service = avatar_service
        self.avatar = avatar
        self.audio_path = audio_path
        self.script_text = script_text

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            vid_path, msg = loop.run_until_complete(
                self.avatar_service.generate_lip_sync_video(
                    avatar=self.avatar,
                    audio_path=self.audio_path,
                    script_text=self.script_text,
                )
            )
            loop.close()
            self.finished.emit(vid_path or "", msg)
        except Exception as e:
            logger.exception("Lỗi khi tạo video người mẫu AI")
            self.finished.emit("", str(e))


class ProductShowcaseDialog(QDialog):
    """Modal dialog showcasing a product with an interactive AI Virtual Presenter."""

    def __init__(
        self,
        product_id: int,
        parent: Optional[QWidget] = None,
        obs_client: Optional[OBSClient] = None,
    ) -> None:
        super().__init__(parent)
        self.product_id = product_id
        self.session = get_session()
        self.product_service = ProductService()
        self.avatar_service = AvatarService(self.session)
        from app.database.repositories.voice_repo import VoiceRepository
        self.voice_service = VoiceService(VoiceRepository(self.session))
        from app.database.repositories.script_repo import ScriptRepository
        self.script_service = ScriptService(ScriptRepository(self.session))
        self.settings_service = SettingsService(self.session)
        self.obs_client = obs_client or OBSClient()

        self.product: Optional[Product] = self.product_service.get_product(product_id)
        self.avatars: list[AIAvatar] = list(self.avatar_service.get_avatars())
        self.current_avatar: Optional[AIAvatar] = self.avatars[0] if self.avatars else None
        self.current_audio_path: Optional[str] = None
        self.audio_duration: float = 0.0
        self.is_playing: bool = False
        self.current_play_time: float = 0.0

        self._init_player()
        self._setup_ui()
        self._load_product_data()

    def _init_player(self) -> None:
        self._player = None
        self._audio_output = None
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            self._player = QMediaPlayer(self)
            self._audio_output = QAudioOutput(self)
            self._player.setAudioOutput(self._audio_output)
            self._player.mediaStatusChanged.connect(self._on_player_status)
        except Exception as e:
            logger.warning("QtMultimedia unavailable: {}", e)

        self.play_timer = QTimer(self)
        self.play_timer.setInterval(200)
        self.play_timer.timeout.connect(self._update_playback_tick)

    def _setup_ui(self) -> None:
        p_name = self.product.name if self.product else "Sản phẩm"
        self.setWindowTitle(f"💃 Người Mẫu AI Giới Thiệu Sản Phẩm — {p_name}")
        self.resize(1080, 780)
        self.setMinimumSize(960, 680)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        # ----------------- Top Header Bar -----------------
        header_bar = QHBoxLayout()
        header_icon = QLabel("💃")
        header_icon.setStyleSheet("font-size: 28px;")
        header_bar.addWidget(header_icon)

        header_info = QVBoxLayout()
        header_info.setSpacing(2)
        title_label = QLabel("Người Mẫu Ảo Giới Thiệu Sản Phẩm (AI Virtual Presenter)")
        title_label.setObjectName("headerLabel")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        header_info.addWidget(title_label)

        sub_label = QLabel("Trình diễn sản phẩm trực quan, tự động đọc kịch bản bán hàng và xuất video/overlay OBS livestream")
        sub_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px;")
        header_info.addWidget(sub_label)
        header_bar.addLayout(header_info, 1)

        self.live_badge = QLabel("● CHƯA PHÁT")
        self.live_badge.setStyleSheet(
            "background-color: #2D3436; color: #DFE6E9; padding: 6px 14px; "
            "border-radius: 12px; font-weight: bold; font-size: 11px;"
        )
        header_bar.addWidget(self.live_badge)
        main_layout.addLayout(header_bar)

        # ----------------- Configuration Controls Row -----------------
        config_card = QFrame()
        config_card.setObjectName("cardFrame")
        config_layout = QHBoxLayout(config_card)
        config_layout.setContentsMargins(14, 10, 14, 10)
        config_layout.setSpacing(12)

        # Avatar picker
        config_layout.addWidget(QLabel("Người mẫu AI:"))
        self.avatar_combo = QComboBox()
        self.avatar_combo.setMinimumWidth(220)
        for av in self.avatars:
            self.avatar_combo.addItem(f"{av.name} ({av.style})", av.id)
        self.avatar_combo.currentIndexChanged.connect(self._on_avatar_changed)
        config_layout.addWidget(self.avatar_combo)

        # Voice picker
        config_layout.addWidget(QLabel("Giọng đọc:"))
        self.voice_combo = QComboBox()
        self.voice_combo.addItem("Hoài My (Nữ truyền cảm)", "vi-VN-HoaiMyNeural")
        self.voice_combo.addItem("Nam Minh (Nam trầm ấm)", "vi-VN-NamMinhNeural")
        config_layout.addWidget(self.voice_combo)

        # Speed picker
        config_layout.addWidget(QLabel("Tốc độ:"))
        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.7, 1.5)
        self.speed_spin.setSingleStep(0.05)
        self.speed_spin.setValue(1.0)
        self.speed_spin.setSuffix("x")
        config_layout.addWidget(self.speed_spin)

        config_layout.addStretch()

        self.ai_script_btn = QPushButton("✨ AI Viết Bài Nói Nhanh")
        self.ai_script_btn.setObjectName("accentButton")
        self.ai_script_btn.setToolTip("AI tự động soạn lời thoại giới thiệu sản phẩm sắc bén, giật gân chốt đơn")
        self.ai_script_btn.clicked.connect(self._on_generate_ai_pitch)
        config_layout.addWidget(self.ai_script_btn)

        main_layout.addWidget(config_card)

        # ----------------- Center Splitter -----------------
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # LEFT: Live Stage Preview
        stage_card = QFrame()
        stage_card.setObjectName("cardFrame")
        stage_layout = QVBoxLayout(stage_card)
        stage_layout.setContentsMargins(14, 14, 14, 14)
        stage_layout.setSpacing(10)

        stage_header = QHBoxLayout()
        stage_title = QLabel("🎥 Khung Mô Phỏng Livestream")
        stage_title.setStyleSheet("font-weight: 600; font-size: 14px;")
        stage_header.addWidget(stage_title)
        stage_header.addStretch()
        self.status_tag = QLabel("Sẵn sàng")
        self.status_tag.setStyleSheet(f"color: {COLORS['accent_secondary']}; font-size: 11px; font-weight: bold;")
        stage_header.addWidget(self.status_tag)
        stage_layout.addLayout(stage_header)

        # Stage Visual Container
        self.stage_box = QFrame()
        self.stage_box.setStyleSheet(
            f"background-color: #12131A; border: 2px solid {COLORS['border']}; border-radius: 12px;"
        )
        self.stage_box.setMinimumHeight(340)
        stage_box_layout = QHBoxLayout(self.stage_box)
        stage_box_layout.setContentsMargins(12, 12, 12, 12)
        stage_box_layout.setSpacing(12)

        # Product Image Preview
        self.prod_preview = QLabel("Ảnh sản phẩm")
        self.prod_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.prod_preview.setStyleSheet(
            f"background-color: #1E202C; border-radius: 8px; color: {COLORS['text_muted']}; border: 1px dashed {COLORS['border']};"
        )
        self.prod_preview.setFixedSize(170, 260)
        self.prod_preview.setScaledContents(True)
        stage_box_layout.addWidget(self.prod_preview)

        # Virtual Presenter Avatar Visual
        self.avatar_preview = QLabel("Người mẫu AI")
        self.avatar_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_preview.setStyleSheet(
            f"background-color: #1E202C; border-radius: 8px; color: {COLORS['text_muted']}; border: 1px solid {COLORS['accent']};"
        )
        self.avatar_preview.setFixedSize(170, 260)
        self.avatar_preview.setScaledContents(True)
        stage_box_layout.addWidget(self.avatar_preview)

        stage_layout.addWidget(self.stage_box, 1)

        # Playback Controls
        play_ctrl_layout = QHBoxLayout()
        self.play_btn = QPushButton("▶️ Phát Giới Thiệu (Nghe AI Thuyết Trình)")
        self.play_btn.setObjectName("primaryButton")
        self.play_btn.setMinimumHeight(38)
        self.play_btn.clicked.connect(self._toggle_playback)
        play_ctrl_layout.addWidget(self.play_btn, 1)

        self.stop_btn = QPushButton("⏹️ Dừng")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setMinimumHeight(38)
        self.stop_btn.clicked.connect(self._stop_playback)
        play_ctrl_layout.addWidget(self.stop_btn)
        stage_layout.addLayout(play_ctrl_layout)

        # Progress bar & Timecode
        progress_row = QHBoxLayout()
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet(f"color: {COLORS['text_accent']}; font-weight: bold; font-size: 12px;")
        progress_row.addWidget(self.time_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        progress_row.addWidget(self.progress_bar, 1)
        stage_layout.addLayout(progress_row)

        # OBS Settings on Stage
        obs_opts_row = QHBoxLayout()
        self.chroma_check = QCheckBox("Tách nền xanh (Chroma Key)")
        self.chroma_check.setChecked(True)
        obs_opts_row.addWidget(self.chroma_check)

        obs_opts_row.addWidget(QLabel("Vị trí OBS:"))
        self.pos_combo = QComboBox()
        self.pos_combo.addItem("Góc dưới phải (Khuyên dùng)", "bottom_right")
        self.pos_combo.addItem("Góc dưới trái", "bottom_left")
        self.pos_combo.addItem("Chính giữa màn hình", "center")
        self.pos_combo.addItem("Toàn màn hình", "full")
        obs_opts_row.addWidget(self.pos_combo)
        stage_layout.addLayout(obs_opts_row)

        splitter.addWidget(stage_card)

        # RIGHT: Product Info & Teleprompter Pitch
        info_card = QFrame()
        info_card.setObjectName("cardFrame")
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(14, 14, 14, 14)
        info_layout.setSpacing(10)

        # Product Summary Box
        summary_box = QFrame()
        summary_box.setObjectName("innerBox")
        summary_layout = QVBoxLayout(summary_box)
        summary_layout.setContentsMargins(10, 8, 10, 8)
        summary_layout.setSpacing(4)

        self.prod_title = QLabel("Tên sản phẩm")
        self.prod_title.setStyleSheet("font-size: 15px; font-weight: bold;")
        self.prod_title.setWordWrap(True)
        summary_layout.addWidget(self.prod_title)

        price_row = QHBoxLayout()
        self.prod_price = QLabel("Giá: —")
        self.prod_price.setStyleSheet("font-size: 13px; font-weight: 600; color: #2ED573;")
        price_row.addWidget(self.prod_price)

        self.prod_promo = QLabel("")
        self.prod_promo.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {COLORS['accent_danger']};")
        price_row.addWidget(self.prod_promo)
        price_row.addStretch()
        summary_layout.addLayout(price_row)

        self.prod_usp = QLabel("Tính năng: —")
        self.prod_usp.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        self.prod_usp.setWordWrap(True)
        summary_layout.addWidget(self.prod_usp)

        info_layout.addWidget(summary_box)

        # Script Teleprompter
        script_header = QHBoxLayout()
        script_label = QLabel("📝 Lời Thoại Người Mẫu AI (Teleprompter)")
        script_label.setStyleSheet("font-weight: 600;")
        script_header.addWidget(script_label)
        script_header.addStretch()

        self.word_count_label = QLabel("0 từ ~ 0s")
        self.word_count_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        script_header.addWidget(self.word_count_label)
        info_layout.addLayout(script_header)

        self.pitch_text = QTextEdit()
        self.pitch_text.setPlaceholderText(
            "Nhập bài nói giới thiệu sản phẩm của Người Mẫu AI ở đây, hoặc bấm '✨ AI Viết Bài Nói Nhanh' để AI tự động soạn..."
        )
        self.pitch_text.textChanged.connect(self._on_script_text_changed)
        info_layout.addWidget(self.pitch_text, 1)

        # Predefined templates
        tpl_row = QHBoxLayout()
        tpl_label = QLabel("Mẫu nhanh:")
        tpl_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        tpl_row.addWidget(tpl_label)

        btn_hook = QPushButton("🎯 Hook Giật Gân")
        btn_hook.clicked.connect(lambda: self._apply_template("hook"))
        tpl_row.addWidget(btn_hook)

        btn_deal = QPushButton("🔥 Flash Sale")
        btn_deal.clicked.connect(lambda: self._apply_template("deal"))
        tpl_row.addWidget(btn_deal)

        btn_review = QPushButton("💎 Review USP")
        btn_review.clicked.connect(lambda: self._apply_template("usp"))
        tpl_row.addWidget(btn_review)

        tpl_row.addStretch()
        info_layout.addLayout(tpl_row)

        splitter.addWidget(info_card)
        splitter.setSizes([500, 520])
        main_layout.addWidget(splitter, 1)

        # ----------------- Bottom Action Bar -----------------
        action_bar = QHBoxLayout()
        action_bar.setSpacing(10)

        copy_btn = QPushButton("📋 Sao Chép Bài Nói")
        copy_btn.clicked.connect(self._copy_pitch)
        action_bar.addWidget(copy_btn)

        self.video_gen_btn = QPushButton("🎬 Tạo Video Người Mẫu AI (MP4)")
        self.video_gen_btn.setObjectName("accentButton")
        self.video_gen_btn.setToolTip("Xuất video người mẫu AI cử động nói giới thiệu sản phẩm để lưu thư viện hoặc đăng TikTok")
        self.video_gen_btn.clicked.connect(self._on_generate_video)
        action_bar.addWidget(self.video_gen_btn)

        action_bar.addStretch()

        self.obs_live_btn = QPushButton("🔴 Phát Lên OBS Live Ngay")
        self.obs_live_btn.setObjectName("dangerButton")
        self.obs_live_btn.setMinimumHeight(40)
        self.obs_live_btn.setStyleSheet("font-weight: bold; font-size: 13px; padding: 8px 20px;")
        self.obs_live_btn.setToolTip("Đưa Người Mẫu AI và sản phẩm lên màn hình livestream OBS Studio")
        self.obs_live_btn.clicked.connect(self._on_send_to_obs)
        action_bar.addWidget(self.obs_live_btn)

        close_btn = QPushButton("Đóng")
        close_btn.clicked.connect(self.accept)
        action_bar.addWidget(close_btn)

        main_layout.addLayout(action_bar)

    def _load_product_data(self) -> None:
        if not self.product:
            return

        self.prod_title.setText(self.product.name)
        if self.product.price:
            self.prod_price.setText(f"Giá niêm yết: {self.product.price:,.0f}₫")
        else:
            self.prod_price.setText("Giá: Liên hệ")

        if self.product.promo_price:
            self.prod_promo.setText(f"🔥 Ưu đãi LIVE: {self.product.promo_price:,.0f}₫")
        else:
            self.prod_promo.setText("")

        feats = self.product.verified_features or self.product.description or "Chất lượng vượt trội, phân phối chính hãng."
        self.prod_usp.setText(f"Tính năng USP: {feats[:160]}...")

        # Load product image if exists
        if self.product.image_path and os.path.exists(self.product.image_path):
            pix = QPixmap(self.product.image_path)
            self.prod_preview.setPixmap(pix.scaled(170, 260, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
        else:
            self.prod_preview.setText(f"📦\n{self.product.name[:20]}")

        # Update initial avatar preview
        self._update_avatar_display()

        # Generate default pitch text if blank
        self._apply_template("hook")

    def _on_avatar_changed(self) -> None:
        idx = self.avatar_combo.currentIndex()
        if 0 <= idx < len(self.avatars):
            self.current_avatar = self.avatars[idx]
            self._update_avatar_display()
            # Sync recommended voice
            if self.current_avatar.voice_id:
                v_idx = self.voice_combo.findData(self.current_avatar.voice_id)
                if v_idx >= 0:
                    self.voice_combo.setCurrentIndex(v_idx)

    def _update_avatar_display(self) -> None:
        if not self.current_avatar:
            return
        img_path = self.current_avatar.preview_image_path or self.current_avatar.media_path
        if img_path and os.path.exists(img_path):
            pix = QPixmap(img_path)
            self.avatar_preview.setPixmap(
                pix.scaled(170, 260, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            )
        else:
            self.avatar_preview.setText(f"💃\n{self.current_avatar.name}")

    def _on_script_text_changed(self) -> None:
        text = self.pitch_text.toPlainText().strip()
        words = len(text.split()) if text else 0
        approx_seconds = int(words / 2.5)  # Average Vietnamese speaking speed
        self.word_count_label.setText(f"{words} từ ~ {approx_seconds} giây")
        # Invalidate audio cache on text change
        self.current_audio_path = None

    def _apply_template(self, tpl_type: str) -> None:
        if not self.product:
            return
        name = self.product.name
        price_str = f"{self.product.promo_price:,.0f} đồng" if self.product.promo_price else (f"{self.product.price:,.0f} đồng" if self.product.price else "giá cực hời")
        feats = (self.product.verified_features or self.product.description or "chất lượng tuyệt hảo").replace("\n", ", ")

        if tpl_type == "hook":
            script = (
                f"Dừng lại 3 giây thôi cả nhà ơi! Em đang cầm trên tay một siêu phẩm cực kỳ hot: {name}! "
                f"Điểm đặc biệt nhất chính là {feats}. "
                f"Riêng trong phiên live hôm nay, shop em trợ giá sốc chỉ còn {price_str}! "
                f"Số lượng có hạn, anh chị nhấn ngay vào giỏ hàng góc trái màn hình để nhận ưu đãi nhé!"
            )
        elif tpl_type == "deal":
            script = (
                f"Flash sale chớp nhoáng chỉ duy nhất trong 2 phút! "
                f"Sản phẩm {name} đình đám với công năng {feats} "
                f"đang được xả kho với giá độc quyền chỉ {price_str}! "
                f"Chỉ còn đúng 10 suất cuối cùng cho ai bấm thanh toán nhanh nhất, chốt đơn ngay kẻo hết ạ!"
            )
        else:  # usp
            script = (
                f"Chào cả nhà, nếu anh chị đang tìm kiếm một giải pháp hoàn hảo thì đừng bỏ qua em {name} này. "
                f"Sản phẩm sở hữu những điểm cộng vượt trội: {feats}. "
                f"Dùng cực kỳ thích và tiện lợi mỗi ngày. "
                f"Đặc biệt hôm nay được trợ giá chỉ còn {price_str}, mời cả nhà cùng trải nghiệm ngay trong giỏ hàng nhé!"
            )
        self.pitch_text.setText(script)

    def _on_generate_ai_pitch(self) -> None:
        if not self.product:
            return
        self.ai_script_btn.setEnabled(False)
        self.ai_script_btn.setText("⏳ AI đang viết...")
        self.status_tag.setText("AI đang soạn bài nói...")

        worker = ScriptGenerateWorker(
            script_service=self.script_service,
            product=self.product,
            style="energetic",
            script_type="full_live",
        )
        worker.finished.connect(self._on_script_generated)
        worker.start()
        self._gen_worker = worker

    def _on_script_generated(self, content: str, err: str) -> None:
        self.ai_script_btn.setEnabled(True)
        self.ai_script_btn.setText("✨ AI Viết Bài Nói Nhanh")
        if err:
            QMessageBox.warning(self, "Lỗi AI", f"Không thể tạo bài nói tự động: {err}")
            self.status_tag.setText("Lỗi sinh kịch bản")
            return
        self.pitch_text.setText(content)
        self.status_tag.setText("Đã cập nhật bài nói AI!")

    # ----------------- Playback & TTS -----------------
    def _toggle_playback(self) -> None:
        if self.is_playing:
            self._pause_playback()
        else:
            self._start_playback()

    def _start_playback(self) -> None:
        text = self.pitch_text.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "Thông báo", "Vui lòng nhập lời thoại trước khi phát.")
            return

        if self.current_audio_path and os.path.exists(self.current_audio_path):
            self._resume_audio()
        else:
            self.play_btn.setEnabled(False)
            self.play_btn.setText("⏳ Đang chuẩn bị giọng đọc...")
            self.status_tag.setText("Đang tổng hợp giọng nói AI...")

            voice_id = self.voice_combo.currentData() or "vi-VN-HoaiMyNeural"
            speed = self.speed_spin.value()

            worker = SpeechWorker(
                avatar_service=self.avatar_service,
                text=text,
                voice_id=voice_id,
                speed=speed,
            )
            worker.finished.connect(self._on_speech_ready)
            worker.start()
            self._speech_worker = worker

    def _on_speech_ready(self, audio_path: str, duration: float, err: str) -> None:
        self.play_btn.setEnabled(True)
        if err or not audio_path:
            QMessageBox.warning(self, "Lỗi giọng nói", f"Không thể tạo âm thanh: {err}")
            self.play_btn.setText("▶️ Phát Giới Thiệu (Nghe AI Thuyết Trình)")
            self.status_tag.setText("Lỗi tạo giọng đọc")
            return

        self.current_audio_path = audio_path
        self.audio_duration = max(1.0, duration)
        self._resume_audio()

    def _resume_audio(self) -> None:
        if not self.current_audio_path:
            return
        if self._player:
            self._player.setSource(QUrl.fromLocalFile(self.current_audio_path))
            self._player.play()
        self.is_playing = True
        self.play_btn.setText("⏸️ Tạm Dừng")
        self.stop_btn.setEnabled(True)
        self.live_badge.setText("● ĐANG THUYẾT TRÌNH")
        self.live_badge.setStyleSheet(
            "background-color: #E84118; color: white; padding: 6px 14px; "
            "border-radius: 12px; font-weight: bold; font-size: 11px;"
        )
        self.status_tag.setText("Người mẫu AI đang thuyết trình...")
        self.play_timer.start()

    def _pause_playback(self) -> None:
        if self._player:
            self._player.pause()
        self.is_playing = False
        self.play_btn.setText("▶️ Tiếp Tục Phát")
        self.play_timer.stop()
        self.status_tag.setText("Đã tạm dừng")

    def _stop_playback(self) -> None:
        if self._player:
            self._player.stop()
        self.is_playing = False
        self.play_timer.stop()
        self.current_play_time = 0.0
        self.progress_bar.setValue(0)
        self.time_label.setText(f"00:00 / {self._fmt_time(self.audio_duration)}")
        self.play_btn.setText("▶️ Phát Giới Thiệu (Nghe AI Thuyết Trình)")
        self.stop_btn.setEnabled(False)
        self.live_badge.setText("● CHƯA PHÁT")
        self.live_badge.setStyleSheet(
            "background-color: #2D3436; color: #DFE6E9; padding: 6px 14px; "
            "border-radius: 12px; font-weight: bold; font-size: 11px;"
        )
        self.status_tag.setText("Sẵn sàng")

    def _on_player_status(self, status) -> None:
        from PySide6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._stop_playback()

    def _update_playback_tick(self) -> None:
        if not self.is_playing or self.audio_duration <= 0:
            return
        self.current_play_time += 0.2
        if self.current_play_time >= self.audio_duration:
            self._stop_playback()
            return

        ratio = min(1.0, self.current_play_time / self.audio_duration)
        self.progress_bar.setValue(int(ratio * 1000))
        self.time_label.setText(f"{self._fmt_time(self.current_play_time)} / {self._fmt_time(self.audio_duration)}")

    def _fmt_time(self, seconds: float) -> str:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins:02d}:{secs:02d}"

    # ----------------- OBS Live Activation -----------------
    def _on_send_to_obs(self) -> None:
        if not self.current_avatar:
            QMessageBox.warning(self, "Thông báo", "Vui lòng chọn Người Mẫu AI.")
            return

        try:
            if not self.obs_client.is_connected:
                # Attempt quick connection
                host, port, pwd = self.settings_service.get_obs_connection()
                self.obs_client.configure(host, port, pwd)
                if not self.obs_client.connect():
                    QMessageBox.warning(
                        self,
                        "Chưa kết nối OBS",
                        f"Không thể kết nối tới OBS Studio tại {host}:{port}.\n"
                        "Vui lòng bật OBS và kích hoạt WebSocket Server trong Tools -> WebSocket Server Settings."
                    )
                    return

            # Ensure avatar has correct position and chroma key
            self.current_avatar.obs_position = self.pos_combo.currentData() or "bottom_right"
            self.current_avatar.transparent_bg = self.chroma_check.isChecked()

            scene_name = self.settings_service.get_obs_scene()
            ok, msg = self.avatar_service.send_avatar_to_obs(
                obs_client=self.obs_client,
                avatar=self.current_avatar,
                scene_name=scene_name,
            )

            if ok:
                # If audio is not yet generated, attempt to generate it from the pitch text
                if not self.current_audio_path:
                    text = self.pitch_text.toPlainText().strip()
                    if text:
                        self.status_tag.setText("Đang tổng hợp giọng nói cho OBS...")
                        voice_id = self.voice_combo.currentData() or "vi-VN-HoaiMyNeural"
                        speed = self.speed_spin.value()
                        try:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            audio_path, duration = loop.run_until_complete(
                                self.avatar_service.generate_speech_for_pitch(
                                    text=text,
                                    voice_id=voice_id,
                                    speed=speed,
                                )
                            )
                            loop.close()
                            if audio_path and os.path.exists(audio_path):
                                self.current_audio_path = audio_path
                                self.audio_duration = max(1.0, duration)
                        except Exception as ex:
                            logger.warning("Không thể tự động sinh âm thanh cho OBS: {}", ex)

                # Play audio pitch into OBS if audio source exists
                if self.current_audio_path and os.path.exists(self.current_audio_path):
                    audio_src = self.settings_service.get_obs_audio_source()
                    self.obs_client.set_media_source_file(audio_src, self.current_audio_path, loop=False)

                self.status_tag.setText("ĐÃ PHÁT TRÊN OBS LIVE!")
                self.live_badge.setText("● ĐANG LIVE TRÊN OBS")
                self.live_badge.setStyleSheet(
                    "background-color: #00B894; color: white; padding: 6px 14px; "
                    "border-radius: 12px; font-weight: bold; font-size: 11px;"
                )
                QMessageBox.information(
                    self,
                    "OBS Livestream",
                    f"✅ Thành công!\n\n{msg}\n\nNgười Mẫu AI đã được kích hoạt trên màn hình OBS Studio."
                )
            else:
                QMessageBox.warning(self, "Lỗi OBS", msg)
        except Exception as e:
            logger.exception("Lỗi khi phát Người Mẫu AI lên OBS")
            QMessageBox.critical(self, "Lỗi OBS", f"Đã xảy ra lỗi khi gửi lên OBS: {e}")


    # ----------------- Video Generation -----------------
    def _on_generate_video(self) -> None:
        if not self.current_avatar:
            QMessageBox.warning(self, "Thông báo", "Vui lòng chọn Người Mẫu AI.")
            return

        text = self.pitch_text.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "Thông báo", "Vui lòng nhập lời thoại trước khi tạo video.")
            return

        self.video_gen_btn.setEnabled(False)
        self.video_gen_btn.setText("⏳ Đang render video...")
        self.status_tag.setText("Đang tạo video Người Mẫu AI...")

        # If audio not ready, create audio first
        if not self.current_audio_path:
            voice_id = self.voice_combo.currentData() or "vi-VN-HoaiMyNeural"
            speed = self.speed_spin.value()
            w_speech = SpeechWorker(self.avatar_service, text, voice_id, speed)
            w_speech.finished.connect(self._continue_video_gen)
            w_speech.start()
            self._speech_worker = w_speech
        else:
            self._start_video_worker()

    def _continue_video_gen(self, audio_path: str, duration: float, err: str) -> None:
        if err or not audio_path:
            self.video_gen_btn.setEnabled(True)
            self.video_gen_btn.setText("🎬 Tạo Video Người Mẫu AI (MP4)")
            QMessageBox.warning(self, "Lỗi", f"Không thể tạo âm thanh cho video: {err}")
            return
        self.current_audio_path = audio_path
        self.audio_duration = duration
        self._start_video_worker()

    def _start_video_worker(self) -> None:
        worker = VideoGenWorker(
            avatar_service=self.avatar_service,
            avatar=self.current_avatar,
            audio_path=self.current_audio_path or "",
            script_text=self.pitch_text.toPlainText().strip(),
        )
        worker.finished.connect(self._on_video_generated)
        worker.start()
        self._video_worker = worker

    def _on_video_generated(self, vid_path: str, msg: str) -> None:
        self.video_gen_btn.setEnabled(True)
        self.video_gen_btn.setText("🎬 Tạo Video Người Mẫu AI (MP4)")
        if not vid_path:
            QMessageBox.warning(self, "Lỗi tạo video", f"Không thể xuất video: {msg}")
            self.status_tag.setText("Lỗi render video")
            return

        # Auto link to product video_path if empty
        if self.product and not self.product.video_path:
            self.product_service.update_product(self.product.id, {"video_path": vid_path})

        self.status_tag.setText("Đã xuất video Người Mẫu AI thành công!")
        QMessageBox.information(
            self,
            "Hoàn tất tạo video",
            f"✅ {msg}\n\nĐường dẫn video:\n{vid_path}\n\nVideo đã sẵn sàng để phát sóng trên OBS hoặc tải lên mạng xã hội!"
        )

    def _copy_pitch(self) -> None:
        text = self.pitch_text.toPlainText().strip()
        if text:
            QApplication.clipboard().setText(text)
            self.status_tag.setText("Đã sao chép kịch bản vào bộ nhớ tạm!")

    def closeEvent(self, event) -> None:
        self._stop_playback()
        super().closeEvent(event)
