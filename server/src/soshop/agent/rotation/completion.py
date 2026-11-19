"""Detect when the current pitch has finished speaking.

The channel rotation waits on the LLM's actual speech completion — not a
wall-clock timer — so each persona swap lands the instant the pitch is
delivered. The ``PitchCompletionSignal`` protocol abstracts the
"is the agent done speaking?" decision so the orchestrator stays
testable without a real LiveKit room.

Production implementation awaits the
``SpeechHandle`` returned by ``AgentSession.generate_reply`` — each
handle is bound to exactly one speech turn (one LLM reply + its TTS
playout), so there is no event-ordering race between back-to-back
swaps. The KB article "How to detect when an agent has finished
speaking" lists this as one of the canonical patterns; we prefer it
over the ``playback_finished`` event listener because each handle is
tied to a single turn (no need to filter stale events fired by the
swap's own ``session.interrupt`` of the previous pitch).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from livekit.agents.voice import AgentSession

logger = logging.getLogger("soshop.agent")


CompletionReason = Literal["natural", "interrupted", "timeout", "session_closed"]


@dataclass(frozen=True)
class CompletionOutcome:
    """Result of a single ``wait_until_complete`` call.

    ``elapsed_s`` is measured from the moment the orchestrator started
    awaiting — i.e., it is the *audible* pitch duration plus any
    LLM-thinking delay, which is exactly what the rotation cadence
    metric wants.
    """

    reason: CompletionReason
    elapsed_s: float


@runtime_checkable
class PitchCompletionSignal(Protocol):
    """Decide when the current pitch is finished.

    Production wires :class:`SpeechHandleCompletion` against the live
    ``AgentSession``; tests inject a fake that resolves on command. The
    protocol is intentionally tiny — one method — so a future
    implementation (e.g., listening to ``playback_finished`` for a more
    precise audio-end signal) can swap in without touching the
    orchestrator.
    """

    async def wait_until_complete(
        self, *, handle: Any | None, max_wait_s: float
    ) -> CompletionOutcome: ...


class SpeechHandleCompletion:
    """Await the ``SpeechHandle`` for the current pitch.

    The handle is produced by ``AgentSession.generate_reply`` inside
    ``_execute_swap_persona`` and stashed on the runner so the
    orchestrator can read it after the swap returns. Awaiting it
    resolves naturally when the LLM finishes its turn and TTS drains
    its audio buffer.

    A ``None`` handle is the degraded "no speech kicked off" path
    (session closed mid-swap, generate_reply raised) — we report it as
    ``natural`` with zero elapsed so the rotation advances immediately
    rather than blocking on a safety timeout for a missing pitch. The
    outer swap_persona log already records the underlying failure.
    """

    def __init__(self, session: AgentSession) -> None:
        # Held only for diagnostic logging; we don't read from session
        # inside ``wait_until_complete`` — the handle carries all the
        # state we need.
        self._session = session

    async def wait_until_complete(
        self, *, handle: Any | None, max_wait_s: float
    ) -> CompletionOutcome:
        start = time.monotonic()
        if handle is None:
            return CompletionOutcome(reason="natural", elapsed_s=0.0)

        # Wrap the handle await in a task so ``asyncio.wait_for`` can
        # apply the safety ceiling without cancelling the underlying
        # speech (which keeps running independently; the next swap's
        # ``session.interrupt(force=True)`` is what stops a runaway).
        wrapper = asyncio.ensure_future(self._await_handle(handle))
        try:
            await asyncio.wait_for(asyncio.shield(wrapper), timeout=max_wait_s)
        except TimeoutError:
            wrapper.cancel()
            return CompletionOutcome(
                reason="timeout",
                elapsed_s=time.monotonic() - start,
            )
        elapsed = time.monotonic() - start
        interrupted = bool(getattr(handle, "interrupted", False))
        return CompletionOutcome(
            reason="interrupted" if interrupted else "natural",
            elapsed_s=elapsed,
        )

    @staticmethod
    async def _await_handle(handle: Any) -> None:
        """Await the SpeechHandle and swallow non-cancellation errors.

        SpeechHandle's documented contract is "awaitable like a Future",
        but a Groq stream error or upstream TTS failure can surface as
        an exception. We log and treat as natural completion so the
        rotation keeps moving — the underlying error is already logged
        by the swap path that produced the handle.
        """
        try:
            await handle
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "pitch_completion: handle await raised; treating as natural",
                exc_info=True,
            )


def build_default_completion(session: AgentSession) -> PitchCompletionSignal:
    """Build the production completion signal bound to a live session.

    Centralized so the channel loop wires it once and stays decoupled
    from the concrete implementation choice.
    """
    return SpeechHandleCompletion(session)
