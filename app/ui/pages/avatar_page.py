"""AI Avatar & Virtual Presenter Management Page."""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from loguru import logger

from app.database.models.avatar import AIAvatar
from app.database.session import get_session
from app.integrations.obs.client import OBSClient
from app.services.avatar_service import AvatarService
from app.services.settings_service import (
    KEY_DID_API_KEY,
    KEY_HEYGEN_API_KEY,
    KEY_OBS_AVATAR_SOURCE,
    SettingsService,
)
from app.ui.styles.dark_theme import COLORS


class AvatarFormDialog(QDialog):
    """Dialog for creating or editing an AI Avatar."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        avatar_data: Optional[dict] = None,
    ) -> None:
        super().__init__(parent)
        self.avatar_data = avatar_data or {}
        self.is_edit = bool(avatar_data and avatar_data.get("id"))
        self.setWindowTitle("Sửa Người Mẫu AI" if self.is_edit else "Thêm Người Mẫu AI Mới")
        self.resize(540, 600)
        self._setup_ui()
        self._load_data()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel("Sửa Người Mẫu AI" if self.is_edit else "Thêm Người Mẫu AI Mới")
        title.setObjectName("headerLabel")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(10)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("VD: Mai Phương — Nữ MC Thời Trang")
        form.addRow("Tên người mẫu *:", self.name_input)

        self.gender_combo = QComboBox()
        self.gender_combo.addItem("Nữ (Female)", "female")
        self.gender_combo.addItem("Nam (Male)", "male")
        form.addRow("Giới tính:", self.gender_combo)

        self.style_input = QLineEdit()
        self.style_input.setPlaceholderText("VD: Thanh lịch, Năng động, Chuyên gia...")
        form.addRow("Phong cách:", self.style_input)

        # Media path
        media_row = QHBoxLayout()
        self.media_input = QLineEdit()
        self.media_input.setPlaceholderText("Đường dẫn file video loop hoặc ảnh chân dung")
        media_row.addWidget(self.media_input, 1)
        browse_btn = QPushButton("📁 Chọn tệp")
        browse_btn.clicked.connect(self._browse_media)
        media_row.addWidget(browse_btn)
        form.addRow("Video / Ảnh mẫu:", media_row)

        self.voice_combo = QComboBox()
        self.voice_combo.addItem("Hoài My — Nữ truyền cảm (vi-VN-HoaiMyNeural)", "vi-VN-HoaiMyNeural")
        self.voice_combo.addItem("Nam Minh — Nam ấm áp (vi-VN-NamMinhNeural)", "vi-VN-NamMinhNeural")
        form.addRow("Giọng đọc mặc định:", self.voice_combo)

        self.pos_combo = QComboBox()
        self.pos_combo.addItem("Góc dưới bên phải (Chuẩn TikTok Live)", "bottom_right")
        self.pos_combo.addItem("Góc dưới bên trái", "bottom_left")
        self.pos_combo.addItem("Chính giữa màn hình", "center")
        self.pos_combo.addItem("Toàn màn hình (Full)", "full")
        form.addRow("Vị trí trên OBS:", self.pos_combo)

        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.15, 1.0)
        self.scale_spin.setSingleStep(0.05)
        self.scale_spin.setValue(0.38)
        self.scale_spin.setSuffix(" tỉ lệ màn hình")
        form.addRow("Kích thước trên OBS:", self.scale_spin)

        self.chroma_check = QCheckBox("Tách nền xanh (Chroma Key) khi phát trên OBS")
        self.chroma_check.setChecked(True)
        form.addRow("Xóa phông nền:", self.chroma_check)

        self.default_check = QCheckBox("Đặt làm Người Mẫu AI mặc định")
        form.addRow("", self.default_check)

        self.desc_input = QTextEdit()
        self.desc_input.setPlaceholderText("Mô tả phong cách, ngữ cảnh phù hợp...")
        self.desc_input.setMaximumHeight(70)
        form.addRow("Ghi chú / Mô tả:", self.desc_input)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Hủy")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("💾 Lưu Người Mẫu")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _browse_media(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn file video hoặc ảnh Người Mẫu AI",
            "",
            "Media Files (*.mp4 *.mov *.avi *.png *.jpg *.jpeg *.webp);;All Files (*)",
        )
        if path:
            self.media_input.setText(path)

    def _load_data(self) -> None:
        if not self.avatar_data:
            return
        self.name_input.setText(self.avatar_data.get("name", ""))
        self.style_input.setText(self.avatar_data.get("style", ""))
        self.media_input.setText(self.avatar_data.get("media_path", "") or "")
        self.desc_input.setText(self.avatar_data.get("description", "") or "")
        self.chroma_check.setChecked(self.avatar_data.get("transparent_bg", True))
        self.default_check.setChecked(self.avatar_data.get("is_default", False))

        gender = self.avatar_data.get("gender", "female")
        g_idx = self.gender_combo.findData(gender)
        if g_idx >= 0:
            self.gender_combo.setCurrentIndex(g_idx)

        voice = self.avatar_data.get("voice_id", "vi-VN-HoaiMyNeural")
        v_idx = self.voice_combo.findData(voice)
        if v_idx >= 0:
            self.voice_combo.setCurrentIndex(v_idx)

        pos = self.avatar_data.get("obs_position", "bottom_right")
        p_idx = self.pos_combo.findData(pos)
        if p_idx >= 0:
            self.pos_combo.setCurrentIndex(p_idx)

        scale = self.avatar_data.get("obs_scale", 0.38)
        self.scale_spin.setValue(scale)

    def _on_save(self) -> None:
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Lỗi", "Vui lòng nhập tên người mẫu.")
            return

        data = {
            "name": name,
            "gender": self.gender_combo.currentData(),
            "style": self.style_input.text().strip() or "Thanh lịch",
            "media_path": self.media_input.text().strip() or None,
            "preview_image_path": self.media_input.text().strip() or None,
            "voice_id": self.voice_combo.currentData(),
            "obs_position": self.pos_combo.currentData(),
            "obs_scale": self.scale_spin.value(),
            "transparent_bg": self.chroma_check.isChecked(),
            "is_default": self.default_check.isChecked(),
            "description": self.desc_input.toPlainText().strip() or None,
        }
        self.saved_data = data
        self.accept()


class ApiConfigDialog(QDialog):
    """Dialog for configuring D-ID and HeyGen Lip-sync API keys."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.session = get_session()
        self.settings_service = SettingsService(self.session)
        self.setWindowTitle("🔑 Cấu hình API Người Mẫu AI (Lip-Sync)")
        self.resize(500, 280)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        title = QLabel("Cấu hình API Video & Lip-Sync Người Mẫu")
        title.setObjectName("headerLabel")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        hint = QLabel(
            "LiveFlow tích hợp sẵn bộ tạo video cục bộ thông minh không cần API key. "
            "Nếu bạn có tài khoản D-ID hoặc HeyGen, hãy điền API Key bên dưới để tạo video cử động môi chuyên sâu."
        )
        hint.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(10)

        self.did_input = QLineEdit()
        self.did_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.did_input.setPlaceholderText("D-ID API Key (Basic auth token)")
        form.addRow("D-ID API Key:", self.did_input)

        self.heygen_input = QLineEdit()
        self.heygen_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.heygen_input.setPlaceholderText("HeyGen API Key (X-Api-Key)")
        form.addRow("HeyGen API Key:", self.heygen_input)

        layout.addLayout(form)

        # Load existing keys
        did_key = self.settings_service.get_secret(KEY_DID_API_KEY) or ""
        heygen_key = self.settings_service.get_secret(KEY_HEYGEN_API_KEY) or ""
        self.did_input.setText(did_key)
        self.heygen_input.setText(heygen_key)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Đóng")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("💾 Lưu Cài Đặt")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _save(self) -> None:
        did_key = self.did_input.text().strip()
        heygen_key = self.heygen_input.text().strip()
        self.settings_service.set_secret(KEY_DID_API_KEY, did_key)
        self.settings_service.set_secret(KEY_HEYGEN_API_KEY, heygen_key)
        QMessageBox.information(self, "Thành công", "Đã lưu thông tin API Key an toàn trong Windows Credential Manager.")
        self.accept()


class AvatarPage(QWidget):
    """Page for managing AI Virtual Presenters and avatars."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.session = get_session()
        self.avatar_service = AvatarService(self.session)
        self.obs_client = OBSClient()
        self._setup_ui()
        self.refresh_avatars()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(16)

        # Header
        header_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        title = QLabel("💃 Quản Lý Người Mẫu AI (AI Avatar Studio)")
        title.setObjectName("headerLabel")
        title_box.addWidget(title)
        subtitle = QLabel("Thư viện người mẫu ảo, MC thuyết trình sản phẩm, hỗ trợ tách phông xanh và overlay trực tiếp lên OBS Studio.")
        subtitle.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px;")
        title_box.addWidget(subtitle)
        header_row.addLayout(title_box, 1)

        self.count_badge = QLabel("0 người mẫu")
        self.count_badge.setObjectName("subHeaderLabel")
        header_row.addWidget(self.count_badge)
        main_layout.addLayout(header_row)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        add_btn = QPushButton("➕ Thêm Người Mẫu Mới")
        add_btn.setObjectName("primaryButton")
        add_btn.clicked.connect(self._on_add_avatar)
        toolbar.addWidget(add_btn)

        api_btn = QPushButton("🔑 Cấu Hình API Lip-Sync")
        api_btn.clicked.connect(self._on_config_api)
        toolbar.addWidget(api_btn)

        toolbar.addStretch()

        refresh_btn = QPushButton("🔄 Làm mới")
        refresh_btn.clicked.connect(self.refresh_avatars)
        toolbar.addWidget(refresh_btn)
        main_layout.addLayout(toolbar)

        # Scroll Area for Avatar Cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.cards_container = QWidget()
        self.cards_layout = QGridLayout(self.cards_container)
        self.cards_layout.setSpacing(16)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self.cards_container)

        main_layout.addWidget(scroll, 1)

    def refresh_avatars(self) -> None:
        """Reload avatars from database and render cards."""
        # Clear existing cards
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        avatars = self.avatar_service.get_avatars(active_only=False)
        self.count_badge.setText(f"{len(avatars)} người mẫu")

        cols = 3
        for i, avatar in enumerate(avatars):
            card = self._create_avatar_card(avatar)
            row = i // cols
            col = i % cols
            self.cards_layout.addWidget(card, row, col)

    def _create_avatar_card(self, avatar: AIAvatar) -> QFrame:
        card = QFrame()
        card.setObjectName("cardFrame")
        card.setStyleSheet(
            f"QFrame#cardFrame {{ background-color: {COLORS['bg_card']}; border: 1px solid {COLORS['border']}; border-radius: 12px; }}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 14, 14, 14)
        card_layout.setSpacing(10)

        # Visual preview
        thumb_label = QLabel()
        thumb_label.setFixedSize(260, 280)
        thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb_label.setStyleSheet("background-color: #12131A; border-radius: 8px;")
        thumb_label.setScaledContents(True)

        img_path = avatar.preview_image_path or avatar.media_path
        if img_path and os.path.exists(img_path):
            pix = QPixmap(img_path)
            thumb_label.setPixmap(pix.scaled(260, 280, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
        else:
            thumb_label.setText(f"💃\n{avatar.name}")
        card_layout.addWidget(thumb_label, 0, Qt.AlignmentFlag.AlignCenter)

        # Title and badges
        title_row = QHBoxLayout()
        name_label = QLabel(avatar.name)
        name_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        name_label.setWordWrap(True)
        title_row.addWidget(name_label, 1)

        if avatar.is_default:
            def_badge = QLabel("⭐ MẶC ĐỊNH")
            def_badge.setStyleSheet("color: #F1C40F; font-size: 10px; font-weight: bold;")
            title_row.addWidget(def_badge)
        card_layout.addLayout(title_row)

        # Style & Voice Info
        style_label = QLabel(f"🎭 Phong cách: {avatar.style}")
        style_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px;")
        card_layout.addWidget(style_label)

        voice_name = "Hoài My (Nữ)" if "HoaiMy" in (avatar.voice_id or "") else "Nam Minh (Nam)"
        voice_label = QLabel(f"🎙️ Giọng đọc: {voice_name}")
        voice_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px;")
        card_layout.addWidget(voice_label)

        pos_text = "Góc dưới phải" if avatar.obs_position == "bottom_right" else ("Góc dưới trái" if avatar.obs_position == "bottom_left" else "Chính giữa")
        obs_label = QLabel(f"📺 OBS: {pos_text} ({'Phông xanh' if avatar.transparent_bg else 'Nguyên bản'})")
        obs_label.setStyleSheet(f"color: {COLORS['accent']}; font-size: 11px;")
        card_layout.addWidget(obs_label)

        if avatar.description:
            desc_label = QLabel(avatar.description)
            desc_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
            desc_label.setWordWrap(True)
            card_layout.addWidget(desc_label)

        card_layout.addStretch()

        # Action buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)

        clips_btn = QPushButton("🎬 Cử chỉ & Duyệt (9 Cues)")
        clips_btn.setToolTip("Quản lý 9 video cử chỉ và phê duyệt clip cho người mẫu này")
        clips_btn.clicked.connect(lambda _, av=avatar: self._on_manage_clips(av))
        btn_layout.addWidget(clips_btn)

        obs_btn = QPushButton("🔴 OBS")
        obs_btn.setObjectName("dangerButton")
        obs_btn.setToolTip("Gửi Người Mẫu AI này lên màn hình OBS Studio")
        obs_btn.clicked.connect(lambda _, av=avatar: self._send_to_obs(av))
        btn_layout.addWidget(obs_btn)

        edit_btn = QPushButton("✏️")
        edit_btn.setToolTip("Sửa thông tin")
        edit_btn.clicked.connect(lambda _, av=avatar: self._on_edit_avatar(av))
        btn_layout.addWidget(edit_btn)

        del_btn = QPushButton("🗑️")
        del_btn.setToolTip("Xóa người mẫu")
        del_btn.clicked.connect(lambda _, avid=avatar.id, avname=avatar.name: self._on_delete_avatar(avid, avname))
        btn_layout.addWidget(del_btn)

        card_layout.addLayout(btn_layout)
        return card

    def _on_manage_clips(self, avatar: AIAvatar) -> None:
        from app.ui.dialogs.clip_library_dialog import ClipLibraryDialog
        dlg = ClipLibraryDialog(avatar, self)
        dlg.exec()

    def _on_add_avatar(self) -> None:
        dlg = AvatarFormDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.avatar_service.create_avatar(dlg.saved_data)
            self.refresh_avatars()

    def _on_edit_avatar(self, avatar: AIAvatar) -> None:
        data = {
            "id": avatar.id,
            "name": avatar.name,
            "gender": avatar.gender,
            "style": avatar.style,
            "media_path": avatar.media_path,
            "voice_id": avatar.voice_id,
            "obs_position": avatar.obs_position,
            "obs_scale": avatar.obs_scale,
            "transparent_bg": avatar.transparent_bg,
            "is_default": avatar.is_default,
            "description": avatar.description,
        }
        dlg = AvatarFormDialog(self, avatar_data=data)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.avatar_service.update_avatar(avatar.id, dlg.saved_data)
            self.refresh_avatars()

    def _on_delete_avatar(self, avatar_id: int, name: str) -> None:
        reply = QMessageBox.question(
            self,
            "Xác nhận xóa",
            f"Bạn có chắc muốn xóa Người Mẫu AI '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.avatar_service.delete_avatar(avatar_id)
            self.refresh_avatars()

    def _send_to_obs(self, avatar: AIAvatar) -> None:
        ok, msg = self.avatar_service.send_avatar_to_obs(self.obs_client, avatar)
        if ok:
            QMessageBox.information(self, "OBS Studio", f"✅ {msg}")
        else:
            QMessageBox.warning(self, "OBS Studio", msg)

    def _on_config_api(self) -> None:
        dlg = ApiConfigDialog(self)
        dlg.exec()
