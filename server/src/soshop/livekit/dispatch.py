"""LiveKit agent dispatch + persona-swap helpers.

Wraps `livekit.api.AgentDispatchService.create_dispatch` with strict Pydantic
validation of the metadata payload. Callers always go through this module so
the agent process can rely on receiving a well-formed `DispatchMetadata`
JSON document.

Provides two persona-swap orchestrators used by `/feed/swipe`:

* `swap_persona_in_room` — single-room re-dispatch (default).
* `prewarm_room` + `dispose_room` — building blocks for the ping-pong
  sibling-room mode. The route layer owns the state machine; this
  module owns the LiveKit calls.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from soshop.agent.schemas import DispatchMetadata
from soshop.livekit.swap_mode import generate_sibling_room_name

if TYPE_CHECKING:
    from livekit.api import LiveKitAPI
    from livekit.protocol.agent_dispatch import AgentDispatch

__all__ = [
    "AGENT_CONTROL_TOPIC",
    "AgentLeaveTimeout",
    "PersonaSwapResult",
    "PrewarmedRoom",
    "build_dispatch_request",
    "create_validated_dispatch",
    "dispose_room",
    "mint_room_access_token",
    "prewarm_room",
    "request_agent_leave",
    "swap_persona_in_room",
    "wait_for_room_agent_empty",
]

logger = logging.getLogger("soshop.livekit.dispatch")

AGENT_CONTROL_TOPIC = "agent_control"
"""Text-stream topic the server uses to send control signals to the agent.

Currently only the `{"action": "leave"}` message is sent — see
`request_agent_leave`. The agent listens on this topic and triggers a
graceful `JobContext.shutdown()`.
"""


class AgentLeaveTimeout(RuntimeError):
    """Raised when the previous agent does not vacate the room in time."""


@dataclass(frozen=True)
class PersonaSwapResult:
    """Outcome of a single-room persona swap, used for telemetry."""

    room: str
    previous_dispatch_id: str | None
    new_dispatch: AgentDispatch
    swap_duration_ms: int


def build_dispatch_request(
    *,
    agent_name: str,
    room: str,
    metadata: DispatchMetadata,
):  # type: ignore[no-untyped-def]
    """Validate metadata and build a `CreateAgentDispatchRequest`.

    Importing `livekit.api` is deferred so this module can be unit-tested
    without the LiveKit runtime installed.
    """
    if not isinstance(metadata, DispatchMetadata):
        raise TypeError(
            "metadata must be a DispatchMetadata instance; "
            "use DispatchMetadata(...) to validate first",
        )
    if not agent_name:
        raise ValueError("agent_name is required")
    if not room:
        raise ValueError("room is required")

    from livekit.api import CreateAgentDispatchRequest

    return CreateAgentDispatchRequest(
        agent_name=agent_name,
        room=room,
        metadata=metadata.to_metadata_string(),
    )


async def create_validated_dispatch(
    api_client: LiveKitAPI,
    *,
    agent_name: str,
    room: str,
    metadata: DispatchMetadata,
) -> AgentDispatch:
    """Validate, then create an agent dispatch via the LiveKit API."""
    request = build_dispatch_request(
        agent_name=agent_name,
        room=room,
        metadata=metadata,
    )
    return await api_client.agent_dispatch.create_dispatch(request)


def mint_room_access_token(
    *,
    api_key: str,
    api_secret: str,
    identity: str,
    room: str,
    metadata: DispatchMetadata,
    ttl_seconds: int = 60 * 60,
    name: str | None = None,
) -> str:
    """Mint a signed LiveKit JWT for a participant joining `room`.

    The token's `metadata` claim carries the same `DispatchMetadata` JSON
    document used to dispatch the agent. The client surfaces these
    fields (persona, product, script, popup) in the UI so the feed can
    render the right product card before the agent's audio arrives.

    Importing `livekit.api` is deferred so this module remains importable
    without the LiveKit runtime installed (matters for unit tests).
    """
    if not api_key or not api_secret:
        raise ValueError("api_key and api_secret are required to mint a token")
    if not identity:
        raise ValueError("identity is required")
    if not room:
        raise ValueError("room is required")
    if not isinstance(metadata, DispatchMetadata):
        raise TypeError(
            "metadata must be a DispatchMetadata instance; "
            "use DispatchMetadata(...) to validate first",
        )

    from livekit.api import AccessToken, VideoGrants

    grants = VideoGrants(
        room=room,
        room_join=True,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )
    token = (
        AccessToken(api_key=api_key, api_secret=api_secret)
        .with_identity(identity)
        .with_grants(grants)
        .with_metadata(metadata.to_metadata_string())
        .with_ttl(timedelta(seconds=ttl_seconds))
    )
    if name:
        token = token.with_name(name)
    return token.to_jwt()


async def request_agent_leave(
    api_client: LiveKitAPI,
    *,
    room: str,
    dispatch_id: str,
) -> None:
    """Ask the current agent to leave the room gracefully.

    Calling `delete_dispatch` is the LiveKit-native signal for the agent
    worker to terminate its job. The worker's shutdown chain interrupts any
    in-flight TTS via the `AgentSession` and closes the participant cleanly,
    which is what gives us the "no audio overlap" guarantee from the
    acceptance criteria — by the time the room is empty of agents, the old
    TTS audio has stopped.

    `delete_dispatch` is idempotent enough for our purposes: if the dispatch
    is already gone (e.g. the agent self-shut-down on a prior call) we log
    and continue rather than failing the swipe.
    """
    if not room:
        raise ValueError("room is required")
    if not dispatch_id:
        raise ValueError("dispatch_id is required")

    try:
        await api_client.agent_dispatch.delete_dispatch(
            dispatch_id=dispatch_id,
            room_name=room,
        )
    except Exception:
        logger.warning(
            "delete_dispatch failed for dispatch_id=%s room=%s; "
            "continuing to wait for agent to vacate room",
            dispatch_id,
            room,
            exc_info=True,
        )


async def wait_for_room_agent_empty(
    api_client: LiveKitAPI,
    *,
    room: str,
    timeout_s: float = 5.0,
    poll_interval_s: float = 0.1,
) -> None:
    """Poll the room until no agent participants remain.

    "Agent participant" means `ParticipantInfo.kind == AGENT`. Standard
    user participants are ignored. The client stays in the room across
    the swap.

    Raises `AgentLeaveTimeout` if an agent is still present after
    `timeout_s` seconds. The default 5s budget leaves room for the rest of
    the ≤4s p95 swap target plus dispatch jitter.
    """
    from livekit.api import ListParticipantsRequest
    from livekit.protocol.models import ParticipantInfo

    deadline = time.monotonic() + timeout_s
    while True:
        resp = await api_client.room.list_participants(
            ListParticipantsRequest(room=room),
        )
        agents_present = [p.identity for p in resp.participants if p.kind == ParticipantInfo.AGENT]
        if not agents_present:
            return
        if time.monotonic() >= deadline:
            raise AgentLeaveTimeout(
                f"agent participants {agents_present!r} still in room {room!r} after {timeout_s}s"
            )
        await asyncio.sleep(poll_interval_s)


async def swap_persona_in_room(
    api_client: LiveKitAPI,
    *,
    agent_name: str,
    room: str,
    next_metadata: DispatchMetadata,
    current_dispatch_id: str | None,
    leave_timeout_s: float = 5.0,
    poll_interval_s: float = 0.1,
) -> PersonaSwapResult:
    """Single-room re-dispatch persona swap (legacy fallback mode).

    1. Tell the current agent to leave (if a dispatch is registered).
    2. Wait for the room to be empty of agent participants.
    3. Dispatch the next persona's agent into the *same* room with full
       `DispatchMetadata`.
    4. Return the new dispatch + measured swap latency for dashboards.

    The client stays connected to the same room throughout: it observes
    a track unsubscribe → loading → resubscribe cycle as the old agent's
    publisher drops and the new agent's publisher comes online.
    """
    started = time.monotonic()

    if current_dispatch_id is not None:
        await request_agent_leave(
            api_client,
            room=room,
            dispatch_id=current_dispatch_id,
        )

    await wait_for_room_agent_empty(
        api_client,
        room=room,
        timeout_s=leave_timeout_s,
        poll_interval_s=poll_interval_s,
    )

    new_dispatch = await create_validated_dispatch(
        api_client,
        agent_name=agent_name,
        room=room,
        metadata=next_metadata,
    )

    swap_duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "persona_swap_completed",
        extra={
            "event": "persona_swap_completed",
            "swap_mode": "single_room",
            "room": room,
            "previous_dispatch_id": current_dispatch_id,
            "new_dispatch_id": new_dispatch.id,
            "new_persona_id": next_metadata.persona_id,
            "session_id": next_metadata.session_id,
            "swap_duration_ms": swap_duration_ms,
        },
    )

    return PersonaSwapResult(
        room=room,
        previous_dispatch_id=current_dispatch_id,
        new_dispatch=new_dispatch,
        swap_duration_ms=swap_duration_ms,
    )


# ---------------------------------------------------------------------------
# Ping-pong building blocks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrewarmedRoom:
    """A freshly-created sibling room with its agent dispatched.

    Returned by :func:`prewarm_room`; the route layer stores this on the
    session registry so the next swipe can promote it to "active".
    """

    room: str
    dispatch_id: str
    persona_id: str
    prewarm_duration_ms: int


async def prewarm_room(
    api_client: LiveKitAPI,
    *,
    agent_name: str,
    session_id: str,
    metadata: DispatchMetadata,
    room: str | None = None,
) -> PrewarmedRoom:
    """Create a sibling room and dispatch its agent (ping-pong B-room).

    Generates a unique room name (unless one is supplied — useful for
    deterministic tests) and immediately dispatches the agent into it.
    Unlike :func:`swap_persona_in_room`, there is no previous agent to
    wait on, so the call returns as soon as the dispatch is created. The
    agent's actual warm-up (LemonSlice avatar boot, TTS prewarm, etc.)
    proceeds in the background until the client connects on the next
    swipe.

    The ``session_id`` argument is used both for room-name generation and
    for telemetry, and must match ``metadata.session_id``.
    """
    if not session_id:
        raise ValueError("session_id is required")
    if metadata.session_id != session_id:
        raise ValueError(
            "metadata.session_id must match session_id "
            f"(got {metadata.session_id!r} vs {session_id!r})"
        )

    target_room = room or generate_sibling_room_name(session_id)
    started = time.monotonic()

    new_dispatch = await create_validated_dispatch(
        api_client,
        agent_name=agent_name,
        room=target_room,
        metadata=metadata,
    )

    prewarm_duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "sibling_room_prewarmed",
        extra={
            "event": "sibling_room_prewarmed",
            "swap_mode": "ping_pong",
            "session_id": session_id,
            "room": target_room,
            "dispatch_id": new_dispatch.id,
            "persona_id": metadata.persona_id,
            "prewarm_duration_ms": prewarm_duration_ms,
        },
    )

    return PrewarmedRoom(
        room=target_room,
        dispatch_id=new_dispatch.id,
        persona_id=metadata.persona_id,
        prewarm_duration_ms=prewarm_duration_ms,
    )


async def dispose_room(
    api_client: LiveKitAPI,
    *,
    room: str,
    dispatch_id: str | None,
    delete_room: bool = True,
) -> None:
    """Tear down a sibling room: stop its agent and (optionally) delete it.

    Used in two situations:

    1. Ping-pong swipe: the previously-active room (A) is disposed once
       the client has switched its subscription to the new active room.
    2. Session end / disconnect cleanup: any orphaned sibling rooms must
       be torn down so we are not paying for idle LemonSlice avatars.

    Both `delete_dispatch` and `delete_room` are best-effort: an error
    here is logged but does not raise, since the caller has typically
    already returned a successful swipe response and reraising would
    just orphan the request. Idempotent: calling on an already-deleted
    room or dispatch is a no-op.
    """
    if not room:
        raise ValueError("room is required")

    if dispatch_id:
        await request_agent_leave(api_client, room=room, dispatch_id=dispatch_id)

    if not delete_room:
        return

    try:
        from livekit.api import DeleteRoomRequest

        await api_client.room.delete_room(DeleteRoomRequest(room=room))
    except Exception:
        logger.warning(
            "delete_room failed for room=%s; orphaned room may need manual cleanup",
            room,
            exc_info=True,
        )
