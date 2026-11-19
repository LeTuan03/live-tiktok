"""Hermetic tests for :mod:`soshop.agent.session`.

The :func:`reconcile_channels` callable in
:mod:`soshop.livekit.channels` is monkey-patched to a recording
async stub so the suite never touches LiveKit or Postgres. Every test
gets a fresh ``AgentSessionManager`` (the module-level singleton is
reset by the autouse fixture) so state never leaks across cases.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC

import pytest

from soshop.agent import session as session_module
from soshop.agent.session import (
    AgentSessionManager,
    _reset_singleton_for_tests,
    get_session_manager,
)

CHANNEL_COUNT = 3


@pytest.fixture
def reconcile_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record every ``reconcile_channels(target)`` call as the int target.

    Patches the symbol on the upstream module so the
    ``from soshop.livekit.channels import reconcile_channels`` inside
    ``AgentSessionManager._reconcile`` picks up the stub.
    """
    calls: list[int] = []

    async def fake_reconcile(target: int) -> list[object]:
        calls.append(target)
        return []

    import soshop.livekit.channels as channels_module

    monkeypatch.setattr(channels_module, "reconcile_channels", fake_reconcile)
    return calls


@pytest.fixture(autouse=True)
def _reset_singleton() -> Iterator[None]:
    _reset_singleton_for_tests()
    yield
    _reset_singleton_for_tests()


async def test_status_defaults_to_inactive(reconcile_calls: list[int]) -> None:
    mgr = AgentSessionManager(channel_count=CHANNEL_COUNT)
    status = mgr.status()
    assert status.active is False
    assert status.expires_at is None
    assert status.seconds_remaining == 0
    assert status.target_channel_count == 0
    assert reconcile_calls == []


async def test_start_flips_active_and_reconciles_to_channel_count(
    reconcile_calls: list[int],
) -> None:
    mgr = AgentSessionManager(channel_count=CHANNEL_COUNT)
    status = await mgr.start(duration_minutes=5)
    assert status.active is True
    assert status.expires_at is not None
    assert status.target_channel_count == CHANNEL_COUNT
    assert status.seconds_remaining > 0
    assert reconcile_calls == [CHANNEL_COUNT]


async def test_second_start_cancels_prior_wind_down_and_resets_expiry(
    reconcile_calls: list[int],
) -> None:
    mgr = AgentSessionManager(channel_count=CHANNEL_COUNT)
    first = await mgr.start(duration_minutes=10)
    first_task = mgr._wind_down_task
    assert first_task is not None
    assert first.expires_at is not None

    second = await mgr.start(duration_minutes=20)
    assert second.expires_at is not None
    assert second.expires_at > first.expires_at
    # Yield once so the cancellation propagates and the prior task settles.
    await asyncio.sleep(0)
    assert first_task.cancelled() or first_task.done()
    assert mgr._wind_down_task is not None
    assert mgr._wind_down_task is not first_task
    # Two reconciles to N — one per start; no zero-target reconcile in between.
    assert reconcile_calls == [CHANNEL_COUNT, CHANNEL_COUNT]


async def test_stop_clears_state_and_reconciles_to_zero(
    reconcile_calls: list[int],
) -> None:
    mgr = AgentSessionManager(channel_count=CHANNEL_COUNT)
    await mgr.start(duration_minutes=5)
    status = await mgr.stop()
    assert status.active is False
    assert status.expires_at is None
    assert status.target_channel_count == 0
    assert reconcile_calls == [CHANNEL_COUNT, 0]


async def test_reconcile_to_target_uses_channel_count_when_active(
    reconcile_calls: list[int],
) -> None:
    mgr = AgentSessionManager(channel_count=CHANNEL_COUNT)
    await mgr.start(duration_minutes=5)
    reconcile_calls.clear()
    await mgr.reconcile_to_target()
    assert reconcile_calls == [CHANNEL_COUNT]


async def test_reconcile_to_target_uses_zero_when_inactive(
    reconcile_calls: list[int],
) -> None:
    mgr = AgentSessionManager(channel_count=CHANNEL_COUNT)
    await mgr.reconcile_to_target()
    assert reconcile_calls == [0]


async def test_wind_down_fires_after_expiry(reconcile_calls: list[int]) -> None:
    """Schedule a ~1s session and let the wind-down task drive teardown."""
    mgr = AgentSessionManager(channel_count=CHANNEL_COUNT)
    # Patch ``timedelta`` for this single start() call so the wind-down
    # sleep is ~1 second instead of 1 minute. Cleaner than time-mocking
    # ``datetime.now`` since ``status()`` reads it too.
    from datetime import datetime, timedelta

    original_now = datetime.now(UTC)
    mgr._expires_at = original_now + timedelta(seconds=1)
    # Drive the reconcile path by calling _reconcile directly via a fake
    # wind-down: emulate what start() would do without the 1-minute floor
    # in the public API.
    mgr._wind_down_task = asyncio.create_task(
        mgr._wind_down(mgr._expires_at), name="agent-session-wind-down-test"
    )
    # Mark the up reconcile as already done by start().
    reconcile_calls.append(CHANNEL_COUNT)
    await asyncio.sleep(1.5)
    assert reconcile_calls[-1] == 0
    assert mgr.status().active is False
    assert mgr._expires_at is None


async def test_singleton_returns_same_instance(reconcile_calls: list[int]) -> None:  # noqa: ARG001
    a = get_session_manager()
    b = get_session_manager()
    assert a is b


async def test_reset_singleton_helper(reconcile_calls: list[int]) -> None:  # noqa: ARG001
    a = get_session_manager()
    _reset_singleton_for_tests()
    b = get_session_manager()
    assert a is not b
    assert session_module._singleton is b
