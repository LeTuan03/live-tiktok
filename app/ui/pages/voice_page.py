"""AI Voice Studio UI Page for managing TTS profiles and voice testing."""

from __future__ import annotations

import asyncio
from typing import List, Optional
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from loguru import logger
from app.database.models.voice import VoiceProfile
from app.database.repositories.voice_repo import VoiceRepository
from app.database.session import get_session
from app.integrations.tts.edge_tts_provider import EdgeTTSProvider
from app.services.voice_service import VoiceService
from app.ui.styles.dark_theme import COLORS


class TestTTSWorker(QThread):
    """Worker thread for non-blocking speech generation test."""

    finished = Signal(str, str)  # (output_path, error_message)

    def __init__(self, text: str, voice_id: str, rate_str: str, output_path: str) -> None:
        super().__init__()
        self.text = text
        self.voice_id = voice_id
        self.rate_str = rate_str
        self.output_path = output_path

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            provider = EdgeTTSProvider()
            loop.run_until_complete(
                provider.generate_speech(
                    text=self.text,
                    output_path=self.output_path,
                    voice_id=self.voice_id,
                    rate=self.rate_str,
                )
            )
            loop.close()
            self.finished.emit(self.output_path, "")
        except Exception as e:
            logger.exception("Lỗi khi đọc thử giọng TTS")
            self.finished.emit("", str(e))


class VoicePage(QWidget):
    """AI Voice Studio UI Page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.profiles: List[VoiceProfile] = []
        self._init_services()
        self._init_ui()
        self.refresh_profiles()

    def _init_services(self) -> None:
        session = get_session()
        self.voice_repo = VoiceRepository(session)
        self.voice_service = VoiceService(self.voice_repo)

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        # Header
        header_layout = QHBoxLayout()
        title_label = QLabel("🔊 AI Voice Studio")
        title_label.setObjectName("headerLabel")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        # Content Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left Panel — Profile List & Form
        left_card = QFrame()
        left_card.setObjectName("cardFrame")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(16, 16, 16, 16)

        profile_title = QLabel("👥 Profile Giọng Đọc (TTS)")
        profile_title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {COLORS['accent']};")
        left_layout.addWidget(profile_title)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Mặc định", "Tên Profile", "Voice ID", "Tốc độ"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        left_layout.addWidget(self.table)

        # Actions under table
        btn_layout = QHBoxLayout()
        self.set_default_btn = QPushButton("⭐ Đặt mặc định")
        self.set_default_btn.clicked.connect(self._set_selected_default)
        btn_layout.addWidget(self.set_default_btn)

        self.delete_btn = QPushButton("🗑️ Xóa")
        self.delete_btn.clicked.connect(self._delete_selected_profile)
        btn_layout.addWidget(self.delete_btn)
        left_layout.addLayout(btn_layout)

        left_layout.addSpacing(12)

        # Add Profile Form
        form_title = QLabel("➕ Thêm Profile Giọng Mới")
        form_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        left_layout.addWidget(form_title)

        form = QFormLayout()
        form.setSpacing(10)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("VD: Nữ miền Nam ngọt ngào")
        form.addRow("Tên Profile:", self.name_input)

        self.voice_combo = QComboBox()
        for v in EdgeTTSProvider.DEFAULT_VOICES:
            self.voice_combo.addItem(f"{v['name']} ({v['id']})", v['id'])
        form.addRow("Giọng TTS:", self.voice_combo)

        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.5, 2.0)
        self.speed_spin.setSingleStep(0.1)
        self.speed_spin.setValue(1.0)
        form.addRow("Tốc độ:", self.speed_spin)

        self.is_default_cb = QCheckBox("Đặt làm giọng mặc định")
        form.addRow("", self.is_default_cb)

        left_layout.addLayout(form)

        self.add_btn = QPushButton("➕ Tạo Profile")
        self.add_btn.setObjectName("primaryButton")
        self.add_btn.clicked.connect(self._add_profile)
        left_layout.addWidget(self.add_btn)

        splitter.addWidget(left_card)

        # Right Panel — Speech Test Studio
        right_card = QFrame()
        right_card.setObjectName("cardFrame")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(16, 16, 16, 16)

        test_title = QLabel("🎙️ Thử Nghiệm Giọng Đọc")
        test_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        right_layout.addWidget(test_title)

        test_desc = QLabel("Nhập văn bản mẫu để kiểm tra ngữ điệu và tốc độ của giọng đọc TTS.")
        test_desc.setStyleSheet(f"color: {COLORS['text_muted']};")
        right_layout.addWidget(test_desc)

        self.test_text = QTextEdit()
        self.test_text.setPlainText(
            "Xin chào tất cả mọi người! Chào mừng anh chị đã quay trở lại phiên livestream bán hàng ngày hôm nay của shop!"
        )
        right_layout.addWidget(self.test_text)

        test_controls = QHBoxLayout()
        self.test_btn = QPushButton("▶️ Đọc thử TTS")
        self.test_btn.setObjectName("accentButton")
        self.test_btn.clicked.connect(self._test_speech)
        test_controls.addWidget(self.test_btn)

        right_layout.addLayout(test_controls)

        self.test_status_label = QLabel("")
        self.test_status_label.setStyleSheet(f"color: {COLORS['accent']}; font-weight: bold;")
        right_layout.addWidget(self.test_status_label)

        splitter.addWidget(right_card)
        splitter.setSizes([500, 500])

        main_layout.addWidget(splitter)

    def refresh_profiles(self) -> None:
        self.profiles = self.voice_service.get_profiles()
        self.table.setRowCount(len(self.profiles))

        for row, p in enumerate(self.profiles):
            def_item = QTableWidgetItem("⭐ Mặc định" if p.is_default else "")
            def_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, def_item)
            self.table.setItem(row, 1, QTableWidgetItem(p.name))
            self.table.setItem(row, 2, QTableWidgetItem(p.voice_id))
            self.table.setItem(row, 3, QTableWidgetItem(f"{p.speed}x"))

    def _add_profile(self) -> None:
        name = self.name_input.text().strip()
        voice_id = self.voice_combo.currentData()
        speed = self.speed_spin.value()
        is_default = self.is_default_cb.isChecked()

        if not name:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng nhập tên Voice Profile!")
            return

        self.voice_service.create_profile(
            name=name,
            voice_id=voice_id,
            speed=speed,
            is_default=is_default,
        )
        self.name_input.clear()
        self.refresh_profiles()
        QMessageBox.information(self, "Thành công", f"Đã thêm Voice Profile '{name}'!")

    def _set_selected_default(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.profiles):
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn 1 profile trong bảng!")
            return

        profile = self.profiles[row]
        self.voice_repo.update_profile(profile.id, is_default=True)
        self.refresh_profiles()

    def _delete_selected_profile(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.profiles):
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng chọn 1 profile trong bảng!")
            return

        profile = self.profiles[row]
        reply = QMessageBox.question(
            self,
            "Xác nhận",
            f"Bạn có chắc muốn xóa Voice Profile '{profile.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.voice_service.delete_profile(profile.id)
            self.refresh_profiles()

    def _test_speech(self) -> None:
        text = self.test_text.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Cảnh báo", "Vui lòng nhập văn bản để đọc thử!")
            return

        voice_id = self.voice_combo.currentData()
        speed_percent = int((self.speed_spin.value() - 1.0) * 100)
        rate_str = f"{'+' if speed_percent >= 0 else ''}{speed_percent}%"

        self.test_btn.setEnabled(False)
        self.test_status_label.setText("⏳ Đang tổng hợp giọng nói Edge TTS...")

        out_path = "storage/audio/test_tts_preview.mp3"

        self.worker = TestTTSWorker(
            text=text,
            voice_id=voice_id,
            rate_str=rate_str,
            output_path=out_path,
        )
        self.worker.finished.connect(self._on_test_finished)
        self.worker.start()

    def _on_test_finished(self, output_path: str, error: str) -> None:
        self.test_btn.setEnabled(True)
        if error or not output_path:
            self.test_status_label.setText(f"❌ Lỗi: {error}")
            QMessageBox.warning(self, "Lỗi TTS", f"Không thể đọc thử TTS: {error}")
        else:
            self.test_status_label.setText(f"✅ Đã tạo file nghe thử: {output_path}")
            QMessageBox.information(self, "Thành công", f"Đã tạo file âm thanh nghe thử thành công tại:\n{output_path}")
