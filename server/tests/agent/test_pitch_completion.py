"""Unit tests for the rotation's pitch-completion signal.

``SpeechHandleCompletion`` awaits the ``SpeechHandle`` returned by
``AgentSession.generate_reply`` (stashed on the runner by the swap
flow). The tests below exercise it against a tiny fake handle so the
behavior across natural completion, interruption, and safety-ceiling
timeout is observable without a real LiveKit room.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import Any

import pytest

_pluginspec = (
    importlib.util.find_spec("livekit.plugins.silero")
    if importlib.util.find_spec("livekit") is not None
    else None
)
pytestmark = pytest.mark.skipif(
    _pluginspec is None,
    reason="agent plugins not installed (run `uv sync --group agent`)",
)


class _FakeHandle:
    """Awaitable stand-in for a LiveKit ``SpeechHandle``.

    Resolves when ``done()`` is called. ``interrupted`` mirrors the
    ``SpeechHandle.interrupted`` flag the production code reads.
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self.interrupted: bool = False

    def done_natural(self) -> None:
        self.interrupted = False
        self._event.set()

    def done_interrupted(self) -> None:
        self.interrupted = True
        self._event.set()

    def __await__(self) -> Any:
        return self._event.wait().__await__()


async def test_completion_natural_returns_natural() -> None:
    from soshop.agent.rotation import SpeechHandleCompletion

    handle = _FakeHandle()
    signal = SpeechHandleCompletion(session=object())  # type: ignore[arg-type]

    async def _finish_soon() -> None:
        await asyncio.sleep(0.01)
        handle.done_natural()

    asyncio.create_task(_finish_soon())
    outcome = await signal.wait_until_complete(handle=handle, max_wait_s=5.0)

    assert outcome.reason == "natural"
    assert outcome.elapsed_s > 0


async def test_completion_interrupted_returns_interrupted() -> None:
    from soshop.agent.rotation import SpeechHandleCompletion

    handle = _FakeHandle()
    signal = SpeechHandleCompletion(session=object())  # type: ignore[arg-type]

    async def _interrupt_soon() -> None:
        await asyncio.sleep(0.01)
        handle.done_interrupted()

    asyncio.create_task(_interrupt_soon())
    outcome = await signal.wait_until_complete(handle=handle, max_wait_s=5.0)

    assert outcome.reason == "interrupted"


async def test_completion_timeout_returns_timeout() -> None:
    """When the handle never resolves, the safety ceiling trips and the
    rotation can advance without waiting forever."""
    from soshop.agent.rotation import SpeechHandleCompletion

    handle = _FakeHandle()  # never signalled
    signal = SpeechHandleCompletion(session=object())  # type: ignore[arg-type]

    outcome = await signal.wait_until_complete(handle=handle, max_wait_s=0.05)

    assert outcome.reason == "timeout"
    assert outcome.elapsed_s >= 0.05


async def test_completion_none_handle_resolves_immediately() -> None:
    """If the swap couldn't kick off speech (session closed, generate_reply
    raised), ``current_pitch_handle`` is None. The completion signal
    returns ``natural`` with zero elapsed so the rotation advances
    instead of blocking on the safety ceiling for nothing."""
    from soshop.agent.rotation import SpeechHandleCompletion

    signal = SpeechHandleCompletion(session=object())  # type: ignore[arg-type]
    outcome = await signal.wait_until_complete(handle=None, max_wait_s=10.0)

    assert outcome.reason == "natural"
    assert outcome.elapsed_s == 0.0


async def test_completion_handle_exception_treated_as_natural() -> None:
    """If the SpeechHandle resolves with an exception (Groq stream
    error, TTS upstream failure), the completion signal logs and treats
    it as natural so the rotation keeps moving — the underlying error
    is already logged by the swap path that produced the handle."""
    from soshop.agent.rotation import SpeechHandleCompletion

    class _ErrorHandle:
        interrupted = False

        def __await__(self) -> Any:
            async def _raise() -> None:
                raise RuntimeError("upstream TTS failure")

            return _raise().__await__()

    signal = SpeechHandleCompletion(session=object())  # type: ignore[arg-type]
    outcome = await signal.wait_until_complete(handle=_ErrorHandle(), max_wait_s=1.0)

    assert outcome.reason == "natural"
