"""Hard session cap with smooth auto-handoff.

Owns the timing for a single persona dispatch: schedules a wrap-up nudge at
``cap - wrap_lead_ms`` (so the LLM has time to land a graceful close), then
at the cap fades audio, dismisses the in-call popup if open, records a
``persona_dwelled`` event with the auto-handoff reason, and triggers the
single-room re-dispatch.

The lifecycle owns the *sequence*; the *effects* (LLM nudge, audio fade,
popup dismiss, event recording, next-persona dispatch) are injected as
hooks so this module stays testable without the LiveKit / pipeline runtime.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final

__all__ = [
    "DEFAULT_SESSION_MAX_MS",
    "DEFAULT_WRAP_LEAD_MS",
    "DWELL_REASON_AUTO_HANDOFF",
    "LifecycleHooks",
    "SessionCapConfig",
    "SessionLifecycle",
]

logger = logging.getLogger("soshop.agent.lifecycle")

DEFAULT_SESSION_MAX_MS: Final[int] = 60_000
DEFAULT_WRAP_LEAD_MS: Final[int] = 30_000
DWELL_REASON_AUTO_HANDOFF: Final[str] = "auto_handoff"

_ENV_SESSION_MAX_MS: Final[str] = "PERSONA_SESSION_MAX_MS"
_ENV_WRAP_LEAD_MS: Final[str] = "PERSONA_SESSION_WRAP_LEAD_MS"


@dataclass(frozen=True)
class SessionCapConfig:
    """Timing knobs for the persona session cap."""

    cap_ms: int = DEFAULT_SESSION_MAX_MS
    wrap_lead_ms: int = DEFAULT_WRAP_LEAD_MS

    def __post_init__(self) -> None:
        if self.cap_ms <= 0:
            raise ValueError(f"cap_ms must be positive, got {self.cap_ms}")
        if self.wrap_lead_ms < 0:
            raise ValueError(f"wrap_lead_ms must be non-negative, got {self.wrap_lead_ms}")

    @property
    def wrap_at_ms(self) -> int:
        """When (ms from session start) to fire the wrap-up nudge.

        Clamped to 0 so a wrap_lead_ms larger than cap_ms still fires
        immediately rather than scheduling into the past.
        """
        return max(0, self.cap_ms - self.wrap_lead_ms)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SessionCapConfig:
        """Build config from environment variables.

        ``PERSONA_SESSION_MAX_MS`` is the hard cap (default 60_000).
        ``PERSONA_SESSION_WRAP_LEAD_MS`` is how long before the cap to
        prompt the LLM to start wrapping (default 30_000). Invalid values
        fall back to the defaults with a warning.

        ``env`` accepts any read-only string mapping so callers can pass
        either ``os.environ`` (an ``_Environ`` view) or a plain dict from
        tests.
        """
        source: Mapping[str, str] = env if env is not None else os.environ
        return cls(
            cap_ms=_read_positive_int(source, _ENV_SESSION_MAX_MS, DEFAULT_SESSION_MAX_MS),
            wrap_lead_ms=_read_non_negative_int(source, _ENV_WRAP_LEAD_MS, DEFAULT_WRAP_LEAD_MS),
        )


def _read_positive_int(source: Mapping[str, str], key: str, default: int) -> int:
    raw = source.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("invalid %s=%r, falling back to %d", key, raw, default)
        return default
    if value <= 0:
        logger.warning("invalid %s=%d (must be > 0), falling back to %d", key, value, default)
        return default
    return value


def _read_non_negative_int(source: Mapping[str, str], key: str, default: int) -> int:
    raw = source.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("invalid %s=%r, falling back to %d", key, raw, default)
        return default
    if value < 0:
        logger.warning("invalid %s=%d (must be >= 0), falling back to %d", key, value, default)
        return default
    return value


AsyncHook = Callable[[], Awaitable[None]]
DwellHook = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class LifecycleHooks:
    """Effect callbacks the lifecycle drives at wrap and cap.

    ``prompt_wrapup`` should nudge the LLM to begin landing the segment
    (e.g. inject a system message like "alright, gotta run, wrap it up").

    ``fade_audio`` should fade the current TTS output rather than cutting
    it abruptly.

    ``dismiss_popup`` should clear the pitch popup if it's currently
    showing (safe to call when no popup is up, a no-op).

    ``record_dwell`` records a ``persona_dwelled`` analytics event with the
    given reason.

    ``dispatch_next`` triggers the single-room re-dispatch flow.
    """

    prompt_wrapup: AsyncHook
    fade_audio: AsyncHook
    dismiss_popup: AsyncHook
    record_dwell: DwellHook
    dispatch_next: AsyncHook


class SessionLifecycle:
    """Schedules the wrap-up nudge and the hard cap auto-handoff.

    Usage::

        lifecycle = SessionLifecycle(config=cfg, hooks=hooks)
        lifecycle.start()
        # ... persona runs ...
        await lifecycle.aclose()  # if user swiped or session ended early

    The lifecycle is single-shot: once the cap fires (or ``aclose`` is
    called) further calls are no-ops, so a manual swipe that races with
    the auto handoff won't double-fire.
    """

    def __init__(
        self,
        *,
        config: SessionCapConfig,
        hooks: LifecycleHooks,
    ) -> None:
        self._config = config
        self._hooks = hooks
        self._wrap_task: asyncio.Task[None] | None = None
        self._cap_task: asyncio.Task[None] | None = None
        self._closed = False
        self._cap_fired = False

    @property
    def config(self) -> SessionCapConfig:
        return self._config

    @property
    def cap_fired(self) -> bool:
        return self._cap_fired

    def start(self) -> None:
        """Schedule the wrap and cap tasks against the running loop.

        Idempotent: a second call after the first does nothing. Must be
        called from inside a running asyncio loop.
        """
        if self._closed:
            logger.debug("SessionLifecycle.start: already closed, ignoring")
            return
        if self._wrap_task is not None or self._cap_task is not None:
            logger.debug("SessionLifecycle.start: already started, ignoring")
            return
        loop = asyncio.get_running_loop()
        wrap_delay = self._config.wrap_at_ms / 1000
        cap_delay = self._config.cap_ms / 1000
        self._wrap_task = loop.create_task(
            self._sleep_then(wrap_delay, self._on_wrap),
            name="persona-session-wrap",
        )
        self._cap_task = loop.create_task(
            self._sleep_then(cap_delay, self._on_cap),
            name="persona-session-cap",
        )
        logger.info(
            "session lifecycle started",
            extra={
                "cap_ms": self._config.cap_ms,
                "wrap_lead_ms": self._config.wrap_lead_ms,
                "wrap_at_ms": self._config.wrap_at_ms,
            },
        )

    async def aclose(self) -> None:
        """Cancel pending wrap/cap tasks (e.g. user swiped manually).

        Safe to call multiple times. Does NOT run the cap effects; this
        is the early-exit path. The auto-handoff path is :meth:`_on_cap`.
        """
        if self._closed:
            return
        self._closed = True
        for task in (self._wrap_task, self._cap_task):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._wrap_task, self._cap_task):
            if task is None:
                continue
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("lifecycle task ended with error during aclose", exc_info=True)

    async def _sleep_then(self, delay_s: float, fn: AsyncHook) -> None:
        try:
            await asyncio.sleep(delay_s)
        except asyncio.CancelledError:
            raise
        if self._closed:
            return
        try:
            await fn()
        except Exception:
            logger.exception("session lifecycle hook failed")

    async def _on_wrap(self) -> None:
        if self._cap_fired:
            return
        logger.info("session wrap-up nudge firing")
        await self._hooks.prompt_wrapup()

    async def _on_cap(self) -> None:
        """Hard cap: fade audio, dismiss popup, record event, hand off.

        Sequence is: stop the wrap task (it's redundant if it hasn't
        fired), fade audio so the TTS doesn't cut abruptly, dismiss the
        in-call popup if any, record the dwell event so analytics see the
        auto handoff, then trigger the next-persona dispatch.

        Each effect is wrapped so a failure in one doesn't block the
        others — the handoff itself is the most important step.
        """
        if self._cap_fired or self._closed:
            return
        self._cap_fired = True
        # The wrap nudge is moot once we're at the cap; cancel it if it
        # somehow hasn't fired (e.g. wrap_lead_ms == 0 edge case).
        if self._wrap_task is not None and not self._wrap_task.done():
            self._wrap_task.cancel()
        logger.info("session cap reached, beginning auto-handoff")
        await _safe_call(self._hooks.fade_audio, "fade_audio")
        await _safe_call(self._hooks.dismiss_popup, "dismiss_popup")
        await _safe_call_with(self._hooks.record_dwell, DWELL_REASON_AUTO_HANDOFF, "record_dwell")
        await _safe_call(self._hooks.dispatch_next, "dispatch_next")


async def _safe_call(fn: AsyncHook, name: str) -> None:
    try:
        await fn()
    except Exception:
        logger.exception("session lifecycle %s hook failed", name)


async def _safe_call_with(fn: DwellHook, arg: str, name: str) -> None:
    try:
        await fn(arg)
    except Exception:
        logger.exception("session lifecycle %s hook failed", name)
