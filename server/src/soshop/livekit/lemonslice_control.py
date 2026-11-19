"""HTTP helper for the LemonSlice self-managed session control endpoint.

LemonSlice exposes ``POST /liveai/sessions/{session_id}/control`` for
mutating an active avatar session without tearing it down. The
``livekit-plugins-lemonslice`` plugin we depend on only wraps the
session-creation call; this module covers the control side so the
in-place persona-swap handler in ``soshop.agent.main`` can update
the avatar image when a viewer swipes.

The HTTP helpers below have no LiveKit imports and are unit-testable
without the agent runtime. ``lemonslice_start`` is the only function in
this module that touches the plugin — it lazy-imports
``livekit.plugins.lemonslice`` so the rest of the module stays
dependency-light.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from livekit.agents import JobContext
    from livekit.agents.voice import AgentSession

__all__ = [
    "LEMONSLICE_CONTROL_BASE",
    "LemonsliceControlError",
    "lemonslice_control",
    "lemonslice_start",
    "lemonslice_terminate",
    "lemonslice_update_image",
]

logger = logging.getLogger("soshop.livekit.lemonslice_control")

LEMONSLICE_CONTROL_BASE = "https://lemonslice.com/api/liveai/sessions"
"""Base URL for the LemonSlice self-managed session API.

Matches the plugin's ``DEFAULT_API_URL`` so a session created by
``lemonslice.AvatarSession`` is reachable at
``{base}/{session_id}/control`` for mutation.
"""


class LemonsliceControlError(RuntimeError):
    """Raised when the LemonSlice control endpoint returns a non-2xx response.

    Carries ``status_code`` and ``body`` so callers can decide whether
    to retry, log, or fall back gracefully.
    """

    def __init__(self, message: str, *, status_code: int | None, body: str | None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


async def lemonslice_control(
    session_id: str,
    payload: dict[str, Any],
    *,
    api_key: str,
    base_url: str | None = None,
    timeout_s: float = 6.0,
    http_session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    """POST a control event to an active LemonSlice session.

    Per the spec the response is up to ~5 s for ``update-image``; the
    default timeout leaves a small margin on top so a slow swap doesn't
    spuriously raise.

    The caller can inject an ``aiohttp.ClientSession`` to share a
    connection pool (and to mock in tests). When omitted, a one-shot
    session is created and closed inside the call.
    """
    if not session_id:
        raise ValueError("session_id is required")
    if not isinstance(payload, dict) or "event" not in payload:
        raise ValueError("payload must be a dict containing an 'event' key")
    if not api_key:
        raise ValueError("api_key is required")

    url = f"{(base_url or LEMONSLICE_CONTROL_BASE).rstrip('/')}/{session_id}/control"
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
    }

    owns_session = http_session is None
    session = http_session or aiohttp.ClientSession()
    try:
        try:
            async with session.post(
                url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout_s),
            ) as response:
                body_text = await response.text()
                if not response.ok:
                    raise LemonsliceControlError(
                        f"lemonslice control returned {response.status}",
                        status_code=response.status,
                        body=body_text,
                    )
                # Endpoint always returns JSON per spec; tolerate empty 2xx
                # bodies by falling back to {} so callers can read .get(...)
                # without an extra try/except.
                if not body_text:
                    return {}
                return await response.json(content_type=None)
        except TimeoutError as exc:
            raise LemonsliceControlError(
                f"lemonslice control timed out after {timeout_s}s",
                status_code=None,
                body=None,
            ) from exc
        except aiohttp.ClientError as exc:
            raise LemonsliceControlError(
                f"lemonslice control transport error: {exc}",
                status_code=None,
                body=None,
            ) from exc
    finally:
        if owns_session and not session.closed:
            await session.close()


async def lemonslice_update_image(
    session_id: str,
    image_url: str,
    *,
    api_key: str,
    base_url: str | None = None,
    timeout_s: float = 6.0,
    http_session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    """Swap the avatar source image on a running LemonSlice session.

    Convenience wrapper around :func:`lemonslice_control` for the
    ``update-image`` event. The avatar continues to publish the same
    LiveKit track; only the source image rendered into that track
    changes, with a sub-second visual transition on LemonSlice's side.
    """
    if not image_url:
        raise ValueError("image_url is required")
    return await lemonslice_control(
        session_id,
        {"event": "update-image", "image_url": image_url},
        api_key=api_key,
        base_url=base_url,
        timeout_s=timeout_s,
        http_session=http_session,
    )


async def lemonslice_start(
    ctx: JobContext,
    session: AgentSession,
    *,
    avatar_image_url: str,
    api_key: str,
    idle_timeout: int | None = None,
) -> str:
    """Boot a LemonSlice avatar publisher attached to ``session``.

    Centralizes the plugin construction so both the 1:1 companion
    entrypoint and the always-on channel loop go through one helper.
    ``idle_timeout`` is threaded through to the plugin only when
    explicitly supplied — when omitted the plugin falls back to
    LemonSlice's documented default (a few minutes of silence and the
    session expires). The channel loop passes ``-1`` so the avatar
    survives between ads when no listener is in the room.

    Returns the LemonSlice session id minted by the plugin. The caller
    stashes it on the ``AgentRunner`` so a later persona swap can call
    :func:`lemonslice_update_image` against the running session.
    """
    from livekit.plugins import lemonslice  # type: ignore[attr-defined]

    # Force a 9:16 portrait output to match the consumer feed's vertical
    # viewport (TikTok / Whatnot / Reels parity). LemonSlice's default
    # render is the reference image's native ratio (~2:3 from the 368×560
    # source), which leaves either black top/bottom bars in a 9:16 frame
    # or — worse — a sideways crop when scaled to cover. Passing
    # ``aspect_ratio="9x16"`` (the literal string the plugin expects;
    # mirrors ``soshop/server/src/soshop/agent/main.py``) makes
    # LemonSlice render straight into the target frame.
    avatar_kwargs: dict[str, Any] = {
        "agent_image_url": avatar_image_url,
        "api_key": api_key,
        "aspect_ratio": "9x16",
    }
    if idle_timeout is not None:
        avatar_kwargs["idle_timeout"] = idle_timeout
    avatar = lemonslice.AvatarSession(**avatar_kwargs)
    return await avatar.start(session, room=ctx.room)


async def lemonslice_terminate(
    session_id: str,
    *,
    api_key: str,
    base_url: str | None = None,
    timeout_s: float = 6.0,
    http_session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    """Immediately end a LemonSlice avatar session.

    The avatar leaves the LiveKit room. Used by session-end cleanup so
    we stop paying for an idle avatar after the viewer disconnects.
    """
    return await lemonslice_control(
        session_id,
        {"event": "terminate"},
        api_key=api_key,
        base_url=base_url,
        timeout_s=timeout_s,
        http_session=http_session,
    )
