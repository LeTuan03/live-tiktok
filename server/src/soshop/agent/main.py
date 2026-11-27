"""SoShop agent entrypoint.

Run as a LiveKit Agents worker. On each job assignment we read the dispatch
metadata, validate it via `DispatchMetadata`, build the persona prompt and
the LLM tool list (signal_product_pitch / signal_pitch_end), then connect
the room.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from livekit.agents import llm

# livekit-agents requires plugins to register on the main thread. The job
# entrypoint can run off it (it does on Windows), so the lazy plugin imports
# in `build_agent_session` would raise there. Importing this module happens on
# the main thread of every job process, so register them here. The plugins
# ship only in the `agent` dependency group, hence the ImportError guard.
with contextlib.suppress(ImportError):
    from livekit.plugins import elevenlabs, groq, silero  # type: ignore[attr-defined]  # noqa: F401

from soshop.agent.channel_loop import (
    CHANNEL_LOOP_SWAP_SOURCE,
    ChannelLoopDeps,
    _ad_to_dispatch_metadata,
    _channel_loop_iteration,
    _load_active_ads,
    _record_channel_ad_ended,
    _record_channel_ad_started,
    _run_channel_iterations,
    _write_channel_cursor,
    run_channel_loop,
)
from soshop.agent.control import (
    _decode_data_packet,
    _dispatch_control_message,
    install_agent_control_handler,
)
from soshop.agent.lifecycle import (
    DWELL_REASON_AUTO_HANDOFF,
    SessionCapConfig,
    SessionLifecycle,
)
from soshop.agent.loaders import (
    load_persona_voice_id as _db_load_persona_voice_id,
)
from soshop.agent.runner import (
    OPENING_LINE_INSTRUCTIONS,
    OPENING_LINE_USER_CUE,
    PERSONA_SWAP_TOPIC,
    AgentRunner,
    AgentRuntimeContext,
    _format_product_context,
    _session_running,
    _strip_template_header,
    build_agent_session,
    build_agent_tools,
    build_lifecycle_hooks,
    build_pitch_tools,
    build_runtime_context,
    load_persona_profile,
    load_persona_prompt,
    load_product_context,
    load_script_body,
    maybe_start_avatar,
    maybe_start_still_avatar,
)
from soshop.agent.schemas import (
    DispatchMetadata,
    DispatchMetadataValidationError,
    PopupPayload,
    parse_dispatch_metadata,
)
from soshop.agent.swap import (
    _announce_persona_swap,
    _execute_swap_persona,
    _interrupt_current_speech,
    _kickoff_opening_line,
    _reset_chat_history,
    _swap_avatar_and_announce,
    _swap_avatar_image,
    _swap_tts_voice,
)
from soshop.agent.tools import PRODUCT_PITCH_TOPIC
from soshop.livekit.lemonslice_control import lemonslice_update_image
from soshop_server.core.config import settings
from soshop_server.db.pool import get_pool

if TYPE_CHECKING:
    from livekit.agents import JobContext

logger = logging.getLogger("soshop.agent")

# How long the companion entrypoint waits for the browser's `viewer_ready`
# signal before delivering the opening pitch anyway. The wait begins only after
# the avatar warmup (post `session.start`), and the session cost cap is ~60s
# counting from session start, so keep this short enough that the fallback
# pitch still fits comfortably inside the cap. In the normal path the signal
# arrives during the warmup and this never elapses.
_VIEWER_READY_TIMEOUT_S = 20.0


__all__ = [
    "CHANNEL_LOOP_SWAP_SOURCE",
    "DWELL_REASON_AUTO_HANDOFF",
    "OPENING_LINE_INSTRUCTIONS",
    "PERSONA_SWAP_TOPIC",
    "PRODUCT_PITCH_TOPIC",
    "AgentRunner",
    "AgentRuntimeContext",
    "ChannelLoopDeps",
    "DispatchMetadata",
    "DispatchMetadataValidationError",
    "PopupPayload",
    "SessionCapConfig",
    "SessionLifecycle",
    "_ad_to_dispatch_metadata",
    "_announce_persona_swap",
    "_channel_loop_iteration",
    "_db_load_persona_voice_id",
    "_decode_data_packet",
    "_dispatch_control_message",
    "_execute_swap_persona",
    "_format_product_context",
    "_interrupt_current_speech",
    "_kickoff_opening_line",
    "_load_active_ads",
    "_record_channel_ad_ended",
    "_record_channel_ad_started",
    "_reset_chat_history",
    "_run_channel_iterations",
    "_session_running",
    "_strip_template_header",
    "_swap_avatar_and_announce",
    "_swap_avatar_image",
    "_swap_tts_voice",
    "_write_channel_cursor",
    "build_agent_session",
    "build_agent_tools",
    "build_lifecycle_hooks",
    "build_pitch_tools",
    "build_runtime_context",
    "entrypoint",
    "get_pool",
    "install_agent_control_handler",
    "lemonslice_update_image",
    "llm",
    "load_persona_profile",
    "load_persona_prompt",
    "load_product_context",
    "load_script_body",
    "main",
    "maybe_start_avatar",
    "parse_dispatch_metadata",
    "run_channel_loop",
    "settings",
]


async def entrypoint(ctx: JobContext) -> None:
    """LiveKit Agents job entrypoint.

    Reads the dispatch metadata, builds the runtime context, constructs
    the AgentSession with VAD/STT/LLM/TTS, starts the LemonSlice avatar
    when configured, connects the room, and starts the session.
    """
    raw_metadata = ctx.job.metadata or ""
    try:
        metadata = parse_dispatch_metadata(raw_metadata)
    except DispatchMetadataValidationError:
        logger.exception(
            "invalid dispatch metadata; shutting down job",
            extra={"job_id": ctx.job.id},
        )
        ctx.shutdown(reason="invalid dispatch metadata")
        return

    if metadata.live_session_id is not None:
        from soshop.agent.live_host import run_live_session

        logger.info(
            "agent mode: studio livestream",
            extra={"mode": "live", "live_session_id": metadata.live_session_id},
        )
        await run_live_session(ctx, metadata)
        return

    if metadata.channel_id is not None:
        logger.info(
            "agent mode: channel broadcast loop",
            extra={
                "mode": "channel",
                "channel_id": metadata.channel_id,
                "ad_start_index": metadata.ad_start_index,
                "session_id": metadata.session_id,
                "lemonslice_idle_timeout": settings.LEMONSLICE_IDLE_TIMEOUT,
            },
        )
        await run_channel_loop(ctx, metadata)
        return

    logger.info(
        "agent mode: 1:1 companion dispatch",
        extra={"mode": "companion", "session_id": metadata.session_id},
    )

    pool = await get_pool()
    runtime = await build_runtime_context(metadata, pool=pool)
    voice_id, avatar_image_url = await _db_load_persona_voice_id(pool, metadata.persona_id)
    cap_config = SessionCapConfig.from_env()
    logger.info(
        "agent runtime context loaded",
        extra={
            "session_id": runtime.metadata.session_id,
            "persona_id": runtime.metadata.persona_id,
            "product_id": runtime.metadata.product_id,
            "script_id": runtime.metadata.script_id,
            "pitch_window_ms": runtime.metadata.pitch_window_ms,
            "has_popup_payload": runtime.metadata.popup_payload is not None,
            "voice_id": voice_id,
            "avatar_configured": bool(avatar_image_url),
            "pitch_topic": PRODUCT_PITCH_TOPIC,
            "session_cap_ms": cap_config.cap_ms,
            "session_wrap_lead_ms": cap_config.wrap_lead_ms,
            "auto_handoff_reason": DWELL_REASON_AUTO_HANDOFF,
        },
    )

    pitch_tools = build_pitch_tools(ctx, runtime)
    session = await build_agent_session(ctx, runtime, voice_id=voice_id)

    # The avatar publisher must be wired BEFORE the agent session starts
    # so it claims the audio sink before the first TTS frame is emitted.
    lemonslice_session_id = await maybe_start_avatar(
        ctx, session, avatar_image_url=avatar_image_url
    )

    await ctx.connect()
    still_avatar = await maybe_start_still_avatar(ctx, session, avatar_image_url=avatar_image_url)

    from soshop.agent.pitch_agent import PitchAgent

    agent_tools: list[llm.Tool | llm.Toolset] = list(
        build_agent_tools(ctx, runtime, pitch_tools=pitch_tools)
    )
    agent = PitchAgent(instructions=runtime.persona_prompt, tools=agent_tools)

    runner = AgentRunner(
        ctx=ctx,
        session=session,
        agent=agent,
        pitch_tools=pitch_tools,
        runtime=runtime,
        lemonslice_session_id=lemonslice_session_id,
        still_avatar=still_avatar,
    )
    install_agent_control_handler(runner)

    lifecycle = SessionLifecycle(
        config=cap_config,
        hooks=build_lifecycle_hooks(ctx, runtime),
    )
    ctx.add_shutdown_callback(lifecycle.aclose)
    lifecycle.start()

    await session.start(agent, room=ctx.room)

    # A one-shot preview (the public /customize page) must not deliver its
    # single pitch while the viewer's browser is still muted: it would play to
    # nobody during the avatar warmup and never repeat. When the dispatch asks
    # for it, hold the opening turn until the browser sends `viewer_ready` on
    # the agent_control topic (it has unlocked audio playback via a user
    # gesture). Fall back to speaking after a timeout so a viewer who never
    # unlocks audio still sees the host try, rather than a forever-silent
    # avatar. Other 1:1 dispatches leave the flag false and speak immediately.
    if runtime.metadata.await_viewer_ready:
        try:
            await asyncio.wait_for(runner.viewer_ready.wait(), timeout=_VIEWER_READY_TIMEOUT_S)
            logger.info(
                "entrypoint: viewer ready, delivering opening pitch",
                extra={
                    "session_id": runtime.metadata.session_id,
                    "persona_id": runtime.metadata.persona_id,
                },
            )
        except TimeoutError:
            logger.warning(
                "entrypoint: viewer_ready timed out; delivering pitch anyway",
                extra={
                    "session_id": runtime.metadata.session_id,
                    "persona_id": runtime.metadata.persona_id,
                },
            )

    # Kick off the persona's opening monologue. `session.start` boots the
    # pipeline but does not speak — the LLM needs an explicit turn. We
    # fire-and-forget so the entrypoint returns and the lifecycle timers
    # start ticking; the SpeechHandle runs in the background. Matches the
    # in-place swap path in `_execute_swap_persona`, which also calls
    # `generate_reply` after re-binding the persona.
    try:
        # `tool_choice="none"` keeps the LLM to a single pitch turn.
        # See the corresponding call in `swap.py::_kickoff_opening_line`
        # for the full rationale — the popup show/dismiss is fully
        # server-owned and the LLM doesn't need to call tools.
        session.generate_reply(
            instructions=OPENING_LINE_INSTRUCTIONS,
            user_input=OPENING_LINE_USER_CUE,
            tool_choice="none",
        )
    except Exception:
        logger.exception(
            "entrypoint: failed to kick off opening monologue",
            extra={
                "session_id": runtime.metadata.session_id,
                "persona_id": runtime.metadata.persona_id,
            },
        )

    # Queue the deterministic product-card show. The auto-show timer
    # fires ~2.5s after the pitch starts, guaranteeing the card lands
    # mid-pitch even when the LLM forgets to call `signal_product_pitch`
    # — and deferring an over-eager LLM call back to that same target.
    # See `PitchTools.schedule_auto_show` for the contract.
    pitch_tools.schedule_auto_show()


def main() -> None:
    """CLI entry: `python -m soshop.agent.main`.

    The livekit-agents worker reads `LIVEKIT_URL` / `LIVEKIT_API_KEY` /
    `LIVEKIT_API_SECRET` from `os.environ` directly (see
    `livekit/agents/worker.py:600`), not through our pydantic
    `Settings`. The plugin constructors (`groq.LLM`, `elevenlabs.TTS`,
    `lemonslice.AvatarSession`) do the same for `GROQ_API_KEY`,
    `ELEVEN_API_KEY`, `LEMONSLICE_API_KEY`. Load `.env` into the
    process environment up front so the agent subprocess ingests the
    same secrets the API server already reads via `Settings`.

    Registers the worker under the explicit `agent_name="plug-agent"`
    so `soshop.api.routes.feed.create_feed_session` (which
    dispatches with that exact name via `create_validated_dispatch`)
    finds this worker. An unnamed worker would get implicit dispatches
    only and the explicit feed dispatch would never match.
    """
    from dotenv import load_dotenv
    from livekit.agents import WorkerOptions, cli

    # `.env` first, `.env.local` second so a local override wins.
    load_dotenv(".env")
    load_dotenv(".env.local", override=True)

    # Register under `settings.AGENT_NAME` (default "plug-agent"), the same
    # name the dispatch sites target via `PLUG_AGENT_NAME`. Reading it from
    # settings (not the FastAPI router stack) keeps the worker process light
    # while letting local dev override `AGENT_NAME` in `.env` so its dispatches
    # never leak to a deployed worker registered on the same LiveKit project.
    #
    # livekit-agents reports host CPU load to LiveKit and sheds jobs above
    # `load_threshold` (0.7 in `start` mode). LiveKit also refuses to hand a job
    # to a worker reporting full load ("no servers available"). On a single dev
    # machine that also runs the browser, Docker and the avatar renderer, the
    # CPU is routinely saturated, so every dispatch was silently dropped and the
    # stream never started. In development the worker therefore reports no load
    # and always accepts; elsewhere the threshold is configurable.
    http: dict[str, Any] = {"host": settings.AGENT_HTTP_HOST}
    if settings.AGENT_HTTP_PORT is not None:
        http["port"] = settings.AGENT_HTTP_PORT
    if settings.ENVIRONMENT == "development":
        options = WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=settings.AGENT_NAME,
            load_fnc=lambda: 0.0,
            load_threshold=float("inf"),
            **http,
        )
    else:
        options = WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=settings.AGENT_NAME,
            load_threshold=settings.AGENT_LOAD_THRESHOLD,
            **http,
        )
    cli.run_app(options)


if __name__ == "__main__":
    main()
