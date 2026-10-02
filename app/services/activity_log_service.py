"""Activity log — persisted events shown on the dashboard."""

from __future__ import annotations

from typing import Optional, Sequence

from loguru import logger
from sqlalchemy import desc, select

from app.database.models.settings import ActivityLog
from app.database.session import get_db, is_initialized


def log_activity(
    category: str,
    action: str,
    detail: str = "",
    level: str = "INFO",
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
) -> None:
    """Write one activity row. Never raises (logging must not break features)."""
    if not is_initialized():
        return
    try:
        with get_db() as db:
            db.add(
                ActivityLog(
                    level=level,
                    category=category,
                    action=action[:200],
                    detail=detail[:2000] if detail else None,
                    entity_type=entity_type,
                    entity_id=entity_id,
                )
            )
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("Không ghi được activity log: {}", e)


def recent_activity(limit: int = 30, min_level: Optional[str] = None) -> Sequence[ActivityLog]:
    if not is_initialized():
        return []
    try:
        with get_db() as db:
            stmt = select(ActivityLog).order_by(desc(ActivityLog.created_at), desc(ActivityLog.id))
            if min_level == "WARNING":
                stmt = stmt.where(ActivityLog.level.in_(["WARNING", "ERROR"]))
            elif min_level == "ERROR":
                stmt = stmt.where(ActivityLog.level == "ERROR")
            return db.scalars(stmt.limit(limit)).all()
    except Exception as e:  # pragma: no cover
        logger.debug("Không đọc được activity log: {}", e)
        return []


def clear_activity() -> int:
    if not is_initialized():
        return 0
    with get_db() as db:
        rows = db.scalars(select(ActivityLog)).all()
        for r in rows:
            db.delete(r)
        return len(rows)
