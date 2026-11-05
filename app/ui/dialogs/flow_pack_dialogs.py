"""Hộp thoại tạo "gói việc" Flow — người mẫu AI hoặc video bán hàng theo sản phẩm."""

from __future__ import annotations

import asyncio
from typing import List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from loguru import logger

from app.config.settings import get_settings
from app.integrations.flow.constants import (
    APPEARANCE_FEMALE,
    APPEARANCE_MALE,
    AVATAR_CUES,
    FLOW_DURATIONS,
    FLOW_MODELS,
    FLOW_RESOLUTIONS,
    credits_for,
)
from app.integrations.flow.pack import FlowPack, build_avatar_pack, build_product_pack
from app.ui.styles.dark_theme import COLORS

VOICE_CHOICES = [
    ("vi-VN-HoaiMyNeural", "Hoài My (nữ, miền Bắc)"),
    ("vi-VN-NamMinhNeural", "Nam Minh (nam, miền Bắc)"),
]


def _fill_model_combo(combo: QComboBox, current: str) -> None:
    for key, model in FLOW_MODELS.items():
        combo.addItem(f"{model.label} — {model.credits} tín dụng/clip", key)
    idx = combo.findData(current)
    combo.setCurrentIndex(idx if idx >= 0 else 0)


class AvatarPackDialog(QDialog):
    """Gói việc tạo người mẫu AI: 1 ảnh chân dung + 1 clip nền + 9 clip cử chỉ."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tạo người mẫu AI bằng Google Flow")
        self.setMinimumWidth(620)
        self._settings = get_settings()
        self._pack: Optional[FlowPack] = None
        self._avatar_id: Optional[int] = None
        self._avatars: List = []
        self._build_ui()
        self._load_avatars()
        self._update_estimate()

    # ------------------------------------------------------------------ ui
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        intro = QLabel(
            "LiveFlow sẽ viết sẵn toàn bộ prompt. Bạn chỉ cần bấm <b>Chạy tự động</b> ở bước sau — "
            "app tự dán prompt vào Flow, tự bấm tạo, tự tải và nạp clip về."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {COLORS['text_secondary']};")
        root.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(10)

        self._avatar_combo = QComboBox()
        self._avatar_combo.currentIndexChanged.connect(self._on_avatar_changed)
        form.addRow("Gán vào người mẫu:", self._avatar_combo)

        self._name_edit = QLineEdit("Mai Phương")
        self._name_edit.setToolTip("Quyết định tên file: 'Mai Phương' -> mai_phuong.mp4")
        form.addRow("Tên người mẫu:", self._name_edit)

        self._gender_combo = QComboBox()
        self._gender_combo.addItem("Nữ", "female")
        self._gender_combo.addItem("Nam", "male")
        self._gender_combo.currentIndexChanged.connect(self._on_gender_changed)
        form.addRow("Giới tính:", self._gender_combo)

        self._look_edit = QTextEdit()
        self._look_edit.setPlainText(APPEARANCE_FEMALE)
        self._look_edit.setFixedHeight(68)
        self._look_edit.setToolTip(
            "Mô tả ngoại hình bằng TIẾNG ANH. Giữ nguyên đoạn này cho cả 10 clip "
            "là cách giữ mặt người mẫu không đổi."
        )
        form.addRow("Ngoại hình (EN):", self._look_edit)

        self._outfit_edit = QLineEdit()
        self._outfit_edit.setPlaceholderText("vd: wearing a red ao dai — để trống cũng được")
        form.addRow("Trang phục thêm:", self._outfit_edit)

        self._model_combo = QComboBox()
        _fill_model_combo(self._model_combo, self._settings.flow_model)
        self._model_combo.currentIndexChanged.connect(self._update_estimate)
        form.addRow("Model trong Flow:", self._model_combo)

        row = QHBoxLayout()
        self._res_combo = QComboBox()
        self._res_combo.addItems(list(FLOW_RESOLUTIONS))
        self._res_combo.setCurrentText(self._settings.flow_resolution)
        row.addWidget(self._res_combo)
        self._dur_combo = QComboBox()
        for d in FLOW_DURATIONS:
            self._dur_combo.addItem(f"{d} giây", d)
        idx = self._dur_combo.findData(self._settings.flow_duration_seconds)
        self._dur_combo.setCurrentIndex(idx if idx >= 0 else self._dur_combo.count() - 1)
        row.addWidget(self._dur_combo)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("Độ phân giải / độ dài:", holder)

        self._green_check = QCheckBox("Nền xanh #00FF00 để OBS tách nền (khuyên dùng)")
        self._green_check.setChecked(True)
        form.addRow("", self._green_check)

        self._portrait_check = QCheckBox(
            "Tạo thêm 1 ảnh chân dung riêng bằng Flow (tốn tín dụng — thường KHÔNG cần)"
        )
        self._portrait_check.setToolTip(
            "Không cần tích: sau khi có clip nền, LiveFlow tự trích một khung làm ảnh chân dung "
            "(miễn phí) để bạn kéo vào ô 'Ingredients' của Flow — mặt người mẫu sẽ giống nhau "
            "ở mọi clip sau. Chỉ tích nếu bạn muốn một ảnh studio riêng."
        )
        self._portrait_check.setChecked(False)
        self._portrait_check.stateChanged.connect(self._update_estimate)
        form.addRow("", self._portrait_check)

        self._cues_check = QCheckBox("Tạo đủ 9 clip cử chỉ (bỏ tích = chỉ làm clip nền)")
        self._cues_check.setChecked(True)
        self._cues_check.stateChanged.connect(self._update_estimate)
        form.addRow("", self._cues_check)

        root.addLayout(form)

        self._estimate_label = QLabel()
        self._estimate_label.setWordWrap(True)
        self._estimate_label.setStyleSheet(
            f"background:{COLORS['bg_light']}; border:1px solid {COLORS['border']};"
            f"border-radius:8px; padding:10px; color:{COLORS['text_primary']};"
        )
        root.addWidget(self._estimate_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Hủy")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        create = QPushButton("✅ Tạo gói việc")
        create.setObjectName("primaryButton")
        create.clicked.connect(self._on_create)
        buttons.addWidget(create)
        root.addLayout(buttons)

    # --------------------------------------------------------------- state
    def _load_avatars(self) -> None:
        self._avatar_combo.addItem("— Không gán (chỉ lưu file) —", None)
        try:
            from app.services.avatar_service import AvatarService

            self._avatars = list(AvatarService().get_avatars())
            for av in self._avatars:
                self._avatar_combo.addItem(f"#{av.id} · {av.name}", av.id)
            if self._avatars:
                self._avatar_combo.setCurrentIndex(1)
        except Exception as e:
            logger.warning("Không nạp được danh sách người mẫu: {}", e)

    def _on_avatar_changed(self) -> None:
        avatar_id = self._avatar_combo.currentData()
        if not avatar_id:
            return
        av = next((a for a in self._avatars if a.id == avatar_id), None)
        if not av:
            return
        self._name_edit.setText(av.name or "")
        gidx = self._gender_combo.findData(getattr(av, "gender", "female"))
        if gidx >= 0:
            self._gender_combo.setCurrentIndex(gidx)

    def _on_gender_changed(self) -> None:
        gender = self._gender_combo.currentData()
        current = self._look_edit.toPlainText().strip()
        if current in ("", APPEARANCE_FEMALE, APPEARANCE_MALE):
            self._look_edit.setPlainText(
                APPEARANCE_FEMALE if gender == "female" else APPEARANCE_MALE
            )
        self._update_estimate()

    def _job_count(self) -> int:
        count = 1  # clip nền
        if self._cues_check.isChecked():
            count += len(AVATAR_CUES)
        return count

    def _update_estimate(self) -> None:
        model_key = self._model_combo.currentData() or self._settings.flow_model
        per = credits_for(model_key, self._settings.flow_credit_cost)
        videos = self._job_count()
        total = videos * per
        if self._portrait_check.isChecked():
            from app.integrations.flow.constants import FLOW_IMAGE_CREDITS

            total += self._settings.flow_image_credit_cost or FLOW_IMAGE_CREDITS

        try:
            from app.integrations.flow.credits import CreditLedger

            remaining = CreditLedger(default_total=self._settings.flow_credits_total).remaining
        except Exception:
            remaining = self._settings.flow_credits_total

        warn = ""
        if total > remaining:
            warn = (
                f"<br><span style='color:{COLORS['accent_danger']}'>⚠ Vượt số dư "
                f"({remaining} tín dụng). Hãy chọn model rẻ hơn hoặc bỏ tích 9 cue.</span>"
            )
        self._estimate_label.setText(
            f"<b>Dự toán:</b> {videos} clip × {per} tín dụng"
            + (" + 1 ảnh" if self._portrait_check.isChecked() else "")
            + f" = <b>{total} tín dụng</b>. Số dư đang ghi nhận: <b>{remaining}</b>.{warn}"
        )

    # -------------------------------------------------------------- create
    def _on_create(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Thiếu thông tin", "Nhập tên người mẫu.")
            return
        self._avatar_id = self._avatar_combo.currentData()
        try:
            self._pack = build_avatar_pack(
                name=name,
                gender=self._gender_combo.currentData() or "female",
                appearance=self._look_edit.toPlainText().strip(),
                outfit_note=self._outfit_edit.text().strip(),
                model=self._model_combo.currentData() or self._settings.flow_model,
                resolution=self._res_combo.currentText(),
                duration=int(self._dur_combo.currentData() or 8),
                green_screen=self._green_check.isChecked(),
                include_cues=None if self._cues_check.isChecked() else [],
                include_portrait=self._portrait_check.isChecked(),
                credit_override=self._settings.flow_credit_cost,
                image_credit_override=self._settings.flow_image_credit_cost,
            )
        except Exception as e:
            logger.exception("Không tạo được gói việc Flow")
            QMessageBox.critical(self, "Lỗi", f"Không tạo được gói việc:\n{e}")
            return
        if self._avatar_id:
            self._pack.meta["avatar_id"] = int(self._avatar_id)
            self._pack.save()
        self.accept()

    @property
    def pack(self) -> Optional[FlowPack]:
        return self._pack

    @property
    def avatar_id(self) -> Optional[int]:
        return self._avatar_id


# ---------------------------------------------------------------------------
class ProductPrepareWorker(QThread):
    """Viết kịch bản + tạo giọng đọc + khung hình đầu, rồi dựng gói việc Flow."""

    progress = Signal(str, int)
    done = Signal(object, object, str)  # (VeoProject | None, FlowPack | None, error)

    def __init__(
        self,
        product,
        avatar,
        *,
        style: str,
        voice_id: str,
        num_scenes: int,
        model: str,
        resolution: str,
        credit_override: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._product = product
        self._avatar = avatar
        self._style = style
        self._voice_id = voice_id
        self._num_scenes = num_scenes
        self._model = model
        self._resolution = resolution
        self._credit_override = credit_override

    def run(self) -> None:  # noqa: D102
        try:
            from app.config.settings import get_settings as _gs
            from app.services.veo_showcase_service import VeoShowcaseService

            service = VeoShowcaseService()
            project = asyncio.run(
                service.create_showcase_project(
                    product=self._product,
                    avatar=self._avatar,
                    style=self._style,
                    voice_id=self._voice_id,
                    num_scenes=self._num_scenes,
                    progress_callback=lambda m, p: self.progress.emit(m, p),
                )
            )
            self.progress.emit("Đang viết prompt cho Flow...", 70)

            scenes = [
                {
                    "scene_index": s.scene_index,
                    "shot_type": s.shot_type,
                    "veo_prompt": s.veo_prompt,
                    "speech_text": s.speech_text,
                    "duration_seconds": s.duration_seconds,
                    "reference_frame": project.reference_frames.get(s.scene_index, ""),
                }
                for s in project.script.scenes
            ]
            pack = build_product_pack(
                title=f"{project.product_name} — {project.script.title}",
                scenes=scenes,
                project_id=project.project_id,
                product_id=project.product_id,
                avatar_name=project.avatar_name,
                model=self._model,
                resolution=self._resolution,
                native_speech=bool(_gs().veo_use_native_audio),
                credit_override=self._credit_override,
                product_image=getattr(self._product, "image_path", None),
            )
            # Lưu dự án cạnh gói việc để ghép video được sau khi tắt app
            (pack.dir / "project.json").write_text(
                project.model_dump_json(indent=2), encoding="utf-8"
            )
            self.progress.emit("Gói việc đã sẵn sàng!", 100)
            self.done.emit(project, pack, "")
        except Exception as e:  # noqa: BLE001
            logger.exception("Chuẩn bị gói việc Flow cho sản phẩm thất bại")
            self.done.emit(None, None, str(e))


class ProductPackDialog(QDialog):
    """Chọn sản phẩm → AI viết kịch bản → dựng gói việc Flow."""

    def __init__(self, parent: Optional[QWidget] = None, product_id: Optional[int] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tạo video bán hàng bằng Google Flow")
        self.setMinimumWidth(620)
        self._settings = get_settings()
        self._pack: Optional[FlowPack] = None
        self._project = None
        self._worker: Optional[ProductPrepareWorker] = None
        self._products: List = []
        self._avatars: List = []
        self._build_ui()
        self._load_data(product_id)
        self._update_estimate()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        intro = QLabel(
            "AI viết kịch bản bám dữ liệu sản phẩm, tạo giọng đọc và khung hình đầu. "
            "Sau đó app tự chạy từng cảnh trong Flow rồi ghép thành video hoàn chỉnh."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {COLORS['text_secondary']};")
        root.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(10)

        self._product_combo = QComboBox()
        form.addRow("Sản phẩm:", self._product_combo)

        self._avatar_combo = QComboBox()
        form.addRow("Người mẫu:", self._avatar_combo)

        self._style_combo = QComboBox()
        for key, label in [
            ("energetic", "Năng lượng cao, chốt đơn nhanh"),
            ("friendly", "Thân thiện, gần gũi"),
            ("expert", "Chuyên gia, phân tích"),
        ]:
            self._style_combo.addItem(label, key)
        form.addRow("Phong cách:", self._style_combo)

        self._voice_combo = QComboBox()
        for vid, label in VOICE_CHOICES:
            self._voice_combo.addItem(label, vid)
        vidx = self._voice_combo.findData(self._settings.tts_default_voice)
        if vidx >= 0:
            self._voice_combo.setCurrentIndex(vidx)
        form.addRow("Giọng đọc dự phòng:", self._voice_combo)

        self._scenes_spin = QSpinBox()
        self._scenes_spin.setRange(2, 6)
        self._scenes_spin.setValue(3)
        self._scenes_spin.valueChanged.connect(self._update_estimate)
        form.addRow("Số phân cảnh:", self._scenes_spin)

        self._model_combo = QComboBox()
        _fill_model_combo(self._model_combo, self._settings.flow_model)
        self._model_combo.currentIndexChanged.connect(self._update_estimate)
        form.addRow("Model trong Flow:", self._model_combo)

        self._res_combo = QComboBox()
        self._res_combo.addItems(list(FLOW_RESOLUTIONS))
        self._res_combo.setCurrentText(self._settings.flow_resolution)
        form.addRow("Độ phân giải:", self._res_combo)

        root.addLayout(form)

        self._estimate_label = QLabel()
        self._estimate_label.setWordWrap(True)
        self._estimate_label.setStyleSheet(
            f"background:{COLORS['bg_light']}; border:1px solid {COLORS['border']};"
            f"border-radius:8px; padding:10px; color:{COLORS['text_primary']};"
        )
        root.addWidget(self._estimate_label)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        root.addWidget(self._progress)
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        root.addWidget(self._status_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self._cancel_btn = QPushButton("Hủy")
        self._cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self._cancel_btn)
        self._create_btn = QPushButton("✨ Viết kịch bản & tạo gói việc")
        self._create_btn.setObjectName("primaryButton")
        self._create_btn.clicked.connect(self._on_create)
        buttons.addWidget(self._create_btn)
        root.addLayout(buttons)

    def _load_data(self, product_id: Optional[int]) -> None:
        try:
            from app.services.product_service import ProductService

            self._products = list(ProductService().list_products(is_active=True))
            for p in self._products:
                self._product_combo.addItem(f"#{p.id} · {p.name}", p.id)
            if product_id:
                idx = self._product_combo.findData(product_id)
                if idx >= 0:
                    self._product_combo.setCurrentIndex(idx)
        except Exception as e:
            logger.warning("Không nạp được sản phẩm: {}", e)

        self._avatar_combo.addItem("— Để AI tự mô tả —", None)
        try:
            from app.services.avatar_service import AvatarService

            self._avatars = list(AvatarService().get_avatars())
            for av in self._avatars:
                self._avatar_combo.addItem(f"#{av.id} · {av.name}", av.id)
            if self._avatars:
                self._avatar_combo.setCurrentIndex(1)
        except Exception as e:
            logger.warning("Không nạp được người mẫu: {}", e)

    def _update_estimate(self) -> None:
        model_key = self._model_combo.currentData() or self._settings.flow_model
        per = credits_for(model_key, self._settings.flow_credit_cost)
        total = per * self._scenes_spin.value()
        try:
            from app.integrations.flow.credits import CreditLedger

            remaining = CreditLedger(default_total=self._settings.flow_credits_total).remaining
        except Exception:
            remaining = self._settings.flow_credits_total
        warn = ""
        if total > remaining:
            warn = f"<br><span style='color:{COLORS['accent_danger']}'>⚠ Vượt số dư ({remaining}).</span>"
        self._estimate_label.setText(
            f"<b>Dự toán:</b> {self._scenes_spin.value()} cảnh × {per} tín dụng = "
            f"<b>{total} tín dụng</b>. Số dư đang ghi nhận: <b>{remaining}</b>.{warn}"
        )

    def _on_create(self) -> None:
        product_id = self._product_combo.currentData()
        if not product_id:
            QMessageBox.warning(self, "Thiếu thông tin", "Chọn một sản phẩm.")
            return
        product = next((p for p in self._products if p.id == product_id), None)
        avatar_id = self._avatar_combo.currentData()
        avatar = next((a for a in self._avatars if a.id == avatar_id), None) if avatar_id else None

        self._create_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(5)
        self._status_label.setText("AI đang đọc dữ liệu sản phẩm...")

        self._worker = ProductPrepareWorker(
            product,
            avatar,
            style=self._style_combo.currentData() or "energetic",
            voice_id=self._voice_combo.currentData() or self._settings.tts_default_voice,
            num_scenes=self._scenes_spin.value(),
            model=self._model_combo.currentData() or self._settings.flow_model,
            resolution=self._res_combo.currentText(),
            credit_override=self._settings.flow_credit_cost,
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_progress(self, message: str, pct: int) -> None:
        self._status_label.setText(message)
        self._progress.setValue(max(5, min(100, pct)))

    def _on_done(self, project, pack, error: str) -> None:
        self._create_btn.setEnabled(True)
        self._cancel_btn.setEnabled(True)
        if error or not pack:
            self._progress.setVisible(False)
            QMessageBox.critical(self, "Lỗi", f"Không chuẩn bị được gói việc:\n{error}")
            return
        self._project = project
        self._pack = pack
        self.accept()

    @property
    def pack(self) -> Optional[FlowPack]:
        return self._pack

    @property
    def project(self):
        return self._project
