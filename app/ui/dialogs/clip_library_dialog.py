"""Clip Library Dialog — management and manual approval for presenter gesture clips (9 Cues, §4.2)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.database.models.avatar import AIAvatar
from app.presenter.clip_library import CUE_DESCRIPTIONS, PresenterClipLibrary
from app.services.script_segment_service import VALID_CUES
from app.ui.styles.dark_theme import COLORS


class ClipLibraryDialog(QDialog):
    """Dialog for reviewing, importing, and approving the 9 cue gesture clips of an AI Presenter."""

    def __init__(self, avatar: AIAvatar, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.avatar = avatar
        self.library = PresenterClipLibrary()
        self.setWindowTitle(f"🎬 Thư Viện Cử Chỉ & Duyệt Clip — {self.avatar.name}")
        self.resize(750, 680)
        self._setup_ui()
        self._load_clips()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Header
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel(f"💃 Thư Viện Cử Chỉ Mẫu: {self.avatar.name}")
        title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {COLORS['accent_secondary']};")
        title_box.addWidget(title)

        hint = QLabel("Quy tắc §4.2: Mỗi cử chỉ cần có clip 3-15s nền xanh. BẮT BUỘC DUYỆT TAY trước khi phát sóng.")
        hint.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
        title_box.addWidget(hint)
        header.addLayout(title_box)
        header.addStretch()

        self.approved_count_label = QLabel("0/9 clip đã duyệt")
        self.approved_count_label.setStyleSheet("font-weight: bold; color: #2ecc71; font-size: 13px;")
        header.addWidget(self.approved_count_label)
        layout.addLayout(header)

        # Scroll area for 9 cues
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        self.cue_list_layout = QVBoxLayout(container)
        self.cue_list_layout.setSpacing(10)
        self.cue_list_layout.setContentsMargins(0, 0, 10, 0)
        scroll.setWidget(container)

        layout.addWidget(scroll, 1)

        # Footer
        footer = QHBoxLayout()
        footer.addStretch()
        close_btn = QPushButton("Đóng")
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

    def _load_clips(self) -> None:
        # Clear layout
        while self.cue_list_layout.count():
            item = self.cue_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        approved_count = 0
        sorted_cues = [
            "WAVE_GREET",
            "HOLD_PRODUCT",
            "SHOW_DETAIL",
            "POINT_PRICE",
            "TRY_ON",
            "CTA_POINT_DOWN",
            "NOD_LISTEN",
            "SURPRISE",
            "IDLE_TALK",
        ]

        for cue in sorted_cues:
            clip = self.library.get_clip_for_cue(self.avatar.id, cue, require_approved=False)
            is_approved = clip.is_approved if clip else False
            if is_approved:
                approved_count += 1

            card = self._create_cue_card(cue, clip)
            self.cue_list_layout.addWidget(card)

        self.approved_count_label.setText(f"{approved_count}/{len(sorted_cues)} clip đã duyệt")

    def _create_cue_card(self, cue: str, clip: Optional[object]) -> QFrame:
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background-color: {COLORS['bg_card']}; border: 1px solid {COLORS['border']}; border-radius: 8px; padding: 10px; }}"
        )
        row = QVBoxLayout(card)
        row.setSpacing(6)

        # Top row: Cue name + Description
        top = QHBoxLayout()
        cue_label = QLabel(f"<b>{cue}</b> — {CUE_DESCRIPTIONS.get(cue, '')}")
        cue_label.setStyleSheet(f"font-size: 13px; color: {COLORS['text_primary']};")
        top.addWidget(cue_label, 1)

        status_badge = QLabel("✅ ĐÃ DUYỆT" if (clip and clip.is_approved) else "⏳ CHƯA DUYỆT")
        status_badge.setStyleSheet(
            "font-weight: bold; font-size: 11px; padding: 2px 6px; border-radius: 4px; "
            + ("background-color: #27ae60; color: white;" if (clip and clip.is_approved) else "background-color: #e67e22; color: white;")
        )
        top.addWidget(status_badge)
        row.addLayout(top)

        # File path & Actions row
        bot = QHBoxLayout()
        path_input = QLineEdit()
        path_input.setPlaceholderText("Chưa có file clip (Chọn video MP4 nền xanh)")
        if clip and clip.file_path:
            path_input.setText(clip.file_path)
        path_input.setReadOnly(True)
        bot.addWidget(path_input, 1)

        browse_btn = QPushButton("📁 Chọn clip")
        browse_btn.clicked.connect(lambda _, c=cue: self._browse_clip_for_cue(c))
        bot.addWidget(browse_btn)

        # Checkbox Duyệt tay
        approve_cb = QCheckBox("Duyệt đạt chuẩn")
        approve_cb.setChecked(bool(clip and clip.is_approved))
        approve_cb.setEnabled(bool(clip and clip.file_path and Path(clip.file_path).exists()))
        approve_cb.toggled.connect(lambda checked, c=cue: self._toggle_approve(c, checked))
        bot.addWidget(approve_cb)

        row.addLayout(bot)
        return card

    def _browse_clip_for_cue(self, cue: str) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Chọn clip cho cử chỉ {cue}",
            "",
            "Video Files (*.mp4 *.mov *.avi *.mkv);;All Files (*)",
        )
        if path:
            self.library.register_clip(
                avatar_id=self.avatar.id,
                cue=cue,
                file_path=path,
                is_approved=False,  # Bắt buộc duyệt tay sau khi kiểm tra
            )
            self._load_clips()

    def _toggle_approve(self, cue: str, checked: bool) -> None:
        self.library.set_approval(self.avatar.id, cue, is_approved=checked)
        self._load_clips()
