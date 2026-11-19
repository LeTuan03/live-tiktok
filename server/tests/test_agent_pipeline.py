"""Pipeline construction tests for the agent module.

Verifies that `build_agent_session` wires the right plugin classes with
the right per-persona settings (voice_id) and that `build_agent_tools`
exposes the existing `signal_product_pitch` / `signal_pitch_end` LLM
tools without relying on a live LiveKit room.

These tests skip when the `agent` dependency group isn't sync'd (the
plugin packages aren't on the import path) — that mirrors CI, which
runs only `uv sync --group dev`.
"""

from __future__ import annotations

import importlib

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


def test_build_agent_tools_registers_signal_product_pitch_and_signal_pitch_end():
    from soshop.agent.main import AgentRuntimeContext, build_agent_tools
    from soshop.agent.schemas import DispatchMetadata, PopupPayload

    metadata = DispatchMetadata(
        persona_id="00000000-0000-0000-0000-000000000001",
        script_id="00000000-0000-0000-0000-000000000002",
        session_id="00000000-0000-0000-0000-000000000003",
        user_id="user_test",
        pitch_window_ms=20_000,
        product_id="00000000-0000-0000-0000-000000000004",
        popup_payload=PopupPayload(
            id="00000000-0000-0000-0000-000000000004",
            title="Earbuds",
            image_url="https://example/p.jpg",
            cta="Tap to shop · $129",
            deep_link_url="https://example/d",
            landing_url="https://example/l",
        ),
    )
    runtime = AgentRuntimeContext(
        metadata=metadata,
        persona_prompt="be a host",
        product_context={"title": "Earbuds"},
    )

    class _FakeRoom:
        class _FakeLocalParticipant:
            async def send_text(self, *_args, **_kwargs):
                return None

        local_participant = _FakeLocalParticipant()

    class _FakeJobContext:
        room = _FakeRoom()

    tools = build_agent_tools(_FakeJobContext(), runtime)

    names = {getattr(t, "name", None) or t.__name__ for t in tools}
    assert "signal_product_pitch" in names
    assert "signal_pitch_end" in names


@pytest.mark.asyncio
async def test_build_agent_session_threads_voice_id_into_elevenlabs(monkeypatch):
    """The persona's voice_id must reach the ElevenLabs TTS constructor.

    Verified by intercepting the plugin classes — we don't open a real
    LiveKit room and don't make a real API call.
    """
    from soshop.agent.main import AgentRuntimeContext, build_agent_session
    from soshop.agent.schemas import DispatchMetadata

    captured: dict[str, dict] = {}

    class _FakeVAD:
        @classmethod
        def load(cls):
            captured["vad_load"] = {}
            return cls()

    class _FakeSTT:
        def __init__(self, **kwargs):
            captured["stt"] = dict(kwargs)

    class _FakeLLM:
        def __init__(self, **kwargs):
            captured["llm"] = dict(kwargs)

    class _FakeTTS:
        def __init__(self, **kwargs):
            captured["tts"] = dict(kwargs)

    class _FakeAgentSession:
        def __init__(self, **kwargs):
            captured["session_kwargs"] = dict(kwargs)

    from livekit.agents import voice as voice_module
    from livekit.plugins import elevenlabs, groq, silero

    monkeypatch.setattr(silero, "VAD", _FakeVAD)
    monkeypatch.setattr(groq, "STT", _FakeSTT)
    monkeypatch.setattr(groq, "LLM", _FakeLLM)
    monkeypatch.setattr(elevenlabs, "TTS", _FakeTTS)
    monkeypatch.setattr(voice_module, "AgentSession", _FakeAgentSession)
    monkeypatch.setattr("soshop.agent.runner.settings.ELEVEN_API_KEY", "test-key")

    metadata = DispatchMetadata(
        persona_id="00000000-0000-0000-0000-000000000001",
        script_id="00000000-0000-0000-0000-000000000002",
        session_id="00000000-0000-0000-0000-000000000003",
        user_id="user_test",
        pitch_window_ms=20_000,
        product_id=None,
        popup_payload=None,
    )
    runtime = AgentRuntimeContext(
        metadata=metadata,
        persona_prompt="be a host",
        product_context=None,
    )

    class _FakeRoom:
        class _LP:
            async def send_text(self, *a, **kw):  # noqa: ARG002
                return None

        local_participant = _LP()

    class _FakeJobContext:
        room = _FakeRoom()

    await build_agent_session(_FakeJobContext(), runtime, voice_id="voice-abc")

    assert captured["tts"]["voice_id"] == "voice-abc"
    assert "session_kwargs" in captured
    sk = captured["session_kwargs"]
    # Tools must NOT be registered on the AgentSession — they live on the
    # Agent so that `Agent.update_tools` during an in-place persona swap
    # cleanly replaces them. Holding tools in both places makes
    # `AgentActivity.tools` (session + agent + mcp) duplicate every entry
    # after the first swap, which raises `duplicate function name` from
    # `llm.ToolContext` on the next LLM turn.
    assert "tools" not in sk or not sk["tools"]


@pytest.mark.asyncio
async def test_build_agent_session_omits_voice_id_when_persona_lacks_one(monkeypatch):
    from livekit.agents import voice as voice_module
    from livekit.plugins import elevenlabs, groq, silero

    from soshop.agent.main import AgentRuntimeContext, build_agent_session
    from soshop.agent.schemas import DispatchMetadata

    tts_kwargs: dict = {}

    class _FakeVAD:
        @classmethod
        def load(cls):
            return cls()

    class _FakeStub:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeTTS(_FakeStub):
        def __init__(self, **kwargs):
            tts_kwargs.update(kwargs)
            super().__init__(**kwargs)

    class _FakeAgentSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(silero, "VAD", _FakeVAD)
    monkeypatch.setattr(groq, "STT", _FakeStub)
    monkeypatch.setattr(groq, "LLM", _FakeStub)
    monkeypatch.setattr(elevenlabs, "TTS", _FakeTTS)
    monkeypatch.setattr(voice_module, "AgentSession", _FakeAgentSession)
    monkeypatch.setattr("soshop.agent.runner.settings.ELEVEN_API_KEY", "test-key")

    metadata = DispatchMetadata(
        persona_id="00000000-0000-0000-0000-000000000001",
        script_id="00000000-0000-0000-0000-000000000002",
        session_id="00000000-0000-0000-0000-000000000003",
        user_id="user_test",
        pitch_window_ms=20_000,
        product_id=None,
        popup_payload=None,
    )
    runtime = AgentRuntimeContext(
        metadata=metadata,
        persona_prompt="be a host",
        product_context=None,
    )

    class _FakeRoom:
        class _LP:
            async def send_text(self, *a, **kw):  # noqa: ARG002
                return None

        local_participant = _LP()

    class _FakeJobContext:
        room = _FakeRoom()

    await build_agent_session(_FakeJobContext(), runtime, voice_id=None)

    # Default elevenlabs.TTS voice — voice_id key must NOT be present.
    assert "voice_id" not in tts_kwargs


@pytest.mark.asyncio
async def test_build_agent_session_falls_back_to_edge_tts_without_eleven_key(monkeypatch):
    """No ElevenLabs key: the session speaks through the keyless Edge TTS."""
    from livekit.agents import voice as voice_module
    from livekit.plugins import groq, silero

    from soshop.agent.edge_voice import EdgeTTS
    from soshop.agent.main import AgentRuntimeContext, build_agent_session
    from soshop.agent.schemas import DispatchMetadata

    class _FakeVAD:
        @classmethod
        def load(cls):
            return cls()

    class _FakeStub:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(silero, "VAD", _FakeVAD)
    monkeypatch.setattr(groq, "STT", _FakeStub)
    monkeypatch.setattr(groq, "LLM", _FakeStub)
    monkeypatch.setattr(voice_module, "AgentSession", _FakeStub)
    monkeypatch.setattr("soshop.agent.runner.settings.ELEVEN_API_KEY", None)
    monkeypatch.setattr("soshop.agent.runner.settings.EDGE_TTS_VOICE", "en-US-AriaNeural")

    runtime = AgentRuntimeContext(
        metadata=DispatchMetadata(
            persona_id="00000000-0000-0000-0000-000000000001",
            script_id="00000000-0000-0000-0000-000000000002",
            session_id="00000000-0000-0000-0000-000000000003",
            user_id="user_test",
            pitch_window_ms=20_000,
            product_id=None,
            popup_payload=None,
        ),
        persona_prompt="be a host",
        product_context=None,
    )

    # An ElevenLabs id is not an Edge voice, so the default voice applies.
    session = await build_agent_session(object(), runtime, voice_id="m8AHWg36LJTQWKmfeGVv")
    tts = session.kwargs["tts"]
    assert isinstance(tts, EdgeTTS)
    assert tts.voice == "en-US-AriaNeural"

    # The persona swap calls update_options(voice_id=...); Edge names stick.
    tts.update_options(voice_id="vi-VN-HoaiMyNeural")
    assert tts.voice == "vi-VN-HoaiMyNeural"
    tts.update_options(voice_id="some-elevenlabs-id")
    assert tts.voice == "en-US-AriaNeural"
