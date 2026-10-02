"""AI Voice Studio — TTS voice profiles, preview playback and batch audio generation."""

from __future__ import annotations

import asyncio
from typing import List, Optional

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
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
from app.database.repositories.script_repo import ScriptRepository
from app.database.repositories.voice_repo import VoiceRepository
from app.database.session import get_session
from app.integrations.tts.edge_tts_provider import EdgeTTSProvider
from app.services.activity_log_service import log_activity
from app.services.voice_service import VoiceService


class PreviewTTSWorker(QThread):
    finished = Signal(str, str)  # (output_path, error)

    def __init__(self, voice_service: VoiceService, text: str, voice_id: str, speed: float) -> None:
        super().__init__()
        self.voice_service = voice_service
        self.text = text
        self.voice_id = voice_id
        self.speed = speed

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            path = loop.run_until_complete(self.voice_service.generate_preview(self.text, self.voice_id, self.speed))
            loop.close()
            self.finished.emit(str(path), "")
        except Exception as e:  # noqa: BLE001
            logger.exception("Lỗi khi đọc thử giọng TTS")
            self.finished.emit("", str(e))


class ListVoicesWorker(QThread):
    finished = Signal(list, str)

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            voices = loop.run_until_complete(EdgeTTSProvider.list_available_voices())
            loop.close()
            self.finished.emit(voices, "")
        except Exception as e:  # noqa: BLE001
            self.finished.emit([], str(e))


class BatchTTSWorker(QThread):
    """Generate audio for every current script that has none yet."""

    progress = Signal(int, int, str)
    finished = Signal(int, int, list)  # done, skipped, errors

    def __init__(self, profile_id: Optional[int]) -> None:
        super().__init__()
        self.profile_id = profile_id

    def run(self) -> None:
        session = get_session()
        try:
            voice_repo = VoiceRepository(session)
            voice_service = VoiceService(voice_repo)
            script_repo = ScriptRepository(session)
            profile = voice_repo.get_profile_by_id(self.profile_id) if self.profile_id else None
            scripts = [s for s in script_repo.list_all() if s.is_current]
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            done = skipped = 0
            errors: list[str] = []
            for i, s in enumerate(scripts, 1):
                self.progress.emit(i, len(scripts), s.title)
                if voice_service.get_cached(s.content, profile):
                    skipped += 1
                    continue
                try:
                    loop.run_until_complete(voice_service.generate_speech(s.id, s.content, profile))
                    done += 1
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{s.title[:40]}: {e}")
            loop.close()
            self.finished.emit(done, skipped, errors)
        finally:
            session.close()


class VoicePage(QWidget):
    """AI Voice Studio UI Page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.profiles: List[VoiceProfile] = []
        self._player = None
        self._workers: list[QThread] = []
        self._init_services()
        self._init_ui()
        self._init_player()
        self.refresh_profiles()

    def _init_services(self) -> None:
        self.session = get_session()
        self.voice_repo = VoiceRepository(self.session)
        self.voice_service = VoiceService(self.voice_repo)

    def _init_player(self) -> None:
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

            self._player = QMediaPlayer(self)
            self._audio_out = QAudioOutput(self)
            self._player.setAudioOutput(self._audio_out)
        except Exception as e:  # pragma: no cover
            logger.warning("QtMultimedia không khả dụng: {}", e)
            self._player = None

    # ------------------------------------------------------------------ UI
    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        header_layout = QHBoxLayout()
        title_label = QLabel("🔊 AI Voice Studio")
        title_label.setObjectName("headerLabel")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        info = QLabel(
            "Giọng đọc dùng Microsoft Edge TTS (miễn phí, cần Internet). Audio của mỗi kịch bản được cache theo nội dung + giọng + tốc độ: "
            "nội dung không đổi thì không tạo lại."
        )
        info.setObjectName("infoBanner")
        info.setWordWrap(True)
        main_layout.addWidget(info)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---- Left: profiles
        left_card = QFrame()
        left_card.setObjectName("cardFrame")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(10)

        profile_title = QLabel("👥 Profile giọng đọc")
        profile_title.setObjectName("sectionTitle")
        left_layout.addWidget(profile_title)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Mặc định", "Tên profile", "Voice ID", "Tốc độ"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._on_profile_selected)
        left_layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.set_default_btn = QPushButton("⭐ Đặt mặc định")
        self.set_default_btn.clicked.connect(self._set_selected_default)
        btn_layout.addWidget(self.set_default_btn)
        self.delete_btn = QPushButton("🗑️ Xóa")
        self.delete_btn.setObjectName("dangerButton")
        self.delete_btn.clicked.connect(self._delete_selected_profile)
        btn_layout.addWidget(self.delete_btn)
        left_layout.addLayout(btn_layout)

        form_title = QLabel("➕ Thêm / sửa profile")
        form_title.setStyleSheet("font-weight: 600; margin-top: 6px;")
        left_layout.addWidget(form_title)

        form = QFormLayout()
        form.setSpacing(8)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("VD: Nữ miền Nam ngọt ngào")
        form.addRow("Tên profile:", self.name_input)

        voice_row = QHBoxLayout()
        self.voice_combo = QComboBox()
        self._fill_voices(EdgeTTSProvider.DEFAULT_VOICES)
        voice_row.addWidget(self.voice_combo, 1)
        self.refresh_voices_btn = QPushButton("🌐")
        self.refresh_voices_btn.setFixedWidth(40)
        self.refresh_voices_btn.setToolTip("Tải toàn bộ giọng tiếng Việt/Anh từ Edge TTS")
        self.refresh_voices_btn.clicked.connect(self._load_voices_online)
        voice_row.addWidget(self.refresh_voices_btn)
        form.addRow("Giọng TTS:", voice_row)

        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.5, 2.0)
        self.speed_spin.setSingleStep(0.05)
        self.speed_spin.setValue(1.0)
        self.speed_spin.setSuffix("x")
        form.addRow("Tốc độ:", self.speed_spin)

        self.is_default_cb = QCheckBox("Đặt làm giọng mặc định")
        form.addRow("", self.is_default_cb)
        left_layout.addLayout(form)

        form_btns = QHBoxLayout()
        self.add_btn = QPushButton("➕ Tạo profile")
        self.add_btn.setObjectName("primaryButton")
        self.add_btn.clicked.connect(self._add_profile)
        form_btns.addWidget(self.add_btn)
        self.update_btn = QPushButton("💾 Cập nhật profile đang chọn")
        self.update_btn.clicked.connect(self._update_profile)
        form_btns.addWidget(self.update_btn)
        left_layout.addLayout(form_btns)

        splitter.addWidget(left_card)

        # ---- Right: preview + batch
        right_card = QFrame()
        right_card.setObjectName("cardFrame")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(10)

        test_title = QLabel("🎙️ Nghe thử giọng")
        test_title.setObjectName("sectionTitle")
        right_layout.addWidget(test_title)
        test_desc = QLabel("Nhập văn bản mẫu, chọn giọng/tốc độ ở form bên trái và bấm Nghe thử.")
        test_desc.setObjectName("hintLabel")
        right_layout.addWidget(test_desc)

        self.test_text = QTextEdit()
        self.test_text.setPlainText(
            "Xin chào cả nhà! Chào mừng anh chị đã quay trở lại phiên livestream hôm nay của shop. "
            "Hôm nay shop có rất nhiều ưu đãi đang chờ mọi người!"
        )
        self.test_text.setMaximumHeight(120)
        right_layout.addWidget(self.test_text)

        test_controls = QHBoxLayout()
        self.test_btn = QPushButton("▶️ Nghe thử")
        self.test_btn.setObjectName("primaryButton")
        self.test_btn.clicked.connect(self._test_speech)
        test_controls.addWidget(self.test_btn)
        self.stop_btn = QPushButton("⏹️ Dừng")
        self.stop_btn.clicked.connect(self._stop_playback)
        test_controls.addWidget(self.stop_btn)
        self.open_btn = QPushButton("📂 Mở file vừa tạo")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_last)
        test_controls.addWidget(self.open_btn)
        test_controls.addStretch()
        right_layout.addLayout(test_controls)

        self.test_status_label = QLabel("")
        self.test_status_label.setObjectName("hintLabel")
        self.test_status_label.setWordWrap(True)
        right_layout.addWidget(self.test_status_label)

        batch_title = QLabel("⚡ Tạo audio hàng loạt cho kịch bản")
        batch_title.setObjectName("sectionTitle")
        right_layout.addWidget(batch_title)
        batch_desc = QLabel(
            "Tạo giọng đọc cho mọi kịch bản hiện tại chưa có audio (dùng giọng mặc định hoặc profile đang chọn). "
            "Làm bước này trước khi lên LIVE để playlist có đủ audio."
        )
        batch_desc.setObjectName("hintLabel")
        batch_desc.setWordWrap(True)
        right_layout.addWidget(batch_desc)
        batch_row = QHBoxLayout()
        self.batch_btn = QPushButton("🚀 Tạo audio cho tất cả kịch bản còn thiếu")
        self.batch_btn.setObjectName("accentButton")
        self.batch_btn.clicked.connect(self._batch_generate)
        batch_row.addWidget(self.batch_btn)
        batch_row.addStretch()
        right_layout.addLayout(batch_row)
        self.batch_status = QLabel("")
        self.batch_status.setObjectName("hintLabel")
        self.batch_status.setWordWrap(True)
        right_layout.addWidget(self.batch_status)

        right_layout.addStretch()
        splitter.addWidget(right_card)
        splitter.setSizes([520, 560])
        main_layout.addWidget(splitter, 1)

        self._last_preview = ""

    def _fill_voices(self, voices: list[dict]) -> None:
        current = self.voice_combo.currentData()
        self.voice_combo.clear()
        for v in voices:
            self.voice_combo.addItem(f"{v['name']}  ({v['id']})", v["id"])
        idx = self.voice_combo.findData(current)
        if idx >= 0:
            self.voice_combo.setCurrentIndex(idx)

    # ------------------------------------------------------------ profiles
    def refresh_profiles(self) -> None:
        self.session.expire_all()
        self.profiles = list(self.voice_service.get_profiles())
        self.table.setRowCount(len(self.profiles))
        for row, p in enumerate(self.profiles):
            def_item = QTableWidgetItem("⭐" if p.is_default else "")
            def_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, def_item)
            self.table.setItem(row, 1, QTableWidgetItem(p.name))
            self.table.setItem(row, 2, QTableWidgetItem(p.voice_id))
            speed_item = QTableWidgetItem(f"{p.speed:.2f}x")
            speed_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, speed_item)

    def _selected_profile(self) -> Optional[VoiceProfile]:
        row = self.table.currentRow()
        if 0 <= row < len(self.profiles):
            return self.profiles[row]
        return None

    def _on_profile_selected(self) -> None:
        p = self._selected_profile()
        if not p:
            return
        self.name_input.setText(p.name)
        idx = self.voice_combo.findData(p.voice_id)
        if idx < 0:
            self.voice_combo.addItem(f"{p.voice_id}", p.voice_id)
            idx = self.voice_combo.count() - 1
        self.voice_combo.setCurrentIndex(idx)
        self.speed_spin.setValue(p.speed)
        self.is_default_cb.setChecked(bool(p.is_default))

    def _add_profile(self) -> None:
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Thiếu tên", "Vui lòng nhập tên profile giọng đọc.")
            return
        self.voice_service.create_profile(
            name=name, voice_id=self.voice_combo.currentData(), speed=self.speed_spin.value(),
            is_default=self.is_default_cb.isChecked(),
        )
        self.name_input.clear()
        self.refresh_profiles()

    def _update_profile(self) -> None:
        p = self._selected_profile()
        if not p:
            QMessageBox.information(self, "Chọn profile", "Hãy chọn một profile trong bảng để cập nhật.")
            return
        self.voice_service.update_profile(
            p.id, name=self.name_input.text().strip() or p.name, voice_id=self.voice_combo.currentData(),
            speed=self.speed_spin.value(), is_default=self.is_default_cb.isChecked() or p.is_default,
        )
        self.refresh_profiles()

    def _set_selected_default(self) -> None:
        p = self._selected_profile()
        if not p:
            QMessageBox.warning(self, "Chọn profile", "Vui lòng chọn một profile trong bảng.")
            return
        self.voice_repo.update_profile(p.id, is_default=True)
        self.refresh_profiles()

    def _delete_selected_profile(self) -> None:
        p = self._selected_profile()
        if not p:
            return
        if len(self.profiles) <= 1:
            QMessageBox.warning(self, "Không thể xóa", "Cần giữ lại ít nhất một profile giọng đọc.")
            return
        reply = QMessageBox.question(
            self, "Xác nhận", f"Xóa profile '{p.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.voice_service.delete_profile(p.id)
            self.refresh_profiles()

    def _load_voices_online(self) -> None:
        self.refresh_voices_btn.setEnabled(False)
        worker = ListVoicesWorker()
        worker.finished.connect(self._on_voices_loaded)
        self._workers.append(worker)
        worker.start()

    def _on_voices_loaded(self, voices: list, error: str) -> None:
        self.refresh_voices_btn.setEnabled(True)
        if error or not voices:
            QMessageBox.warning(self, "Không tải được danh sách giọng", error or "Danh sách rỗng")
            return
        self._fill_voices(voices)
        self.test_status_label.setText(f"🌐 Đã tải {len(voices)} giọng từ Edge TTS.")

    # ------------------------------------------------------------- preview
    def _test_speech(self) -> None:
        text = self.test_text.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Thiếu văn bản", "Vui lòng nhập văn bản để nghe thử.")
            return
        self.test_btn.setEnabled(False)
        self.test_status_label.setText("⏳ Đang tổng hợp giọng nói (Edge TTS)...")
        worker = PreviewTTSWorker(self.voice_service, text, self.voice_combo.currentData(), self.speed_spin.value())
        worker.finished.connect(self._on_test_finished)
        self._workers.append(worker)
        worker.start()

    def _on_test_finished(self, output_path: str, error: str) -> None:
        self.test_btn.setEnabled(True)
        if error or not output_path:
            self.test_status_label.setText(f"❌ Lỗi: {error}")
            QMessageBox.warning(self, "Lỗi TTS", f"Không thể tạo giọng đọc thử:\n{error}")
            return
        self._last_preview = output_path
        self.open_btn.setEnabled(True)
        self.test_status_label.setText(f"✅ Đang phát: {output_path}")
        self._play(output_path)

    def _play(self, path: str) -> None:
        if self._player is None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            return
        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()

    def _stop_playback(self) -> None:
        if self._player is not None:
            self._player.stop()

    def _open_last(self) -> None:
        if self._last_preview:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._last_preview))

    # --------------------------------------------------------------- batch
    def _batch_generate(self) -> None:
        p = self._selected_profile() or self.voice_service.get_default_profile()
        self.batch_btn.setEnabled(False)
        self.batch_status.setText("⏳ Đang tạo audio...")
        worker = BatchTTSWorker(p.id if p else None)
        worker.progress.connect(lambda i, n, t: self.batch_status.setText(f"⏳ {i}/{n}: {t[:50]}"))
        worker.finished.connect(self._on_batch_finished)
        self._workers.append(worker)
        worker.start()

    def _on_batch_finished(self, done: int, skipped: int, errors: list) -> None:
        self.batch_btn.setEnabled(True)
        msg = f"✅ Đã tạo {done} audio, bỏ qua {skipped} (đã có cache)."
        if errors:
            msg += f" Lỗi {len(errors)}: " + "; ".join(errors[:3])
        self.batch_status.setText(msg)
        log_activity("tts", f"Tạo audio hàng loạt: {done} mới, {skipped} cache", "; ".join(errors[:3]), level="WARNING" if errors else "INFO")
