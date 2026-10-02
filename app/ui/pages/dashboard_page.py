"""Dashboard page — live status overview, quick-start checklist and recent activity."""

from __future__ import annotations

from datetime import timezone

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from loguru import logger

from app.ui.styles.dark_theme import COLORS

# Navigation indexes (must match NAV_ITEMS in main_window)
NAV_DASHBOARD, NAV_PRODUCTS, NAV_MEDIA, NAV_SCRIPT, NAV_VOICE, NAV_LIVE, NAV_SCHEDULER, NAV_COMMENTS, NAV_SETTINGS = range(9)


class StatusCard(QFrame):
    """A dashboard status card with icon, value, and label."""

    def __init__(self, icon: str, title: str, value: str = "—", status_color: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setMinimumSize(190, 110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(6)

        header = QHBoxLayout()
        icon_label = QLabel(icon)
        icon_label.setStyleSheet("font-size: 22px;")
        header.addWidget(icon_label)
        header.addStretch()
        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"font-size: 12px; color: {status_color or 'transparent'};")
        header.addWidget(self._status_dot)
        layout.addLayout(header)

        self._value_label = QLabel(value)
        self._value_label.setStyleSheet(f"font-size: 24px; font-weight: 700; color: {COLORS['text_primary']};")
        self._value_label.setWordWrap(True)
        layout.addWidget(self._value_label)

        title_label = QLabel(title.upper())
        title_label.setStyleSheet(f"font-size: 11px; color: {COLORS['text_secondary']}; letter-spacing: 1px;")
        layout.addWidget(title_label)

    def set_value(self, value: str) -> None:
        self._value_label.setText(value)

    def set_status_color(self, color: str) -> None:
        self._status_dot.setStyleSheet(f"font-size: 12px; color: {color};")


class ChecklistRow(QFrame):
    """One quick-start step with status and a 'go' button."""

    def __init__(self, step: int, title: str, hint: str, nav_index: int, on_navigate, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("innerBox")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        self._status = QLabel("○")
        self._status.setFixedWidth(22)
        self._status.setStyleSheet(f"font-size: 16px; color: {COLORS['text_muted']};")
        layout.addWidget(self._status)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        self._title = QLabel(f"Bước {step}: {title}")
        self._title.setStyleSheet("font-weight: 600;")
        text_box.addWidget(self._title)
        self._hint = QLabel(hint)
        self._hint.setObjectName("mutedLabel")
        self._hint.setWordWrap(True)
        text_box.addWidget(self._hint)
        layout.addLayout(text_box, 1)

        go = QPushButton("Đi tới →")
        go.setFixedWidth(90)
        go.clicked.connect(lambda: on_navigate(nav_index))
        layout.addWidget(go)

    def set_done(self, done: bool, hint: str | None = None) -> None:
        if done:
            self._status.setText("✅")
            self._status.setStyleSheet(f"font-size: 15px; color: {COLORS['status_online']};")
        else:
            self._status.setText("○")
            self._status.setStyleSheet(f"font-size: 16px; color: {COLORS['text_muted']};")
        if hint is not None:
            self._hint.setText(hint)


class ActivityItem(QFrame):
    """A single activity log entry."""

    def __init__(self, level: str, message: str, timestamp: str = "", parent=None) -> None:
        super().__init__(parent)
        color = self._level_color(level)
        self.setStyleSheet(
            f"QFrame {{ background-color: {COLORS['bg_medium']}; border-left: 3px solid {color}; "
            f"border-radius: 4px; }} QLabel {{ background: transparent; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(12)

        level_label = QLabel(level)
        level_label.setFixedWidth(64)
        level_label.setStyleSheet(f"color: {color}; font-weight: 600; font-size: 11px;")
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

    navigate_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._obs_connected = False
        self._live_state = "Chưa chạy"
        self._setup_ui()
        self.refresh_data()

        self._timer = QTimer(self)
        self._timer.setInterval(15000)
        self._timer.timeout.connect(self.refresh_data)
        self._timer.start()

    # ------------------------------------------------------------------ UI
    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(16)

        header_row = QHBoxLayout()
        header_label = QLabel("Bảng điều khiển")
        header_label.setObjectName("headerLabel")
        header_row.addWidget(header_label)
        header_row.addStretch()
        refresh_btn = QPushButton("🔄 Làm mới")
        refresh_btn.clicked.connect(self.refresh_data)
        header_row.addWidget(refresh_btn)
        main_layout.addLayout(header_row)

        subtitle = QLabel("Tổng quan trạng thái hệ thống, các bước chuẩn bị LIVE và hoạt động gần đây")
        subtitle.setObjectName("subHeaderLabel")
        main_layout.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(0, 0, 8, 0)
        scroll_layout.setSpacing(20)

        # --- Status Cards Grid ---
        cards_grid = QGridLayout()
        cards_grid.setSpacing(14)

        self._card_app = StatusCard("🖥️", "Ứng dụng", "Đang chạy", COLORS["status_online"])
        self._card_obs = StatusCard("🎬", "OBS Studio", "Chưa kết nối", COLORS["status_offline"])
        self._card_platform = StatusCard("📱", "Nền tảng", "TikTok · Thủ công", COLORS["status_warning"])
        self._card_ai = StatusCard("🤖", "AI Provider", "Chưa cấu hình", COLORS["status_offline"])
        self._card_products = StatusCard("📦", "Sản phẩm", "0")
        self._card_videos = StatusCard("🎥", "Video", "0")
        self._card_scripts = StatusCard("📝", "Kịch bản", "0")
        self._card_audio = StatusCard("🔊", "Audio TTS", "0")
        self._card_playlists = StatusCard("📅", "Playlist", "0")
        self._card_session = StatusCard("🔴", "Phiên LIVE", "Chưa có")
        self._card_live_state = StatusCard("📡", "Điều phối", "Chưa chạy", COLORS["status_offline"])
        self._card_errors = StatusCard("⚠️", "Cảnh báo/lỗi", "0")

        cards = [
            self._card_app, self._card_obs, self._card_platform, self._card_ai,
            self._card_products, self._card_videos, self._card_scripts, self._card_audio,
            self._card_playlists, self._card_session, self._card_live_state, self._card_errors,
        ]
        for i, card in enumerate(cards):
            cards_grid.addWidget(card, i // 4, i % 4)
        scroll_layout.addLayout(cards_grid)

        # --- Quick start checklist ---
        quick_card = QFrame()
        quick_card.setObjectName("cardFrame")
        quick_layout = QVBoxLayout(quick_card)
        quick_layout.setContentsMargins(18, 16, 18, 16)
        quick_layout.setSpacing(10)
        quick_title = QLabel("🚀 Chuẩn bị LIVE — làm theo thứ tự")
        quick_title.setObjectName("sectionTitle")
        quick_layout.addWidget(quick_title)

        nav = self.navigate_requested.emit
        self._step_ai = ChecklistRow(1, "Cấu hình AI (Gemini/OpenAI)", "Cần API Key để tạo kịch bản và gợi ý trả lời bình luận.", NAV_SETTINGS, nav)
        self._step_products = ChecklistRow(2, "Thêm sản phẩm", "Nhập tay, dán link Shopee/TikTok Shop hoặc import CSV.", NAV_PRODUCTS, nav)
        self._step_media = ChecklistRow(3, "Import video sản phẩm & gắn với sản phẩm", "Video sẽ được OBS phát khi giới thiệu sản phẩm.", NAV_MEDIA, nav)
        self._step_scripts = ChecklistRow(4, "Tạo kịch bản AI và giọng đọc TTS", "Mỗi sản phẩm cần 1 kịch bản + 1 file audio.", NAV_SCRIPT, nav)
        self._step_playlist = ChecklistRow(5, "Tạo playlist thứ tự giới thiệu", "Chọn sản phẩm, thời lượng, vòng lặp.", NAV_SCHEDULER, nav)
        self._step_obs = ChecklistRow(6, "Kết nối OBS & cấu hình RTMP TikTok/Shopee", "Bật WebSocket trong OBS, dán Stream Key, rồi bấm Bắt đầu LIVE.", NAV_LIVE, nav)
        for row in (self._step_ai, self._step_products, self._step_media, self._step_scripts, self._step_playlist, self._step_obs):
            quick_layout.addWidget(row)
        scroll_layout.addWidget(quick_card)

        # --- Recent Activity Section ---
        activity_header = QLabel("📋 Hoạt động & cảnh báo gần đây")
        activity_header.setObjectName("sectionTitle")
        scroll_layout.addWidget(activity_header)

        self._activity_container = QWidget()
        self._activity_layout = QVBoxLayout(self._activity_container)
        self._activity_layout.setContentsMargins(0, 0, 0, 0)
        self._activity_layout.setSpacing(6)
        scroll_layout.addWidget(self._activity_container)
        scroll_layout.addStretch()

        scroll.setWidget(scroll_widget)
        main_layout.addWidget(scroll, 1)

    # --------------------------------------------------------------- data
    def refresh_data(self) -> None:
        """Refresh dashboard data from database."""
        try:
            self._refresh_counts()
            self._refresh_activity()
        except Exception as e:  # noqa: BLE001
            logger.exception("Dashboard refresh failed")
            self._set_activity([("ERROR", f"Không thể tải dữ liệu bảng điều khiển: {e}", "")])

    def _refresh_counts(self) -> None:
        from app.database.repositories.product_repo import ProductRepository
        from app.database.repositories.media_repo import MediaRepository
        from app.database.session import get_db
        from app.database.models.script import Script
        from app.database.models.voice import GeneratedAudio
        from app.database.models.playlist import Playlist, PlaylistItem
        from app.services.live_session_service import LiveSessionService
        from app.services.settings_service import SettingsService
        from sqlalchemy import func, select

        product_count = ProductRepository.count()
        active_count = ProductRepository.count(is_active=True)
        video_count = MediaRepository.count("video")

        with get_db() as db:
            script_count = int(db.scalar(select(func.count(Script.id))) or 0)
            audio_count = int(db.scalar(select(func.count(GeneratedAudio.id))) or 0)
            playlist_count = int(db.scalar(select(func.count(Playlist.id))) or 0)
            item_count = int(db.scalar(select(func.count(PlaylistItem.id))) or 0)
            settings_service = SettingsService(db)
            ai_ok = settings_service.is_llm_configured()
            provider = settings_service.get_setting("llm_provider", "gemini")
            platform = settings_service.get_platform()

        self._card_products.set_value(f"{product_count}" + (f" ({active_count} bật)" if product_count else ""))
        self._card_videos.set_value(str(video_count))
        self._card_scripts.set_value(str(script_count))
        self._card_audio.set_value(str(audio_count))
        self._card_playlists.set_value(f"{playlist_count}" + (f" · {item_count} mục" if playlist_count else ""))

        self._card_ai.set_value(("Gemini" if provider == "gemini" else "OpenAI") if ai_ok else "Chưa cấu hình")
        self._card_ai.set_status_color(COLORS["status_online"] if ai_ok else COLORS["status_offline"])

        platform_name = "TikTok LIVE" if platform == "tiktok" else "Shopee Live"
        self._card_platform.set_value(f"{platform_name}\nOBS RTMP · Bình luận thủ công")

        latest = LiveSessionService().latest_session()
        if latest and latest.started_at:
            started = latest.started_at
            if started.tzinfo:
                started = started.astimezone()
            self._card_session.set_value(f"{started.strftime('%d/%m %H:%M')}\n{latest.status}")
        else:
            self._card_session.set_value("Chưa có")

        # Checklist
        self._step_ai.set_done(ai_ok)
        self._step_products.set_done(product_count > 0, f"Đã có {product_count} sản phẩm." if product_count else None)
        self._step_media.set_done(video_count > 0, f"Đã import {video_count} video." if video_count else None)
        self._step_scripts.set_done(script_count > 0 and audio_count > 0,
                                    f"{script_count} kịch bản, {audio_count} audio." if script_count else None)
        self._step_playlist.set_done(item_count > 0, f"{playlist_count} playlist, {item_count} mục." if item_count else None)
        self._step_obs.set_done(self._obs_connected)

    def _refresh_activity(self) -> None:
        from app.services.activity_log_service import recent_activity

        rows = recent_activity(limit=20)
        items: list[tuple[str, str, str]] = []
        for r in rows:
            ts = r.created_at
            if ts is not None:
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                ts_text = ts.astimezone().strftime("%d/%m %H:%M")
            else:
                ts_text = ""
            msg = r.action + (f" — {r.detail}" if r.detail else "")
            items.append((r.level, msg, ts_text))
        if not items:
            items = [("INFO", "Chưa có hoạt động nào. Hãy bắt đầu từ bước 1 ở trên.", "")]
        self._set_activity(items)
        error_count = sum(1 for lvl, _, _ in items if lvl in ("WARNING", "ERROR"))
        self._card_errors.set_value(str(error_count))
        self._card_errors.set_status_color(COLORS["status_warning"] if error_count else COLORS["status_online"])

    def _set_activity(self, items: list[tuple[str, str, str]]) -> None:
        while self._activity_layout.count():
            child = self._activity_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        for level, msg, ts in items:
            self._activity_layout.addWidget(ActivityItem(level, msg, ts))

    # ------------------------------------------------------ external hooks
    def update_obs_status(self, connected: bool) -> None:
        self._obs_connected = connected
        self._card_obs.set_value("Đã kết nối" if connected else "Chưa kết nối")
        self._card_obs.set_status_color(COLORS["status_online"] if connected else COLORS["status_offline"])
        self._step_obs.set_done(connected)

    def update_live_state(self, state_label: str, running: bool) -> None:
        self._card_live_state.set_value(state_label)
        self._card_live_state.set_status_color(COLORS["status_online"] if running else COLORS["status_offline"])
