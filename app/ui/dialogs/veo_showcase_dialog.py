"""Veo 3 Video Studio Dialog — Automated multi-scene generative video workflow.

Allows users to generate photorealistic AI product showcase videos with Google Veo 3,
preview individual scenes, re-roll single scenes, and stitch final videos with speech & BGM.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from loguru import logger

from app.config.settings import get_settings
from app.database.models.avatar import AIAvatar
from app.database.models.product import Product
from app.database.repositories.avatar_repo import AvatarRepository
from app.database.repositories.product_repo import ProductRepository
from app.database.session import get_db
from app.services.veo_script_service import VeoScene
from app.services.veo_showcase_service import VeoProject, VeoShowcaseService
from app.ui.styles.dark_theme import COLORS


class VeoPrepareWorker(QThread):
    """Worker to create script, audio, and reference frames."""

    progress = Signal(str, int)
    finished = Signal(object, str)

    def __init__(
        self,
        service: VeoShowcaseService,
        product: Product,
        avatar: Optional[AIAvatar],
        num_scenes: int = 3,
    ) -> None:
        super().__init__()
        self.service = service
        self.product = product
        self.avatar = avatar
        self.num_scenes = num_scenes

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            project = loop.run_until_complete(
                self.service.create_showcase_project(
                    product=self.product,
                    avatar=self.avatar,
                    num_scenes=self.num_scenes,
                    progress_callback=lambda msg, pct: self.progress.emit(msg, pct),
                )
            )
            loop.close()
            self.finished.emit(project, "")
        except Exception as e:
            logger.exception("Lỗi khởi tạo dự án Veo")
            self.finished.emit(None, str(e))


class VeoRenderSceneWorker(QThread):
    """Worker to generate a single scene via Veo 3 API."""

    progress = Signal(str, int)
    finished = Signal(int, str, str)  # scene_index, video_path, error

    def __init__(
        self,
        service: VeoShowcaseService,
        project: VeoProject,
        scene_index: int,
    ) -> None:
        super().__init__()
        self.service = service
        self.project = project
        self.scene_index = scene_index

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            video_path = loop.run_until_complete(
                self.service.generate_scene_video(
                    project=self.project,
                    scene_index=self.scene_index,
                    progress_callback=lambda msg, pct: self.progress.emit(msg, pct),
                )
            )
            loop.close()
            self.finished.emit(self.scene_index, video_path, "")
        except Exception as e:
            logger.exception("Lỗi render Scene {}", self.scene_index)
            self.finished.emit(self.scene_index, "", str(e))


class VeoStitchWorker(QThread):
    """Worker to stitch all scenes together with BGM."""

    progress = Signal(str, int)
    finished = Signal(str, str)  # final_video_path, error

    def __init__(
        self,
        service: VeoShowcaseService,
        project: VeoProject,
        bgm_path: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.service = service
        self.project = project
        self.bgm_path = bgm_path

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            final_path = loop.run_until_complete(
                self.service.stitch_final_showcase_video(
                    project=self.project,
                    bgm_path=self.bgm_path,
                    progress_callback=lambda msg, pct: self.progress.emit(msg, pct),
                )
            )
            loop.close()
            self.finished.emit(final_path, "")
        except Exception as e:
            logger.exception("Lỗi ghép video Veo")
            self.finished.emit("", str(e))


class VeoShowcaseDialog(QDialog):
    """Dialog for automated end-to-end Veo 3 Video generation."""

    def __init__(self, product: Product, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        from app.database.session import get_session
        from app.services.avatar_service import AvatarService

        self.session = get_session()
        self.avatar_service = AvatarService(self.session)
        self.product = product
        self.settings = get_settings()
        self.service = VeoShowcaseService(session=self.session)
        self.project: Optional[VeoProject] = None

        self.setWindowTitle(f"Google Veo 3 Video Studio — {product.name}")
        self.resize(1200, 800)
        self.setMinimumSize(1000, 680)

        self._load_avatars()
        self._init_ui()
        self._init_media_player()

        # Automatically start project preparation once event loop is running
        from PySide6.QtCore import QTimer
        QTimer.singleShot(200, self._start_preparation)

    def _load_avatars(self) -> None:
        try:
            self.avatars = list(self.avatar_service.get_avatars())
        except Exception:
            self.avatars = []
        self.current_avatar = self.avatars[0] if self.avatars else None

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(12)

        # 1. Header Bar
        header = QHBoxLayout()
        icon = QLabel("🎬")
        icon.setStyleSheet("font-size: 26px;")
        header.addWidget(icon)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_lbl = QLabel(f"Tạo Video Bán Hàng Veo 3 — {self.product.name}")
        title_lbl.setStyleSheet("font-size: 17px; font-weight: bold; color: #FFFFFF;")
        title_box.addWidget(title_lbl)

        sub_lbl = QLabel("Tự động viết kịch bản, tạo cử chỉ người mẫu tương tác với sản phẩm bằng Google Veo 3 API")
        sub_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px;")
        title_box.addWidget(sub_lbl)
        header.addLayout(title_box, 1)

        model_badge = QLabel("⚡ Model: Veo 3.1 Fast")
        model_badge.setStyleSheet(
            "background-color: #0984E3; color: white; padding: 6px 12px; border-radius: 12px; font-weight: bold; font-size: 11px;"
        )
        header.addWidget(model_badge)
        main_layout.addLayout(header)

        # 2. Control Row
        ctrl_card = QFrame()
        ctrl_card.setObjectName("cardFrame")
        ctrl_layout = QHBoxLayout(ctrl_card)
        ctrl_layout.setContentsMargins(14, 10, 14, 10)
        ctrl_layout.setSpacing(14)

        ctrl_layout.addWidget(QLabel("Người mẫu AI:"))
        self.avatar_combo = QComboBox()
        self.avatar_combo.setMinimumWidth(200)
        for av in self.avatars:
            self.avatar_combo.addItem(f"{av.name} ({av.style})", av.id)
        self.avatar_combo.currentIndexChanged.connect(self._on_avatar_changed)
        ctrl_layout.addWidget(self.avatar_combo)

        ctrl_layout.addWidget(QLabel("Số phân cảnh:"))
        self.scene_count_combo = QComboBox()
        self.scene_count_combo.addItems(["3 phân cảnh (~15 giây)", "4 phân cảnh (~20 giây)"])
        ctrl_layout.addWidget(self.scene_count_combo)

        ctrl_layout.addStretch()

        self.btn_auto_all = QPushButton("🚀 Tự Động Tạo Toàn Bộ Video (One-Click)")
        self.btn_auto_all.setObjectName("accentButton")
        self.btn_auto_all.setStyleSheet("font-weight: bold; font-size: 13px; padding: 8px 18px;")
        self.btn_auto_all.clicked.connect(self._on_generate_all_scenes)
        ctrl_layout.addWidget(self.btn_auto_all)

        main_layout.addWidget(ctrl_card)

        # 3. Main Center Area (Splitter: Scenes Cards on Left, Video Player on Right)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left side: Scroll Area for Scene Cards
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")

        self.scenes_container = QWidget()
        self.scenes_layout = QVBoxLayout(self.scenes_container)
        self.scenes_layout.setContentsMargins(0, 0, 8, 0)
        self.scenes_layout.setSpacing(12)
        scroll.setWidget(self.scenes_container)
        splitter.addWidget(scroll)

        # Right side: Live Player Preview
        player_card = QFrame()
        player_card.setObjectName("cardFrame")
        player_layout = QVBoxLayout(player_card)
        player_layout.setContentsMargins(12, 12, 12, 12)
        player_layout.setSpacing(8)

        player_header = QHBoxLayout()
        self.player_title = QLabel("▶ Khung Xem Trước Video")
        self.player_title.setStyleSheet("font-weight: bold; font-size: 13px;")
        player_header.addWidget(self.player_title)
        player_header.addStretch()
        player_layout.addLayout(player_header)

        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumSize(280, 500)
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        player_layout.addWidget(self.video_widget, 1)

        # Player controls
        btn_row = QHBoxLayout()
        self.play_btn = QPushButton("▶ Phát")
        self.play_btn.clicked.connect(self._toggle_playback)
        btn_row.addWidget(self.play_btn)
        player_layout.addLayout(btn_row)

        splitter.addWidget(player_card)
        splitter.setSizes([620, 380])
        main_layout.addWidget(splitter, 1)

        # 4. Progress and Status Bar
        self.status_lbl = QLabel("Đang chuẩn bị kịch bản và khung hình...")
        self.status_lbl.setStyleSheet(f"color: {COLORS['text_muted']}; font-size: 12px;")
        main_layout.addWidget(self.status_lbl)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        main_layout.addWidget(self.progress_bar)

        # 5. Bottom Action Bar
        action_bar = QHBoxLayout()
        action_bar.setSpacing(10)

        self.btn_stitch = QPushButton("🧩 Ghép Video Hoàn Chỉnh")
        self.btn_stitch.setEnabled(False)
        self.btn_stitch.clicked.connect(self._on_stitch_video)
        action_bar.addWidget(self.btn_stitch)

        self.btn_save_live = QPushButton("🔴 Lưu & Đưa Vào Playlist Live")
        self.btn_save_live.setObjectName("dangerButton")
        self.btn_save_live.setEnabled(False)
        self.btn_save_live.clicked.connect(self._on_save_and_apply_to_live)
        action_bar.addWidget(self.btn_save_live)

        action_bar.addStretch()

        btn_close = QPushButton("Đóng")
        btn_close.clicked.connect(self.close)
        action_bar.addWidget(btn_close)

        main_layout.addLayout(action_bar)

    def _init_media_player(self) -> None:
        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)
        self.audio_output.setVolume(1.0)

    def _toggle_playback(self) -> None:
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
            self.play_btn.setText("▶ Tiếp tục")
        else:
            self.media_player.play()
            self.play_btn.setText("⏸ Tạm dừng")

    def _on_avatar_changed(self) -> None:
        idx = self.avatar_combo.currentIndex()
        if 0 <= idx < len(self.avatars):
            self.current_avatar = self.avatars[idx]
            self._start_preparation()

    def _start_preparation(self) -> None:
        self.progress_bar.setValue(10)
        self.status_lbl.setText("AI đang phân tích sản phẩm và chuẩn bị kịch bản phân cảnh...")
        num = 3 if self.scene_count_combo.currentIndex() == 0 else 4

        self.prep_worker = VeoPrepareWorker(
            service=self.service,
            product=self.product,
            avatar=self.current_avatar,
            num_scenes=num,
        )
        self.prep_worker.progress.connect(self._on_progress_update)
        self.prep_worker.finished.connect(self._on_preparation_finished)
        self.prep_worker.start()

    def _on_progress_update(self, msg: str, pct: int) -> None:
        self.status_lbl.setText(msg)
        self.progress_bar.setValue(pct)

    def _on_preparation_finished(self, project: Optional[VeoProject], error: str) -> None:
        if error or not project:
            QMessageBox.critical(self, "Lỗi chuẩn bị", f"Không thể chuẩn bị kịch bản Veo: {error}")
            self.status_lbl.setText("Gặp lỗi khi tạo kịch bản.")
            return

        self.project = project
        self.progress_bar.setValue(100)
        self.status_lbl.setText("Kịch bản và ảnh tham chiếu đã sẵn sàng. Bạn có thể bấm 'Tự Động Tạo Toàn Bộ' hoặc tạo từng cảnh!")
        self._populate_scene_cards()

    def _populate_scene_cards(self) -> None:
        # Clear existing
        while self.scenes_layout.count():
            item = self.scenes_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self.project:
            return

        for scene in self.project.script.scenes:
            card = self._create_scene_card(scene)
            self.scenes_layout.addWidget(card)

        self.scenes_layout.addStretch()

    def _create_scene_card(self, scene: VeoScene) -> QWidget:
        card = QFrame()
        card.setObjectName("cardFrame")
        card.setStyleSheet(
            "QFrame#cardFrame { background-color: #21252B; border: 1px solid #3A3F4B; border-radius: 8px; }"
        )
        layout = QHBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        # 1. Thumbnail / Reference Frame
        thumb_lbl = QLabel()
        thumb_lbl.setFixedSize(90, 150)
        thumb_lbl.setStyleSheet("background-color: #111; border-radius: 6px;")
        thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        frame_path = self.project.reference_frames.get(scene.scene_index)
        if frame_path and os.path.exists(frame_path):
            pix = QPixmap(frame_path)
            thumb_lbl.setPixmap(
                pix.scaled(90, 150, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            )
        else:
            thumb_lbl.setText("🖼\nFrame")
        layout.addWidget(thumb_lbl)

        # 2. Scene Info (Type, Speech, Prompt)
        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)

        header_row = QHBoxLayout()
        title = QLabel(f"Phân Cảnh {scene.scene_index}: [{scene.shot_type}]")
        title.setStyleSheet("font-weight: bold; font-size: 13px; color: #00CEC9;")
        header_row.addWidget(title)

        status_badge = QLabel("Chưa tạo" if scene.status == "PENDING" else scene.status)
        status_badge.setObjectName(f"status_{scene.scene_index}")
        status_badge.setStyleSheet(
            "background-color: #636E72; color: white; padding: 2px 8px; border-radius: 4px; font-size: 10px;"
        )
        header_row.addWidget(status_badge)
        header_row.addStretch()
        info_layout.addLayout(header_row)

        # Speech text
        speech_lbl = QLabel(f"🗣 <b>Lời thoại MC:</b> {scene.speech_text}")
        speech_lbl.setWordWrap(True)
        speech_lbl.setStyleSheet("font-size: 12px; color: #DFE6E9;")
        info_layout.addWidget(speech_lbl)

        # Prompt
        prompt_lbl = QLabel(f"🎬 <b>Prompt Veo:</b> {scene.veo_prompt[:130]}...")
        prompt_lbl.setWordWrap(True)
        prompt_lbl.setStyleSheet(f"font-size: 11px; color: {COLORS['text_muted']};")
        info_layout.addWidget(prompt_lbl)

        layout.addLayout(info_layout, 1)

        # 3. Actions for this scene
        btn_box = QVBoxLayout()
        btn_box.setSpacing(6)

        btn_render = QPushButton("🔄 Tạo Lại Cảnh")
        btn_render.setStyleSheet("padding: 5px 12px; font-size: 11px;")
        btn_render.clicked.connect(lambda _, s=scene.scene_index: self._render_single_scene(s))
        btn_box.addWidget(btn_render)

        btn_preview = QPushButton("▶ Xem Cảnh")
        btn_preview.setStyleSheet("padding: 5px 12px; font-size: 11px;")
        btn_preview.clicked.connect(lambda _, s=scene.scene_index: self._preview_scene(s))
        btn_box.addWidget(btn_preview)

        btn_box.addStretch()
        layout.addLayout(btn_box)

        return card

    def _render_single_scene(self, scene_index: int) -> None:
        if not self.project:
            return

        self.btn_auto_all.setEnabled(False)
        self.status_lbl.setText(f"Đang gửi yêu cầu tạo video cho Phân cảnh {scene_index}...")
        self.progress_bar.setValue(15)

        self.scene_worker = VeoRenderSceneWorker(self.service, self.project, scene_index)
        self.scene_worker.progress.connect(self._on_progress_update)
        self.scene_worker.finished.connect(self._on_scene_render_finished)
        self.scene_worker.start()

    def _on_scene_render_finished(self, scene_index: int, video_path: str, error: str) -> None:
        self.btn_auto_all.setEnabled(True)
        if error or not video_path:
            QMessageBox.warning(self, "Lỗi render", f"Lỗi khi render Phân cảnh {scene_index}: {error}")
            self.status_lbl.setText(f"Phân cảnh {scene_index} gặp lỗi: {error}")
            return

        self.status_lbl.setText(f"Phân cảnh {scene_index} đã hoàn tất!")
        self.progress_bar.setValue(100)

        # Update badge
        badge = self.findChild(QLabel, f"status_{scene_index}")
        if badge:
            badge.setText("✅ Hoàn thành")
            badge.setStyleSheet("background-color: #00B894; color: white; padding: 2px 8px; border-radius: 4px;")

        # Play in preview
        self._preview_scene(scene_index)

        # Check if all scenes are done to enable stitch button
        all_done = all(
            bool(self.project.aligned_video_clips.get(s.scene_index))
            for s in self.project.script.scenes
        )
        if all_done:
            self.btn_stitch.setEnabled(True)

    def _on_generate_all_scenes(self) -> None:
        """Sequential generation of all scenes."""
        if not self.project or not self.project.script.scenes:
            return

        self.btn_auto_all.setEnabled(False)
        self._current_gen_queue = [s.scene_index for s in self.project.script.scenes]
        self._process_next_scene_in_queue()

    def _process_next_scene_in_queue(self) -> None:
        if not hasattr(self, "_current_gen_queue") or not self._current_gen_queue:
            self.btn_auto_all.setEnabled(True)
            self.btn_stitch.setEnabled(True)
            self.status_lbl.setText("Đã tạo xong toàn bộ các phân cảnh! Bạn có thể bấm 'Ghép Video Hoàn Chỉnh'.")
            # Auto trigger stitch
            self._on_stitch_video()
            return

        scene_index = self._current_gen_queue.pop(0)
        self.status_lbl.setText(f"Đang render Phân cảnh {scene_index}...")

        self.scene_worker = VeoRenderSceneWorker(self.service, self.project, scene_index)
        self.scene_worker.progress.connect(self._on_progress_update)
        self.scene_worker.finished.connect(self._on_queue_scene_finished)
        self.scene_worker.start()

    def _on_queue_scene_finished(self, scene_index: int, video_path: str, error: str) -> None:
        if error:
            logger.warning("Scene {} error in batch: {}", scene_index, error)
        else:
            badge = self.findChild(QLabel, f"status_{scene_index}")
            if badge:
                badge.setText("✅ Hoàn thành")
                badge.setStyleSheet("background-color: #00B894; color: white; padding: 2px 8px; border-radius: 4px;")

        # Next
        self._process_next_scene_in_queue()

    def _preview_scene(self, scene_index: int) -> None:
        if not self.project:
            return
        video_path = self.project.aligned_video_clips.get(scene_index) or self.project.raw_video_clips.get(scene_index)
        if not video_path or not os.path.exists(video_path):
            QMessageBox.information(self, "Chưa có video", f"Phân cảnh {scene_index} chưa được render.")
            return

        from PySide6.QtCore import QUrl

        self.player_title.setText(f"▶ Xem Phân Cảnh {scene_index}")
        self.media_player.setSource(QUrl.fromLocalFile(video_path))
        self.media_player.play()
        self.play_btn.setText("⏸ Tạm dừng")

    def _on_stitch_video(self) -> None:
        if not self.project:
            return

        self.btn_stitch.setEnabled(False)
        self.status_lbl.setText("Đang ghép nối toàn bộ các phân cảnh và lồng nhạc nền...")
        self.progress_bar.setValue(90)

        self.stitch_worker = VeoStitchWorker(self.service, self.project)
        self.stitch_worker.progress.connect(self._on_progress_update)
        self.stitch_worker.finished.connect(self._on_stitch_finished)
        self.stitch_worker.start()

    def _on_stitch_finished(self, final_video_path: str, error: str) -> None:
        self.btn_stitch.setEnabled(True)
        if error or not final_video_path:
            QMessageBox.critical(self, "Lỗi ghép video", f"Không thể ghép video: {error}")
            self.status_lbl.setText("Lỗi ghép video.")
            return

        self.progress_bar.setValue(100)
        self.status_lbl.setText("🎉 Video bán hàng AI hoàn chỉnh đã được tạo thành công!")
        self.btn_save_live.setEnabled(True)

        # Play final video in player
        from PySide6.QtCore import QUrl

        self.player_title.setText("🎉 Video Bán Hàng Hoàn Chỉnh (Veo 3 + Audio + Sub)")
        self.media_player.setSource(QUrl.fromLocalFile(final_video_path))
        self.media_player.play()
        self.play_btn.setText("⏸ Tạm dừng")

    def _on_save_and_apply_to_live(self) -> None:
        if not self.project or not self.project.final_video_path:
            return

        try:
            with get_db() as session:
                repo = ProductRepository(session)
                repo.update(self.product.id, {"video_path": self.project.final_video_path})
            QMessageBox.information(
                self,
                "Thành công",
                f"Đã lưu video bán hàng Veo 3 vào sản phẩm '{self.product.name}'.\n"
                "Khi bạn bắt đầu Livestream, hệ thống sẽ tự động phát video này!",
            )
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "Lỗi lưu", f"Không thể lưu vào database: {e}")

    def closeEvent(self, event) -> None:
        for worker_name in ["prep_worker", "scene_worker", "stitch_worker"]:
            w = getattr(self, worker_name, None)
            if w and w.isRunning():
                try:
                    w.quit()
                    w.wait(500)
                except Exception:
                    pass
        if hasattr(self, "media_player"):
            self.media_player.stop()
        if hasattr(self, "session") and self.session:
            try:
                self.session.close()
            except Exception:
                pass
        super().closeEvent(event)
