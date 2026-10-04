"""Render Worker — QThread-based asynchronous rendering worker keeping the GUI 100% fluid (Fix #19)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger
from PySide6.QtCore import QThread, Signal

from app.render.compositor import VideoCompositor
from app.render.queue import RenderJob, RenderQueue


class RenderWorker(QThread):
    """Executes a VideoCompositor job in a separate thread and reports progress signals."""

    progress_updated = Signal(str, int, str)  # job_id, percent (0-100), message
    job_finished = Signal(object, bool, str)  # job (RenderJob), success, error_message

    def __init__(self, job: RenderJob, compositor: Optional[VideoCompositor] = None) -> None:
        super().__init__()
        self.job = job
        self.compositor = compositor or VideoCompositor()
        self.queue = RenderQueue.get_instance()

    def run(self) -> None:
        job = self.job
        job.status = "rendering"
        self._report_progress(10, "Chuẩn bị tài nguyên và bối cảnh...")

        try:
            if not job.scene_set:
                from app.sets.library import SceneSetLibrary
                job.scene_set = SceneSetLibrary().get_set_or_default("studio_gradient")

            self._report_progress(25, "Đang dựng khung hình bối cảnh và bóng đổ...")

            # Run ffmpeg composition
            self._report_progress(45, "FFmpeg đang render video 1080×1920 30fps...")

            out_path = self.compositor.render_composition(
                output_mp4=job.output_mp4,
                scene_set=job.scene_set,
                audio_path=job.audio_path,
                product_image=job.product_image,
                closeup_media=job.closeup_media,
                banner_path=job.banner_path,
                presenter_clip=job.presenter_clip,
                shot_plan=job.shot_plan,
                duration_seconds=job.duration_seconds,
            )

            self._report_progress(85, "Đang kiểm tra chất lượng file xuất (Verify)...")
            verification = self.compositor.verify_rendered_video(out_path, expected_duration=job.duration_seconds)

            if not verification.get("valid", True):
                errors = "; ".join(verification.get("errors", []))
                logger.warning("Video render không đạt chuẩn kiểm tra: {}", errors)

            self._report_progress(100, "Đã hoàn thành video review!")
            job.status = "completed"
            job.progress = 100
            self.queue.mark_completed(job.id, verification)
            self.job_finished.emit(job, True, "")

        except Exception as e:
            logger.exception("Lỗi trong quá trình render video")
            err_msg = str(e)
            job.status = "failed"
            job.error_message = err_msg
            self.queue.mark_failed(job.id, err_msg)
            self.job_finished.emit(job, False, err_msg)

    def _report_progress(self, percent: int, msg: str) -> None:
        self.queue.update_progress(self.job.id, percent, msg)
        self.progress_updated.emit(self.job.id, percent, msg)
