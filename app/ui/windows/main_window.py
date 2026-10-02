"""Main application window with sidebar navigation."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QAction, QBrush, QCloseEvent, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from loguru import logger

from app.config.settings import APP_VERSION
from app.database.session import get_session
from app.services.settings_service import KEY_CLOSE_TO_TRAY, SettingsService
from app.ui.pages.comment_page import CommentPage
from app.ui.pages.dashboard_page import DashboardPage
from app.ui.pages.live_studio_page import LiveStudioPage
from app.ui.pages.media_page import MediaPage
from app.ui.pages.product_page import ProductPage
from app.ui.pages.scheduler_page import SchedulerPage
from app.ui.pages.script_page import ScriptPage
from app.ui.pages.settings_page import SettingsPage
from app.ui.pages.voice_page import VoicePage
from app.ui.styles.dark_theme import COLORS, get_stylesheet

# Sidebar navigation items (order == stacked widget index)
NAV_ITEMS = [
    ("🏠", "Bảng điều khiển"),
    ("📦", "Sản phẩm"),
    ("🎥", "Thư viện Media"),
    ("📝", "Script Studio"),
    ("🔊", "Voice Studio"),
    ("🔴", "Live Studio"),
    ("📅", "Playlist & Lịch trình"),
    ("💬", "Trợ lý bình luận"),
    ("⚙️", "Cài đặt"),
]


def make_app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(QColor(108, 92, 231)))
    painter.setPen(QColor(0, 0, 0, 0))
    painter.drawEllipse(2, 2, 60, 60)
    painter.setFont(QFont("Segoe UI Emoji", 28))
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(QRect(0, 0, 64, 64), Qt.AlignmentFlag.AlignCenter, "⚡")
    painter.end()
    return QIcon(pixmap)


class MainWindow(QMainWindow):
    """Main application window with sidebar navigation and stacked pages."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"LiveFlow AI Studio v{APP_VERSION}")
        self.setMinimumSize(1180, 720)
        self.resize(1480, 920)
        self.setStyleSheet(get_stylesheet())
        self._force_quit = False
        self._settings_service = SettingsService(get_session())

        self._setup_tray()

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # --- Sidebar ---
        sidebar_widget = QWidget()
        sidebar_widget.setFixedWidth(240)
        sidebar_widget.setStyleSheet(f"background-color: {COLORS['bg_darkest']};")
        sidebar_layout = QVBoxLayout(sidebar_widget)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        logo_frame = QWidget()
        logo_frame.setStyleSheet(f"background-color: {COLORS['bg_darkest']}; border-bottom: 1px solid {COLORS['border']};")
        logo_layout = QVBoxLayout(logo_frame)
        logo_layout.setContentsMargins(20, 20, 20, 16)
        logo_layout.setSpacing(4)
        app_icon = QLabel("⚡")
        app_icon.setStyleSheet("font-size: 32px; background: transparent;")
        logo_layout.addWidget(app_icon)
        app_title = QLabel("LiveFlow AI")
        app_title.setStyleSheet(f"font-size: 18px; font-weight: 700; color: {COLORS['text_primary']}; background: transparent;")
        logo_layout.addWidget(app_title)
        app_subtitle = QLabel("TikTok · Shopee Live Commerce Studio")
        app_subtitle.setStyleSheet(f"font-size: 11px; color: {COLORS['text_muted']}; background: transparent;")
        logo_layout.addWidget(app_subtitle)
        sidebar_layout.addWidget(logo_frame)

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

        version_label = QLabel(f"v{APP_VERSION}")
        version_label.setStyleSheet(
            f"color: {COLORS['text_muted']}; font-size: 11px; padding: 12px 20px; "
            f"background-color: {COLORS['bg_darkest']}; border-top: 1px solid {COLORS['border']};"
        )
        sidebar_layout.addWidget(version_label)
        splitter.addWidget(sidebar_widget)

        # --- Content Area ---
        self._content_stack = QStackedWidget()
        self._content_stack.setStyleSheet(f"background-color: {COLORS['bg_dark']};")

        self._dashboard_page = DashboardPage()
        self._product_page = ProductPage()
        self._media_page = MediaPage()
        self._script_page = ScriptPage()
        self._voice_page = VoicePage()
        self._live_page = LiveStudioPage()
        self._scheduler_page = SchedulerPage()
        self._comment_page = CommentPage()
        self._settings_page = SettingsPage()

        for page in (
            self._dashboard_page, self._product_page, self._media_page, self._script_page, self._voice_page,
            self._live_page, self._scheduler_page, self._comment_page, self._settings_page,
        ):
            self._content_stack.addWidget(page)

        # --- Cross-page wiring ---
        self._dashboard_page.navigate_requested.connect(self.navigate_to)
        self._live_page.obs_status_changed.connect(self.update_obs_status)
        self._live_page.live_state_changed.connect(self._dashboard_page.update_live_state)
        self._settings_page.settings_saved.connect(self._on_settings_saved)
        self._scheduler_page.playlists_changed.connect(self._live_page.refresh_playlists)
        self._comment_page.speak_requested.connect(self._live_page.interject_reply_audio)
        self._content_stack.currentChanged.connect(self._on_page_shown)

        splitter.addWidget(self._content_stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
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
        self._status_platform = QLabel("")
        self._status_platform.setStyleSheet(f"color: {COLORS['status_warning']}; padding: 4px 8px;")
        status_bar.addWidget(self._status_platform)
        self._refresh_platform_status()
        status_bar.addPermanentWidget(QLabel(f"LiveFlow AI Studio v{APP_VERSION}"))

        logger.info("Main window initialized")

    # ---------------------------------------------------------------- tray
    def _setup_tray(self) -> None:
        icon = make_app_icon()
        self.setWindowIcon(icon)
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(icon)
        self._tray.setToolTip("LiveFlow AI Studio")

        tray_menu = QMenu()
        show_action = QAction("Hiển thị cửa sổ", self)
        show_action.triggered.connect(self._show_from_tray)
        tray_menu.addAction(show_action)
        tray_menu.addSeparator()
        quit_action = QAction("Thoát LiveFlow", self)
        quit_action.triggered.connect(self.quit_app)
        tray_menu.addAction(quit_action)
        self._tray.setContextMenu(tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.DoubleClick, QSystemTrayIcon.ActivationReason.Trigger):
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ---------------------------------------------------------- navigation
    def navigate_to(self, index: int) -> None:
        if 0 <= index < self._content_stack.count():
            self._nav_list.setCurrentRow(index)

    def _on_nav_changed(self, index: int) -> None:
        if 0 <= index < self._content_stack.count():
            self._content_stack.setCurrentIndex(index)
            logger.debug("Navigated to page: {}", NAV_ITEMS[index][1])

    def _on_page_shown(self, index: int) -> None:
        """Refresh data-heavy pages when they become visible."""
        page = self._content_stack.widget(index)
        try:
            if page is self._dashboard_page:
                self._dashboard_page.refresh_data()
            elif page is self._product_page:
                self._product_page.refresh_data()
            elif page is self._media_page:
                self._media_page.refresh_data()
            elif page is self._script_page:
                self._script_page.refresh_products()
            elif page is self._scheduler_page:
                self._scheduler_page.refresh_playlists()
            elif page is self._live_page:
                self._live_page.refresh_playlists()
            elif page is self._comment_page:
                self._comment_page.refresh_products()
                self._comment_page.refresh_all()
        except Exception:  # noqa: BLE001
            logger.exception("Lỗi khi làm mới trang")

    def _on_settings_saved(self) -> None:
        self._live_page.reload_settings()
        self._refresh_platform_status()
        self._dashboard_page.refresh_data()

    def _refresh_platform_status(self) -> None:
        platform = self._settings_service.get_platform()
        name = "TikTok LIVE" if platform == "tiktok" else "Shopee Live"
        self._status_platform.setText(f"◌ {name}: OBS RTMP · bình luận thủ công")

    # ------------------------------------------------------------ lifecycle
    def closeEvent(self, event: QCloseEvent) -> None:
        if self._force_quit:
            event.accept()
            return

        if self._settings_service.get_bool(KEY_CLOSE_TO_TRAY, False) and self._tray.isVisible():
            event.ignore()
            self.hide()
            self._tray.showMessage(
                "LiveFlow AI Studio",
                "Ứng dụng đang chạy nền. Nhấp đúp biểu tượng ở khay hệ thống để mở lại; chuột phải → Thoát để tắt.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
            logger.info("Window minimized to system tray")
            return

        live_running = self._live_page.live_state_changed is not None and self._is_live_running()
        box = QMessageBox(self)
        box.setWindowTitle("Thoát LiveFlow AI Studio")
        box.setIcon(QMessageBox.Icon.Question)
        text = "Bạn muốn thoát ứng dụng hay thu nhỏ xuống khay hệ thống?"
        if live_running:
            text = "⚠️ Điều phối LIVE đang chạy! Thoát sẽ dừng điều phối (OBS vẫn tiếp tục stream).\n\n" + text
        box.setText(text)
        quit_btn = box.addButton("Thoát", QMessageBox.ButtonRole.AcceptRole)
        tray_btn = box.addButton("Thu nhỏ xuống khay", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Hủy", QMessageBox.ButtonRole.RejectRole)
        remember = QCheckBox("Ghi nhớ lựa chọn (đổi lại trong Cài đặt)")
        box.setCheckBox(remember)
        box.exec()
        clicked = box.clickedButton()
        if clicked is tray_btn and self._tray.isVisible():
            if remember.isChecked():
                self._settings_service.set_bool(KEY_CLOSE_TO_TRAY, True)
            event.ignore()
            self.hide()
            return
        if clicked is quit_btn:
            if remember.isChecked():
                self._settings_service.set_bool(KEY_CLOSE_TO_TRAY, False)
            self.quit_app()
            event.accept()
            return
        event.ignore()

    def _is_live_running(self) -> bool:
        o = getattr(self._live_page, "_orchestrator", None)
        return bool(o and o.isRunning())

    def quit_app(self) -> None:
        """Stop background workers and quit (never touches the OBS process)."""
        if self._force_quit:
            return
        self._force_quit = True
        logger.info("Shutting down UI...")
        try:
            self._live_page.shutdown()
        except Exception:  # noqa: BLE001
            logger.exception("Lỗi khi dừng Live Studio")
        self._tray.hide()
        QApplication.quit()

    def update_obs_status(self, connected: bool) -> None:
        if connected:
            self._status_obs.setText("● OBS: Đã kết nối")
            self._status_obs.setStyleSheet(f"color: {COLORS['status_online']}; padding: 4px 8px;")
        else:
            self._status_obs.setText("◌ OBS: Chưa kết nối")
            self._status_obs.setStyleSheet(f"color: {COLORS['status_offline']}; padding: 4px 8px;")
        self._dashboard_page.update_obs_status(connected)
