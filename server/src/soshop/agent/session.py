"""On-demand agent-session manager.

The FastAPI app boots with agents OFF (channel count = 0) — the public
preview is gated behind LemonSlice avatar cost, so we do not want to
keep agents warm 24/7. Operators bring the channel set up by hitting
``POST /agents/session`` (gated by the site password); the
``AgentSessionManager`` schedules a wind-down task that reconciles back
to zero when the configured duration elapses. A second POST resets the
clock.

The 5-minute periodic reconciler in :mod:`soshop_server.main` defers to
:meth:`AgentSessionManager.reconcile_to_target` so it picks up the
current target instead of hardcoding ``settings.CHANNEL_COUNT`` — this
keeps the safety net in lockstep with the on-demand session state and
also handles the edge case where the wind-down task was lost (e.g. a
process restart between the original POST and the expiry).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from soshop_server.api.deps import StrictModel

logger = logging.getLogger("soshop.agent.session")


class SessionStatus(StrictModel):
    """Snapshot of the on-demand agent session's state."""

    active: bool
    expires_at: datetime | None
    seconds_remaining: int  # 0 when inactive
    target_channel_count: int  # CHANNEL_COUNT when active, 0 when inactive


class AgentSessionManager:
    """Process-wide controller for the on-demand agent session.

    The class is the **only** mutator of session state; the ``status()``
    read is lock-free because every mutation is an atomic attribute
    assignment (no compound state).
    """

    def __init__(self, channel_count: int) -> None:
        self._channel_count = channel_count
        self._expires_at: datetime | None = None
        self._wind_down_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    def status(self) -> SessionStatus:
        now = datetime.now(UTC)
        active = self._expires_at is not None and self._expires_at > now
        remaining = (
            int((self._expires_at - now).total_seconds())
            if active and self._expires_at is not None
            else 0
        )
        return SessionStatus(
            active=active,
            expires_at=self._expires_at if active else None,
            seconds_remaining=max(0, remaining),
            target_channel_count=self._channel_count if active else 0,
        )

    async def start(self, duration_minutes: int) -> SessionStatus:
        """Start (or extend) an agent session.

        A second call cancels the prior wind-down task and resets the
        expiry clock — idempotent in the sense that callers do not need
        to check whether a session is already running.
        """
        async with self._lock:
            self._cancel_wind_down()
            self._expires_at = datetime.now(UTC) + timedelta(minutes=duration_minutes)
            await self._reconcile(self._channel_count)
            self._wind_down_task = asyncio.create_task(
                self._wind_down(self._expires_at),
                name="agent-session-wind-down",
            )
            logger.info(
                "agent_session_started",
                extra={
                    "event": "agent_session_started",
                    "expires_at": self._expires_at.isoformat(),
                    "duration_minutes": duration_minutes,
                },
            )
            return self.status()

    async def stop(self) -> SessionStatus:
        """Tear the agent set down immediately."""
        async with self._lock:
            self._cancel_wind_down()
            self._expires_at = None
            await self._reconcile(0)
            logger.info(
                "agent_session_stopped",
                extra={"event": "agent_session_stopped"},
            )
            return self.status()

    async def reconcile_to_target(self) -> None:
        """Safety-net entry called by the 5-minute periodic reconciler."""
        async with self._lock:
            target = self._channel_count if self._is_active_unlocked() else 0
            if target == 0 and self._expires_at is not None:
                # Session expired but the wind-down task did not fire (e.g.
                # the task was lost on a process restart). Clear the state
                # so subsequent reads report inactive.
                self._expires_at = None
                self._cancel_wind_down()
            await self._reconcile(target)

    def _is_active_unlocked(self) -> bool:
        return self._expires_at is not None and self._expires_at > datetime.now(UTC)

    def _cancel_wind_down(self) -> None:
        if self._wind_down_task is not None and not self._wind_down_task.done():
            self._wind_down_task.cancel()
        self._wind_down_task = None

    async def _wind_down(self, expires_at: datetime) -> None:
        try:
            sleep_s = (expires_at - datetime.now(UTC)).total_seconds()
            if sleep_s > 0:
                await asyncio.sleep(sleep_s)
        except asyncio.CancelledError:
            return
        try:
            async with self._lock:
                # Confirm we still own this expiry — a newer start() may
                # have rescheduled before the task woke up.
                if self._expires_at == expires_at:
                    self._expires_at = None
                    await self._reconcile(0)
                    logger.info(
                        "agent_session_wound_down",
                        extra={"event": "agent_session_wound_down"},
                    )
        except Exception:
            logger.exception("agent session wind-down failed")

    @staticmethod
    async def _reconcile(target: int) -> None:
        # Local import to keep this module light at import-time and to
        # mirror the lazy-import pattern used by ``soshop_server.main``.
        from soshop.livekit.channels import reconcile_channels

        await reconcile_channels(target)


_singleton: AgentSessionManager | None = None


def get_session_manager() -> AgentSessionManager:
    """Return the process-wide ``AgentSessionManager`` (lazily constructed)."""
    global _singleton
    if _singleton is None:
        from soshop_server.core.config import settings

        _singleton = AgentSessionManager(channel_count=settings.CHANNEL_COUNT)
    return _singleton


def _reset_singleton_for_tests() -> None:
    """Test-only — clear the module-level singleton between tests."""
    global _singleton
    _singleton = None
