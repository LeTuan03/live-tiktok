"""Placeholder pages for navigation — will be fully implemented in later phases."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.ui.styles.dark_theme import COLORS


class PlaceholderPage(QWidget):
    """A placeholder page showing a coming-soon message."""

    def __init__(
        self,
        title: str,
        icon: str = "🚧",
        description: str = "Tính năng này sẽ được triển khai trong phase tiếp theo.",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(16)

        icon_label = QLabel(icon)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet("font-size: 64px;")
        layout.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setObjectName("headerLabel")
        layout.addWidget(title_label)

        desc_label = QLabel(description)
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_label.setObjectName("subHeaderLabel")
        desc_label.setWordWrap(True)
        desc_label.setMaximumWidth(500)
        layout.addWidget(desc_label)


class ProductPage(PlaceholderPage):
    def __init__(self, parent=None):
        super().__init__("Quản lý sản phẩm", "📦", "Thêm, sửa, xóa và quản lý sản phẩm livestream.", parent)


class MediaPage(PlaceholderPage):
    def __init__(self, parent=None):
        super().__init__("Thư viện Media", "🎥", "Import và quản lý video, audio cho livestream.", parent)


class ScriptPage(PlaceholderPage):
    def __init__(self, parent=None):
        super().__init__("AI Script Studio", "📝", "Tạo kịch bản giới thiệu sản phẩm bằng AI.", parent)


class VoicePage(PlaceholderPage):
    def __init__(self, parent=None):
        super().__init__("AI Voice Studio", "🔊", "Tạo giọng nói TTS từ kịch bản sản phẩm.", parent)


class LiveStudioPage(PlaceholderPage):
    def __init__(self, parent=None):
        super().__init__("Live Studio", "🔴", "Điều phối livestream với OBS Studio.", parent)


class SchedulerPage(PlaceholderPage):
    def __init__(self, parent=None):
        super().__init__("Lịch trình", "📅", "Lên lịch giới thiệu sản phẩm tự động.", parent)


class CommentPage(PlaceholderPage):
    def __init__(self, parent=None):
        super().__init__("Trợ lý bình luận", "💬", "AI hỗ trợ trả lời bình luận livestream.", parent)


class SettingsPage(PlaceholderPage):
    def __init__(self, parent=None):
        super().__init__("Cài đặt", "⚙️", "Cấu hình ứng dụng, API keys và tích hợp.", parent)
