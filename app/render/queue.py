"""Render Queue — manages asynchronous offline video composition jobs without freezing the UI."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger

from app.render.shot_planner import ShotPlan
from app.sets.scene_set import SceneSet


@dataclass
class RenderJob:
    """A background video rendering job."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    product_id: Optional[int] = None
    product_name: str = "Sản phẩm"
    status: str = "queued"  # queued, rendering, completed, failed, cancelled
    progress: int = 0  # 0 to 100
    message: str = "Đang chờ trong hàng đợi..."
    
    # Input paths and settings
    output_mp4: str = ""
    scene_set: Optional[SceneSet] = None
    audio_path: str = ""
    product_image: Optional[str] = None
    closeup_media: Optional[str] = None
    banner_path: Optional[str] = None
    presenter_clip: Optional[str] = None
    shot_plan: Optional[ShotPlan] = None
    duration_seconds: Optional[float] = None
    
    # Output & Diagnostics
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error_message: Optional[str] = None
    verification: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.scene_set:
            d["scene_set"] = self.scene_set.to_dict()
        return d


class RenderQueue:
    """In-memory manager of render jobs with status tracking and thread safety."""

    _instance: Optional[RenderQueue] = None

    def __init__(self) -> None:
        self.jobs: Dict[str, RenderJob] = {}

    @classmethod
    def get_instance(cls) -> RenderQueue:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def create_job(
        self,
        output_mp4: str | Path,
        scene_set: SceneSet,
        audio_path: str | Path,
        product_id: Optional[int] = None,
        product_name: str = "Sản phẩm",
        product_image: Optional[str | Path] = None,
        closeup_media: Optional[str | Path] = None,
        banner_path: Optional[str | Path] = None,
        presenter_clip: Optional[str | Path] = None,
        shot_plan: Optional[ShotPlan] = None,
        duration_seconds: Optional[float] = None,
    ) -> RenderJob:
        job = RenderJob(
            product_id=product_id,
            product_name=product_name,
            output_mp4=str(output_mp4),
            scene_set=scene_set,
            audio_path=str(audio_path),
            product_image=str(product_image) if product_image else None,
            closeup_media=str(closeup_media) if closeup_media else None,
            banner_path=str(banner_path) if banner_path else None,
            presenter_clip=str(presenter_clip) if presenter_clip else None,
            shot_plan=shot_plan,
            duration_seconds=duration_seconds,
        )
        self.jobs[job.id] = job
        logger.info("Đã tạo render job mới [id={}]: {}", job.id, job.product_name)
        return job

    def get_job(self, job_id: str) -> Optional[RenderJob]:
        return self.jobs.get(job_id)

    def list_jobs(self) -> List[RenderJob]:
        return list(self.jobs.values())

    def update_progress(self, job_id: str, progress: int, message: str) -> None:
        job = self.jobs.get(job_id)
        if job:
            job.progress = max(0, min(100, progress))
            job.message = message

    def mark_completed(self, job_id: str, verification: Optional[Dict[str, Any]] = None) -> None:
        job = self.jobs.get(job_id)
        if job:
            job.status = "completed"
            job.progress = 100
            job.message = "Hoàn tất render thành công!"
            job.completed_at = time.time()
            job.verification = verification

    def mark_failed(self, job_id: str, error: str) -> None:
        job = self.jobs.get(job_id)
        if job:
            job.status = "failed"
            job.message = f"Thất bại: {error}"
            job.error_message = error
            job.completed_at = time.time()
