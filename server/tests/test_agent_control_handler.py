"""Unit tests for the in-place persona-swap handler.

Exercises ``_execute_swap_persona`` against fully-stubbed
``Agent`` / ``AgentSession`` / ``PitchTools`` instances so the test
runs without a LiveKit room or real plugins. Skipped when the agent
plugin group isn't installed (matches `test_agent_pipeline.py`).
"""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass, field
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


# ---------------------------------------------------------------------------
# Stub doubles
# ---------------------------------------------------------------------------


class _FakeTTS:
    def __init__(self) -> None:
        self.voice_calls: list[dict[str, Any]] = []

    def update_options(self, **kwargs: Any) -> None:
        self.voice_calls.append(kwargs)


class _FakeSession:
    def __init__(self, *, interrupt_raises: bool = False) -> None:
        self.tts = _FakeTTS()
        self.interrupted = 0
        self.generate_reply_calls: list[dict[str, Any]] = []
        self._interrupt_raises = interrupt_raises
        # Truthy sentinel mirrors `AgentSession._activity` — the swap
        # path probes this to skip session calls on a closed session
        # (see `_session_running` in `soshop.agent.main`).
        self._activity: object = object()

    async def interrupt(self, force: bool = False) -> None:  # noqa: FBT001, FBT002
        self.interrupted += 1
        if self._interrupt_raises:
            raise RuntimeError("simulated interrupt failure")

    def generate_reply(self, **kwargs: Any) -> object:
        self.generate_reply_calls.append(kwargs)
        return object()  # SpeechHandle stand-in


class _FakeAgent:
    def __init__(self) -> None:
        self.instructions_updates: list[str] = []
        self.tool_updates: list[list[Any]] = []
        self.chat_ctx_resets: int = 0

    async def update_instructions(self, instructions: str) -> None:
        self.instructions_updates.append(instructions)

    async def update_tools(self, tools: list[Any]) -> None:
        self.tool_updates.append(list(tools))

    async def update_chat_ctx(self, chat_ctx: Any) -> None:
        # Stand-in for `livekit.agents.voice.Agent.update_chat_ctx`. The
        # swap path calls this with an empty `ChatContext` to drop the
        # previous segment's items before the new persona's first turn.
        self.chat_ctx_resets += 1


class _FakePitchTools:
    def __init__(self) -> None:
        self.rebind_calls: list[dict[str, Any]] = []
        self.schedule_auto_show_calls: int = 0

    def rebind(self, **kwargs: Any) -> None:
        self.rebind_calls.append(kwargs)

    def schedule_auto_show(self) -> None:
        self.schedule_auto_show_calls += 1


@dataclass
class _Spies:
    next_metadata_seen: list[Any] = field(default_factory=list)
    voice_id_calls: list[str] = field(default_factory=list)
    avatar_image_calls: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def metadata_factory():
    from soshop.agent.schemas import DispatchMetadata

    def _make(persona: str = "persona-next", product: str | None = None) -> DispatchMetadata:
        return DispatchMetadata(
            persona_id=persona,
            script_id="script-x",
            session_id="sess-1",
            user_id="user_test",
            pitch_window_ms=20_000,
            product_id=product,
            popup_payload=None,
        )

    return _make


@pytest.fixture
def initial_runtime():
    from soshop.agent.main import AgentRuntimeContext
    from soshop.agent.schemas import DispatchMetadata

    metadata = DispatchMetadata(
        persona_id="persona-initial",
        script_id="script-initial",
        session_id="sess-1",
        user_id="user_test",
        pitch_window_ms=20_000,
        product_id=None,
        popup_payload=None,
    )
    return AgentRuntimeContext(
        metadata=metadata,
        persona_prompt="initial prompt",
        product_context=None,
    )


@pytest.fixture
def runner(initial_runtime):
    """Build an AgentRunner with all dependencies stubbed."""
    from soshop.agent.main import AgentRunner

    class _FakeJobContext:
        room = object()  # not exercised in these tests

    return AgentRunner(
        ctx=_FakeJobContext(),  # type: ignore[arg-type]
        session=_FakeSession(),  # type: ignore[arg-type]
        agent=_FakeAgent(),  # type: ignore[arg-type]
        pitch_tools=_FakePitchTools(),  # type: ignore[arg-type]
        runtime=initial_runtime,
        lemonslice_session_id="ls-sess-1",
    )


@pytest.fixture
def patched_handler(monkeypatch):
    """Patch DB loaders / lemonslice / pool / find_function_tools.

    Returns a ``_Spies`` instance the test asserts on.
    """
    from soshop.agent import swap as agent_swap
    from soshop.agent.main import AgentRuntimeContext

    spies = _Spies()
    pool_sentinel = object()

    async def fake_get_pool() -> object:
        return pool_sentinel

    async def fake_build_runtime_context(metadata, *, pool=None):
        spies.next_metadata_seen.append(metadata)
        return AgentRuntimeContext(
            metadata=metadata,
            persona_prompt=f"prompt for {metadata.persona_id}",
            product_context=None,
        )

    async def fake_load_voice_id(pool, persona_id):
        spies.voice_id_calls.append(persona_id)
        return ("voice-next", "https://cdn/avatar-next.png")

    async def fake_lemonslice_update_image(session_id, image_url, *, api_key, **_kw):
        spies.avatar_image_calls.append((session_id, image_url))
        return {"success": True}

    monkeypatch.setattr(agent_swap, "get_pool", fake_get_pool)
    monkeypatch.setattr(agent_swap, "build_runtime_context", fake_build_runtime_context)
    monkeypatch.setattr(agent_swap, "_db_load_persona_voice_id", fake_load_voice_id)
    monkeypatch.setattr(agent_swap, "lemonslice_update_image", fake_lemonslice_update_image)
    # Make find_function_tools deterministic without depending on the
    # @llm.function_tool decorator chain — the handler only uses the
    # returned list to feed agent.update_tools.
    monkeypatch.setattr(agent_swap.llm, "find_function_tools", lambda obj: ["tool-stub"])
    monkeypatch.setattr(agent_swap.settings, "LEMONSLICE_API_KEY", "key")
    return spies


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_swap_persona_happy_path_runs_all_steps_in_order(
    runner, patched_handler, metadata_factory
) -> None:
    from soshop.agent.main import _execute_swap_persona

    next_md = metadata_factory(persona="persona-next", product="prod-y")

    await _execute_swap_persona(runner, next_md, source="test")

    # Step 2: TTS interrupted exactly once.
    assert runner.session.interrupted == 1
    # Step 3: prompt swapped to the new persona.
    assert runner.agent.instructions_updates == ["prompt for persona-next"]
    # Step 3b: chat history reset so prior segment's tool calls /
    # product references don't leak into the new persona's first turn.
    assert runner.agent.chat_ctx_resets == 1
    # Step 4: ElevenLabs voice swapped.
    assert runner.session.tts.voice_calls == [{"voice_id": "voice-next"}]
    # Step 5: pitch tools rebound and refreshed on the LLM.
    assert runner.pitch_tools.rebind_calls == [{"product_id": "prod-y", "popup_payload": None}]
    assert runner.agent.tool_updates == [["tool-stub"]]
    # Step 6: avatar image swapped via the LemonSlice control endpoint.
    assert patched_handler.avatar_image_calls == [("ls-sess-1", "https://cdn/avatar-next.png")]
    # Step 7: runner.runtime now reflects the new persona.
    assert runner.runtime.metadata.persona_id == "persona-next"
    # Step 8: a new opening line was kicked off with `tool_choice="none"`
    # so the SDK doesn't auto-launch follow-up LLM steps after a tool
    # call (the root cause of the "2-3 pitches per product" bug — each
    # tool call kicked off another step on the same SpeechHandle, so
    # the orchestrator waited through every step before rotating).
    assert len(runner.session.generate_reply_calls) == 1
    kickoff_kwargs = runner.session.generate_reply_calls[0]
    assert kickoff_kwargs.get("tool_choice") == "none"


async def test_swap_persona_serializes_concurrent_swipes(
    runner, patched_handler, metadata_factory
) -> None:
    from soshop.agent.main import _execute_swap_persona

    md_a = metadata_factory(persona="persona-a")
    md_b = metadata_factory(persona="persona-b")

    await asyncio.gather(
        _execute_swap_persona(runner, md_a, source="t"),
        _execute_swap_persona(runner, md_b, source="t"),
    )

    # Both swaps ran (lock serializes — does not coalesce).
    assert runner.agent.instructions_updates == [
        "prompt for persona-a",
        "prompt for persona-b",
    ] or runner.agent.instructions_updates == [
        "prompt for persona-b",
        "prompt for persona-a",
    ]
    # The final state matches the *last* swap to acquire the lock.
    last = runner.agent.instructions_updates[-1]
    assert runner.runtime.persona_prompt == last


# ---------------------------------------------------------------------------
# Failure modes (each step's failure is isolated; partial_failure is logged)
# ---------------------------------------------------------------------------


async def test_swap_persona_aborts_when_db_load_fails(
    runner, monkeypatch, metadata_factory
) -> None:
    from soshop.agent import swap as agent_swap
    from soshop.agent.main import _execute_swap_persona

    async def boom(*_a, **_kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(agent_swap, "build_runtime_context", boom)
    # get_pool is still needed by the handler before build_runtime_context;
    # provide a no-op so the failure path runs there.
    monkeypatch.setattr(agent_swap, "get_pool", lambda: _ready(None))

    await _execute_swap_persona(runner, metadata_factory(), source="t")

    # NOTHING was mutated — the handler aborted early.
    assert runner.session.interrupted == 0
    assert runner.agent.instructions_updates == []
    assert runner.session.tts.voice_calls == []
    assert runner.pitch_tools.rebind_calls == []
    assert runner.session.generate_reply_calls == []
    # runtime unchanged.
    assert runner.runtime.metadata.persona_id == "persona-initial"


async def test_swap_persona_continues_when_avatar_update_fails(
    runner, patched_handler, metadata_factory, monkeypatch
) -> None:
    from soshop.agent import swap as agent_swap
    from soshop.agent.main import _execute_swap_persona
    from soshop.livekit.lemonslice_control import LemonsliceControlError

    async def fail(*_a, **_kw):
        raise LemonsliceControlError("nope", status_code=502, body=None)

    monkeypatch.setattr(agent_swap, "lemonslice_update_image", fail)

    await _execute_swap_persona(runner, metadata_factory(), source="t")

    # Prompt and voice still swapped.
    assert runner.agent.instructions_updates == ["prompt for persona-next"]
    assert runner.session.tts.voice_calls == [{"voice_id": "voice-next"}]
    # And the new opening still fires.
    assert len(runner.session.generate_reply_calls) == 1


async def test_swap_persona_skips_voice_when_voice_id_missing(
    runner, patched_handler, metadata_factory, monkeypatch
) -> None:
    from soshop.agent import swap as agent_swap
    from soshop.agent.main import _execute_swap_persona

    async def no_voice(*_a, **_kw):
        return (None, "https://cdn/avatar-next.png")

    monkeypatch.setattr(agent_swap, "_db_load_persona_voice_id", no_voice)

    await _execute_swap_persona(runner, metadata_factory(), source="t")

    assert runner.session.tts.voice_calls == []
    # Other steps still run.
    assert runner.agent.instructions_updates == ["prompt for persona-next"]


async def test_swap_persona_continues_when_interrupt_raises(
    runner, patched_handler, metadata_factory
) -> None:
    from soshop.agent.main import _execute_swap_persona

    runner.session = _FakeSession(interrupt_raises=True)

    await _execute_swap_persona(runner, metadata_factory(), source="t")

    # Subsequent steps still ran despite the interrupt failure.
    assert runner.agent.instructions_updates == ["prompt for persona-next"]
    assert len(runner.session.generate_reply_calls) == 1


async def _ready(value):  # tiny awaitable returning a value
    return value


# ---------------------------------------------------------------------------
# viewer_ready (customize one-shot pitch gate)
# ---------------------------------------------------------------------------


async def test_viewer_ready_sets_runner_event(runner) -> None:
    """The browser's `viewer_ready` control message releases the held pitch.

    The /customize entrypoint awaits ``runner.viewer_ready`` before its
    one-shot opening turn so the pitch is never delivered to a still-muted
    client. The control router must flip that event when the signal arrives.
    """
    from soshop.agent.control import _dispatch_control_message

    assert not runner.viewer_ready.is_set()
    await _dispatch_control_message(runner, {"action": "viewer_ready"}, source="test")
    assert runner.viewer_ready.is_set()
