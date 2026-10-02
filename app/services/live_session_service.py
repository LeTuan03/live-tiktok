"""Live session bookkeeping — persisted state for each livestream run."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from sqlalchemy import desc, select

from app.database.models.live_session import LiveSession, SchedulerJob
from app.database.session import get_db, is_initialized


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LiveSessionService:
    """Creates and updates ``live_sessions`` / ``scheduler_jobs`` rows.

    Uses short-lived sessions so it is safe to call from the scheduler thread.
    """

    def start_session(self, name: str, playlist_id: Optional[int], platform: str, obs_scene: str = "") -> Optional[int]:
        if not is_initialized():
            return None
        try:
            with get_db() as db:
                session = LiveSession(
                    name=name,
                    playlist_id=playlist_id,
                    status="running",
                    started_at=_now(),
                    platform=platform,
                    obs_scene=obs_scene or None,
                )
                db.add(session)
                db.flush()
                return session.id
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Không tạo được bản ghi phiên LIVE: {}", e)
            return None

    def set_status(self, session_id: Optional[int], status: str, notes: Optional[str] = None) -> None:
        if not session_id or not is_initialized():
            return
        try:
            with get_db() as db:
                s = db.get(LiveSession, session_id)
                if not s:
                    return
                s.status = status
                if notes:
                    s.notes = (s.notes + "\n" if s.notes else "") + notes
                if status in ("stopped", "completed", "error"):
                    s.ended_at = _now()
                    if s.started_at:
                        started = s.started_at if s.started_at.tzinfo else s.started_at.replace(tzinfo=timezone.utc)
                        s.total_duration_seconds = (s.ended_at - started).total_seconds()
        except Exception as e:  # pragma: no cover
            logger.debug("Không cập nhật được phiên LIVE: {}", e)

    def increment(self, session_id: Optional[int], field: str, by: int = 1) -> None:
        if not session_id or not is_initialized():
            return
        try:
            with get_db() as db:
                s = db.get(LiveSession, session_id)
                if s and hasattr(s, field):
                    setattr(s, field, (getattr(s, field) or 0) + by)
        except Exception as e:  # pragma: no cover
            logger.debug("Không cập nhật được bộ đếm phiên LIVE: {}", e)

    def add_job(
        self,
        session_id: Optional[int],
        product_id: Optional[int],
        playlist_item_id: Optional[int],
        status: str,
        error: Optional[str] = None,
    ) -> None:
        if not session_id or not is_initialized():
            return
        try:
            with get_db() as db:
                job = SchedulerJob(
                    session_id=session_id,
                    product_id=product_id,
                    playlist_item_id=playlist_item_id,
                    status=status,
                    started_at=_now(),
                    ended_at=_now() if status in ("completed", "failed", "skipped") else None,
                    error_message=error,
                )
                db.add(job)
        except Exception as e:  # pragma: no cover
            logger.debug("Không ghi được scheduler job: {}", e)

    def latest_session(self) -> Optional[LiveSession]:
        if not is_initialized():
            return None
        with get_db() as db:
            return db.scalars(select(LiveSession).order_by(desc(LiveSession.id)).limit(1)).first()

    def count_sessions(self) -> int:
        if not is_initialized():
            return 0
        from sqlalchemy import func

        with get_db() as db:
            return int(db.scalar(select(func.count(LiveSession.id))) or 0)
