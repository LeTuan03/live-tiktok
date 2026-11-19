"""Unit tests for PitchAgent's dead-voice TTS fallback.

Covers the decision helper (:func:`_switch_tts_to_default_voice`) and the
end-to-end ``tts_node`` record-and-replay behaviour: when the configured voice
produces no audio, the agent switches the live TTS to the plugin default voice
and re-speaks the turn rather than going silent.
"""

from __future__ import annotations

import types
from collections.abc import AsyncIterable

import pytest
from livekit.agents import APIStatusError
from livekit.agents.voice import Agent

# The ElevenLabs plugin ships only in the `agent` dependency group. CI installs
# `--group dev` only, so skip this whole module when the plugin stack is absent,
# matching the sibling agent tests. Run `uv sync --group agent` to exercise it.
pytest.importorskip("livekit.plugins.elevenlabs")

from livekit.plugins.elevenlabs.tts import DEFAULT_VOICE_ID  # noqa: E402

from soshop.agent.pitch_agent import PitchAgent, _switch_tts_to_default_voice  # noqa: E402


class _FakeTTS:
    """Stand-in for the ElevenLabs plugin exposing the bits we read."""

    def __init__(self, voice_id: str | None, *, with_opts: bool = True) -> None:
        if with_opts:
            self._opts = types.SimpleNamespace(voice_id=voice_id)
        self.calls: list[str | None] = []

    def update_options(self, *, voice_id: str | None = None, **_: object) -> None:
        self.calls.append(voice_id)
        if hasattr(self, "_opts"):
            self._opts.voice_id = voice_id


def _agent_with(tts: object) -> object:
    return types.SimpleNamespace(session=types.SimpleNamespace(tts=tts))


# ---------------------------------------------------------------------------
# _switch_tts_to_default_voice
# ---------------------------------------------------------------------------


def test_switch_updates_voice_and_returns_previous() -> None:
    tts = _FakeTTS("badVoice123")
    previous = _switch_tts_to_default_voice(_agent_with(tts))
    assert previous == "badVoice123"
    assert tts.calls == [DEFAULT_VOICE_ID]
    assert tts._opts.voice_id == DEFAULT_VOICE_ID


def test_switch_is_noop_when_already_on_default() -> None:
    # The default voice itself failing is a real outage; re-switching would loop.
    tts = _FakeTTS(DEFAULT_VOICE_ID)
    assert _switch_tts_to_default_voice(_agent_with(tts)) is None
    assert tts.calls == []


def test_switch_is_noop_without_update_options() -> None:
    assert _switch_tts_to_default_voice(_agent_with(object())) is None


def test_switch_is_noop_when_no_live_tts() -> None:
    assert _switch_tts_to_default_voice(_agent_with(None)) is None


def test_switch_swallows_session_access_error() -> None:
    class _NoActivity:
        @property
        def session(self) -> object:  # mirrors Agent.session raising pre-activation
            raise RuntimeError("no active activity")

    assert _switch_tts_to_default_voice(_NoActivity()) is None


def test_switch_swallows_update_options_error() -> None:
    class _RaisingTTS:
        _opts = types.SimpleNamespace(voice_id="bad")

        def update_options(self, **_: object) -> None:
            raise RuntimeError("plugin boom")

    assert _switch_tts_to_default_voice(_agent_with(_RaisingTTS())) is None


def test_switch_returns_empty_string_when_current_unreadable() -> None:
    # Switchable, but the current voice can't be read (no _opts).
    tts = _FakeTTS(None, with_opts=False)
    assert _switch_tts_to_default_voice(_agent_with(tts)) == ""
    assert tts.calls == [DEFAULT_VOICE_ID]


# ---------------------------------------------------------------------------
# PitchAgent.tts_node record-and-replay
# ---------------------------------------------------------------------------


class _FakeFrame:
    def __init__(self, label: str) -> None:
        self.label = label


async def _text(*chunks: str) -> AsyncIterable[str]:
    for chunk in chunks:
        yield chunk


def _detached_agent(tts: object) -> PitchAgent:
    """A PitchAgent whose ``session.tts`` resolves to ``tts`` without a real
    AgentActivity (``Agent.__init__`` needs an event loop and full wiring)."""
    agent = object.__new__(PitchAgent)
    agent._activity = types.SimpleNamespace(session=types.SimpleNamespace(tts=tts))
    return agent


async def test_tts_node_falls_back_and_replays_on_dead_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    async def fake_tts_node(
        agent: object, text: AsyncIterable[str], _ms: object
    ) -> AsyncIterable[_FakeFrame]:
        consumed = [chunk async for chunk in text]
        calls.append(consumed)
        if len(calls) == 1:
            # Dead-voice signature: the plugin surfaces it as retryable
            # "connection closed" after producing no audio.
            raise APIStatusError("connection closed")
        for chunk in consumed:
            yield _FakeFrame(chunk)

    monkeypatch.setattr(Agent.default, "tts_node", fake_tts_node)

    tts = _FakeTTS("badVoice123")
    agent = _detached_agent(tts)

    frames = [f async for f in agent.tts_node(_text("Hello ", "there world"), None)]

    assert len(calls) == 2, "should retry exactly once after the dead-voice failure"
    assert tts.calls == [DEFAULT_VOICE_ID], "should switch the live TTS to the default voice"
    # The replayed turn carries the full (filtered) text, in some chunking.
    assert "".join(f.label for f in frames) == "Hello there world"


async def test_tts_node_does_not_retry_after_audio_started(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    async def fake_tts_node(
        agent: object, text: AsyncIterable[str], _ms: object
    ) -> AsyncIterable[_FakeFrame]:
        calls.append(1)
        async for chunk in text:
            yield _FakeFrame(chunk)  # produce audio first...
        raise APIStatusError("connection closed")  # ...then drop mid-stream

    monkeypatch.setattr(Agent.default, "tts_node", fake_tts_node)
    tts = _FakeTTS("goodVoice")
    agent = _detached_agent(tts)

    with pytest.raises(APIStatusError):
        _ = [f async for f in agent.tts_node(_text("partial"), None)]

    assert len(calls) == 1, "a mid-stream drop must not be replayed"
    assert tts.calls == [], "must not switch voice once audio has played"


async def test_tts_node_reraises_when_default_voice_already_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    async def fake_tts_node(
        agent: object, text: AsyncIterable[str], _ms: object
    ) -> AsyncIterable[_FakeFrame]:
        calls.append(1)
        _ = [chunk async for chunk in text]
        raise APIStatusError("connection closed")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(Agent.default, "tts_node", fake_tts_node)
    tts = _FakeTTS(DEFAULT_VOICE_ID)  # already on the default voice
    agent = _detached_agent(tts)

    with pytest.raises(APIStatusError):
        _ = [f async for f in agent.tts_node(_text("hi"), None)]

    assert len(calls) == 1, "no fallback retry when the default voice is the one failing"
    assert tts.calls == []
