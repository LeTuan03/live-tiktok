"""Server → agent control-message helper.

Wraps ``LiveKitAPI.room.send_data`` so the FastAPI route layer can push a
JSON control payload onto the ``agent_control`` topic that the agent
process listens on (see ``soshop.agent.main.install_agent_control_handler``).

Used by ``/feed/swipe`` to drive the in-place persona swap without
re-dispatching the agent: instead of evicting the worker via
``delete_dispatch``, the route sends a ``{"action": "swap_persona", ...}``
data packet that the running agent consumes to mutate its own session in
place.

Imports of ``livekit.api`` / ``livekit.protocol`` are deferred so this
module remains importable in unit tests that don't pull in the runtime.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from soshop.livekit.dispatch import AGENT_CONTROL_TOPIC

if TYPE_CHECKING:
    from livekit.api import LiveKitAPI

__all__ = ["send_agent_control"]

logger = logging.getLogger("soshop.livekit.control")


async def send_agent_control(
    api_client: LiveKitAPI,
    *,
    room: str,
    payload: dict[str, Any],
    destination_identities: list[str] | None = None,
) -> None:
    """Send a JSON control message to the agent listening in ``room``.

    Delivery is reliable and topic-scoped to ``AGENT_CONTROL_TOPIC``;
    other in-room participants ignore unknown topics, so leaving
    ``destination_identities`` empty (broadcast) is safe and avoids
    threading the agent's participant identity through the route layer.

    Raises whatever ``LiveKitAPI`` raises on transport failure — callers
    decide whether to surface a 5xx or fall back to the legacy redispatch
    path.
    """
    if not room:
        raise ValueError("room is required")
    if not isinstance(payload, dict):
        raise TypeError("payload must be a JSON-serializable dict")

    from livekit.api import SendDataRequest
    from livekit.protocol.models import DataPacket

    body = json.dumps(payload).encode("utf-8")
    request = SendDataRequest(
        room=room,
        data=body,
        kind=DataPacket.Kind.RELIABLE,
        topic=AGENT_CONTROL_TOPIC,
        destination_identities=list(destination_identities or []),
    )
    try:
        await api_client.room.send_data(request)
    except Exception:
        logger.exception(
            "agent_control_send_failed",
            extra={
                "event": "agent_control_send_failed",
                "room": room,
                "topic": AGENT_CONTROL_TOPIC,
                "action": payload.get("action"),
            },
        )
        raise
