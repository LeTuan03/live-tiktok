"""Google Flow Studio — làm toàn bộ việc tạo video Flow ngay trong LiveFlow.

Flow không có API công khai, nên trang này nhúng thẳng flow.google.com vào app:

    đăng nhập Google 1 lần  ->  LiveFlow tự dán prompt  ->  tự bấm Tạo
    ->  tự bắt file tải về  ->  tự chuẩn hoá 1080x1920  ->  tự nạp vào thư viện/dự án

Người vận hành chỉ bấm **Chạy tự động**. Nếu Google đổi giao diện Flow làm phần tự bấm
không nhận ra nút, các nút thủ công bên cạnh vẫn làm được từng bước — và vẫn ở trong app,
không phải mở trình duyệt khác.
"""

from __future__ import annotations

import time
import webbrowser
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from loguru import logger

from app.config.settings import get_settings
from app.integrations.flow.browser_automation import (
    CHROME_USER_AGENT,
    js_click_download,
    js_click_generate,
    js_dl_state,
    js_download_src,
    js_set_prompt,
    js_state,
    js_video_srcs,
    library_js,
    load_selectors,
    parse_result,
)
from app.integrations.flow.constants import FLOW_URL, model_label
from app.integrations.flow.credits import CreditLedger
from app.integrations.flow.importer import ImportResult, import_job
from app.integrations.flow.pack import (
    KIND_AVATAR_BASE,
    KIND_AVATAR_CUE,
    KIND_PORTRAIT,
    KIND_PRODUCT_SCENE,
    FlowJob,
    FlowPack,
    list_packs,
)
from app.integrations.flow.watcher import FlowDownloadWatcher, default_download_dirs
from app.ui.styles.dark_theme import COLORS

# Trạng thái của bộ chạy tự động
S_IDLE = "idle"
S_FILL = "fill"
S_GENERATE = "generate"
S_WAIT_VIDEO = "wait_video"
S_DOWNLOAD = "download"
S_IMPORT = "import"
S_MANUAL_WAIT = "manual_wait"

STATUS_LABEL = {
    "PENDING": "⏳ chờ",
    "IMPORTED": "✅ đã nạp",
    "SKIPPED": "⏭ bỏ qua",
}


class FlowImportWorker(QThread):
    """Chạy ffmpeg chuẩn hoá + nạp clip ngoài luồng giao diện."""

    done = Signal(object)

    def __init__(self, pack: FlowPack, job: FlowJob, source: Path,
                 avatar_id: Optional[int], parent=None) -> None:
        super().__init__(parent)
        self._pack, self._job, self._source, self._avatar_id = pack, job, source, avatar_id

    def run(self) -> None:  # noqa: D102
        try:
            result = import_job(self._pack, self._job, self._source, avatar_id=self._avatar_id)
        except Exception as e:  # noqa: BLE001
            logger.exception("Nạp clip Flow thất bại")
            result = ImportResult(False, self._job.job_id, message=str(e))
        self.done.emit(result)


class FlowStitchWorker(QThread):
    """Ghép các cảnh đã nạp thành video bán hàng hoàn chỉnh."""

    progress = Signal(str, int)
    done = Signal(str, str)

    def __init__(self, pack: FlowPack, parent=None) -> None:
        super().__init__(parent)
        self._pack = pack

    def run(self) -> None:  # noqa: D102
        import asyncio

        try:
            from app.services.veo_showcase_service import VeoProject, VeoShowcaseService

            project_file = self._pack.dir / "project.json"
            if not project_file.exists():
                self.done.emit("", "Không thấy project.json trong gói việc — hãy tạo lại gói.")
                return
            project = VeoProject.model_validate_json(project_file.read_text(encoding="utf-8"))
            service = VeoShowcaseService()

            async def work() -> str:
                scenes = [j for j in self._pack.jobs if j.kind == KIND_PRODUCT_SCENE and j.is_done]
                for i, job in enumerate(scenes, start=1):
                    self.progress.emit(
                        f"Đồng bộ tiếng & phụ đề cảnh {job.scene_index} ({i}/{len(scenes)})...",
                        int(10 + 60 * i / max(len(scenes), 1)),
                    )
                    await service.attach_external_clip(project, job.scene_index, job.imported_path)
                self.progress.emit("Đang ghép video hoàn chỉnh...", 85)
                return await service.stitch_final_showcase_video(project)

            final = asyncio.run(work())
            project_file.write_text(project.model_dump_json(indent=2), encoding="utf-8")
            self.done.emit(final, "")
        except Exception as e:  # noqa: BLE001
            logger.exception("Ghép video Flow thất bại")
            self.done.emit("", str(e))


class FlowPage(QWidget):
    """Trang điều phối Flow: danh sách việc bên trái, trình duyệt Flow bên phải."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._settings = get_settings()
        self._ledger = CreditLedger(default_total=self._settings.flow_credits_total)
        self._selectors = load_selectors()

        self._pack: Optional[FlowPack] = None
        self._packs: List[FlowPack] = []
        self._avatar_id: Optional[int] = None

        self._state = S_IDLE
        self._auto_running = False
        self._active_job: Optional[FlowJob] = None
        self._known_srcs: List[str] = []
        self._deadline = 0.0
        self._dl_deadline = 0.0
        self._dl_fallback_done = False
        self._import_worker: Optional[FlowImportWorker] = None
        self._stitch_worker: Optional[FlowStitchWorker] = None
        self._import_queue: List[Path] = []
        self._downloads: List[Any] = []
        self._web_ready = False

        self._view = None
        self._page = None
        self._profile = None

        self._build_ui()
        self._init_browser()

        self._watcher = FlowDownloadWatcher(
            [self._settings.effective_flow_downloads_dir, *default_download_dirs()]
        )
        self._watcher.prime()

        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self.refresh_packs()
        self._update_credit_label()

    # ------------------------------------------------------------------- ui
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("🎞️ Google Flow Studio")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()

        self._credit_label = QLabel()
        self._credit_label.setStyleSheet(
            f"background:{COLORS['bg_light']}; border:1px solid {COLORS['border']};"
            f"border-radius:8px; padding:6px 12px; color:{COLORS['text_primary']};"
            "font-weight:600;"
        )
        header.addWidget(self._credit_label)

        sync_btn = QPushButton("🔄 Đồng bộ số dư")
        sync_btn.setToolTip("Nhập số tín dụng thật đang hiện trên Flow để chốt lại sổ")
        sync_btn.clicked.connect(self._on_sync_credits)
        header.addWidget(sync_btn)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([460, 900])
        root.addWidget(splitter, 1)

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(420)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 10, 0)
        layout.setSpacing(10)

        # --- chọn / tạo gói việc ---
        pack_row = QHBoxLayout()
        self._pack_combo = QComboBox()
        self._pack_combo.currentIndexChanged.connect(self._on_pack_selected)
        pack_row.addWidget(self._pack_combo, 1)
        reload_btn = QPushButton("🔄")
        reload_btn.setFixedWidth(36)
        reload_btn.setToolTip("Nạp lại danh sách gói việc")
        reload_btn.clicked.connect(self.refresh_packs)
        pack_row.addWidget(reload_btn)
        layout.addLayout(pack_row)

        new_row = QHBoxLayout()
        avatar_pack_btn = QPushButton("💃 Gói người mẫu")
        avatar_pack_btn.setToolTip("1 ảnh chân dung + 1 clip nền + 9 clip cử chỉ")
        avatar_pack_btn.clicked.connect(self._on_new_avatar_pack)
        new_row.addWidget(avatar_pack_btn)
        product_pack_btn = QPushButton("📦 Gói sản phẩm")
        product_pack_btn.setToolTip("AI viết kịch bản nhiều cảnh cho một sản phẩm")
        product_pack_btn.clicked.connect(self._on_new_product_pack)
        new_row.addWidget(product_pack_btn)
        layout.addLayout(new_row)

        # --- bảng việc ---
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["#", "Việc cần tạo", "Tín dụng", "Trạng thái"])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        head = self._table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._table, 1)

        # --- điều khiển ---
        self._run_btn = QPushButton("▶️ Chạy tự động cả gói")
        self._run_btn.setObjectName("primaryButton")
        self._run_btn.clicked.connect(self._on_run_auto)
        layout.addWidget(self._run_btn)

        row2 = QHBoxLayout()
        self._stop_btn = QPushButton("⏹ Dừng")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop_auto)
        row2.addWidget(self._stop_btn)
        one_btn = QPushButton("⤵️ Chạy việc đang chọn")
        one_btn.clicked.connect(self._on_run_selected)
        row2.addWidget(one_btn)
        layout.addLayout(row2)

        row3 = QHBoxLayout()
        paste_btn = QPushButton("📋 Dán prompt")
        paste_btn.setToolTip("Chỉ dán prompt của việc đang chọn vào Flow, không bấm tạo")
        paste_btn.clicked.connect(self._on_manual_paste)
        row3.addWidget(paste_btn)
        gen_btn = QPushButton("⚡ Bấm Tạo")
        gen_btn.clicked.connect(self._on_manual_generate)
        row3.addWidget(gen_btn)
        dl_btn = QPushButton("⬇️ Tải clip")
        dl_btn.setToolTip("Bấm nút Download của Flow cho clip mới nhất")
        dl_btn.clicked.connect(self._on_manual_download)
        row3.addWidget(dl_btn)
        layout.addLayout(row3)

        row4 = QHBoxLayout()
        self._stitch_btn = QPushButton("🎬 Ghép video hoàn chỉnh")
        self._stitch_btn.setToolTip("Chỉ dùng cho gói sản phẩm, khi mọi cảnh đã nạp xong")
        self._stitch_btn.clicked.connect(self._on_stitch)
        self._stitch_btn.setEnabled(False)
        row4.addWidget(self._stitch_btn)
        skip_btn = QPushButton("⏭ Bỏ qua việc")
        skip_btn.clicked.connect(self._on_skip_job)
        row4.addWidget(skip_btn)
        layout.addLayout(row4)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFixedHeight(150)
        self._log.setStyleSheet(
            f"background:{COLORS['bg_darkest']}; color:{COLORS['text_secondary']};"
            f"border:1px solid {COLORS['border']}; border-radius:8px; font-family:Consolas;"
        )
        layout.addWidget(self._log)
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        back_btn = QPushButton("←")
        back_btn.setFixedWidth(34)
        back_btn.clicked.connect(lambda: self._view and self._view.back())
        bar.addWidget(back_btn)
        reload_btn = QPushButton("⟳")
        reload_btn.setFixedWidth(34)
        reload_btn.clicked.connect(lambda: self._view and self._view.reload())
        bar.addWidget(reload_btn)
        home_btn = QPushButton("🏠 Mở Flow")
        home_btn.clicked.connect(self._open_flow)
        bar.addWidget(home_btn)

        self._browser_status = QLabel("Chưa mở")
        self._browser_status.setStyleSheet(f"color:{COLORS['text_muted']};")
        bar.addWidget(self._browser_status, 1)

        ext_btn = QPushButton("🌐 Mở bằng Chrome")
        ext_btn.setToolTip(
            "Dự phòng khi Google chặn đăng nhập trong trình duyệt nhúng. "
            "LiveFlow vẫn tự nhận file bạn tải về trong thư mục Downloads."
        )
        ext_btn.clicked.connect(lambda: webbrowser.open(FLOW_URL))
        bar.addWidget(ext_btn)
        layout.addLayout(bar)

        self._browser_holder = QFrame()
        self._browser_holder.setFrameShape(QFrame.Shape.NoFrame)
        self._browser_layout = QVBoxLayout(self._browser_holder)
        self._browser_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._browser_holder, 1)
        return panel

    # -------------------------------------------------------------- browser
    def _init_browser(self) -> None:
        """Dựng trình duyệt nhúng. Thiếu QtWebEngine thì vẫn chạy được ở chế độ dự phòng."""
        try:
            from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except Exception as e:  # pragma: no cover
            logger.warning("Không dùng được QtWebEngine: {}", e)
            msg = QLabel(
                "⚠️ Không nạp được trình duyệt nhúng (QtWebEngine).\n\n"
                "Cài bằng:   pip install PySide6-Addons\n\n"
                "Trong lúc chờ, bấm '🌐 Mở bằng Chrome' rồi tải clip như bình thường — "
                "LiveFlow vẫn tự nhận file trong thư mục Downloads và nạp vào đúng chỗ."
            )
            msg.setWordWrap(True)
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            msg.setStyleSheet(f"color:{COLORS['accent_warning']}; padding:24px;")
            self._browser_layout.addWidget(msg)
            return

        storage = self._settings.data_dir / "flow_browser"
        storage.mkdir(parents=True, exist_ok=True)

        # Profile có tên = cookie/đăng nhập được giữ lại giữa các lần mở app.
        # KHÔNG gán parent cho profile: profile phải sống lâu hơn page, nếu để Qt xoá
        # profile trước page thì Chromium cảnh báo "Expect troubles" và có thể tụt.
        self._profile = QWebEngineProfile("liveflow_flow")
        self._profile.setPersistentStoragePath(str(storage))
        self._profile.setCachePath(str(storage / "cache"))
        self._profile.setHttpUserAgent(CHROME_USER_AGENT)
        try:
            self._profile.setPersistentCookiesPolicy(
                QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
            )
        except Exception:
            pass
        self._profile.setDownloadPath(str(self._settings.effective_flow_downloads_dir))
        self._profile.downloadRequested.connect(self._on_download_requested)

        self._view = QWebEngineView(self)
        self._page = QWebEnginePage(self._profile, self._view)
        self._view.setPage(self._page)
        self._view.loadFinished.connect(self._on_load_finished)
        self._browser_layout.addWidget(self._view)

        # Dọn theo đúng thứ tự khi tắt app: page trước, profile sau.
        qapp = QApplication.instance()
        if qapp is not None:
            qapp.aboutToQuit.connect(self._shutdown_browser)

        self._open_flow()

    def _shutdown_browser(self) -> None:
        """Xoá page trước rồi mới nhả profile — tránh cảnh báo của Chromium khi tắt app."""
        try:
            self._timer.stop()
        except Exception:
            pass
        try:
            if self._view is not None:
                self._view.stop()
                self._view.setPage(None)
            if self._page is not None:
                self._page.deleteLater()
        except Exception as e:  # noqa: BLE001
            logger.debug("Lỗi khi dọn trình duyệt nhúng: {}", e)
        finally:
            self._page = None

    def _open_flow(self) -> None:
        if self._view is None:
            webbrowser.open(FLOW_URL)
            return
        self._view.setUrl(QUrl(FLOW_URL))
        self._log_msg("Đang mở flow.google.com — đăng nhập Google một lần, app sẽ ghi nhớ.")

    def _on_load_finished(self, ok: bool) -> None:
        if not ok or self._page is None:
            self._browser_status.setText("Tải trang thất bại")
            return
        self._page.runJavaScript(library_js(self._selectors), self._on_library_installed)

    def _on_library_installed(self, result: Any) -> None:
        self._web_ready = bool(result)
        self._poll_browser_state()

    def _run_js(self, js: str, callback=None) -> bool:
        if self._page is None:
            self._log_msg("⚠️ Không có trình duyệt nhúng — dùng nút '🌐 Mở bằng Chrome'.")
            return False
        self._page.runJavaScript(js, callback or (lambda _: None))
        return True

    def _poll_browser_state(self) -> None:
        if self._page is None:
            return
        self._page.runJavaScript(js_state(), self._on_browser_state)

    def _on_browser_state(self, raw: Any) -> None:
        state = parse_result(raw)
        if not isinstance(state, dict):
            self._browser_status.setText("Đang tải giao diện Flow...")
            return
        if state.get("onSignIn"):
            self._browser_status.setText("⚠️ Đang ở trang đăng nhập Google — đăng nhập để dùng tiếp")
            return
        if state.get("onAbout"):
            self._browser_status.setText(
                "⚠️ Đang ở trang giới thiệu Flow — bấm 'Sign in' rồi mở một Project"
            )
            return
        box = "✅ thấy ô prompt" if state.get("promptFound") else "⚠️ chưa mở Project (không thấy ô prompt)"
        btn = "✅ thấy nút Tạo" if state.get("generateFound") else "⚠️ chưa thấy nút Tạo"
        self._browser_status.setText(f"✅ đã đăng nhập  ·  {box}  ·  {btn}")

    # ---------------------------------------------------------------- packs
    def refresh_packs(self) -> None:
        """Nạp lại danh sách gói việc trong thư mục dữ liệu."""
        current = self._pack.pack_id if self._pack else ""
        self._packs = list_packs(self._settings.effective_flow_dir)
        self._pack_combo.blockSignals(True)
        self._pack_combo.clear()
        if not self._packs:
            self._pack_combo.addItem("— Chưa có gói việc nào, bấm nút bên dưới để tạo —", None)
        for p in self._packs:
            done = len(p.done_jobs)
            self._pack_combo.addItem(
                f"{p.title}  ({done}/{len(p.jobs)} xong · {p.credits_remaining_estimate} tín dụng còn lại)",
                p.pack_id,
            )
        self._pack_combo.blockSignals(False)
        idx = self._pack_combo.findData(current) if current else 0
        self._pack_combo.setCurrentIndex(max(0, idx))
        self._on_pack_selected()

    def _on_pack_selected(self) -> None:
        pack_id = self._pack_combo.currentData()
        self._pack = next((p for p in self._packs if p.pack_id == pack_id), None)
        self._avatar_id = None
        if self._pack:
            raw = self._pack.meta.get("avatar_id")
            self._avatar_id = int(raw) if raw else None
        self._refresh_table()

    def set_pack(self, pack: FlowPack, avatar_id: Optional[int] = None) -> None:
        """Chọn một gói việc vừa tạo (gọi từ trang Người mẫu / Sản phẩm)."""
        if avatar_id:
            pack.meta["avatar_id"] = int(avatar_id)
            pack.save()
        self.refresh_packs()
        idx = self._pack_combo.findData(pack.pack_id)
        if idx >= 0:
            self._pack_combo.setCurrentIndex(idx)

    def _refresh_table(self) -> None:
        self._table.setRowCount(0)
        if not self._pack:
            self._stitch_btn.setEnabled(False)
            return
        for job in self._pack.jobs:
            row = self._table.rowCount()
            self._table.insertRow(row)
            order = QTableWidgetItem(f"{job.order:02d}")
            order.setData(Qt.ItemDataRole.UserRole, job.job_id)
            self._table.setItem(row, 0, order)
            self._table.setItem(row, 1, QTableWidgetItem(job.title))
            self._table.setItem(row, 2, QTableWidgetItem(str(job.credits)))
            status = QTableWidgetItem(STATUS_LABEL.get(job.status, job.status))
            if job.status == "IMPORTED":
                status.setForeground(Qt.GlobalColor.green)
            self._table.setItem(row, 3, status)

        scenes = [j for j in self._pack.jobs if j.kind == KIND_PRODUCT_SCENE]
        self._stitch_btn.setEnabled(bool(scenes) and all(j.is_done for j in scenes))
        if self._pack.pending_jobs:
            self._run_btn.setText(
                f"▶️ Chạy tự động ({len(self._pack.pending_jobs)} việc còn lại · "
                f"{self._pack.credits_remaining_estimate} tín dụng)"
            )
        else:
            self._run_btn.setText("▶️ Chạy tự động cả gói (đã xong hết)")

    def _selected_job(self) -> Optional[FlowJob]:
        if not self._pack:
            return None
        row = self._table.currentRow()
        if row < 0:
            return self._pack.next_pending()
        item = self._table.item(row, 0)
        return self._pack.job(item.data(Qt.ItemDataRole.UserRole)) if item else None

    # -------------------------------------------------------------- credits
    def _update_credit_label(self) -> None:
        self._credit_label.setText(
            f"💳 Tín dụng Flow: {self._ledger.remaining} còn  ·  {self._ledger.spent} đã dùng"
        )

    def _on_sync_credits(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        value, ok = QInputDialog.getInt(
            self,
            "Đồng bộ số dư tín dụng",
            "Nhập số tín dụng đang hiện trên flow.google.com:",
            self._ledger.remaining, 0, 1_000_000, 10,
        )
        if ok:
            self._ledger.sync_to(value)
            self._update_credit_label()
            self._log_msg(f"Đã chốt số dư tín dụng = {value}")

    # ------------------------------------------------------------ tạo gói
    def _on_new_avatar_pack(self) -> None:
        from app.ui.dialogs.flow_pack_dialogs import AvatarPackDialog

        dlg = AvatarPackDialog(self)
        if dlg.exec() and dlg.pack:
            self.set_pack(dlg.pack, dlg.avatar_id)
            self._log_msg(
                f"Đã tạo gói '{dlg.pack.title}': {len(dlg.pack.jobs)} việc, "
                f"ước {dlg.pack.credits_estimate} tín dụng."
            )

    def _on_new_product_pack(self, product_id: Optional[int] = None) -> None:
        from app.ui.dialogs.flow_pack_dialogs import ProductPackDialog

        dlg = ProductPackDialog(self, product_id=product_id)
        if dlg.exec() and dlg.pack:
            self.set_pack(dlg.pack)
            self._log_msg(
                f"Đã tạo gói '{dlg.pack.title}': {len(dlg.pack.jobs)} cảnh, "
                f"ước {dlg.pack.credits_estimate} tín dụng."
            )

    def open_product_pack(self, product_id: int) -> None:
        """Mở thẳng hộp thoại gói sản phẩm (gọi từ trang Sản phẩm)."""
        self._on_new_product_pack(product_id)

    # ------------------------------------------------------- bộ chạy tự động
    def _on_run_auto(self) -> None:
        if not self._pack:
            QMessageBox.information(self, "Chưa có gói việc", "Tạo một gói việc trước đã.")
            return
        if not self._pack.pending_jobs:
            QMessageBox.information(self, "Đã xong", "Gói việc này không còn việc nào đang chờ.")
            return
        need = self._pack.credits_remaining_estimate
        if not self._ledger.can_afford(need):
            answer = QMessageBox.question(
                self,
                "Có thể không đủ tín dụng",
                f"Gói còn cần khoảng {need} tín dụng nhưng sổ chỉ ghi nhận {self._ledger.remaining}.\n\n"
                "Vẫn chạy chứ? (Flow sẽ tự dừng khi hết tín dụng thật)",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._auto_running = True
        self._stop_btn.setEnabled(True)
        self._run_btn.setEnabled(False)
        self._log_msg("▶️ Bắt đầu chạy tự động.")
        self._auto_next()

    def _on_stop_auto(self) -> None:
        self._auto_running = False
        self._state = S_IDLE
        self._active_job = None
        self._stop_btn.setEnabled(False)
        self._run_btn.setEnabled(True)
        self._log_msg("⏹ Đã dừng. Các việc đã nạp vẫn được giữ.")

    def _auto_next(self) -> None:
        if not self._auto_running or not self._pack:
            return
        job = self._pack.next_pending()
        if not job:
            self._auto_running = False
            self._stop_btn.setEnabled(False)
            self._run_btn.setEnabled(True)
            self._log_msg("🎉 Xong toàn bộ gói việc.")
            self._refresh_table()
            if self._stitch_btn.isEnabled():
                self._log_msg("Bấm '🎬 Ghép video hoàn chỉnh' để xuất video bán hàng.")
            return
        self._start_job(job)

    def _start_job(self, job: FlowJob) -> None:
        self._active_job = job
        if job.kind == KIND_PORTRAIT:
            self._log_msg(
                f"[{job.order:02d}] {job.title}: đây là việc tạo ẢNH — "
                "chuyển Flow sang chế độ tạo ảnh rồi bấm '📋 Dán prompt'."
            )
            self._state = S_MANUAL_WAIT
            if self._auto_running:
                # không tự chạy việc ảnh để tránh tạo sai chế độ
                job.status = "SKIPPED"
                self._pack.save()
                self._refresh_table()
                self._auto_next()
            return

        self._state = S_FILL
        self._log_msg(f"[{job.order:02d}] {job.title} — đang dán prompt vào Flow...")
        # Ghi lại danh sách video đang có để biết clip nào là clip mới
        self._run_js(js_video_srcs(), self._on_snapshot_before_fill)

    def _on_snapshot_before_fill(self, raw: Any) -> None:
        srcs = parse_result(raw)
        self._known_srcs = list(srcs if isinstance(srcs, list) else [])
        job = self._active_job
        if not job:
            return
        self._run_js(js_set_prompt(job.prompt), self._on_prompt_set)

    def _on_prompt_set(self, raw: Any) -> None:
        job = self._active_job
        if not job:
            return
        result = parse_result(raw)
        ok = isinstance(result, dict) and result.get("ok")
        if not ok:
            why = (result or {}).get("error", "không rõ") if isinstance(result, dict) else "không rõ"
            self._log_msg(
                f"⚠️ Không dán được prompt ({why}). Bấm vào ô prompt trong Flow rồi thử lại, "
                "hoặc ghi đè bộ chọn trong flow_selectors.json."
            )
            self._state = S_IDLE
            if self._auto_running:
                self._on_stop_auto()
            return

        self._log_msg("   đã dán prompt.")
        if not (self._settings.flow_auto_generate and self._auto_running):
            self._state = S_MANUAL_WAIT
            self._log_msg("   bấm '⚡ Bấm Tạo' (hoặc bấm trực tiếp trong Flow) để tạo clip.")
            return
        self._state = S_GENERATE
        self._run_js(js_click_generate(), self._on_generate_clicked)

    def _on_generate_clicked(self, raw: Any) -> None:
        job = self._active_job
        if not job:
            return
        result = parse_result(raw)
        ok = isinstance(result, dict) and result.get("ok")
        if not ok:
            self._log_msg("⚠️ Không tìm thấy nút Tạo. Bấm tay trong Flow, app vẫn tự nhận clip.")
            self._state = S_MANUAL_WAIT
            return
        via = result.get("via", "click")
        self._ledger.spend(job.title, job.credits, self._pack.pack_id if self._pack else "", job.job_id)
        self._update_credit_label()
        self._log_msg(
            f"   đã bấm Tạo ({via}) — trừ {job.credits} tín dụng, còn {self._ledger.remaining}. "
            "Đang chờ Flow render..."
        )
        self._state = S_WAIT_VIDEO
        self._deadline = time.time() + max(60, self._settings.flow_wait_timeout_seconds)
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)

    def _on_wait_poll(self, raw: Any) -> None:
        state = parse_result(raw)
        if self._state != S_WAIT_VIDEO or not isinstance(state, dict):
            return
        videos = state.get("videos") or []
        fresh = [s for s in videos if s not in self._known_srcs]
        if fresh:
            self._log_msg("   Flow đã render xong, đang tải clip về...")
            self._state = S_DOWNLOAD
            self._trigger_download(fresh[-1])
            return
        if time.time() > self._deadline:
            self._log_msg(
                "⚠️ Hết thời gian chờ mà chưa thấy clip mới. "
                "Kiểm tra trong Flow xem có bị lỗi/hết tín dụng không, rồi bấm '⬇️ Tải clip'."
            )
            self._state = S_IDLE
            self._progress.setVisible(False)
            if self._auto_running:
                self._on_stop_auto()

    def _trigger_download(self, src: str) -> None:
        """Tải nguồn video mới (fetch -> blob). Bị CORS chặn thì bấm nút Download của Flow."""
        name = self._download_name(self._active_job)
        self._dl_deadline = time.time() + 30
        self._dl_fallback_done = False
        self._run_js(js_download_src(src, name), self._on_download_triggered)

    def _on_download_triggered(self, raw: Any) -> None:
        result = parse_result(raw)
        if isinstance(result, dict) and result.get("ok"):
            self._log_msg(f"   đang lấy file về ({result.get('via')})...")
        else:
            self._log_msg("   không lấy được link video, chuyển sang bấm Download của Flow.")
            self._fallback_click_download()

    def _on_dl_state(self, raw: Any) -> None:
        state = parse_result(raw)
        if self._state != S_DOWNLOAD or not isinstance(state, dict):
            return
        if state.get("state") == "err" and not self._dl_fallback_done:
            self._log_msg(f"   tải trực tiếp bị chặn ({state.get('error', '')[:80]}).")
            self._fallback_click_download()
        elif time.time() > getattr(self, "_dl_deadline", 0) and not self._dl_fallback_done:
            self._log_msg("   chưa thấy file về sau 30 giây.")
            self._fallback_click_download()

    def _fallback_click_download(self) -> None:
        """Bấm đúng nút Download của Flow — cách chắc chắn nhất khi fetch bị chặn."""
        if self._state != S_DOWNLOAD or self._dl_fallback_done:
            return
        self._dl_fallback_done = True
        self._dl_deadline = time.time() + 60
        self._log_msg("   đang bấm nút Download trong Flow...")
        self._run_js(js_click_download(), self._on_fallback_download_clicked)

    def _on_fallback_download_clicked(self, raw: Any) -> None:
        result = parse_result(raw)
        if not (isinstance(result, dict) and result.get("ok")):
            self._log_msg(
                "⚠️ Không thấy nút Download trong Flow. Bấm tay nút Download ở khung bên phải — "
                "app vẫn tự nhận file và nạp vào đúng chỗ."
            )
            self._state = S_MANUAL_WAIT
            if self._auto_running:
                self._on_stop_auto()

    @staticmethod
    def _download_name(job: Optional[FlowJob]) -> str:
        if not job:
            return f"flow_clip_{int(time.time())}.mp4"
        tag = job.cue.lower() or (f"scene_{job.scene_index}" if job.scene_index else job.kind)
        return f"{job.order:02d}_{tag}_{int(time.time())}.mp4"

    # -------------------------------------------------------- tải & nạp file
    def _on_download_requested(self, item: Any) -> None:
        """Mọi file tải trong trình duyệt nhúng đều được chuyển về thư mục của LiveFlow."""
        try:
            suggested = item.downloadFileName() or ""
            suffix = Path(suggested).suffix or ".mp4"
            job = self._active_job or self._selected_job()
            name = Path(self._download_name(job)).with_suffix(suffix).name
            item.setDownloadDirectory(str(self._settings.effective_flow_downloads_dir))
            item.setDownloadFileName(name)
            item.accept()
            self._downloads.append(item)
            try:
                item.isFinishedChanged.connect(partial(self._on_download_finished, item))
            except Exception:
                item.stateChanged.connect(lambda _s, it=item: self._on_download_finished(it))
            self._log_msg(f"   đang tải: {name}")
        except Exception as e:  # noqa: BLE001
            logger.exception("Lỗi khi nhận file tải về từ Flow")
            self._log_msg(f"⚠️ Lỗi nhận file tải về: {e}")

    def _on_download_finished(self, item: Any) -> None:
        try:
            if not item.isFinished():
                return
            path = Path(item.downloadDirectory()) / item.downloadFileName()
        except Exception:
            return
        if not path.exists():
            self._log_msg("⚠️ File tải về không tồn tại — thử bấm '⬇️ Tải clip' lần nữa.")
            return
        # Trình duyệt nhúng đã đưa file này cho ta rồi — đừng để watcher nhận lại lần hai.
        self._watcher.mark_seen(path)
        self._queue_import(path)

    def _queue_import(self, path: Path) -> None:
        self._import_queue.append(path)
        self._drain_import_queue()

    def _drain_import_queue(self) -> None:
        if self._import_worker is not None or not self._import_queue:
            return
        if not self._pack:
            self._import_queue.clear()
            return
        job = self._active_job if (self._active_job and not self._active_job.is_done) else None
        job = job or self._selected_job_pending() or self._pack.next_pending()
        if not job:
            self._log_msg("⚠️ Có file mới nhưng không còn việc nào đang chờ — bỏ qua.")
            self._import_queue.clear()
            return

        source = self._import_queue.pop(0)
        self._import_source = source
        self._state = S_IMPORT
        self._log_msg(f"   đang chuẩn hoá & nạp '{source.name}' cho [{job.order:02d}] {job.title}...")
        self._import_worker = FlowImportWorker(self._pack, job, source, self._avatar_id, self)
        self._import_worker.done.connect(self._on_import_done)
        self._import_worker.start()

    def _ask_for_ingredients(self, poster_path: str) -> None:
        """Nhắc kéo ảnh chân dung vào ô Ingredients — bước giữ mặt người mẫu không đổi.

        Chỉ hỏi một lần cho mỗi người mẫu, sau khi clip nền đã có. Ảnh được trích từ
        chính clip đó nên không tốn thêm tín dụng.
        """
        if not self._pack or not any(
            j.kind == KIND_AVATAR_CUE and j.status == "PENDING" for j in self._pack.jobs
        ):
            return
        self._log_msg(f"   ảnh chân dung đã trích: {poster_path}")
        box = QMessageBox(self)
        box.setWindowTitle("Giữ mặt người mẫu giống nhau")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(
            "Clip nền đã xong. Để 8 clip cử chỉ tiếp theo <b>ra đúng một người</b>, "
            "hãy kéo ảnh này vào ô <b>Ingredients</b> trong khung Flow:"
        )
        box.setInformativeText(
            f"{poster_path}\n\n"
            "Kéo xong thì bấm OK — app chạy tiếp.\n"
            "Bỏ qua cũng được, nhưng mặt có thể khác nhau giữa các clip."
        )
        open_btn = box.addButton("📂 Mở thư mục ảnh", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Bỏ qua", QMessageBox.ButtonRole.RejectRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is open_btn:
            try:
                import os

                os.startfile(str(Path(poster_path).parent))  # noqa: S606
            except Exception as e:  # noqa: BLE001
                logger.debug("Không mở được thư mục ảnh: {}", e)

    def _cleanup_download(self, source: Optional[Path]) -> None:
        """Xoá file thô LiveFlow tự tải về sau khi đã chuẩn hoá xong.

        Chỉ xoá file nằm trong thư mục tải về của chính LiveFlow — file trong Downloads
        của người dùng thì giữ nguyên, không tự ý xoá.
        """
        if not source:
            return
        try:
            own_dir = self._settings.effective_flow_downloads_dir.resolve()
            if source.resolve().parent == own_dir and source.exists():
                source.unlink()
        except Exception as e:  # noqa: BLE001
            logger.debug("Không xoá được file thô {}: {}", source, e)

    def _selected_job_pending(self) -> Optional[FlowJob]:
        job = self._selected_job()
        return job if job and job.status == "PENDING" else None

    def _on_import_done(self, result: ImportResult) -> None:
        self._import_worker = None
        self._progress.setVisible(False)
        if result.ok:
            self._log_msg(f"   ✅ {result.message or 'đã nạp'} → {Path(result.dest_path).name}")
            for w in result.warnings:
                self._log_msg(f"      ⚠️ {w}")
            self._cleanup_download(getattr(self, "_import_source", None))
        else:
            self._log_msg(f"   ❌ Nạp thất bại: {result.message}")
            if self._auto_running:
                self._log_msg("      Dừng chạy tự động để bạn xem lại clip này.")
                self._on_stop_auto()

        finished_job = self._active_job
        self._active_job = None
        self._state = S_IDLE
        self._refresh_table()
        self._update_credit_label()

        if self._import_queue:
            self._drain_import_queue()
        elif self._auto_running and result.ok:
            if finished_job and finished_job.kind == KIND_AVATAR_BASE and result.poster_path:
                self._ask_for_ingredients(result.poster_path)
            QTimer.singleShot(1200, self._auto_next)

    # ------------------------------------------------------------ nút thủ công
    def _on_run_selected(self) -> None:
        job = self._selected_job()
        if not job:
            QMessageBox.information(self, "Chưa chọn việc", "Chọn một dòng trong bảng.")
            return
        if job.is_done:
            answer = QMessageBox.question(
                self, "Việc này đã xong", "Chạy lại sẽ tốn thêm tín dụng. Tiếp tục?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            job.status = "PENDING"
        self._auto_running = False
        self._start_job(job)

    def _on_manual_paste(self) -> None:
        job = self._selected_job()
        if not job:
            return
        self._active_job = job
        self._run_js(js_video_srcs(), self._on_snapshot_before_fill)

    def _on_manual_generate(self) -> None:
        if not self._active_job:
            self._active_job = self._selected_job()
        if not self._active_job:
            return
        self._state = S_GENERATE
        self._run_js(js_click_generate(), self._on_generate_clicked)

    def _on_manual_download(self) -> None:
        if not self._active_job:
            self._active_job = self._selected_job()
        self._state = S_DOWNLOAD
        self._run_js(js_click_download(), lambda r: self._log_msg(
            "   đã bấm Download trong Flow."
            if isinstance(parse_result(r), dict) and parse_result(r).get("ok")
            else "⚠️ Không thấy nút Download — bấm tay trong khung Flow, app vẫn tự nhận file."
        ))

    def _on_skip_job(self) -> None:
        job = self._selected_job()
        if not job or not self._pack:
            return
        job.status = "SKIPPED"
        self._pack.save()
        self._refresh_table()
        self._log_msg(f"⏭ Bỏ qua [{job.order:02d}] {job.title}")
        if self._auto_running:
            self._auto_next()

    def _on_stitch(self) -> None:
        if not self._pack:
            return
        self._stitch_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setRange(0, 100)
        self._stitch_worker = FlowStitchWorker(self._pack, self)
        self._stitch_worker.progress.connect(
            lambda m, p: (self._log_msg(f"   {m}"), self._progress.setValue(p))
        )
        self._stitch_worker.done.connect(self._on_stitch_done)
        self._stitch_worker.start()

    def _on_stitch_done(self, final_path: str, error: str) -> None:
        self._stitch_worker = None
        self._progress.setVisible(False)
        self._stitch_btn.setEnabled(True)
        if error or not final_path:
            self._log_msg(f"❌ Ghép video thất bại: {error}")
            QMessageBox.critical(self, "Lỗi ghép video", error or "Không rõ nguyên nhân")
            return
        self._log_msg(f"🎬 Xong: {final_path}")
        QMessageBox.information(
            self,
            "Đã xuất video",
            f"Video hoàn chỉnh:\n{final_path}\n\nĐã gắn vào sản phẩm, dùng được ngay trong Playlist.",
        )

    # ---------------------------------------------------------------- vòng lặp
    def _tick(self) -> None:
        """Nhịp 2 giây: canh file tải về, theo dõi tiến trình render, cập nhật trạng thái."""
        if self._settings.flow_auto_import and self._import_worker is None:
            try:
                for f in self._watcher.poll():
                    self._log_msg(f"📥 Thấy file mới: {f.name}")
                    self._queue_import(f)
            except Exception as e:  # noqa: BLE001
                logger.debug("Lỗi khi canh thư mục tải về: {}", e)

        if self._state == S_WAIT_VIDEO and self._page is not None:
            self._page.runJavaScript(js_state(), self._on_wait_poll)
        elif self._state == S_DOWNLOAD and self._page is not None:
            self._page.runJavaScript(js_dl_state(), self._on_dl_state)
        elif self._state in (S_IDLE, S_MANUAL_WAIT):
            self._status_counter = getattr(self, "_status_counter", 0) + 1
            if self._status_counter % 5 == 0:
                self._poll_browser_state()

    def _log_msg(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self._log.append(f"[{stamp}] {message}")
        self._log.verticalScrollBar().setValue(self._log.verticalScrollBar().maximum())
        logger.info("Flow Studio: {}", message)

    # Được MainWindow gọi khi trang được hiện
    def refresh_data(self) -> None:
        self.refresh_packs()
        self._update_credit_label()
