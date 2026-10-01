"""Dashboard page — main overview with status cards and recent activity."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ui.styles.dark_theme import COLORS


class StatusCard(QFrame):
    """A dashboard status card with icon, value, and label."""

    def __init__(
        self,
        icon: str,
        title: str,
        value: str = "—",
        status_color: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setMinimumSize(200, 120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        # Header row with icon and status indicator
        header = QHBoxLayout()
        icon_label = QLabel(icon)
        icon_label.setStyleSheet(f"font-size: 24px; color: {COLORS['text_accent']};")
        header.addWidget(icon_label)
        header.addStretch()

        if status_color:
            status_dot = QLabel("●")
            status_dot.setStyleSheet(f"font-size: 12px; color: {status_color};")
            header.addWidget(status_dot)

        layout.addLayout(header)

        # Value
        self._value_label = QLabel(value)
        self._value_label.setStyleSheet(
            f"font-size: 28px; font-weight: 700; color: {COLORS['text_primary']};"
        )
        layout.addWidget(self._value_label)

        # Title
        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"font-size: 12px; color: {COLORS['text_secondary']}; text-transform: uppercase; letter-spacing: 1px;"
        )
        layout.addWidget(title_label)

    def set_value(self, value: str) -> None:
        self._value_label.setText(value)

    def set_status_color(self, color: str) -> None:
        # Update status dot if present
        pass


class ActivityItem(QFrame):
    """A single activity log entry."""

    def __init__(
        self,
        level: str,
        message: str,
        timestamp: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: {COLORS['bg_medium']};
                border-left: 3px solid {self._level_color(level)};
                border-radius: 4px;
                padding: 4px;
            }}
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        level_label = QLabel(level)
        level_label.setFixedWidth(60)
        level_label.setStyleSheet(
            f"color: {self._level_color(level)}; font-weight: 600; font-size: 11px;"
        )
        layout.addWidget(level_label)

        msg_label = QLabel(message)
        msg_label.setStyleSheet(f"color: {COLORS['text_primary']}; font-size: 12px;")
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label, 1)

        if timestamp:
            time_label = QLabel(timestamp)
            time_label.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 11px;")
            layout.addWidget(time_label)

    @staticmethod
    def _level_color(level: str) -> str:
        return {
            "INFO": COLORS["status_info"],
            "WARNING": COLORS["status_warning"],
            "ERROR": COLORS["status_error"],
            "SUCCESS": COLORS["status_online"],
        }.get(level, COLORS["text_muted"])


class DashboardPage(QWidget):
    """Main dashboard showing application status overview."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(24)

        # Header
        header_label = QLabel("Bảng điều khiển")
        header_label.setObjectName("headerLabel")
        main_layout.addWidget(header_label)

        subtitle = QLabel("Tổng quan trạng thái hệ thống và hoạt động gần đây")
        subtitle.setObjectName("subHeaderLabel")
        main_layout.addWidget(subtitle)

        # Scroll area for content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(24)

        # --- Status Cards Grid ---
        cards_grid = QGridLayout()
        cards_grid.setSpacing(16)

        self._card_app = StatusCard(
            "🖥️", "Trạng thái ứng dụng", "Đang chạy",
            status_color=COLORS["status_online"]
        )
        self._card_obs = StatusCard(
            "🎬", "OBS Studio", "Chưa kết nối",
            status_color=COLORS["status_offline"]
        )
        self._card_tiktok = StatusCard(
            "📱", "TikTok", "Thủ công",
            status_color=COLORS["status_warning"]
        )
        self._card_products = StatusCard("📦", "Sản phẩm", "0")
        self._card_videos = StatusCard("🎥", "Video", "0")
        self._card_session = StatusCard("🔴", "Phiên LIVE", "Chưa có")
        self._card_tts = StatusCard("🔊", "Hàng đợi TTS", "0")
        self._card_scripts = StatusCard("📝", "Kịch bản", "0")

        cards_grid.addWidget(self._card_app, 0, 0)
        cards_grid.addWidget(self._card_obs, 0, 1)
        cards_grid.addWidget(self._card_tiktok, 0, 2)
        cards_grid.addWidget(self._card_products, 0, 3)
        cards_grid.addWidget(self._card_videos, 1, 0)
        cards_grid.addWidget(self._card_session, 1, 1)
        cards_grid.addWidget(self._card_tts, 1, 2)
        cards_grid.addWidget(self._card_scripts, 1, 3)

        scroll_layout.addLayout(cards_grid)

        # --- Recent Activity Section ---
        activity_header = QLabel("📋 Hoạt động gần đây")
        activity_header.setStyleSheet(
            f"font-size: 16px; font-weight: 600; color: {COLORS['text_primary']}; padding-top: 8px;"
        )
        scroll_layout.addWidget(activity_header)

        self._activity_layout = QVBoxLayout()
        self._activity_layout.setSpacing(6)

        # Add default welcome activities
        self._add_activity("INFO", "Ứng dụng đã khởi động thành công", "Vừa xong")
        self._add_activity("INFO", "Database đã sẵn sàng", "Vừa xong")
        self._add_activity("WARNING", "OBS Studio chưa được kết nối", "Vừa xong")
        self._add_activity("INFO", "Chế độ TikTok: Thủ công", "Vừa xong")

        scroll_layout.addLayout(self._activity_layout)
        scroll_layout.addStretch()

        scroll.setWidget(scroll_widget)
        main_layout.addWidget(scroll, 1)

    def _add_activity(self, level: str, message: str, timestamp: str = "") -> None:
        item = ActivityItem(level, message, timestamp)
        self._activity_layout.addWidget(item)

    def refresh_data(self) -> None:
        """Refresh dashboard data from database."""
        # Will be connected to actual data in Phase 2+
        pass

    def update_product_count(self, count: int) -> None:
        self._card_products.set_value(str(count))

    def update_video_count(self, count: int) -> None:
        self._card_videos.set_value(str(count))

    def update_obs_status(self, connected: bool) -> None:
        status = "Đã kết nối" if connected else "Chưa kết nối"
        self._card_obs.set_value(status)
