"""Auto Showcase Batch Dialog — 1-Click automated video generation & livestream sequencing."""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from loguru import logger

from app.database.models.avatar import AIAvatar
from app.database.models.product import Product
from app.database.session import get_session
from app.services.auto_showcase_service import AutoShowcaseService
from app.ui.styles.dark_theme import COLORS


class AutoShowcaseBatchWorker(QThread):
    """Worker thread running batch video rendering in background without freezing Qt UI."""

    progress = Signal(int, str)  # overall_percent, message
    product_done = Signal(int, str)  # product_id, video_path
    batch_finished = Signal(int, str)  # count_success, playlist_name
    batch_failed = Signal(str)  # error_message

    def __init__(
        self,
        product_ids: list[int],
        avatar_id: int,
    ) -> None:
        super().__init__()
        self.product_ids = product_ids
        self.avatar_id = avatar_id
        self._is_cancelled = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def run(self) -> None:
        session = get_session()
        try:
            avatar = session.query(AIAvatar).filter_by(id=self.avatar_id).first()
            if not avatar:
                self.batch_failed.emit("Không tìm thấy Người Mẫu AI đã chọn.")
                return

            products = (
                session.query(Product)
                .filter(Product.id.in_(self.product_ids))
                .all()
            )
            if not products:
                self.batch_failed.emit("Chưa chọn sản phẩm nào để tạo video.")
                return

            service = AutoShowcaseService(session)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            total = len(products)
            success_count = 0

            for idx, product in enumerate(products):
                if self._is_cancelled:
                    break

                base_pct = int((idx / total) * 90)
                step_pct = int(90 / total)

                def _cb(p: int, msg: str, _idx=idx, _b=base_pct, _s=step_pct, _name=product.name):
                    current_pct = _b + int((p / 100.0) * _s)
                    self.progress.emit(
                        min(95, current_pct),
                        f"[{_idx + 1}/{total}] {_name[:28]}...: {msg}",
                    )

                try:
                    vid_path = loop.run_until_complete(
                        service.render_product_showcase_video(
                            product=product,
                            avatar=avatar,
                            progress_callback=_cb,
                        )
                    )
                    success_count += 1
                    self.product_done.emit(product.id, vid_path)
                except Exception as e:
                    logger.exception("Lỗi khi render video sản phẩm {}: {}", product.id, e)

            # Sync to LiveStream playlist
            self.progress.emit(95, "Đang đồng bộ danh sách phát Livestream tự động...")
            playlist = service.sync_to_livestream_playlist("Livestream Tự Động AI")
            loop.close()

            self.progress.emit(100, f"Hoàn tất! Đã tạo thành công {success_count}/{total} video.")
            self.batch_finished.emit(success_count, playlist.name)

        except Exception as e:
            logger.exception("Lỗi trong quá trình batch render: {}", e)
            self.batch_failed.emit(str(e))
        finally:
            session.close()


class AutoShowcaseBatchDialog(QDialog):
    """Full dialog allowing 1-click generation of talking presenter showcase videos & auto livestream playlist."""

    stream_now_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("✨ Tự Động Tạo Video Giới Thiệu & Lên Lịch Livestream Bán Hàng")
        self.setMinimumSize(920, 720)
        self.resize(1000, 780)

        self._session = get_session()
        self._avatars: list[AIAvatar] = []
        self._products: list[Product] = []
        self._worker: Optional[AutoShowcaseBatchWorker] = None

        self._setup_ui()
        self._load_data()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # Header Title
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        title = QLabel("✨ Tự Động Hóa Livestream Bán Hàng Với Người Mẫu AI")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #FFFFFF;")
        title_box.addWidget(title)

        desc = QLabel(
            "Chỉ cần 3 bước đơn giản: Chọn Người Mẫu AI → Chọn Sản Phẩm → Bấm 'Bắt đầu tạo tự động'.\n"
            "Hệ thống sẽ tự động viết lời dẫn hấp dẫn, đọc giọng chuẩn tiếng Việt, cử động môi khớp khẩu hình (Lip-Sync) "
            "và ghép sẵn vào danh sách phát liên tục cho buổi LIVE."
        )
        desc.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 13px; line-height: 1.4;")
        desc.setWordWrap(True)
        title_box.addWidget(desc)
        layout.addLayout(title_box)

        # Step 1: Chọn Người Mẫu AI
        step1_frame = QFrame()
        step1_frame.setStyleSheet(
            f"background: {COLORS['bg_secondary']}; border: 1px solid {COLORS['border']}; border-radius: 8px; padding: 10px;"
        )
        step1_layout = QVBoxLayout(step1_frame)
        step1_layout.setContentsMargins(12, 10, 12, 10)
        step1_layout.setSpacing(8)

        step1_lbl = QLabel("Bước 1: Chọn Người Mẫu AI Đại Diện Gian Hàng")
        step1_lbl.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {COLORS['accent_secondary']};")
        step1_layout.addWidget(step1_lbl)

        av_row = QHBoxLayout()
        av_row.setSpacing(16)
        self._avatar_combo = QComboBox()
        self._avatar_combo.setMinimumHeight(38)
        self._avatar_combo.setStyleSheet("font-size: 13px; font-weight: 500;")
        self._avatar_combo.currentIndexChanged.connect(self._on_avatar_changed)
        av_row.addWidget(self._avatar_combo, 1)

        self._avatar_preview_lbl = QLabel()
        self._avatar_preview_lbl.setFixedSize(54, 54)
        self._avatar_preview_lbl.setStyleSheet(
            "border: 2px solid #38bdf8; border-radius: 27px; background: #1e293b;"
        )
        self._avatar_preview_lbl.setScaledContents(True)
        av_row.addWidget(self._avatar_preview_lbl)

        self._avatar_desc_lbl = QLabel()
        self._avatar_desc_lbl.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
        av_row.addWidget(self._avatar_desc_lbl, 2)

        step1_layout.addLayout(av_row)
        layout.addWidget(step1_frame)

        # Step 2: Chọn danh sách sản phẩm
        step2_header = QHBoxLayout()
        step2_lbl = QLabel("Bước 2: Chọn Các Sản Phẩm Cần Tạo Video Giới Thiệu")
        step2_lbl.setStyleSheet("font-weight: bold; font-size: 14px; color: #FFFFFF;")
        step2_header.addWidget(step2_lbl)
        step2_header.addStretch()

        self._select_all_btn = QPushButton("✓ Chọn tất cả")
        self._select_all_btn.setFixedHeight(28)
        self._select_all_btn.clicked.connect(self._select_all_products)
        step2_header.addWidget(self._select_all_btn)

        self._deselect_all_btn = QPushButton("✗ Bỏ chọn")
        self._deselect_all_btn.setFixedHeight(28)
        self._deselect_all_btn.clicked.connect(self._deselect_all_products)
        step2_header.addWidget(self._deselect_all_btn)

        layout.addLayout(step2_header)

        # Products Table
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["Chọn", "Ảnh", "Tên sản phẩm", "Giá bán", "Video Giới Thiệu"])
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(0, 60)
        self._table.setColumnWidth(1, 60)
        self._table.setColumnWidth(3, 130)
        self._table.setColumnWidth(4, 180)
        self._table.verticalHeader().setDefaultSectionSize(48)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table, 1)

        # Step 3: Progress & Action Controls
        progress_box = QVBoxLayout()
        progress_box.setSpacing(6)

        self._status_label = QLabel("Sẵn sàng bắt đầu tạo video.")
        self._status_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #38bdf8;")
        progress_box.addWidget(self._status_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(18)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        progress_box.addWidget(self._progress_bar)
        layout.addLayout(progress_box)

        # Action Buttons Row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self._close_btn = QPushButton("Đóng")
        self._close_btn.setFixedHeight(40)
        self._close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._close_btn)

        btn_row.addStretch()

        self._start_btn = QPushButton("🚀 Bắt Đầu Tạo Video Tự Động (1-Click)")
        self._start_btn.setFixedHeight(42)
        self._start_btn.setStyleSheet(
            "background-color: #f43f5e; color: #ffffff; font-weight: bold; font-size: 14px; "
            "padding: 0 24px; border-radius: 8px;"
        )
        self._start_btn.clicked.connect(self._on_start_batch)
        btn_row.addWidget(self._start_btn)

        self._stream_btn = QPushButton("🔴 Đồng Bộ & Chuyển Sang Live Studio Để Phát Ngay")
        self._stream_btn.setFixedHeight(42)
        self._stream_btn.setStyleSheet(
            "background-color: #059669; color: #ffffff; font-weight: bold; font-size: 14px; "
            "padding: 0 20px; border-radius: 8px;"
        )
        self._stream_btn.clicked.connect(self._on_stream_now)
        btn_row.addWidget(self._stream_btn)

        layout.addLayout(btn_row)

    def _load_data(self) -> None:
        """Load avatars and products from database."""
        self._avatars = self._session.query(AIAvatar).order_by(AIAvatar.id).all()
        self._avatar_combo.clear()
        for av in self._avatars:
            gender_txt = "Nữ" if av.gender == "female" else "Nam"
            self._avatar_combo.addItem(f"👤 {av.name} ({gender_txt} • {av.role or 'Đại diện bán hàng'})", av.id)

        self._products = self._session.query(Product).filter_by(is_active=True).order_by(Product.id).all()
        self._populate_products_table()
        self._on_avatar_changed(0)

    def _on_avatar_changed(self, index: int) -> None:
        if index < 0 or index >= len(self._avatars):
            return
        av = self._avatars[index]
        desc = f"Giọng đọc: {av.voice_id or 'vi-VN-HoaiMyNeural'} • Phong cách: Thân thiện, chốt đơn tự nhiên"
        self._avatar_desc_lbl.setText(desc)

        # Load avatar portrait thumbnail
        p = av.preview_image_path or av.media_path or ""
        if p.lower().endswith(".mp4"):
            p_img = p[:-4] + ".jpg"
            if os.path.exists(p_img):
                p = p_img

        if p and os.path.exists(p):
            pix = QPixmap(p).scaled(54, 54, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            self._avatar_preview_lbl.setPixmap(pix)
        else:
            self._avatar_preview_lbl.setText("Avatar")

    def _populate_products_table(self) -> None:
        self._table.setRowCount(len(self._products))

        for row, prod in enumerate(self._products):
            # Checkbox
            chk_widget = QWidget()
            chk_lay = QHBoxLayout(chk_widget)
            chk_lay.setContentsMargins(0, 0, 0, 0)
            chk_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk = QCheckBox()
            chk.setChecked(True)  # Default all checked
            chk.setProperty("product_id", prod.id)
            chk_lay.addWidget(chk)
            self._table.setCellWidget(row, 0, chk_widget)

            # Thumbnail
            img_lbl = QLabel()
            img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if prod.image_path and os.path.exists(prod.image_path):
                pix = QPixmap(prod.image_path).scaled(
                    42, 42, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
                img_lbl.setPixmap(pix)
            else:
                img_lbl.setText("📦")
                img_lbl.setStyleSheet("font-size: 20px;")
            self._table.setCellWidget(row, 1, img_lbl)

            # Name
            self._table.setItem(row, 2, QTableWidgetItem(prod.name))

            # Price
            price_val = prod.promo_price or prod.price or 0
            price_txt = f"{price_val:,.0f}₫".replace(",", ".")
            p_item = QTableWidgetItem(price_txt)
            p_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 3, p_item)

            # Video Status
            status_widget = QWidget()
            status_lay = QHBoxLayout(status_widget)
            status_lay.setContentsMargins(4, 2, 4, 2)
            status_lay.setSpacing(6)

            if prod.video_path and os.path.exists(prod.video_path):
                v_lbl = QLabel("🎬 Đã có video")
                v_lbl.setStyleSheet("color: #10b981; font-weight: bold; font-size: 12px;")
                status_lay.addWidget(v_lbl)

                play_btn = QPushButton("▶️ Xem")
                play_btn.setFixedSize(65, 26)
                play_btn.setStyleSheet("font-size: 11px; padding: 2px;")
                play_btn.clicked.connect(lambda _, vp=prod.video_path: self._play_video(vp))
                status_lay.addWidget(play_btn)
            else:
                v_lbl = QLabel("⏳ Chưa có video")
                v_lbl.setStyleSheet("color: #94a3b8; font-size: 12px;")
                status_lay.addWidget(v_lbl)

            status_lay.addStretch()
            self._table.setCellWidget(row, 4, status_widget)

    def _select_all_products(self) -> None:
        for row in range(self._table.rowCount()):
            chk_widget = self._table.cellWidget(row, 0)
            if chk_widget:
                chk = chk_widget.findChild(QCheckBox)
                if chk:
                    chk.setChecked(True)

    def _deselect_all_products(self) -> None:
        for row in range(self._table.rowCount()):
            chk_widget = self._table.cellWidget(row, 0)
            if chk_widget:
                chk = chk_widget.findChild(QCheckBox)
                if chk:
                    chk.setChecked(False)

    def _get_selected_product_ids(self) -> list[int]:
        selected = []
        for row in range(self._table.rowCount()):
            chk_widget = self._table.cellWidget(row, 0)
            if chk_widget:
                chk = chk_widget.findChild(QCheckBox)
                if chk and chk.isChecked():
                    pid = chk.property("product_id")
                    if pid:
                        selected.append(int(pid))
        return selected

    def _play_video(self, video_path: str) -> None:
        if os.path.exists(video_path):
            try:
                os.startfile(video_path)
            except Exception as e:
                QMessageBox.warning(self, "Lỗi mở video", f"Không thể mở file video: {e}")

    def _on_start_batch(self) -> None:
        selected_ids = self._get_selected_product_ids()
        if not selected_ids:
            QMessageBox.warning(self, "Chưa chọn sản phẩm", "Vui lòng tích chọn ít nhất 1 sản phẩm để tạo video.")
            return

        av_idx = self._avatar_combo.currentIndex()
        if av_idx < 0 or av_idx >= len(self._avatars):
            QMessageBox.warning(self, "Chưa chọn người mẫu", "Vui lòng chọn một Người Mẫu AI.")
            return
        avatar = self._avatars[av_idx]

        self._start_btn.setEnabled(False)
        self._start_btn.setText("⏳ Đang xử lý tự động...")
        self._progress_bar.setValue(5)
        self._status_label.setText(f"Đang bắt đầu tạo video cho {len(selected_ids)} sản phẩm với {avatar.name}...")

        self._worker = AutoShowcaseBatchWorker(
            product_ids=selected_ids,
            avatar_id=avatar.id,
        )
        self._worker.progress.connect(self._on_worker_progress)
        self._worker.product_done.connect(self._on_product_done)
        self._worker.batch_finished.connect(self._on_batch_finished)
        self._worker.batch_failed.connect(self._on_batch_failed)
        self._worker.start()

    def _on_worker_progress(self, percent: int, message: str) -> None:
        self._progress_bar.setValue(percent)
        self._status_label.setText(message)

    def _on_product_done(self, product_id: int, video_path: str) -> None:
        # Refresh product row status
        for row in range(self._table.rowCount()):
            chk_widget = self._table.cellWidget(row, 0)
            if chk_widget:
                chk = chk_widget.findChild(QCheckBox)
                if chk and chk.property("product_id") == product_id:
                    status_widget = QWidget()
                    status_lay = QHBoxLayout(status_widget)
                    status_lay.setContentsMargins(4, 2, 4, 2)
                    status_lay.setSpacing(6)

                    v_lbl = QLabel("🎬 Đã có video")
                    v_lbl.setStyleSheet("color: #10b981; font-weight: bold; font-size: 12px;")
                    status_lay.addWidget(v_lbl)

                    play_btn = QPushButton("▶️ Xem")
                    play_btn.setFixedSize(65, 26)
                    play_btn.setStyleSheet("font-size: 11px; padding: 2px;")
                    play_btn.clicked.connect(lambda _, vp=video_path: self._play_video(vp))
                    status_lay.addWidget(play_btn)

                    status_lay.addStretch()
                    self._table.setCellWidget(row, 4, status_widget)
                    break

    def _on_batch_finished(self, success_count: int, playlist_name: str) -> None:
        self._start_btn.setEnabled(True)
        self._start_btn.setText("🚀 Bắt Đầu Tạo Video Tự Động (1-Click)")
        self._progress_bar.setValue(100)
        self._status_label.setText(f"🎉 Hoàn tất! Đã tạo thành công {success_count} video và đồng bộ vào playlist '{playlist_name}'.")

        QMessageBox.information(
            self,
            "Tạo Video Thành Công!",
            f"🎉 Đã tạo thành công {success_count} video giới thiệu sản phẩm với Người Mẫu AI!\n\n"
            f"Hệ thống đã tự động sắp xếp vào danh sách phát: '{playlist_name}'.\n\n"
            "Bạn có thể bấm 'Đồng Bộ & Chuyển Sang Live Studio Để Phát Ngay' để bắt đầu phiên livestream hoàn chỉnh!",
        )

    def _on_batch_failed(self, error_msg: str) -> None:
        self._start_btn.setEnabled(True)
        self._start_btn.setText("🚀 Bắt Đầu Tạo Video Tự Động (1-Click)")
        self._status_label.setText(f"❌ Có lỗi xảy ra: {error_msg}")
        QMessageBox.critical(self, "Lỗi tạo video", f"Không thể hoàn tất tạo video:\n{error_msg}")

    def _on_stream_now(self) -> None:
        """Sync playlist and emit stream_now_requested to navigate to Live Studio."""
        service = AutoShowcaseService(self._session)
        service.sync_to_livestream_playlist("Livestream Tự Động AI")
        self.stream_now_requested.emit()
        self.accept()

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(1000)
        self._session.close()
        super().closeEvent(event)
