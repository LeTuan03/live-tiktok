"""Comment Assistant — manual-mode workflow: paste → classify → grounded suggestion → approve → send yourself."""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from loguru import logger

from app.core.exceptions import TikTokCapabilityUnsupportedError
from app.database.models.comment import AISuggestion, CommentEvent
from app.database.repositories.comment_repo import CommentRepository
from app.database.repositories.product_repo import ProductRepository
from app.database.repositories.voice_repo import VoiceRepository
from app.database.session import get_session
from app.integrations.llm.factory import get_llm_provider
from app.integrations.tiktok.capabilities import Capability, Platform, get_capability
from app.services.activity_log_service import log_activity
from app.services.comment_service import CATEGORY_LABELS, CommentService
from app.services.platform_service import PlatformService
from app.services.product_service import ProductService
from app.services.settings_service import KEY_COMMENT_HONORIFIC, SettingsService
from app.services.voice_service import VoiceService
from app.ui.styles.dark_theme import COLORS


class AISuggestWorker(QThread):
    finished = Signal(int, dict, str)  # comment_id, data, error

    def __init__(self, service: CommentService, provider, comment_id: int, text: str, category: str,
                 product, policies: Dict[str, str], honorific: str) -> None:
        super().__init__()
        self.service = service
        self.provider = provider
        self.comment_id = comment_id
        self.text = text
        self.category = category
        self.product = product
        self.policies = policies
        self.honorific = honorific

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            data = loop.run_until_complete(
                self.service.suggest_with_ai(self.text, self.category, self.product, self.provider, self.policies, self.honorific)
            )
            loop.close()
            self.finished.emit(self.comment_id, data, "")
        except Exception as e:  # noqa: BLE001
            logger.exception("Lỗi AI gợi ý trả lời bình luận")
            self.finished.emit(self.comment_id, {}, str(e))


class SpeakWorker(QThread):
    finished = Signal(str, str)  # path, error

    def __init__(self, voice_service: VoiceService, text: str) -> None:
        super().__init__()
        self.voice_service = voice_service
        self.text = text

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            path = loop.run_until_complete(self.voice_service.generate_adhoc(self.text))
            loop.close()
            self.finished.emit(str(path), "")
        except Exception as e:  # noqa: BLE001
            self.finished.emit("", str(e))


STATUS_LABELS = {
    "pending": "⏳ Chờ duyệt",
    "approved": "✅ Đã duyệt",
    "rejected": "🚫 Từ chối",
    "sent": "📨 Đã gửi (API)",
    "sent_manual": "📨 Đã gửi (thủ công)",
    "failed": "❌ Lỗi gửi",
}


class CommentPage(QWidget):
    """Comment Assistant UI Page (manual mode)."""

    speak_requested = Signal(str, float)  # audio path, duration

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.comments: List[CommentEvent] = []
        self._selected: Optional[AISuggestion] = None
        self._workers: list[QThread] = []
        self._init_services()
        self._init_ui()
        self.refresh_products()
        self.refresh_all()

    def _init_services(self) -> None:
        self.session = get_session()
        self.settings_service = SettingsService(self.session)
        platform = Platform(self.settings_service.get_platform())
        self.platform_service = PlatformService(platform)
        self.comment_repo = CommentRepository(self.session)
        self.comment_service = CommentService(self.comment_repo, self.platform_service)
        self.product_service = ProductService(ProductRepository(self.session))
        self.voice_service = VoiceService(VoiceRepository(self.session))

    # ------------------------------------------------------------------ UI
    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(12)

        header_layout = QHBoxLayout()
        title_label = QLabel("💬 Trợ lý bình luận LIVE")
        title_label.setObjectName("headerLabel")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        self.mode_badge = QLabel("CHẾ ĐỘ THỦ CÔNG")
        self.mode_badge.setStyleSheet(
            f"background-color: {COLORS['accent_warning']}; color: #1b1b1b; padding: 6px 14px; border-radius: 12px; font-weight: bold;"
        )
        header_layout.addWidget(self.mode_badge)
        main_layout.addLayout(header_layout)

        self.banner = QLabel("")
        self.banner.setObjectName("warningBanner")
        self.banner.setWordWrap(True)
        main_layout.addWidget(self.banner)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---- Left: input
        left_card = QFrame()
        left_card.setObjectName("cardFrame")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(10)

        in_title = QLabel("📥 Nhập bình luận của khách")
        in_title.setObjectName("sectionTitle")
        left_layout.addWidget(in_title)

        prod_row = QHBoxLayout()
        prod_row.addWidget(QLabel("Sản phẩm đang nói đến:"))
        self.product_combo = QComboBox()
        prod_row.addWidget(self.product_combo, 1)
        refresh = QPushButton("🔄")
        refresh.setFixedWidth(40)
        refresh.clicked.connect(self.refresh_products)
        prod_row.addWidget(refresh)
        left_layout.addLayout(prod_row)

        self.user_input = QLineEdit()
        self.user_input.setPlaceholderText("Tên người xem (tùy chọn)")
        left_layout.addWidget(self.user_input)

        self.comment_input = QPlainTextEdit()
        self.comment_input.setPlaceholderText(
            "Dán 1 hoặc nhiều bình luận, mỗi dòng một bình luận.\n"
            "Hỗ trợ dạng 'tên: nội dung' — VD:\nminh_anh: giá bao nhiêu shop?\nhoa98: có size M không?"
        )
        self.comment_input.setMinimumHeight(140)
        left_layout.addWidget(self.comment_input)

        opts = QHBoxLayout()
        self.use_ai_cb = QCheckBox("Dùng AI viết câu trả lời (có căn cứ)")
        self.use_ai_cb.setChecked(True)
        opts.addWidget(self.use_ai_cb)
        opts.addStretch()
        self.honorific_combo = QComboBox()
        self.honorific_combo.setEditable(True)
        for h in ("anh/chị", "bạn", "chị", "anh", "mình", "cả nhà"):
            self.honorific_combo.addItem(h)
        self.honorific_combo.setCurrentText(self.settings_service.get_setting(KEY_COMMENT_HONORIFIC, "anh/chị"))
        self.honorific_combo.setToolTip("Cách gọi khách trong câu trả lời")
        opts.addWidget(QLabel("Gọi khách là:"))
        opts.addWidget(self.honorific_combo)
        left_layout.addLayout(opts)

        self.process_btn = QPushButton("⚡ Phân loại & gợi ý trả lời")
        self.process_btn.setObjectName("primaryButton")
        self.process_btn.clicked.connect(self._process_input)
        left_layout.addWidget(self.process_btn)

        self.status_label = QLabel("")
        self.status_label.setObjectName("hintLabel")
        self.status_label.setWordWrap(True)
        left_layout.addWidget(self.status_label)

        # Auto-reply (always off, gated)
        auto_box = QFrame()
        auto_box.setObjectName("innerBox")
        auto_layout = QVBoxLayout(auto_box)
        auto_layout.setContentsMargins(12, 10, 12, 10)
        self.auto_reply_cb = QCheckBox("Tự động gửi trả lời (auto-reply)")
        self.auto_reply_cb.setChecked(False)
        self.auto_reply_cb.setEnabled(False)
        auto_layout.addWidget(self.auto_reply_cb)
        self.auto_reply_note = QLabel("")
        self.auto_reply_note.setObjectName("mutedLabel")
        self.auto_reply_note.setWordWrap(True)
        auto_layout.addWidget(self.auto_reply_note)
        left_layout.addWidget(auto_box)

        left_layout.addStretch()
        splitter.addWidget(left_card)

        # ---- Right: queue + approval
        right_card = QFrame()
        right_card.setObjectName("cardFrame")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(10)

        q_header = QHBoxLayout()
        q_title = QLabel("🗂️ Hàng đợi bình luận & duyệt trả lời")
        q_title.setObjectName("sectionTitle")
        q_header.addWidget(q_title)
        q_header.addStretch()
        clear_btn = QPushButton("🧹 Xóa lịch sử")
        clear_btn.clicked.connect(self._clear_history)
        q_header.addWidget(clear_btn)
        right_layout.addLayout(q_header)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Thời gian", "Người xem", "Bình luận", "Phân loại", "Trạng thái"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(3, 130)
        self.table.setColumnWidth(4, 150)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        right_layout.addWidget(self.table, 1)

        reply_title = QLabel("✍️ Câu trả lời đề xuất (sửa được trước khi duyệt)")
        reply_title.setStyleSheet("font-weight: 600;")
        right_layout.addWidget(reply_title)
        self.reply_edit = QTextEdit()
        self.reply_edit.setMaximumHeight(90)
        right_layout.addWidget(self.reply_edit)
        self.grounding_label = QLabel("")
        self.grounding_label.setObjectName("mutedLabel")
        self.grounding_label.setWordWrap(True)
        right_layout.addWidget(self.grounding_label)

        actions = QHBoxLayout()
        self.approve_btn = QPushButton("✅ Duyệt")
        self.approve_btn.setObjectName("successButton")
        self.approve_btn.clicked.connect(self._approve)
        actions.addWidget(self.approve_btn)
        self.copy_btn = QPushButton("📋 Sao chép")
        self.copy_btn.clicked.connect(self._copy_reply)
        actions.addWidget(self.copy_btn)
        self.speak_btn = QPushButton("🔊 Đọc trên LIVE")
        self.speak_btn.setObjectName("accentButton")
        self.speak_btn.setToolTip("Tạo giọng đọc AI cho câu trả lời và phát xen vào phiên LIVE đang điều phối")
        self.speak_btn.clicked.connect(self._speak_reply)
        actions.addWidget(self.speak_btn)
        self.sent_btn = QPushButton("📨 Đã gửi thủ công")
        self.sent_btn.setToolTip("Đánh dấu bạn đã tự dán câu trả lời lên TikTok/Shopee")
        self.sent_btn.clicked.connect(self._mark_sent)
        actions.addWidget(self.sent_btn)
        self.reject_btn = QPushButton("🚫 Bỏ")
        self.reject_btn.setObjectName("dangerButton")
        self.reject_btn.clicked.connect(self._reject)
        actions.addWidget(self.reject_btn)
        actions.addStretch()
        right_layout.addLayout(actions)

        splitter.addWidget(right_card)
        splitter.setSizes([430, 770])
        main_layout.addWidget(splitter, 1)

        self._refresh_banner()
        self._set_action_buttons(False)

    def _refresh_banner(self) -> None:
        platform = self.platform_service.platform
        read_info = get_capability(platform, Capability.READ_COMMENTS)
        send_info = get_capability(platform, Capability.SEND_COMMENT)
        self.banner.setText(
            f"{platform.display_name}: đọc bình luận — {read_info.status.label}; gửi trả lời — {send_info.status.label}. "
            "Quy trình: dán bình luận → AI phân loại và gợi ý câu trả lời chỉ dựa trên dữ liệu sản phẩm/chính sách shop → "
            "bạn duyệt → sao chép dán lên app hoặc để AI đọc bằng giọng nói trên LIVE."
        )
        self.auto_reply_note.setText(
            f"Auto-reply bị khóa vì {platform.display_name} chưa cung cấp API gửi bình luận hợp lệ cho ứng dụng này. "
            "LiveFlow không giả lập thao tác người dùng để vượt giới hạn nền tảng."
        )

    # --------------------------------------------------------------- data
    def refresh_products(self) -> None:
        current = self.product_combo.currentData()
        self.product_combo.blockSignals(True)
        self.product_combo.clear()
        self.product_combo.addItem("— Không gắn sản phẩm cụ thể —", None)
        for p in self.product_service.list_products(is_active=True):
            self.product_combo.addItem(p.name, p.id)
        idx = self.product_combo.findData(current)
        self.product_combo.setCurrentIndex(max(idx, 0))
        self.product_combo.blockSignals(False)

    def set_current_product(self, product_id: Optional[int]) -> None:
        """Called by Live Studio when the presented product changes."""
        idx = self.product_combo.findData(product_id)
        if idx >= 0:
            self.product_combo.setCurrentIndex(idx)

    def refresh_all(self) -> None:
        self.session.expire_all()
        self.comments = list(self.comment_service.get_recent_comments(200))
        self.table.setRowCount(len(self.comments))
        for row, c in enumerate(self.comments):
            sug = c.suggestions[-1] if c.suggestions else None
            ts = c.received_at.astimezone().strftime("%H:%M") if c.received_at and c.received_at.tzinfo else (
                c.received_at.strftime("%H:%M") if c.received_at else ""
            )
            status = STATUS_LABELS.get(sug.status, sug.status) if sug else ("🚫 Bỏ qua" if c.is_spam else "—")
            if sug and sug.needs_data and sug.status == "pending":
                status += " ⚠️"
            cells = [ts, c.username, c.content, CATEGORY_LABELS.get(c.category or "other", c.category or ""), status]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, c.id)
                if c.is_spam:
                    item.setForeground(Qt.GlobalColor.gray)
                self.table.setItem(row, col, item)

    # ------------------------------------------------------------ process
    def _parse_lines(self) -> List[tuple[str, str]]:
        default_user = self.user_input.text().strip() or "Khách"
        out: List[tuple[str, str]] = []
        for raw in self.comment_input.toPlainText().splitlines():
            line = raw.strip()
            if not line:
                continue
            user, text = default_user, line
            if ":" in line and len(line.split(":", 1)[0]) <= 30:
                head, tail = line.split(":", 1)
                if tail.strip():
                    user, text = head.strip() or default_user, tail.strip()
            out.append((user, text))
        return out

    def _process_input(self) -> None:
        pairs = self._parse_lines()
        if not pairs:
            QMessageBox.information(self, "Chưa có bình luận", "Hãy dán ít nhất một bình luận.")
            return
        honorific = self.honorific_combo.currentText().strip() or "anh/chị"
        self.settings_service.set_setting(KEY_COMMENT_HONORIFIC, honorific)
        pid = self.product_combo.currentData()
        product = self.product_service.get_product(pid) if pid else None
        policies = self.settings_service.get_shop_policies()

        provider = None
        if self.use_ai_cb.isChecked():
            try:
                provider = get_llm_provider(self.settings_service)
            except Exception as e:  # noqa: BLE001
                self.status_label.setText(f"⚠️ AI chưa sẵn sàng ({e}). Dùng câu trả lời mẫu có căn cứ.")

        created = 0
        for user, text in pairs:
            event, suggestion = self.comment_service.process_comment(
                user, text, product=product, policies=policies, honorific=honorific
            )
            created += 1
            if provider and suggestion is not None and not event.is_spam:
                worker = AISuggestWorker(self.comment_service, provider, event.id, text, event.category or "other",
                                         product, policies, honorific)
                worker.finished.connect(self._on_ai_suggested)
                worker.finished.connect(lambda *_a, w=worker: self._workers.remove(w) if w in self._workers else None)
                self._workers.append(worker)
                worker.start()

        self.comment_input.clear()
        log_activity("comment", f"Xử lý {created} bình luận", f"Sản phẩm: {product.name if product else '—'}")
        self.status_label.setText(
            f"✅ Đã xử lý {created} bình luận." + (" AI đang viết câu trả lời..." if provider else "")
        )
        self.refresh_all()
        if self.table.rowCount():
            self.table.selectRow(0)

    def _on_ai_suggested(self, comment_id: int, data: dict, error: str) -> None:
        if error or not data.get("reply"):
            self.status_label.setText(f"⚠️ AI không trả lời được ({error or 'rỗng'}) — dùng câu mẫu.")
            return
        comment = self.comment_repo.get_comment(comment_id)
        if not comment:
            return
        # Replace the template suggestion with the AI one (only while still pending).
        pending = [s for s in comment.suggestions if s.status == "pending"]
        if pending:
            self.comment_repo.update_suggestion(
                pending[-1].id, suggested_reply=data["reply"],
            )
            s = self.comment_repo.get_suggestion(pending[-1].id)
            if s:
                s.needs_data = bool(data.get("needs_data"))
                s.grounding_note = f"AI: {data.get('note', '')}"
                self.session.commit()
        else:
            self.comment_service.attach_ai_suggestion(comment_id, data, comment.product_id)
        selected_id = self._selected.comment_id if self._selected else None
        self.refresh_all()
        if selected_id == comment_id:
            self._select_comment(comment_id)
        if not self._workers or all(not w.isRunning() for w in self._workers):
            self.status_label.setText("✅ AI đã gợi ý xong. Hãy duyệt từng câu trả lời.")

    # ----------------------------------------------------------- approval
    def _select_comment(self, comment_id: int) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == comment_id:
                self.table.selectRow(row)
                break

    def _on_row_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.comments):
            self._selected = None
            self.reply_edit.clear()
            self.grounding_label.setText("")
            self._set_action_buttons(False)
            return
        c = self.comments[row]
        sug = c.suggestions[-1] if c.suggestions else None
        self._selected = sug
        if sug:
            self.reply_edit.setPlainText(sug.suggested_reply)
            note = sug.grounding_note or ""
            if sug.needs_data:
                note = "⚠️ THIẾU DỮ LIỆU — câu trả lời chỉ hứa kiểm tra lại, không đưa thông tin. " + note
            self.grounding_label.setText(note)
            self._set_action_buttons(True)
        else:
            self.reply_edit.setPlainText("")
            self.grounding_label.setText("Bình luận spam/không phù hợp — không gợi ý trả lời.")
            self._set_action_buttons(False)

    def _set_action_buttons(self, enabled: bool) -> None:
        for b in (self.approve_btn, self.copy_btn, self.speak_btn, self.sent_btn, self.reject_btn):
            b.setEnabled(enabled)

    def _approve(self) -> None:
        if not self._selected:
            return
        text = self.reply_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Trống", "Câu trả lời không được để trống.")
            return
        self.comment_service.approve(self._selected.id, edited_reply=text)
        self._reselect_after_refresh()

    def _reject(self) -> None:
        if not self._selected:
            return
        self.comment_service.reject(self._selected.id)
        self._reselect_after_refresh()

    def _copy_reply(self) -> None:
        text = self.reply_edit.toPlainText().strip()
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        if self._selected and self._selected.status == "pending":
            self.comment_service.approve(self._selected.id, edited_reply=text)
        self.status_label.setText("📋 Đã sao chép — dán vào ô chat trên TikTok/Shopee rồi bấm 'Đã gửi thủ công'.")
        self._reselect_after_refresh()

    def _mark_sent(self) -> None:
        if not self._selected:
            return
        # Guard: automated sending must never be reported as success.
        try:
            self.comment_service.send_reply(self._selected.id)
        except TikTokCapabilityUnsupportedError:
            pass
        self.comment_service.mark_sent_manually(self._selected.id)
        log_activity("comment", "Đã gửi trả lời (thủ công)", self.reply_edit.toPlainText()[:120])
        self._reselect_after_refresh()

    def _speak_reply(self) -> None:
        text = self.reply_edit.toPlainText().strip()
        if not text:
            return
        if self._selected and self._selected.status == "pending":
            self.comment_service.approve(self._selected.id, edited_reply=text)
        self.speak_btn.setEnabled(False)
        self.status_label.setText("⏳ Đang tạo giọng đọc cho câu trả lời...")
        worker = SpeakWorker(self.voice_service, text)
        worker.finished.connect(self._on_speech_ready)
        worker.finished.connect(lambda *_a, w=worker: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(worker)
        worker.start()

    def _on_speech_ready(self, path: str, error: str) -> None:
        self.speak_btn.setEnabled(True)
        if error or not path:
            QMessageBox.warning(self, "Lỗi TTS", f"Không tạo được giọng đọc: {error}")
            self.status_label.setText("")
            return
        from app.services.voice_service import estimate_mp3_duration

        duration = estimate_mp3_duration(path) or 6.0
        self.speak_requested.emit(path, float(duration))
        if self._selected:
            self.comment_service.mark_sent_manually(self._selected.id, by="voice")
        log_activity("comment", "Đọc câu trả lời trên LIVE", self.reply_edit.toPlainText()[:120])
        self.status_label.setText("🔊 Câu trả lời sẽ được đọc trên LIVE (xen vào sau câu đang nói).")
        self._reselect_after_refresh()

    def _reselect_after_refresh(self) -> None:
        cid = self._selected.comment_id if self._selected else None
        self.refresh_all()
        if cid is not None:
            self._select_comment(cid)

    def _clear_history(self) -> None:
        reply = QMessageBox.question(
            self, "Xóa lịch sử", "Xóa toàn bộ bình luận và gợi ý đã lưu?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            n = self.comment_service.clear_history()
            self.status_label.setText(f"🧹 Đã xóa {n} bình luận.")
            self.refresh_all()
