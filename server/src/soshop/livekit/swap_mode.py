"""Swap-mode policy for `/feed/swipe`.

Three persona-swap implementations are supported:

* ``in_place`` (default): mutate the running agent in place via a
  ``swap_persona`` control message on the ``agent_control`` data
  channel. The agent process spans the whole feed session. Implemented
  in ``soshop.livekit.control`` + ``soshop.agent.main:_execute_swap_persona``.

* ``single_room`` (legacy fallback): re-dispatch the agent inside the
  same LiveKit room. The client never reconnects. Implemented in
  ``dispatch.py`` as :func:`swap_persona_in_room`.

* ``ping_pong`` (legacy fallback): the server keeps a *sibling* room
  pre-warmed alongside the active one. On swipe, the client switches
  its LiveKit subscription from the active room (A) to the pre-warmed
  sibling (B); the server disposes A and immediately starts
  pre-warming a fresh sibling for the *next* swipe. Wins ~500ms of
  latency at ~2× LemonSlice cost.

The active mode is selected by the ``FEED_SWAP_MODE`` environment variable
and resolved by :func:`get_swap_mode`. Unknown values fall back to
``in_place`` rather than raising — operators flipping the flag should
not be able to take the API down with a typo.

This module is intentionally tiny and import-light so it can be read at
request time without pulling in the LiveKit runtime.
"""

from __future__ import annotations

import logging
import os
import secrets
from enum import StrEnum

__all__ = [
    "FEED_SWAP_MODE_ENV",
    "SwapMode",
    "generate_sibling_room_name",
    "get_swap_mode",
]

logger = logging.getLogger("soshop.livekit.swap_mode")

FEED_SWAP_MODE_ENV = "FEED_SWAP_MODE"
"""Environment variable name that selects the persona-swap implementation."""


class SwapMode(StrEnum):
    """Selected swap implementation."""

    IN_PLACE = "in_place"
    SINGLE_ROOM = "single_room"
    PING_PONG = "ping_pong"


def get_swap_mode() -> SwapMode:
    """Resolve the active swap mode from the environment.

    Re-read on every call so tests can flip the env var without a process
    restart, and so deploys can roll the flag without redeploying. Unknown
    values log a warning and fall back to ``IN_PLACE`` so a typo cannot
    disable swiping in production.
    """
    raw = os.environ.get(FEED_SWAP_MODE_ENV, "").strip().lower()
    if not raw:
        return SwapMode.IN_PLACE
    try:
        return SwapMode(raw)
    except ValueError:
        logger.warning(
            "unknown_feed_swap_mode",
            extra={
                "event": "unknown_feed_swap_mode",
                "value": raw,
                "fallback": SwapMode.IN_PLACE.value,
            },
        )
        return SwapMode.IN_PLACE


def generate_sibling_room_name(session_id: str) -> str:
    """Build a unique room name for a pre-warmed sibling room.

    Uses ``secrets.token_hex`` so two concurrent pre-warm requests for the
    same session cannot collide on a wall-clock-derived suffix. The
    session id is embedded so logs and dashboards can group sibling rooms
    back to a session without a registry lookup.
    """
    if not session_id:
        raise ValueError("session_id is required")
    return f"feed-{session_id}-{secrets.token_hex(4)}"
