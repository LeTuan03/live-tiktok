"""Organic pause between rotated pitches.

A small randomized gap (300–700ms) between pitches keeps the cadence
from feeling mechanical without producing audible dead air. RNG and
sleep are injected so tests can drive deterministic outcomes.
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

JITTER_LO_MS = 300
JITTER_HI_MS = 700


async def jitter_pause(
    *,
    rng: random.Random,
    sleep: Callable[[float], Awaitable[None]],
    lo_ms: int = JITTER_LO_MS,
    hi_ms: int = JITTER_HI_MS,
) -> float:
    """Sleep for a uniformly-random ``[lo_ms, hi_ms]`` milliseconds.

    Returns the chosen pause duration in seconds so the caller can log
    the actual pause for analytics. ``rng`` is taken as a dependency so
    tests can seed it; ``sleep`` mirrors ``asyncio.sleep`` so tests can
    swap it out without touching the event loop.
    """
    if lo_ms < 0 or hi_ms < lo_ms:
        raise ValueError(f"invalid jitter band: lo={lo_ms} hi={hi_ms}")
    ms = rng.uniform(lo_ms, hi_ms)
    seconds = ms / 1000
    await sleep(seconds)
    return seconds


def default_jitter() -> Callable[[], Awaitable[float]]:
    """Build the production zero-arg jitter callable.

    Binds ``SystemRandom`` and ``asyncio.sleep`` so the orchestrator can
    invoke ``await jitter()`` without threading the RNG through every
    call site. Returns the chosen pause duration in seconds.
    """
    rng = random.SystemRandom()

    async def _jitter() -> float:
        return await jitter_pause(rng=rng, sleep=asyncio.sleep)

    return _jitter
