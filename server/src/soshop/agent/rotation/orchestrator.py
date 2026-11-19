"""Completion-driven persona rotation for the 24/7 channel.

Replaces the previous timer-based loop body (``_channel_loop_iteration``)
with an iteration that drives a swap, awaits the
:class:`PitchCompletionSignal`, and pauses for a small organic jitter
before advancing the cursor. The :class:`ChannelLoopDeps` injection
seam keeps every external collaborator (DB writes, popup load, the
swap itself) replaceable so the orchestrator can be unit-tested
without LiveKit or asyncpg.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from soshop.agent.rotation.completion import (
    CompletionOutcome,
    PitchCompletionSignal,
)
from soshop.agent.rotation.cursor import RotationCursor
from soshop.agent.schemas import DispatchMetadata, PopupPayload

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import asyncpg

    from soshop.agent.runner import AgentRunner
    from soshop_server.db.models import Ad

logger = logging.getLogger("soshop.agent")


CHANNEL_LOOP_SWAP_SOURCE = "channel_loop"
"""Source tag attached to ``persona_swap_completed`` events emitted by
the channel rotation loop, so dashboards can distinguish a viewer-driven
swipe (``data-packet:<sender>`` / ``text-stream:<identity>``) from the
agent's own periodic rotation."""


def _ad_to_dispatch_metadata(
    base: DispatchMetadata,
    ad: Ad,
    *,
    popup_payload: PopupPayload | None,
) -> DispatchMetadata:
    """Build a per-ad ``DispatchMetadata`` for ``_execute_swap_persona``.

    The session/user/channel envelope stays constant across the
    rotation; only persona / product / script / popup change.
    ``pitch_window_ms`` is set to ``0`` — the completion-driven rotation
    no longer reads it, but the field is retained on the schema for
    transport compatibility with the 1:1 companion path that still
    references it in logs.
    """
    return DispatchMetadata(
        persona_id=str(ad.persona_id),
        product_id=str(ad.product_id),
        script_id=str(ad.script_id),
        pitch_window_ms=0,
        popup_payload=popup_payload,
        session_id=base.session_id,
        user_id=base.user_id,
        channel_id=base.channel_id,
        ad_start_index=base.ad_start_index,
    )


@dataclass
class ChannelLoopDeps:
    """Injection seam for the channel rotation loop body.

    Production wires the real DB writers, the in-place persona swap,
    the LiveKit speech-handle completion signal, and a system-RNG
    jitter pause. Unit tests pass async mocks for every field so the
    orchestrator can exercise its cursor and log-write logic without
    LiveKit or asyncpg.
    """

    swap_persona: Callable[[AgentRunner, DispatchMetadata, str], Awaitable[None]]
    write_cursor: Callable[[asyncpg.Pool, str, int], Awaitable[None]]
    record_ad_started: Callable[[asyncpg.Pool, str, Ad], Awaitable[int]]
    record_ad_ended: Callable[[asyncpg.Pool, int], Awaitable[None]]
    load_popup: Callable[[asyncpg.Pool, str | None], Awaitable[PopupPayload | None]]
    wait_for_pitch_complete: Callable[..., Awaitable[CompletionOutcome]]
    jitter: Callable[[], Awaitable[float]]


@dataclass
class PitchOrchestrator:
    """Drive the channel rotation by chaining swap → wait-to-finish → pause.

    Owns the cursor and the iteration loop. Knows nothing about
    LiveKit speech handles, asyncpg connection pools, or LemonSlice —
    it delegates each external concern through ``ChannelLoopDeps``
    and ``PitchCompletionSignal``.
    """

    runner: AgentRunner
    pool: asyncpg.Pool
    ads: list[Ad]
    channel_id: str
    deps: ChannelLoopDeps
    completion: PitchCompletionSignal
    cursor: RotationCursor
    min_slot_s: float = 0.0
    """Floor on one rotation slot (swap to next swap). ``0`` disables it. A
    pitch that fails fast (LLM rate limit, TTS outage) otherwise completes in
    a few seconds and the loop re-requests immediately, which keeps a
    token-per-minute limit permanently exhausted."""

    @classmethod
    def build(
        cls,
        *,
        runner: AgentRunner,
        pool: asyncpg.Pool,
        ads: list[Ad],
        channel_id: str,
        deps: ChannelLoopDeps,
        completion: PitchCompletionSignal,
        start_cursor: int,
    ) -> PitchOrchestrator:
        return cls(
            runner=runner,
            pool=pool,
            ads=ads,
            channel_id=channel_id,
            deps=deps,
            completion=completion,
            cursor=RotationCursor(index=start_cursor),
        )

    async def run(self, *, max_iterations: int | None = None) -> None:
        """Drive the rotation forever (or for a bounded number of iterations).

        ``max_iterations`` is the test-only short-circuit. Production passes
        ``None`` and the loop only exits on a closed session (worker
        drain, dispatch deletion, fatal SDK error).

        The session-liveness probe is a defense-in-depth check —
        production sessions are started with
        ``RoomInputOptions(close_on_disconnect=False)`` so viewer
        disconnects do *not* close the session. A worker drain or
        explicit ``ctx.shutdown`` is the only realistic trigger; when
        that happens we break out promptly instead of stacking
        ``persona_swap_skipped`` warnings.
        """
        from soshop.agent.runner import _session_running

        iterations = 0
        while True:
            if not _session_running(self.runner.session):
                logger.warning(
                    "channel_loop_exit: session closed",
                    extra={
                        "event": "channel_loop_exit",
                        "channel_id": self.channel_id,
                        "iterations": iterations,
                    },
                )
                return
            ad = self.cursor.current(self.ads)
            await self._run_iteration(ad)
            self.cursor.advance()
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                return

    async def _run_iteration(self, ad: Ad) -> None:
        """Drive one rotation slot end-to-end.

        Order matters: the start log + cursor write happen *before* the
        swap so a hard crash inside ``swap_persona`` still leaves a
        trail pointing at the offending ad. The wait-and-jitter run
        inside the same ``try`` block; the ``finally`` closes the
        channel_ad_log row even if an outer task gets cancelled
        mid-wait.

        Once the swap kicks off the new persona's
        ``generate_reply`` (storing the resulting ``SpeechHandle`` on
        ``runner.current_pitch_handle``), the orchestrator awaits that
        handle's completion. ``ad.duration_ms`` is reinterpreted as a
        *safety ceiling* — if the LLM hangs or TTS stalls, the wait
        times out and the next swap's ``session.interrupt(force=True)``
        force-cuts the stuck speech. The jitter that follows is the
        organic breath between pitches; it keeps the rotation from
        feeling mechanical.
        """
        popup_payload = await self.deps.load_popup(self.pool, str(ad.product_id))
        next_md = _ad_to_dispatch_metadata(
            self.runner.runtime.metadata,
            ad,
            popup_payload=popup_payload,
        )
        log_id = await self.deps.record_ad_started(self.pool, self.channel_id, ad)
        slot_started = time.monotonic()
        try:
            await self.deps.write_cursor(self.pool, self.channel_id, ad.sequence_order)
            await self.deps.swap_persona(self.runner, next_md, CHANNEL_LOOP_SWAP_SOURCE)
            handle = getattr(self.runner, "current_pitch_handle", None)
            outcome = await self.deps.wait_for_pitch_complete(
                handle=handle,
                max_wait_s=ad.duration_ms / 1000,
            )
            # Fire the server-driven dismiss only on natural completion.
            # Timeout / interrupt / session_closed outcomes are aborts:
            # the next swap's `interrupt(force=True)` + `rebind` will
            # supersede any UI state we'd set here, and emitting a
            # dismiss in those branches would race the next show. On
            # natural completion this clears the compact pill so the
            # ~0.5s gap before the next ad's auto-show feels clean
            # instead of leaving a stale collapsed pill on screen.
            if outcome.reason == "natural":
                pitch_tools = getattr(self.runner, "pitch_tools", None)
                emit_dismiss = getattr(pitch_tools, "emit_dismiss_auto", None)
                if callable(emit_dismiss):
                    try:
                        await emit_dismiss()
                    except Exception:
                        logger.exception("auto_dismiss after pitch_completed failed")
            jitter_s = await self.deps.jitter()
            remaining_s = self.min_slot_s - (time.monotonic() - slot_started)
            if remaining_s > 0:
                await asyncio.sleep(remaining_s)
            logger.info(
                "pitch_completed",
                extra={
                    "event": "pitch_completed",
                    "channel_id": self.channel_id,
                    "ad_id": str(ad.id),
                    "persona_id": str(ad.persona_id),
                    "product_id": str(ad.product_id),
                    "reason": outcome.reason,
                    "elapsed_s": round(outcome.elapsed_s, 3),
                    "jitter_s": round(jitter_s, 3),
                },
            )
        except asyncio.CancelledError:
            logger.warning(
                "pitch_iteration_cancelled",
                extra={
                    "event": "pitch_iteration_cancelled",
                    "channel_id": self.channel_id,
                    "ad_id": str(ad.id),
                },
            )
            raise
        finally:
            await self.deps.record_ad_ended(self.pool, log_id)
