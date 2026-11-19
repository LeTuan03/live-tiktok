"""Unit tests for the channel broadcast loop.

Exercises ``_run_channel_iterations`` (the deterministic loop body that
drives the rotation) with every external collaborator stubbed —
LiveKit, asyncpg, the LemonSlice control endpoint, and the persona
swap handler are all replaced with simple async spies.

The rotation is completion-driven: each iteration calls
``deps.wait_for_pitch_complete`` instead of sleeping for a fixed
``ad.duration_ms``. The fake completion below resolves naturally on
each call so the tests can assert the cursor / log / swap behavior
without needing a real ``SpeechHandle``.

Skipped when the agent plugin group isn't installed (matches
``test_agent_control_handler.py``).
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

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
# Helpers
# ---------------------------------------------------------------------------

_AD_UUIDS = [
    UUID("00000000-0000-0000-0000-0000000000a1"),
    UUID("00000000-0000-0000-0000-0000000000a2"),
    UUID("00000000-0000-0000-0000-0000000000a3"),
]
_PERSONA_UUIDS = [
    UUID("00000000-0000-0000-0000-0000000000b1"),
    UUID("00000000-0000-0000-0000-0000000000b2"),
    UUID("00000000-0000-0000-0000-0000000000b3"),
]
_PRODUCT_UUIDS = [
    UUID("00000000-0000-0000-0000-0000000000c1"),
    UUID("00000000-0000-0000-0000-0000000000c2"),
    UUID("00000000-0000-0000-0000-0000000000c3"),
]
_SCRIPT_UUIDS = [
    UUID("00000000-0000-0000-0000-0000000000d1"),
    UUID("00000000-0000-0000-0000-0000000000d2"),
    UUID("00000000-0000-0000-0000-0000000000d3"),
]
_ADVERTISER_UUID = UUID("00000000-0000-0000-0000-0000000000ad")


def _make_ads() -> list[Any]:
    from soshop_server.db.models import Ad, AdStatus

    now = datetime.now(UTC)
    return [
        Ad(
            id=_AD_UUIDS[i],
            advertiser_id=_ADVERTISER_UUID,
            persona_id=_PERSONA_UUIDS[i],
            product_id=_PRODUCT_UUIDS[i],
            script_id=_SCRIPT_UUIDS[i],
            duration_ms=45_000,
            sequence_order=i + 1,
            status=AdStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        for i in range(3)
    ]


def _make_base_metadata() -> Any:
    from soshop.agent.schemas import DispatchMetadata

    return DispatchMetadata(
        persona_id=str(_PERSONA_UUIDS[0]),
        script_id=str(_SCRIPT_UUIDS[0]),
        session_id="sess-channel-loop",
        user_id="anon-channel",
        pitch_window_ms=0,
        product_id=str(_PRODUCT_UUIDS[0]),
        popup_payload=None,
        channel_id="channel-0",
        ad_start_index=0,
    )


def _make_runner(base_metadata: Any) -> Any:
    from soshop.agent.main import AgentRunner, AgentRuntimeContext

    runtime = AgentRuntimeContext(
        metadata=base_metadata,
        persona_prompt="initial prompt",
        product_context=None,
    )

    class _FakeJobContext:
        room = object()

    class _FakeSession:
        # Truthy sentinel mirrors `AgentSession._activity` so the
        # liveness probe in `_run_channel_iterations` keeps the loop
        # running under the test fake.
        _activity = object()

    class _FakeAgent:
        pass

    class _FakePitchTools:
        pass

    return AgentRunner(
        ctx=_FakeJobContext(),  # type: ignore[arg-type]
        session=_FakeSession(),  # type: ignore[arg-type]
        agent=_FakeAgent(),  # type: ignore[arg-type]
        pitch_tools=_FakePitchTools(),  # type: ignore[arg-type]
        runtime=runtime,
        lemonslice_session_id="ls-sess",
    )


def _make_fake_completion(
    reason: str = "natural",
    elapsed_s: float = 12.5,
    record: list[dict[str, Any]] | None = None,
) -> Any:
    """Build a ``wait_for_pitch_complete`` async fake.

    Returns the same ``CompletionOutcome(reason, elapsed_s)`` every call
    and (optionally) appends the call kwargs to ``record`` so the test
    can assert the safety ceiling was passed through correctly.
    """
    from soshop.agent.rotation import CompletionOutcome

    async def _wait(*, handle: Any, max_wait_s: float) -> CompletionOutcome:  # noqa: ARG001
        if record is not None:
            record.append({"max_wait_s": max_wait_s, "handle": handle})
        return CompletionOutcome(reason=reason, elapsed_s=elapsed_s)  # type: ignore[arg-type]

    return _wait


async def _zero_jitter() -> float:
    return 0.0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_channel_loop_advances_cursor_swaps_persona_and_writes_log() -> None:
    """Three iterations: three swaps, three log rows, three completion waits.

    The recorded event order verifies the [started → cursor → swap →
    completion → jitter → ended] sequencing — the cursor write lands
    before the swap so a crash inside ``swap_persona`` still leaves a
    trail at the offending ad, and the completion wait happens before
    the jitter pause.
    """
    from soshop.agent.main import (
        CHANNEL_LOOP_SWAP_SOURCE,
        ChannelLoopDeps,
        _run_channel_iterations,
    )

    ads = _make_ads()
    base_metadata = _make_base_metadata()
    runner = _make_runner(base_metadata)

    pool_sentinel = object()
    swap_calls: list[dict[str, Any]] = []
    cursor_writes: list[tuple[str, int]] = []
    started_calls: list[tuple[str, int]] = []
    ended_calls: list[int] = []
    completion_waits: list[dict[str, Any]] = []
    jitter_calls = {"n": 0}
    event_order: list[str] = []
    next_log_id = {"v": 100}

    async def fake_swap_persona(runner_arg: Any, next_md: Any, source: str) -> None:
        event_order.append("swap")
        swap_calls.append(
            {
                "persona_id": next_md.persona_id,
                "product_id": next_md.product_id,
                "script_id": next_md.script_id,
                "session_id": next_md.session_id,
                "channel_id": next_md.channel_id,
                "popup_payload": next_md.popup_payload,
                "pitch_window_ms": next_md.pitch_window_ms,
                "source": source,
            }
        )

    async def fake_write_cursor(pool: Any, channel_id: str, sequence_order: int) -> None:
        assert pool is pool_sentinel
        event_order.append("write_cursor")
        cursor_writes.append((channel_id, sequence_order))

    async def fake_record_started(pool: Any, channel_id: str, ad: Any) -> int:
        assert pool is pool_sentinel
        event_order.append("started")
        started_calls.append((channel_id, ad.sequence_order))
        log_id = next_log_id["v"]
        next_log_id["v"] += 1
        return log_id

    async def fake_record_ended(pool: Any, log_id: int) -> None:
        assert pool is pool_sentinel
        event_order.append("ended")
        ended_calls.append(log_id)

    fake_wait = _make_fake_completion(record=completion_waits)

    async def recorded_wait(*, handle: Any, max_wait_s: float) -> Any:
        event_order.append("wait_complete")
        return await fake_wait(handle=handle, max_wait_s=max_wait_s)

    async def fake_jitter() -> float:
        event_order.append("jitter")
        jitter_calls["n"] += 1
        return 0.0

    from soshop.agent.schemas import PopupPayload

    popup_calls: list[str | None] = []

    def _fake_popup(pid: str | None) -> PopupPayload | None:
        if pid is None:
            return None
        return PopupPayload(
            id=pid,
            title=f"Product {pid}",
            image_url="https://cdn.example/img.jpg",
            cta="Tap to shop",
            deep_link_url="https://example.com/p",
            landing_url="https://example.com/p",
        )

    async def fake_load_popup(_pool: Any, product_id: str | None) -> PopupPayload | None:
        popup_calls.append(product_id)
        return _fake_popup(product_id)

    deps = ChannelLoopDeps(
        swap_persona=fake_swap_persona,
        write_cursor=fake_write_cursor,
        record_ad_started=fake_record_started,
        record_ad_ended=fake_record_ended,
        load_popup=fake_load_popup,
        wait_for_pitch_complete=recorded_wait,
        jitter=fake_jitter,
    )

    await _run_channel_iterations(
        runner,
        pool_sentinel,  # type: ignore[arg-type]
        ads=ads,
        start_cursor=0,
        channel_id="channel-0",
        deps=deps,
        max_iterations=3,
    )

    _assert_three_iteration_result(
        cursor_writes=cursor_writes,
        swap_calls=swap_calls,
        started_calls=started_calls,
        ended_calls=ended_calls,
        completion_waits=completion_waits,
        jitter_calls=jitter_calls,
        popup_calls=popup_calls,
        event_order=event_order,
        base_metadata=base_metadata,
        ads=ads,
        swap_source=CHANNEL_LOOP_SWAP_SOURCE,
    )


def _assert_three_iteration_result(
    *,
    cursor_writes: list[tuple[str, int]],
    swap_calls: list[dict[str, Any]],
    started_calls: list[tuple[str, int]],
    ended_calls: list[int],
    completion_waits: list[dict[str, Any]],
    jitter_calls: dict[str, int],
    popup_calls: list[str | None],
    event_order: list[str],
    base_metadata: Any,
    ads: list[Any],
    swap_source: str,
) -> None:
    """Bundle the channel-loop happy-path assertions so the test stays under
    ruff's max-complexity threshold."""
    expected_per_iteration = [
        ("channel-0", ads[0].sequence_order),
        ("channel-0", ads[1].sequence_order),
        ("channel-0", ads[2].sequence_order),
    ]
    assert cursor_writes == expected_per_iteration
    assert started_calls == expected_per_iteration
    assert ended_calls == [100, 101, 102]
    assert [c["source"] for c in swap_calls] == [swap_source] * 3
    assert [c["persona_id"] for c in swap_calls] == [str(u) for u in _PERSONA_UUIDS]
    assert [c["product_id"] for c in swap_calls] == [str(u) for u in _PRODUCT_UUIDS]
    assert [c["script_id"] for c in swap_calls] == [str(u) for u in _SCRIPT_UUIDS]
    assert [c["session_id"] for c in swap_calls] == [base_metadata.session_id] * 3
    assert [c["channel_id"] for c in swap_calls] == ["channel-0"] * 3
    assert [c["popup_payload"].id for c in swap_calls] == [str(u) for u in _PRODUCT_UUIDS]
    assert [c["pitch_window_ms"] for c in swap_calls] == [0, 0, 0]
    assert [c["max_wait_s"] for c in completion_waits] == [45.0, 45.0, 45.0]
    assert jitter_calls["n"] == 3
    assert popup_calls == [str(u) for u in _PRODUCT_UUIDS]
    assert (
        event_order == ["started", "write_cursor", "swap", "wait_complete", "jitter", "ended"] * 3
    )


async def test_channel_loop_wraps_cursor_modulo_rotation_length() -> None:
    """``start_cursor`` larger than the rotation wraps modulo ``len(ads)``."""
    from soshop.agent.main import ChannelLoopDeps, _run_channel_iterations

    ads = _make_ads()
    base_metadata = _make_base_metadata()
    runner = _make_runner(base_metadata)

    swap_persona_ids: list[str] = []

    async def fake_swap_persona(runner_arg: Any, next_md: Any, source: str) -> None:
        swap_persona_ids.append(next_md.persona_id)

    async def noop(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_started(*args: Any, **kwargs: Any) -> int:
        return 1

    deps = ChannelLoopDeps(
        swap_persona=fake_swap_persona,
        write_cursor=noop,
        record_ad_started=fake_started,
        record_ad_ended=noop,
        load_popup=noop,
        wait_for_pitch_complete=_make_fake_completion(),
        jitter=_zero_jitter,
    )

    # Start at cursor=5: 5 % 3 = 2 → ad index 2, then 0, then 1.
    await _run_channel_iterations(
        runner,
        object(),  # type: ignore[arg-type]
        ads=ads,
        start_cursor=5,
        channel_id="channel-0",
        deps=deps,
        max_iterations=3,
    )

    assert swap_persona_ids == [
        str(_PERSONA_UUIDS[2]),
        str(_PERSONA_UUIDS[0]),
        str(_PERSONA_UUIDS[1]),
    ]


async def test_channel_loop_closes_log_even_if_completion_cancelled() -> None:
    """A ``CancelledError`` raised inside ``wait_for_pitch_complete``
    still closes the in-flight ``channel_ad_log`` row before propagating."""
    import asyncio

    from soshop.agent.main import ChannelLoopDeps, _run_channel_iterations

    ads = _make_ads()
    base_metadata = _make_base_metadata()
    runner = _make_runner(base_metadata)

    ended_ids: list[int] = []

    async def fake_swap_persona(*args: Any, **kwargs: Any) -> None:
        return None

    async def noop(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_started(*args: Any, **kwargs: Any) -> int:
        return 999

    async def fake_ended(_pool: Any, log_id: int) -> None:
        ended_ids.append(log_id)

    async def cancel_wait(*, handle: Any, max_wait_s: float) -> Any:  # noqa: ARG001
        raise asyncio.CancelledError

    deps = ChannelLoopDeps(
        swap_persona=fake_swap_persona,
        write_cursor=noop,
        record_ad_started=fake_started,
        record_ad_ended=fake_ended,
        load_popup=noop,
        wait_for_pitch_complete=cancel_wait,
        jitter=_zero_jitter,
    )

    with pytest.raises(asyncio.CancelledError):
        await _run_channel_iterations(
            runner,
            object(),  # type: ignore[arg-type]
            ads=ads,
            start_cursor=0,
            channel_id="channel-0",
            deps=deps,
            max_iterations=3,
        )

    # The log for the first iteration must have been closed by the
    # ``finally`` even though the completion wait raised.
    assert ended_ids == [999]


async def test_channel_loop_exits_when_session_has_closed() -> None:
    """A closed session (e.g. participant_disconnected) must break out
    of the rotation instead of looping forever on a dead handle."""
    from soshop.agent.main import ChannelLoopDeps, _run_channel_iterations

    ads = _make_ads()
    base_metadata = _make_base_metadata()
    runner = _make_runner(base_metadata)
    # Simulate the SDK's "session closed" state by dropping the
    # truthy `_activity` sentinel installed by the harness.
    runner.session._activity = None  # type: ignore[attr-defined]

    swap_count = 0

    async def fake_swap_persona(*args: Any, **kwargs: Any) -> None:
        nonlocal swap_count
        swap_count += 1

    async def noop(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_started(*args: Any, **kwargs: Any) -> int:
        return 1

    deps = ChannelLoopDeps(
        swap_persona=fake_swap_persona,
        write_cursor=noop,
        record_ad_started=fake_started,
        record_ad_ended=noop,
        load_popup=noop,
        wait_for_pitch_complete=_make_fake_completion(),
        jitter=_zero_jitter,
    )

    await _run_channel_iterations(
        runner,
        object(),  # type: ignore[arg-type]
        ads=ads,
        start_cursor=0,
        channel_id="channel-0",
        deps=deps,
        max_iterations=10,
    )

    # Loop bailed before the first swap because the liveness probe
    # short-circuited the iteration.
    assert swap_count == 0


async def test_channel_loop_advances_on_safety_timeout() -> None:
    """A ``timeout`` outcome from ``wait_for_pitch_complete`` still
    advances the rotation — that's the whole point of the safety
    ceiling. The next swap's ``session.interrupt`` is what stops the
    stuck TTS."""
    from soshop.agent.main import ChannelLoopDeps, _run_channel_iterations

    ads = _make_ads()
    base_metadata = _make_base_metadata()
    runner = _make_runner(base_metadata)

    swap_calls: list[str] = []

    async def fake_swap_persona(_runner: Any, next_md: Any, _source: str) -> None:
        swap_calls.append(next_md.persona_id)

    async def noop(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_started(*args: Any, **kwargs: Any) -> int:
        return 1

    deps = ChannelLoopDeps(
        swap_persona=fake_swap_persona,
        write_cursor=noop,
        record_ad_started=fake_started,
        record_ad_ended=noop,
        load_popup=noop,
        wait_for_pitch_complete=_make_fake_completion(reason="timeout", elapsed_s=45.0),
        jitter=_zero_jitter,
    )

    await _run_channel_iterations(
        runner,
        object(),  # type: ignore[arg-type]
        ads=ads,
        start_cursor=0,
        channel_id="channel-0",
        deps=deps,
        max_iterations=3,
    )

    # Even with every wait returning "timeout", the rotation advanced
    # through all three ads.
    assert swap_calls == [str(u) for u in _PERSONA_UUIDS]


async def test_channel_loop_fires_auto_dismiss_on_natural_completion() -> None:
    """The orchestrator emits a server-side dismiss after every natural
    completion so the client's compact pill clears before the next ad's
    auto-show fires. Skipped on timeout / interrupt / session_closed —
    those are aborts and the next swap's ``interrupt(force=True)``
    supersedes any UI state.
    """
    from soshop.agent.main import ChannelLoopDeps, _run_channel_iterations

    ads = _make_ads()
    base_metadata = _make_base_metadata()
    runner = _make_runner(base_metadata)

    dismiss_calls: list[None] = []

    class _PitchToolsSpy:
        async def emit_dismiss_auto(self) -> bool:
            dismiss_calls.append(None)
            return True

    runner.pitch_tools = _PitchToolsSpy()  # type: ignore[assignment]

    async def fake_swap_persona(*args: Any, **kwargs: Any) -> None:
        return None

    async def noop(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_started(*args: Any, **kwargs: Any) -> int:
        return 1

    deps = ChannelLoopDeps(
        swap_persona=fake_swap_persona,
        write_cursor=noop,
        record_ad_started=fake_started,
        record_ad_ended=noop,
        load_popup=noop,
        wait_for_pitch_complete=_make_fake_completion(reason="natural"),
        jitter=_zero_jitter,
    )

    await _run_channel_iterations(
        runner,
        object(),  # type: ignore[arg-type]
        ads=ads,
        start_cursor=0,
        channel_id="channel-0",
        deps=deps,
        max_iterations=3,
    )

    assert len(dismiss_calls) == 3


async def test_channel_loop_skips_auto_dismiss_on_timeout() -> None:
    """Timeout outcomes are aborts — no server dismiss, the next swap's
    interrupt handles UI state."""
    from soshop.agent.main import ChannelLoopDeps, _run_channel_iterations

    ads = _make_ads()
    base_metadata = _make_base_metadata()
    runner = _make_runner(base_metadata)

    dismiss_calls: list[None] = []

    class _PitchToolsSpy:
        async def emit_dismiss_auto(self) -> bool:
            dismiss_calls.append(None)
            return True

    runner.pitch_tools = _PitchToolsSpy()  # type: ignore[assignment]

    async def fake_swap_persona(*args: Any, **kwargs: Any) -> None:
        return None

    async def noop(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_started(*args: Any, **kwargs: Any) -> int:
        return 1

    deps = ChannelLoopDeps(
        swap_persona=fake_swap_persona,
        write_cursor=noop,
        record_ad_started=fake_started,
        record_ad_ended=noop,
        load_popup=noop,
        wait_for_pitch_complete=_make_fake_completion(reason="timeout"),
        jitter=_zero_jitter,
    )

    await _run_channel_iterations(
        runner,
        object(),  # type: ignore[arg-type]
        ads=ads,
        start_cursor=0,
        channel_id="channel-0",
        deps=deps,
        max_iterations=3,
    )

    assert dismiss_calls == []
