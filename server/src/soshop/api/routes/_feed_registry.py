"""Process-local feed-session registry used by ``/feed/*`` routes.

Holds the active LiveKit dispatch state for each running feed session so
``/feed/swipe`` can locate the room and dispatch to mutate. Split out of
``soshop.api.routes.feed`` so sibling route modules can depend on
the registry without pulling in the entire route module.

Thread-safe. Deliberately not persistent: a multi-instance deployment
swaps this out for Redis or a DB-backed registry in a follow-up.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

from soshop_server.core.config import settings

PLUG_AGENT_NAME = settings.AGENT_NAME
"""Worker `agent_name` registered by the SoShop agent (see `soshop.agent.main`).

Reads `settings.AGENT_NAME` so local dev can isolate itself from a deployed
worker on the same LiveKit project by overriding `AGENT_NAME` in `.env`."""


@dataclass
class FeedSessionState:
    """Active LiveKit dispatch state for a feed session.

    Populated when the session is first created (separate endpoint owned
    by an upstream issue) and updated by `/feed/swipe` after each swap.

    In ``ping_pong`` mode the optional ``prewarm_*`` fields hold
    the sibling room that is being warmed for the *next* swipe. They are
    ``None`` in ``single_room`` mode and on the first swipe of a
    ping-pong session (before the first pre-warm has been kicked off).
    """

    session_id: str
    user_id: str
    room_name: str
    dispatch_id: str
    persona_id: str
    prewarm_room_name: str | None = None
    prewarm_dispatch_id: str | None = None
    prewarm_persona_id: str | None = None


class FeedSessionRegistry:
    """Process-local map of `session_id -> FeedSessionState`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, FeedSessionState] = {}

    def get(self, session_id: str) -> FeedSessionState | None:
        with self._lock:
            return self._sessions.get(session_id)

    def upsert(self, state: FeedSessionState) -> None:
        with self._lock:
            self._sessions[state.session_id] = state

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


_FEED_REGISTRY = FeedSessionRegistry()


def get_feed_session_registry() -> FeedSessionRegistry:
    """FastAPI dependency for the process-wide session registry."""
    return _FEED_REGISTRY


Registry = Annotated[FeedSessionRegistry, Depends(get_feed_session_registry)]
