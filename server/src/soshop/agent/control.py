"""Agent-control topic message routing for the running SoShop agent."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from soshop.agent.runner import AgentRunner
from soshop.agent.schemas import (
    DispatchMetadataValidationError,
    parse_dispatch_metadata,
)
from soshop.agent.swap import _execute_swap_persona
from soshop.livekit.dispatch import AGENT_CONTROL_TOPIC

if TYPE_CHECKING:
    from livekit.rtc import DataPacket, TextStreamReader

logger = logging.getLogger("soshop.agent")


async def _dispatch_control_message(runner: AgentRunner, message: dict, *, source: str) -> None:
    """Route a parsed ``agent_control`` message to its handler.

    Recognized actions:

    * ``leave`` — graceful shutdown (legacy fallback path uses this).
    * ``swap_persona`` — in-place persona mutation.

    Unknown actions are logged and ignored so a malformed control
    message never crashes a live session.
    """
    action = message.get("action")
    if action == "viewer_ready":
        # The browser has unlocked audio playback and is ready to hear the
        # pitch. The companion entrypoint awaits this before its opening turn.
        runner.viewer_ready.set()
        logger.info(
            "agent_control: viewer_ready received",
            extra={
                "session_id": runner.runtime.metadata.session_id,
                "persona_id": runner.runtime.metadata.persona_id,
                "source": source,
            },
        )
        return
    if action == "leave":
        logger.info(
            "agent_control: leave signal received — initiating graceful shutdown",
            extra={
                "session_id": runner.runtime.metadata.session_id,
                "persona_id": runner.runtime.metadata.persona_id,
                "source": source,
            },
        )
        runner.ctx.shutdown(reason="persona_swap_leave")
        return
    if action == "swap_persona":
        try:
            next_metadata = parse_dispatch_metadata(json.dumps(message.get("metadata")))
        except DispatchMetadataValidationError:
            logger.warning(
                "agent_control: swap_persona payload failed validation",
                extra={"source": source},
            )
            return
        await _execute_swap_persona(runner, next_metadata, source=source)
        return
    logger.info("agent_control: unknown action %r — ignoring", action)


def _decode_data_packet(packet: DataPacket) -> dict | None:
    """Parse a JSON dict payload from a ``data_received`` packet.

    Returns ``None`` (and logs a warning) when the topic doesn't match,
    the bytes aren't valid UTF-8 / JSON, or the decoded value isn't a
    dict — all conditions a well-formed control message satisfies.
    """
    if packet.topic != AGENT_CONTROL_TOPIC:
        return None
    try:
        message = json.loads(packet.data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        logger.warning("agent_control: ignoring malformed data-packet payload")
        return None
    if not isinstance(message, dict):
        logger.warning("agent_control: data-packet payload is not a dict")
        return None
    return message


def install_agent_control_handler(runner: AgentRunner) -> None:
    """Listen for control messages on the ``agent_control`` topic.

    Two transports converge here so both in-room participants and the
    FastAPI server can talk to a running agent:

    * **Text-stream**: in-room participants (e.g. a client sending a leave
      signal) call ``LocalParticipant.send_text(..., topic="agent_control")``.
    * **Data packets**: the server calls ``RoomService.SendData`` via
      :func:`soshop.livekit.control.send_agent_control`, which
      surfaces here as a ``data_received`` event with no participant.

    Both paths converge on :func:`_dispatch_control_message`.
    """
    ctx = runner.ctx

    def _handle_text_stream(reader: TextStreamReader, participant_identity: str) -> None:
        async def _read() -> None:
            try:
                payload = await reader.read_all()
                message = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                logger.warning(
                    "agent_control: ignoring malformed text-stream payload from %s",
                    participant_identity,
                )
                return
            except Exception:
                logger.exception("agent_control: failed to read text stream")
                return
            if not isinstance(message, dict):
                logger.warning("agent_control: text-stream payload is not a dict")
                return
            await _dispatch_control_message(
                runner, message, source=f"text-stream:{participant_identity}"
            )

        asyncio.create_task(_read())

    def _handle_data_packet(packet: DataPacket) -> None:
        message = _decode_data_packet(packet)
        if message is None:
            return
        sender = packet.participant.identity if packet.participant is not None else "server"
        asyncio.create_task(
            _dispatch_control_message(runner, message, source=f"data-packet:{sender}")
        )

    ctx.room.register_text_stream_handler(AGENT_CONTROL_TOPIC, _handle_text_stream)
    ctx.room.on("data_received", _handle_data_packet)
