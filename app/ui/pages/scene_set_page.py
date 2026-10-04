"""SceneSetPage — UI for managing review environments, spatial alignment, floor_y calibration, and live 9:16 preview."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from loguru import logger

from app.sets.library import SceneSetLibrary
from app.sets.scene_set import SceneSet
from app.ui.styles.dark_theme import COLORS


class SceneSetCanvas(QWidget):
    """Visual 9:16 canvas preview showing background, floor_y line, presenter anchor, and product zone."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(320, 568)  # 9:16 scaled down
        self.setMaximumWidth(420)
        self.current_set: Optional[SceneSet] = None
        self._bg_pixmap: Optional[QPixmap] = None

    def set_scene(self, scene_set: Optional[SceneSet]) -> None:
        self.current_set = scene_set
        self._bg_pixmap = None
        if scene_set and scene_set.background and os.path.exists(scene_set.background):
            try:
                self._bg_pixmap = QPixmap(scene_set.background)
            except Exception:
                self._bg_pixmap = None
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Scale coordinate factors: 1080x1920 -> widget dimensions
        w = self.width()
        h = self.height()
        sx = w / 1080.0
        sy = h / 1920.0

        # 1. Background
        if self._bg_pixmap and not self._bg_pixmap.isNull():
            painter.drawPixmap(0, 0, w, h, self._bg_pixmap)
        else:
            painter.fillRect(0, 0, w, h, QColor(22, 25, 34))

        if not self.current_set:
            painter.setPen(QColor(150, 150, 160))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Chọn một bối cảnh để xem trước")
            painter.end()
            return

        s = self.current_set

        # 2. Product Zone Overlay (Dotted Gold Rectangle)
        zx1, zy1, zx2, zy2 = s.product_zone
        p_rect = QRect(int(zx1 * sx), int(zy1 * sy), int((zx2 - zx1) * sx), int((zy2 - zy1) * sy))
        painter.setPen(QPen(QColor(255, 215, 0, 200), 2, Qt.PenStyle.DashLine))
        painter.setBrush(QColor(255, 215, 0, 35))
        painter.drawRoundedRect(p_rect, 6, 6)
        painter.setPen(QColor(255, 215, 0, 220))
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.drawText(p_rect.x() + 6, p_rect.y() + 18, "Vùng Sản Phẩm")

        # 3. Floor Line (floor_y)
        floor_screen_y = int(s.floor_y * sy)
        painter.setPen(QPen(QColor(254, 44, 85, 230), 2, Qt.PenStyle.SolidLine))
        painter.drawLine(0, floor_screen_y, w, floor_screen_y)
        painter.setPen(QColor(254, 44, 85, 230))
        painter.drawText(8, floor_screen_y - 6, f"Mặt sàn (floor_y = {s.floor_y}px)")

        # 4. Presenter Anchor & Perspective Guide
        anchor_screen_x = int(s.anchor_x * sx)
        model_h_screen = int(s.model_height_px * sy)
        model_top_y = floor_screen_y - model_h_screen

        # Presenter shadow indicator
        shadow_w = int(220 * sx)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, int(s.shadow_alpha * 255)))
        painter.drawEllipse(QPoint(anchor_screen_x, floor_screen_y), shadow_w // 2, int(15 * sy))

        # Presenter standing silhouette guide
        painter.setPen(QPen(QColor(108, 92, 231, 200), 2, Qt.PenStyle.DashDotLine))
        painter.setBrush(QColor(108, 92, 231, 40))
        model_w = int(320 * sx)
        painter.drawRoundedRect(
            anchor_screen_x - model_w // 2,
            model_top_y,
            model_w,
            model_h_screen,
            12,
            12,
        )

        painter.setPen(QColor(255, 255, 255, 220))
        painter.drawText(anchor_screen_x - 50, model_top_y + 24, "Vị Trí Mẫu")

        # Top border label
        painter.setPen(QColor(255, 255, 255, 180))
        painter.drawText(10, 24, f"Khung Dọc 9:16 — {s.name}")
        painter.end()


class SceneSetPage(QWidget):
    """Page for creating and calibrating studio review scene sets."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.library = SceneSetLibrary()
        self._setup_ui()
        self._load_sets_list()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # =========================================================
        # LEFT: List of Sets + Settings Form
        # =========================================================
        left_card = QFrame()
        left_card.setObjectName("cardFrame")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(12)

        title = QLabel("🎬 Quản Lý Bối Cảnh Review (Scene Sets)")
        title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {COLORS['accent_primary_hover']};")
        left_layout.addWidget(title)

        desc = QLabel(
            "Mỗi bối cảnh chuẩn hoá đường mặt sàn (floor_y), điểm đứng người mẫu, "
            "vùng đặt sản phẩm và độ sâu trường ảnh để clip review tự nhiên nhất."
        )
        desc.setObjectName("hintLabel")
        desc.setWordWrap(True)
        left_layout.addWidget(desc)

        # Set selection list
        self.set_list = QListWidget()
        self.set_list.setMaximumHeight(140)
        self.set_list.itemClicked.connect(self._on_set_selected)
        left_layout.addWidget(self.set_list)

        # Form
        form = QFormLayout()
        form.setSpacing(10)

        self.name_input = QLineEdit()
        form.addRow("Tên bối cảnh:", self.name_input)

        self.cat_combo = QComboBox()
        self.cat_combo.addItem("Gia dụng & Đời sống", "gia_dung")
        self.cat_combo.addItem("Đồ bếp & Thực phẩm", "bep")
        self.cat_combo.addItem("Mỹ phẩm & Skincare", "my_pham")
        self.cat_combo.addItem("Thời trang & Phụ kiện", "thoi_trang")
        self.cat_combo.addItem("Công nghệ & Gaming", "cong_nghe")
        self.cat_combo.addItem("Đa năng dự phòng", "da_nang")
        form.addRow("Ngành hàng:", self.cat_combo)

        bg_row = QHBoxLayout()
        self.bg_path_input = QLineEdit()
        self.bg_path_input.setPlaceholderText("Đường dẫn ảnh/video nền 1080×1920")
        self.bg_path_input.textChanged.connect(self._on_bg_changed)
        bg_row.addWidget(self.bg_path_input, 1)
        self.browse_bg_btn = QPushButton("📁 Chọn file")
        self.browse_bg_btn.clicked.connect(self._browse_bg)
        bg_row.addWidget(self.browse_bg_btn)
        form.addRow("Tệp nền (9:16):", bg_row)

        # Calibration Controls
        self.floor_spin = QSpinBox()
        self.floor_spin.setRange(800, 1850)
        self.floor_spin.setValue(1520)
        self.floor_spin.setSuffix(" px")
        self.floor_spin.valueChanged.connect(self._on_param_changed)
        form.addRow("Mặt sàn (floor_y):", self.floor_spin)

        self.anchor_spin = QSpinBox()
        self.anchor_spin.setRange(100, 980)
        self.anchor_spin.setValue(540)
        self.anchor_spin.setSuffix(" px")
        self.anchor_spin.valueChanged.connect(self._on_param_changed)
        form.addRow("Tâm đứng (anchor_x):", self.anchor_spin)

        self.height_spin = QSpinBox()
        self.height_spin.setRange(600, 1600)
        self.height_spin.setValue(1180)
        self.height_spin.setSuffix(" px")
        self.height_spin.valueChanged.connect(self._on_param_changed)
        form.addRow("Chiều cao mẫu:", self.height_spin)

        self.blur_spin = QDoubleSpinBox()
        self.blur_spin.setRange(0.0, 12.0)
        self.blur_spin.setSingleStep(0.5)
        self.blur_spin.setValue(2.5)
        self.blur_spin.setSuffix(" px")
        self.blur_spin.valueChanged.connect(self._on_param_changed)
        form.addRow("Độ mờ nền (DoF):", self.blur_spin)

        self.shadow_spin = QDoubleSpinBox()
        self.shadow_spin.setRange(0.0, 1.0)
        self.shadow_spin.setSingleStep(0.05)
        self.shadow_spin.setValue(0.35)
        self.shadow_spin.valueChanged.connect(self._on_param_changed)
        form.addRow("Độ đậm bóng đổ:", self.shadow_spin)

        left_layout.addLayout(form)

        # Action Buttons
        btn_row = QHBoxLayout()
        self.new_btn = QPushButton("➕ Thêm mới")
        self.new_btn.clicked.connect(self._new_custom_set)
        btn_row.addWidget(self.new_btn)

        self.save_btn = QPushButton("💾 Lưu bối cảnh")
        self.save_btn.setObjectName("primaryButton")
        self.save_btn.clicked.connect(self._save_current_set)
        btn_row.addWidget(self.save_btn)
        left_layout.addLayout(btn_row)

        left_layout.addStretch()
        splitter.addWidget(left_card)

        # =========================================================
        # RIGHT: Interactive 9:16 Canvas Preview
        # =========================================================
        right_card = QFrame()
        right_card.setObjectName("cardFrame")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(10)

        preview_header = QLabel("👁️ Xem Trước Khung Hình Review 9:16")
        preview_header.setStyleSheet("font-weight: bold; font-size: 15px;")
        right_layout.addWidget(preview_header)

        preview_hint = QLabel("Đường đỏ: Mặt sàn. Ô vàng: Bàn/kệ trưng bày SP. Vùng tím: Vị trí người mẫu.")
        preview_hint.setObjectName("hintLabel")
        right_layout.addWidget(preview_hint)

        self.canvas = SceneSetCanvas()
        right_layout.addWidget(self.canvas, 1, alignment=Qt.AlignmentFlag.AlignCenter)

        splitter.addWidget(right_card)
        splitter.setSizes([600, 480])
        layout.addWidget(splitter)

    def _load_sets_list(self) -> None:
        self.set_list.clear()
        sets = self.library.list_sets()
        for s in sets:
            badge = " [Tự tạo]" if s.is_custom else ""
            item = QListWidgetItem(f"🎬 {s.name}{badge}")
            item.setData(Qt.ItemDataRole.UserRole, s)
            self.set_list.addItem(item)
        if self.set_list.count() > 0:
            self.set_list.setCurrentRow(0)
            self._on_set_selected(self.set_list.item(0))

    def _on_set_selected(self, item: QListWidgetItem) -> None:
        s: SceneSet = item.data(Qt.ItemDataRole.UserRole)
        if not s:
            return
        self.name_input.setText(s.name)
        idx = self.cat_combo.findData(s.category)
        if idx >= 0:
            self.cat_combo.setCurrentIndex(idx)
        self.bg_path_input.setText(s.background)
        self.floor_spin.setValue(s.floor_y)
        self.anchor_spin.setValue(s.anchor_x)
        self.height_spin.setValue(s.model_height_px)
        self.blur_spin.setValue(s.bg_blur)
        self.shadow_spin.setValue(s.shadow_alpha)
        self.canvas.set_scene(s)

    def _on_param_changed(self) -> None:
        if not self.canvas.current_set:
            return
        self.canvas.current_set.floor_y = self.floor_spin.value()
        self.canvas.current_set.anchor_x = self.anchor_spin.value()
        self.canvas.current_set.model_height_px = self.height_spin.value()
        self.canvas.current_set.bg_blur = self.blur_spin.value()
        self.canvas.current_set.shadow_alpha = self.shadow_spin.value()
        self.canvas.update()

    def _on_bg_changed(self, text: str) -> None:
        if self.canvas.current_set:
            self.canvas.current_set.background = text.strip()
            self.canvas.set_scene(self.canvas.current_set)

    def _browse_bg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn tệp ảnh hoặc video nền 9:16",
            "",
            "Media Files (*.jpg *.jpeg *.png *.mp4 *.mov);;All Files (*)",
        )
        if path:
            self.bg_path_input.setText(path)

    def _new_custom_set(self) -> None:
        import time
        new_id = f"custom_set_{int(time.time())}"
        new_s = SceneSet(
            id=new_id,
            name="Bối cảnh phòng quay mới",
            category="da_nang",
            background="",
            is_custom=True,
        )
        self.canvas.set_scene(new_s)
        self.name_input.setText(new_s.name)
        self.bg_path_input.setText("")
        self.floor_spin.setValue(1520)
        self.anchor_spin.setValue(540)

    def _save_current_set(self) -> None:
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập tên bối cảnh.")
            return

        cur = self.canvas.current_set
        set_id = cur.id if cur else f"custom_{abs(hash(name)) % 100000}"
        updated_set = SceneSet(
            id=set_id,
            name=name,
            category=self.cat_combo.currentData(),
            background=self.bg_path_input.text().strip(),
            floor_y=self.floor_spin.value(),
            anchor_x=self.anchor_spin.value(),
            model_height_px=self.height_spin.value(),
            bg_blur=self.blur_spin.value(),
            shadow_alpha=self.shadow_spin.value(),
            is_custom=True,
        )
        self.library.save_custom_set(updated_set)
        QMessageBox.information(self, "Thành công", f"Đã lưu bối cảnh '{name}'.")
        self._load_sets_list()
