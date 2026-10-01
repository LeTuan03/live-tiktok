"""Main application window with sidebar navigation."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QSystemTrayIcon,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from loguru import logger

from app.ui.pages.dashboard_page import DashboardPage
from app.ui.pages.product_page import ProductPage
from app.ui.pages.media_page import MediaPage
from app.ui.pages.script_page import ScriptPage
from app.ui.pages.voice_page import VoicePage
from app.ui.pages.live_studio_page import LiveStudioPage
from app.ui.pages.scheduler_page import SchedulerPage
from app.ui.pages.comment_page import CommentPage
from app.ui.pages.settings_page import SettingsPage
from app.ui.styles.dark_theme import COLORS, get_stylesheet


# Sidebar navigation items
NAV_ITEMS = [
    ("🏠", "Bảng điều khiển"),
    ("📦", "Sản phẩm"),
    ("🎥", "Thư viện Media"),
    ("📝", "Script Studio"),
    ("🔊", "Voice Studio"),
    ("🔴", "Live Studio"),
    ("📅", "Lịch trình"),
    ("💬", "Trợ lý bình luận"),
    ("⚙️", "Cài đặt"),
]


class MainWindow(QMainWindow):
    """Main application window with sidebar navigation and stacked pages."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LiveFlow AI Studio")
        self.setMinimumSize(1200, 750)
        self.resize(1440, 900)

        # Apply dark theme
        self.setStyleSheet(get_stylesheet())

        # Setup system tray
        self._setup_tray()

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Splitter for sidebar + content
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # --- Sidebar ---
        sidebar_widget = QWidget()
        sidebar_widget.setFixedWidth(240)
        sidebar_widget.setStyleSheet(f"background-color: {COLORS['bg_darkest']};")
        sidebar_layout = QVBoxLayout(sidebar_widget)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        # App logo / title area
        logo_frame = QWidget()
        logo_frame.setStyleSheet(
            f"""
            background-color: {COLORS['bg_darkest']};
            border-bottom: 1px solid {COLORS['border']};
            """
        )
        logo_layout = QVBoxLayout(logo_frame)
        logo_layout.setContentsMargins(20, 20, 20, 16)
        logo_layout.setSpacing(4)

        app_icon = QLabel("⚡")
        app_icon.setStyleSheet("font-size: 32px;")
        logo_layout.addWidget(app_icon)

        app_title = QLabel("LiveFlow AI")
        app_title.setStyleSheet(
            f"""
            font-size: 18px;
            font-weight: 700;
            color: {COLORS['text_primary']};
            background: transparent;
            """
        )
        logo_layout.addWidget(app_title)

        app_subtitle = QLabel("TikTok Live Commerce Studio")
        app_subtitle.setStyleSheet(
            f"font-size: 11px; color: {COLORS['text_muted']}; background: transparent;"
        )
        logo_layout.addWidget(app_subtitle)

        sidebar_layout.addWidget(logo_frame)

        # Navigation list
        self._nav_list = QListWidget()
        self._nav_list.setObjectName("sidebar")
        self._nav_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        for icon, label in NAV_ITEMS:
            item = QListWidgetItem(f"  {icon}  {label}")
            item.setSizeHint(QSize(0, 44))
            self._nav_list.addItem(item)

        self._nav_list.setCurrentRow(0)
        self._nav_list.currentRowChanged.connect(self._on_nav_changed)
        sidebar_layout.addWidget(self._nav_list, 1)

        # Version info at bottom
        version_label = QLabel("v0.1.0")
        version_label.setStyleSheet(
            f"""
            color: {COLORS['text_muted']};
            font-size: 11px;
            padding: 12px 20px;
            background-color: {COLORS['bg_darkest']};
            border-top: 1px solid {COLORS['border']};
            """
        )
        sidebar_layout.addWidget(version_label)

        splitter.addWidget(sidebar_widget)

        # --- Content Area ---
        self._content_stack = QStackedWidget()
        self._content_stack.setStyleSheet(f"background-color: {COLORS['bg_dark']};")

        # Create pages
        self._dashboard_page = DashboardPage()
        self._product_page = ProductPage()
        self._media_page = MediaPage()
        self._script_page = ScriptPage()
        self._voice_page = VoicePage()
        self._live_page = LiveStudioPage()
        self._scheduler_page = SchedulerPage()
        self._comment_page = CommentPage()
        self._settings_page = SettingsPage()

        self._content_stack.addWidget(self._dashboard_page)  # 0
        self._content_stack.addWidget(self._product_page)     # 1
        self._content_stack.addWidget(self._media_page)        # 2
        self._content_stack.addWidget(self._script_page)       # 3
        self._content_stack.addWidget(self._voice_page)        # 4
        self._content_stack.addWidget(self._live_page)         # 5
        self._content_stack.addWidget(self._scheduler_page)    # 6
        self._content_stack.addWidget(self._comment_page)      # 7
        self._content_stack.addWidget(self._settings_page)     # 8

        splitter.addWidget(self._content_stack)
        splitter.setStretchFactor(0, 0)  # Sidebar doesn't stretch
        splitter.setStretchFactor(1, 1)  # Content stretches

        main_layout.addWidget(splitter)

        # --- Status Bar ---
        status_bar = QStatusBar()
        self.setStatusBar(status_bar)

        self._status_app = QLabel("● Sẵn sàng")
        self._status_app.setStyleSheet(f"color: {COLORS['status_online']}; padding: 4px 8px;")
        status_bar.addWidget(self._status_app)

        self._status_obs = QLabel("◌ OBS: Chưa kết nối")
        self._status_obs.setStyleSheet(f"color: {COLORS['status_offline']}; padding: 4px 8px;")
        status_bar.addWidget(self._status_obs)

        self._status_tiktok = QLabel("◌ TikTok: Thủ công")
        self._status_tiktok.setStyleSheet(f"color: {COLORS['status_warning']}; padding: 4px 8px;")
        status_bar.addWidget(self._status_tiktok)

        status_bar.addPermanentWidget(
            QLabel(f"LiveFlow AI Studio v0.1.0")
        )

        logger.info("Main window initialized")

    def _setup_tray(self) -> None:
        """Setup system tray icon for background operation."""
        self._tray = QSystemTrayIcon(self)

        # Create a programmatic tray icon (purple circle with lightning bolt)
        from PySide6.QtGui import QPixmap, QPainter, QColor, QFont, QBrush
        from PySide6.QtCore import QRect

        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Purple circle background
        painter.setBrush(QBrush(QColor(108, 92, 231)))
        painter.setPen(QColor(0, 0, 0, 0))
        painter.drawEllipse(2, 2, 60, 60)
        # Lightning bolt emoji
        font = QFont("Segoe UI Emoji", 28)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(QRect(0, 0, 64, 64), Qt.AlignmentFlag.AlignCenter, "⚡")
        painter.end()

        self._tray.setIcon(QIcon(pixmap))
        self.setWindowIcon(QIcon(pixmap))
        self._tray.setToolTip("LiveFlow AI Studio")

        tray_menu = QMenu()

        show_action = QAction("Hiển thị", self)
        show_action.triggered.connect(self._show_from_tray)
        tray_menu.addAction(show_action)

        tray_menu.addSeparator()

        quit_action = QAction("Thoát", self)
        quit_action.triggered.connect(self._quit_app)
        tray_menu.addAction(quit_action)

        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_nav_changed(self, index: int) -> None:
        """Handle sidebar navigation changes."""
        if 0 <= index < self._content_stack.count():
            self._content_stack.setCurrentIndex(index)
            logger.debug("Navigated to page: {}", NAV_ITEMS[index][1])

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.activateWindow()

    def _quit_app(self) -> None:
        """Quit the application safely."""
        self._tray.hide()
        QApplication.quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Minimize to system tray instead of closing."""
        event.ignore()
        self.hide()
        self._tray.showMessage(
            "LiveFlow AI Studio",
            "Ứng dụng đang chạy nền. Nhấp đúp vào biểu tượng để mở lại.",
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )
        logger.info("Window minimized to system tray")

    def update_obs_status(self, connected: bool) -> None:
        """Update OBS status in status bar and dashboard."""
        if connected:
            self._status_obs.setText("● OBS: Đã kết nối")
            self._status_obs.setStyleSheet(
                f"color: {COLORS['status_online']}; padding: 4px 8px;"
            )
        else:
            self._status_obs.setText("◌ OBS: Chưa kết nối")
            self._status_obs.setStyleSheet(
                f"color: {COLORS['status_offline']}; padding: 4px 8px;"
            )
        self._dashboard_page.update_obs_status(connected)
