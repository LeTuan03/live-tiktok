"""Cost guardrails for LemonSlice avatar usage.

Two caps are enforced before a `/feed/session` is granted:

  1. **Global concurrent rooms** — `MAX_CONCURRENT_ROOMS`. We approximate
     the number of in-flight rooms with the number of `feed_sessions`
     rows whose `ended_at IS NULL`. When the cap is hit, the route returns
     503 with a `Retry-After` header.
  2. **Per-user daily avatar minutes** — `USER_DAILY_AVATAR_MINUTES`,
     default 5. We sum `value_ms` from `feed_events` of kind `view` /
     `view_complete` for the requesting user, scoped to the current UTC
     day. When the cap is reached, the caller is downgraded to a
     non-avatar slot ("come back tomorrow"); the route does not 503 in
     this case — it just changes mode.

The midnight reset for the daily cap is implicit: the SQL filters on
`ts >= date_trunc('day', NOW() AT TIME ZONE 'UTC')`, so the counter
naturally rolls over at 00:00 UTC.

Counters are computed from the existing `feed_sessions` / `feed_events`
tables — no new schema. A future move to Redis or a dedicated counters
table is a drop-in replacement at the `CostLimitsService` seam.

Every decision emits a structured log line so the cost dashboard can
chart headroom for both caps (`cost_limits_room_slot_decision`,
`cost_limits_user_minutes_decision`).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

__all__ = [
    "CostLimitsService",
    "PostgresCostLimitsService",
    "RoomSlotDecision",
    "UserMinutesDecision",
    "cost_limits_from_env",
]

logger = logging.getLogger("soshop.services.cost_limits")


# ---------------------------------------------------------------------------
# Defaults / config
# ---------------------------------------------------------------------------

DEFAULT_MAX_CONCURRENT_ROOMS = 50
DEFAULT_USER_DAILY_AVATAR_MINUTES = 5
DEFAULT_RETRY_AFTER_SECONDS = 30


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "cost_limits_invalid_env",
            extra={"event": "cost_limits_invalid_env", "name": name, "value": raw},
        )
        return default


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoomSlotDecision:
    granted: bool
    active_count: int
    limit: int
    retry_after_seconds: int


@dataclass(frozen=True)
class UserMinutesDecision:
    avatar_allowed: bool
    minutes_used: float
    minutes_limit: int
    seconds_until_utc_midnight: int


# ---------------------------------------------------------------------------
# Connection protocol — keeps this module free of an asyncpg import-time dep
# ---------------------------------------------------------------------------


class _Conn(Protocol):
    async def fetchval(self, query: str, *args: Any) -> Any: ...


# ---------------------------------------------------------------------------
# Service seam
# ---------------------------------------------------------------------------


class CostLimitsService(Protocol):
    """Seam allowing future Redis-backed implementations."""

    async def check_room_slot(self, conn: _Conn) -> RoomSlotDecision: ...

    async def check_user_minutes(self, conn: _Conn, user_id: str) -> UserMinutesDecision: ...


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_ACTIVE_ROOMS_SQL = """
SELECT COUNT(*)::bigint
FROM feed_sessions
WHERE ended_at IS NULL
"""

# Sum dwell `value_ms` across view-style events for the user in the current
# UTC day. The bounded range lets Postgres use the index on `feed_events.ts`
# and makes the midnight reset implicit.
_USER_DAILY_MS_SQL = """
SELECT COALESCE(SUM(fe.value_ms), 0)::bigint
FROM feed_events fe
JOIN feed_sessions fs ON fs.id = fe.session_id
WHERE fs.user_id = $1
  AND fe.event_kind IN ('view', 'view_complete')
  AND fe.value_ms IS NOT NULL
  AND fe.ts >= date_trunc('day', NOW() AT TIME ZONE 'UTC')
  AND fe.ts <  date_trunc('day', NOW() AT TIME ZONE 'UTC') + INTERVAL '1 day'
"""


# ---------------------------------------------------------------------------
# Time helper
# ---------------------------------------------------------------------------


def _seconds_until_utc_midnight(now: datetime | None = None) -> int:
    now = now if now is not None else datetime.now(tz=UTC)
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    next_midnight = today_midnight + timedelta(days=1)
    return max(int((next_midnight - now).total_seconds()), 0)


# ---------------------------------------------------------------------------
# Postgres-backed implementation
# ---------------------------------------------------------------------------


@dataclass
class PostgresCostLimitsService:
    """Counters computed from existing `feed_sessions` / `feed_events` tables."""

    max_concurrent_rooms: int
    user_daily_avatar_minutes: int
    retry_after_seconds: int = DEFAULT_RETRY_AFTER_SECONDS

    async def check_room_slot(self, conn: _Conn) -> RoomSlotDecision:
        active = int(await conn.fetchval(_ACTIVE_ROOMS_SQL) or 0)
        granted = active < self.max_concurrent_rooms
        decision = RoomSlotDecision(
            granted=granted,
            active_count=active,
            limit=self.max_concurrent_rooms,
            retry_after_seconds=self.retry_after_seconds,
        )
        logger.info(
            "cost_limits_room_slot_decision",
            extra={
                "event": "cost_limits_room_slot_decision",
                "granted": granted,
                "active_count": active,
                "limit": self.max_concurrent_rooms,
                "headroom": max(self.max_concurrent_rooms - active, 0),
            },
        )
        return decision

    async def check_user_minutes(self, conn: _Conn, user_id: str) -> UserMinutesDecision:
        ms_used = int(await conn.fetchval(_USER_DAILY_MS_SQL, user_id) or 0)
        minutes_used = round(ms_used / 60_000.0, 4)
        avatar_allowed = minutes_used < self.user_daily_avatar_minutes
        seconds_until = _seconds_until_utc_midnight()
        decision = UserMinutesDecision(
            avatar_allowed=avatar_allowed,
            minutes_used=minutes_used,
            minutes_limit=self.user_daily_avatar_minutes,
            seconds_until_utc_midnight=seconds_until,
        )
        logger.info(
            "cost_limits_user_minutes_decision",
            extra={
                "event": "cost_limits_user_minutes_decision",
                "user_id": user_id,
                "avatar_allowed": avatar_allowed,
                "minutes_used": minutes_used,
                "minutes_limit": self.user_daily_avatar_minutes,
                "seconds_until_reset": seconds_until,
                "headroom_minutes": round(
                    max(float(self.user_daily_avatar_minutes) - minutes_used, 0.0),
                    4,
                ),
            },
        )
        return decision


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def cost_limits_from_env() -> PostgresCostLimitsService:
    """Build a `PostgresCostLimitsService` from process env."""
    return PostgresCostLimitsService(
        max_concurrent_rooms=_env_int("MAX_CONCURRENT_ROOMS", DEFAULT_MAX_CONCURRENT_ROOMS),
        user_daily_avatar_minutes=_env_int(
            "USER_DAILY_AVATAR_MINUTES", DEFAULT_USER_DAILY_AVATAR_MINUTES
        ),
        retry_after_seconds=_env_int(
            "MAX_CONCURRENT_ROOMS_RETRY_AFTER", DEFAULT_RETRY_AFTER_SECONDS
        ),
    )
