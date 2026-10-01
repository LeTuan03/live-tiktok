"""Comment Assistant UI Page for live viewer Q&A automation."""

from __future__ import annotations

from typing import List, Optional
from PySide6.QtCore import Qt
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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.database.models.comment import CommentEvent
from app.database.repositories.comment_repo import CommentRepository
from app.database.session import get_session
from app.services.comment_service import CommentService
from app.ui.styles.dark_theme import COLORS


class CommentPage(QWidget):
    """Comment Assistant UI Page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.comments: List[CommentEvent] = []

        self._init_services()
        self._init_ui()
        self.refresh_all()

    def _init_services(self) -> None:
        session = get_session()
        self.comment_repo = CommentRepository(session)
        self.comment_service = CommentService(self.comment_repo)

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        # Header
        header_layout = QHBoxLayout()
        title_label = QLabel("💬 Trợ Lý Bình Luận Livestream")
        title_label.setObjectName("headerLabel")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        # Main Card — Live Comment Simulator & Stream
        card = QFrame()
        card.setObjectName("cardFrame")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)

        stream_title = QLabel("📥 Bình Luận Trực Tiếp & Phê Duyệt Trả Lời AI")
        stream_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        card_layout.addWidget(stream_title)

        # Comment simulator test bar
        sim_layout = QHBoxLayout()
        self.sim_user_input = QLineEdit("User_TikTok_123")
        self.sim_user_input.setFixedWidth(160)
        sim_layout.addWidget(self.sim_user_input)

        self.sim_text_input = QLineEdit("Sản phẩm này có bao nhiêu tiền vậy shop?")
        sim_layout.addWidget(self.sim_text_input)

        self.sim_btn = QPushButton("⚡ Giả Lập Bình Luận")
        self.sim_btn.setObjectName("accentButton")
        self.sim_btn.clicked.connect(self._simulate_comment)
        sim_layout.addWidget(self.sim_btn)

        card_layout.addLayout(sim_layout)
        card_layout.addSpacing(12)

        # Logs Table
        self.log_table = QTableWidget()
        self.log_table.setColumnCount(4)
        self.log_table.setHorizontalHeaderLabels(["Người Xem", "Nội Dung Bình Luận", "Gợi Ý Trả Lời AI", "Danh Mục"])
        self.log_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.log_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        card_layout.addWidget(self.log_table)

        main_layout.addWidget(card)

    def refresh_all(self) -> None:
        self.comments = self.comment_service.get_recent_comments()
        self.log_table.setRowCount(len(self.comments))
        for row, c in enumerate(self.comments):
            reply = c.suggestions[0].suggested_reply if c.suggestions else ""
            self.log_table.setItem(row, 0, QTableWidgetItem(c.username))
            self.log_table.setItem(row, 1, QTableWidgetItem(c.content))
            self.log_table.setItem(row, 2, QTableWidgetItem(reply))
            self.log_table.setItem(row, 3, QTableWidgetItem(c.category or "general"))

    def _simulate_comment(self) -> None:
        username = self.sim_user_input.text().strip() or "Anonymous"
        comment = self.sim_text_input.text().strip()
        if not comment:
            return

        self.comment_service.process_comment(username, comment)
        self.sim_text_input.clear()
        self.refresh_all()
