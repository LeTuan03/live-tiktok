"""In-process change notifications for broadcast event streams.

Writers call :meth:`Hub.notify` after committing a change; each open
``/broadcasts/{id}/events`` stream waits in :meth:`Hub.wait` and re-sends the
broadcast when woken. Streams also wake on a timeout, so a change made by
another API process (which this hub cannot see) still reaches viewers, only
later.
"""

from __future__ import annotations

import asyncio
import contextlib


class Hub:
    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}

    def notify(self, key: str) -> None:
        event = self._events.pop(key, None)
        if event is not None:
            event.set()

    async def wait(self, key: str, timeout: float) -> bool:
        """Block until ``key`` changes or ``timeout`` passes. True on a change."""
        event = self._events.setdefault(key, asyncio.Event())
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(event.wait(), timeout)
            return True
        return False


hub = Hub()
