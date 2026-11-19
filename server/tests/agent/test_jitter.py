"""Unit tests for the organic pause between rotated pitches.

``jitter_pause`` takes its RNG and sleep callable as dependencies so a
test can drive deterministic outcomes and assert the chosen pause
duration stays inside the configured band.
"""

from __future__ import annotations

import importlib
import random

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


async def test_jitter_pause_is_inside_band() -> None:
    """100 iterations against a seeded RNG land every pause in
    ``[JITTER_LO_MS, JITTER_HI_MS]`` (300–700 ms by default)."""
    from soshop.agent.rotation.jitter import (
        JITTER_HI_MS,
        JITTER_LO_MS,
        jitter_pause,
    )

    rng = random.Random(0)  # noqa: S311 — deterministic seed for unit test
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    for _ in range(100):
        chosen = await jitter_pause(rng=rng, sleep=fake_sleep)
        assert JITTER_LO_MS / 1000 <= chosen <= JITTER_HI_MS / 1000

    assert len(sleeps) == 100
    for s in sleeps:
        assert JITTER_LO_MS / 1000 <= s <= JITTER_HI_MS / 1000


async def test_jitter_pause_rejects_invalid_band() -> None:
    """Negative or inverted bands raise so a config typo doesn't ship
    silently."""
    from soshop.agent.rotation.jitter import jitter_pause

    rng = random.Random(0)  # noqa: S311 — deterministic seed for unit test

    async def _noop(_seconds: float) -> None:
        return None

    with pytest.raises(ValueError):
        await jitter_pause(rng=rng, sleep=_noop, lo_ms=-1, hi_ms=100)
    with pytest.raises(ValueError):
        await jitter_pause(rng=rng, sleep=_noop, lo_ms=500, hi_ms=200)


async def test_default_jitter_callable_returns_value() -> None:
    """``default_jitter()`` wires production deps and returns the chosen
    pause in seconds. Smoke check that the value lands inside the band
    so the runtime contract holds even when the rotation orchestrator
    discards the return value."""
    from soshop.agent.rotation.jitter import (
        JITTER_HI_MS,
        JITTER_LO_MS,
        default_jitter,
    )

    jitter = default_jitter()
    chosen = await jitter()
    assert JITTER_LO_MS / 1000 <= chosen <= JITTER_HI_MS / 1000
