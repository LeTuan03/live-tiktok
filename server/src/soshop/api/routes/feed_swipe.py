"""Feed persona-swap route — `/feed/swipe`.

Triggers a persona swap inside a feed session. The implementation is
selected by the ``FEED_SWAP_MODE`` env var:

* ``in_place`` (default): mutate the running agent in place via a
  ``swap_persona`` control message on the ``agent_control`` data
  channel. The agent process spans the whole feed session, no
  redispatch, no reconnect.
* ``single_room`` (legacy fallback): re-dispatch the agent inside the
  same LiveKit room; the client never reconnects.
* ``ping_pong`` (legacy fallback): the server keeps a sibling
  room pre-warmed; on swipe the client switches its LiveKit
  subscription from the active room (A) to the pre-warmed sibling
  (B), the previous A is disposed, and a fresh sibling is pre-warmed
  for the *next* swipe. The response carries ``room_name`` +
  ``livekit_token`` for the new active room.

Latency is measured and logged as ``persona_swap_completed`` for
dashboards in all modes.

Shared session/auth/registry plumbing lives in `feed.py`; this module
is wired into the top-level feed router via `include_router`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import Field

from soshop.agent.schemas import DispatchMetadata, PopupPayload
from soshop.api.routes.feed import (
    PLUG_AGENT_NAME,
    FeedSessionRegistry,
    FeedSessionState,
    LkApi,
    OptionalUserCtx,
    Registry,
    _assert_user_matches,
)
from soshop.livekit.control import send_agent_control
from soshop.livekit.dispatch import (
    AGENT_CONTROL_TOPIC,
    AgentLeaveTimeout,
    PersonaSwapResult,
    PrewarmedRoom,
    dispose_room,
    mint_room_access_token,
    prewarm_room,
    swap_persona_in_room,
)
from soshop.livekit.swap_mode import SwapMode, get_swap_mode
from soshop_server.api.deps import StrictModel
from soshop_server.core.config import settings

if TYPE_CHECKING:
    from livekit.api import LiveKitAPI

logger = logging.getLogger("soshop.api.feed")

router = APIRouter(tags=["feed"])


class SwipeNextPersona(StrictModel):
    """The persona to swap *into* on this swipe.

    Mirrors `DispatchMetadata` since these fields flow straight into the
    new dispatch's metadata. Kept separate from `SwipeRequest` so
    `session_id` / `user_id` (which come from the active session,
    not the swipe) are not duplicated.
    """

    persona_id: str = Field(min_length=1)
    product_id: str | None = None
    script_id: str = Field(min_length=1)
    pitch_window_ms: int = Field(ge=0)
    popup_payload: PopupPayload | None = None


class SwipeRequest(StrictModel):
    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    next: SwipeNextPersona
    prewarm_next: SwipeNextPersona | None = Field(
        default=None,
        description=(
            "Persona to dispatch into the freshly pre-warmed sibling room "
            "after this swipe. Only consumed in ping_pong mode; ignored in "
            "single_room mode. Clients pull the next-next item from the "
            "feed cursor and pass it here so the next swipe can be served "
            "from a hot room."
        ),
    )


class SwipeResponse(StrictModel):
    room_name: str
    dispatch_id: str
    persona_id: str
    swap_mode: Literal["in_place", "single_room", "ping_pong"] = "in_place"
    swap_duration_ms: int
    livekit_token: str | None = Field(
        default=None,
        description=(
            "JWT for the new active room. Always set in ping_pong mode "
            "(the client reconnects to a different room); null in "
            "in_place and single_room modes (the existing token stays "
            "valid because the room does not change)."
        ),
    )


def _build_dispatch_metadata(
    next_persona: SwipeNextPersona, *, session_id: str, user_id: str
) -> DispatchMetadata:
    return DispatchMetadata(
        persona_id=next_persona.persona_id,
        product_id=next_persona.product_id,
        script_id=next_persona.script_id,
        pitch_window_ms=next_persona.pitch_window_ms,
        popup_payload=next_persona.popup_payload,
        session_id=session_id,
        user_id=user_id,
    )


async def _swipe_in_place(
    *,
    state: FeedSessionState,
    body: SwipeRequest,
    registry: FeedSessionRegistry,
    api_client: LiveKitAPI,
) -> SwipeResponse:
    """In-place persona swap (default mode).

    Sends a ``swap_persona`` control message to the running agent; the
    agent mutates its own session — interrupts current TTS, swaps
    instructions / voice / pitch tools, calls the LemonSlice control
    endpoint to swap the avatar image, and starts the new opening line.
    The agent process and LiveKit room are reused across personas, so
    the client keeps its existing token.
    """
    started = _monotonic_ms_now()

    next_metadata = _build_dispatch_metadata(
        body.next,
        session_id=state.session_id,
        user_id=state.user_id,
    )

    payload = {
        "action": "swap_persona",
        "metadata": json.loads(next_metadata.to_metadata_string()),
    }
    try:
        await send_agent_control(
            api_client,
            room=state.room_name,
            payload=payload,
        )
    except Exception as exc:
        logger.exception(
            "persona_swap_failed: control message could not be delivered",
            extra={
                "event": "persona_swap_failed",
                "swap_mode": SwapMode.IN_PLACE.value,
                "session_id": state.session_id,
                "room": state.room_name,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="agent control message could not be delivered",
        ) from exc

    # The dispatch persists for the whole feed session in in-place
    # mode — only the persona id changes on each swipe.
    registry.upsert(
        FeedSessionState(
            session_id=state.session_id,
            user_id=state.user_id,
            room_name=state.room_name,
            dispatch_id=state.dispatch_id,
            persona_id=next_metadata.persona_id,
        )
    )

    swap_duration_ms = _monotonic_ms_now() - started
    logger.info(
        "persona_swap_completed",
        extra={
            "event": "persona_swap_completed",
            "swap_mode": SwapMode.IN_PLACE.value,
            "session_id": state.session_id,
            "user_id": state.user_id,
            "room": state.room_name,
            "previous_persona_id": state.persona_id,
            "new_persona_id": next_metadata.persona_id,
            "dispatch_id": state.dispatch_id,
            "swap_duration_ms": swap_duration_ms,
            "agent_control_topic": AGENT_CONTROL_TOPIC,
        },
    )

    return SwipeResponse(
        room_name=state.room_name,
        dispatch_id=state.dispatch_id,
        persona_id=next_metadata.persona_id,
        swap_mode=SwapMode.IN_PLACE.value,
        swap_duration_ms=swap_duration_ms,
        livekit_token=None,
    )


async def _swipe_single_room(
    *,
    state: FeedSessionState,
    body: SwipeRequest,
    registry: FeedSessionRegistry,
    api_client: LiveKitAPI,
) -> SwipeResponse:
    """Single-room re-dispatch swap."""
    next_metadata = _build_dispatch_metadata(
        body.next,
        session_id=state.session_id,
        user_id=state.user_id,
    )

    try:
        result: PersonaSwapResult = await swap_persona_in_room(
            api_client,
            agent_name=PLUG_AGENT_NAME,
            room=state.room_name,
            next_metadata=next_metadata,
            current_dispatch_id=state.dispatch_id,
        )
    except AgentLeaveTimeout as exc:
        logger.error(
            "persona_swap_failed: previous agent did not vacate room %s",
            state.room_name,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="previous agent did not vacate the room in time",
        ) from exc

    registry.upsert(
        FeedSessionState(
            session_id=state.session_id,
            user_id=state.user_id,
            room_name=state.room_name,
            dispatch_id=result.new_dispatch.id,
            persona_id=next_metadata.persona_id,
        )
    )

    logger.info(
        "persona_swap_completed",
        extra={
            "event": "persona_swap_completed",
            "swap_mode": SwapMode.SINGLE_ROOM.value,
            "session_id": state.session_id,
            "user_id": state.user_id,
            "room": state.room_name,
            "previous_persona_id": state.persona_id,
            "new_persona_id": next_metadata.persona_id,
            "previous_dispatch_id": state.dispatch_id,
            "new_dispatch_id": result.new_dispatch.id,
            "swap_duration_ms": result.swap_duration_ms,
            "agent_control_topic": AGENT_CONTROL_TOPIC,
        },
    )

    return SwipeResponse(
        room_name=state.room_name,
        dispatch_id=result.new_dispatch.id,
        persona_id=next_metadata.persona_id,
        swap_mode=SwapMode.SINGLE_ROOM.value,
        swap_duration_ms=result.swap_duration_ms,
        livekit_token=None,
    )


async def _swipe_ping_pong(
    *,
    state: FeedSessionState,
    body: SwipeRequest,
    registry: FeedSessionRegistry,
    api_client: LiveKitAPI,
) -> SwipeResponse:
    """Ping-pong sibling-room swap.

    Two paths:

    * **Hot path** — a sibling room is already pre-warmed and its persona
      matches ``body.next``. Promote it to active, dispose the previous
      active room in the background, mint a new join token, and kick off
      pre-warming the next sibling using ``body.prewarm_next``.
    * **Cold path** — no matching pre-warm (first swipe in a session, or
      the persona changed). Pre-warm a fresh sibling for ``body.next``,
      promote it as active, dispose the previous active room. The first
      swipe pays the full dispatch cost; subsequent swipes ride the hot
      path.
    """
    started = _monotonic_ms_now()

    next_metadata = _build_dispatch_metadata(
        body.next,
        session_id=state.session_id,
        user_id=state.user_id,
    )

    new_room, new_dispatch_id, prewarm_hit = await _resolve_ping_pong_active_room(
        state=state,
        body=body,
        api_client=api_client,
        next_metadata=next_metadata,
    )

    # Mint the join token for the new active room. The client reconnects
    # to this room as part of the swipe.
    livekit_token = mint_room_access_token(
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
        identity=state.user_id,
        room=new_room,
        metadata=next_metadata,
    )

    cleanup_tasks = _ping_pong_cleanup_tasks(
        state=state, prewarm_hit=prewarm_hit, api_client=api_client
    )

    new_prewarm = await _maybe_prewarm_next(body=body, state=state, api_client=api_client)

    # Persist the new state before logging so a concurrent swipe sees it.
    registry.upsert(
        _build_post_swap_state(
            state=state,
            new_room=new_room,
            new_dispatch_id=new_dispatch_id,
            persona_id=body.next.persona_id,
            new_prewarm=new_prewarm,
        )
    )

    swap_duration_ms = _monotonic_ms_now() - started

    logger.info(
        "persona_swap_completed",
        extra=_persona_swap_log_extra(
            state=state,
            body=body,
            new_room=new_room,
            new_dispatch_id=new_dispatch_id,
            prewarm_hit=prewarm_hit,
            new_prewarm=new_prewarm,
            swap_duration_ms=swap_duration_ms,
        ),
    )

    # Don't `await` cleanup — the response should not block on it. The
    # tasks are owned by the event loop; a strong reference is parked on
    # the registry's dead-letter list so they aren't garbage-collected
    # mid-flight.
    _track_background_tasks(cleanup_tasks)

    return SwipeResponse(
        room_name=new_room,
        dispatch_id=new_dispatch_id,
        persona_id=body.next.persona_id,
        swap_mode=SwapMode.PING_PONG.value,
        swap_duration_ms=swap_duration_ms,
        livekit_token=livekit_token,
    )


async def _resolve_ping_pong_active_room(
    *,
    state: FeedSessionState,
    body: SwipeRequest,
    api_client: LiveKitAPI,
    next_metadata: DispatchMetadata,
) -> tuple[str, str, bool]:
    """Pick the active room for this swipe; return ``(room, dispatch_id, prewarm_hit)``.

    Hot path: the pre-warmed sibling matches ``body.next.persona_id``;
    promote it. Cold path: dispatch a fresh sibling synchronously
    (paying the full dispatch latency on first swipe / persona change).
    Any stale pre-warm cleanup happens in ``_ping_pong_cleanup_tasks``.
    """
    prewarm_hit = (
        state.prewarm_room_name is not None
        and state.prewarm_dispatch_id is not None
        and state.prewarm_persona_id == body.next.persona_id
    )
    if (
        prewarm_hit
        and state.prewarm_room_name is not None
        and state.prewarm_dispatch_id is not None
    ):
        return state.prewarm_room_name, state.prewarm_dispatch_id, True

    prewarmed = await prewarm_room(
        api_client,
        agent_name=PLUG_AGENT_NAME,
        session_id=state.session_id,
        metadata=next_metadata,
    )
    return prewarmed.room, prewarmed.dispatch_id, False


def _ping_pong_cleanup_tasks(
    *,
    state: FeedSessionState,
    prewarm_hit: bool,
    api_client: LiveKitAPI,
) -> list[asyncio.Task[object]]:
    """Spawn background dispose tasks for the previous active + stale prewarm rooms."""
    tasks: list[asyncio.Task[object]] = [
        asyncio.create_task(
            dispose_room(
                api_client,
                room=state.room_name,
                dispatch_id=state.dispatch_id,
            ),
            name=f"dispose_active_room:{state.room_name}",
        )
    ]
    if not prewarm_hit and state.prewarm_room_name and state.prewarm_dispatch_id:
        tasks.append(
            asyncio.create_task(
                dispose_room(
                    api_client,
                    room=state.prewarm_room_name,
                    dispatch_id=state.prewarm_dispatch_id,
                ),
                name=f"dispose_stale_prewarm:{state.prewarm_room_name}",
            )
        )
    return tasks


async def _maybe_prewarm_next(
    *,
    body: SwipeRequest,
    state: FeedSessionState,
    api_client: LiveKitAPI,
) -> PrewarmedRoom | None:
    """Kick off pre-warming for the *next* sibling if the client supplied one."""
    if body.prewarm_next is None:
        return None
    prewarm_metadata = _build_dispatch_metadata(
        body.prewarm_next,
        session_id=state.session_id,
        user_id=state.user_id,
    )
    return await prewarm_room(
        api_client,
        agent_name=PLUG_AGENT_NAME,
        session_id=state.session_id,
        metadata=prewarm_metadata,
    )


def _build_post_swap_state(
    *,
    state: FeedSessionState,
    new_room: str,
    new_dispatch_id: str,
    persona_id: str,
    new_prewarm: PrewarmedRoom | None,
) -> FeedSessionState:
    return FeedSessionState(
        session_id=state.session_id,
        user_id=state.user_id,
        room_name=new_room,
        dispatch_id=new_dispatch_id,
        persona_id=persona_id,
        prewarm_room_name=new_prewarm.room if new_prewarm else None,
        prewarm_dispatch_id=new_prewarm.dispatch_id if new_prewarm else None,
        prewarm_persona_id=new_prewarm.persona_id if new_prewarm else None,
    )


def _persona_swap_log_extra(
    *,
    state: FeedSessionState,
    body: SwipeRequest,
    new_room: str,
    new_dispatch_id: str,
    prewarm_hit: bool,
    new_prewarm: PrewarmedRoom | None,
    swap_duration_ms: int,
) -> dict[str, object]:
    return {
        "event": "persona_swap_completed",
        "swap_mode": SwapMode.PING_PONG.value,
        "session_id": state.session_id,
        "user_id": state.user_id,
        "room": new_room,
        "previous_room": state.room_name,
        "previous_persona_id": state.persona_id,
        "new_persona_id": body.next.persona_id,
        "previous_dispatch_id": state.dispatch_id,
        "new_dispatch_id": new_dispatch_id,
        "prewarm_hit": prewarm_hit,
        "next_prewarm_room": new_prewarm.room if new_prewarm else None,
        "next_prewarm_persona_id": new_prewarm.persona_id if new_prewarm else None,
        "swap_duration_ms": swap_duration_ms,
        "agent_control_topic": AGENT_CONTROL_TOPIC,
    }


def _monotonic_ms_now() -> int:
    import time

    return int(time.monotonic() * 1000)


# Module-level set keeping background cleanup tasks alive until they
# finish. Using a set + discard-on-done avoids the asyncio "Task was
# destroyed but it is pending!" warning when the task object is the only
# reference and gets GC'd before the loop runs it.
_BACKGROUND_TASKS: set[asyncio.Task[object]] = set()


def _track_background_tasks(tasks: list[asyncio.Task[object]]) -> None:
    for task in tasks:
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)


@router.post(
    "/feed/swipe",
    response_model=SwipeResponse,
    summary="Swap the active persona in the user's feed session",
    deprecated=True,
)
async def swipe_feed(
    body: SwipeRequest,
    user: OptionalUserCtx,
    registry: Registry,
    api_client: LkApi,
    response: Response,
) -> SwipeResponse:
    response.headers["X-Deprecated"] = "1"
    # Signed-in callers: token is the source of truth for the user.
    # Anonymous callers: the session id (minted as `anon-<hex>`) is the
    # only available correlation handle, and its ownership is checked
    # below against the registry.
    if user is not None:
        _assert_user_matches(user.user_id, body.user_id)

    state = registry.get(body.session_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="feed session not found; start a session before swiping",
        )
    # Don't allow one signed-in user's session_id to be steered by
    # another signed-in user's swipe. (Anonymous sessions are not
    # bound to an authenticated identity, so this check skips them.)
    if user is not None and state.user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="session does not belong to this user",
        )

    mode = get_swap_mode()
    if mode is SwapMode.IN_PLACE:
        return await _swipe_in_place(
            state=state,
            body=body,
            registry=registry,
            api_client=api_client,
        )
    if mode is SwapMode.PING_PONG:
        return await _swipe_ping_pong(
            state=state,
            body=body,
            registry=registry,
            api_client=api_client,
        )
    return await _swipe_single_room(
        state=state,
        body=body,
        registry=registry,
        api_client=api_client,
    )
