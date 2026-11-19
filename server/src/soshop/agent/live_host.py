"""LiveKit glue for a studio livestream: one agent job per live session.

``run_live_session`` is the entrypoint for dispatches that carry a
``live_session_id``. It builds a speech-only pipeline (LLM + TTS, no STT: a
livestream host talks to an audience and reads comments, it does not listen
to a microphone), starts the avatar, and hands control to
:class:`soshop.agent.live_director.LiveDirector`.

Room topics:

* ``agent_control`` (data packets from the API): studio commands.
* ``live_chat`` (text streams from viewers and the host): ``{"name", "text"}``.
* ``live_state`` (text streams from this agent): the current ``LiveState``.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import logging
import time
from collections import deque
from collections.abc import AsyncIterable
from typing import TYPE_CHECKING, Any

import asyncpg
from livekit import rtc
from livekit.agents import llm
from livekit.agents.voice import Agent

from soshop.agent.live_director import (
    Command,
    LiveDirector,
    LiveProduct,
    Mode,
    ProductStatus,
    SpeakResult,
)
from soshop.agent.live_prompts import (
    MARKUP_START_RE,
    HostProfile,
    ProductFacts,
    build_system_prompt,
    clean_spoken_chunk,
    clean_spoken_text,
    history_cue,
    looks_like_speech,
)
from soshop.livekit.dispatch import AGENT_CONTROL_TOPIC
from soshop.services import studio, studio_assets
from soshop_server.core.config import settings
from soshop_server.db.pool import get_pool

if TYPE_CHECKING:
    from livekit.agents import JobContext, ModelSettings
    from livekit.agents.voice import AgentSession

    from soshop.agent.schemas import DispatchMetadata

logger = logging.getLogger("soshop.agent.live")

LIVE_CHAT_TOPIC = "live_chat"
LIVE_STATE_TOPIC = "live_state"

# Completed turns kept as chat history. Small on purpose: every request
# resends it, and free LLM tiers meter input tokens per minute.
_HISTORY_TURNS = 2
_TURN_MAX_WAIT_S = 90.0
# After cutting a speech, give the audio/avatar pipeline a moment to flush and
# report the old segment as finished. Starting the next turn immediately lets
# that late report land on the new speech, which then "completes" after a word.
_INTERRUPT_SETTLE_S = 0.6
# A real turn is two to four sentences. Fewer words than this means the speech
# was cut short without being flagged as interrupted, so it is spoken again.
_MIN_SPOKEN_WORDS = 4
_VIEWER_WAIT_S = 30.0


class LiveHostAgent(Agent):
    """Agent whose TTS input is stripped of emoji and markup the LLM may emit."""

    async def tts_node(
        self,
        text: AsyncIterable[str],
        model_settings: ModelSettings,
    ) -> AsyncIterable[rtc.AudioFrame]:
        async def _clean() -> AsyncIterable[str]:
            muted = False
            async for chunk in text:
                if muted:
                    continue  # keep draining so the LLM stream finishes cleanly
                markup = MARKUP_START_RE.search(chunk)
                if markup is not None:
                    # The model switched to tool-call or JSON output. Say what
                    # came before it and stay silent for the rest of the turn.
                    chunk = chunk[: markup.start()]
                    muted = True
                cleaned = clean_spoken_chunk(chunk)
                if cleaned:
                    yield cleaned

        async for frame in Agent.default.tts_node(self, _clean(), model_settings):
            yield frame


class SessionSpeaker:
    """``Speaker`` backed by a running ``AgentSession``."""

    def __init__(self, session: AgentSession, agent: Agent) -> None:
        self._session = session
        self._agent = agent
        self._last_text: str | None = None
        # Only turns that were spoken in full go back to the model as history.
        # A reply cut by an interruption, left in the context, gets imitated:
        # the model starts answering in fragments and then in tool-call debris.
        self._history: deque[tuple[str, str]] = deque(maxlen=_HISTORY_TURNS)
        session.on("conversation_item_added", self._on_item)
        session.on("error", self._on_error)

    def _on_item(self, event: Any) -> None:
        item = getattr(event, "item", None)
        if getattr(item, "role", None) == "assistant":
            text = getattr(item, "text_content", None)
            if text:
                self._last_text = text

    def _on_error(self, event: Any) -> None:
        logger.warning(
            "live: pipeline error from %s: %s",
            type(getattr(event, "source", None)).__name__,
            getattr(event, "error", event),
        )

    async def set_instructions(self, instructions: str) -> None:
        await self._agent.update_instructions(instructions)

    async def speak(self, cue: str, interrupt: asyncio.Event) -> SpeakResult:
        self._last_text = None
        started = time.monotonic()
        try:
            await self._load_history()
            handle = self._session.generate_reply(user_input=cue)
        except Exception:
            logger.exception("live: generate_reply failed")
            return SpeakResult("failed")

        speech = asyncio.ensure_future(_await_quietly(handle))
        interrupted = asyncio.ensure_future(interrupt.wait())
        done, _ = await asyncio.wait(
            {speech, interrupted}, timeout=_TURN_MAX_WAIT_S, return_when=asyncio.FIRST_COMPLETED
        )
        interrupted.cancel()
        if speech not in done:
            was_interrupt = interrupt.is_set()
            await self._cut()
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(speech, timeout=3.0)
            return SpeakResult("interrupted" if was_interrupt else "failed")

        await asyncio.sleep(0)  # let the conversation_item_added callback land
        text = clean_spoken_text(self._last_text or "")
        if getattr(handle, "interrupted", False):
            return SpeakResult("interrupted", text)
        if len(text.split()) < _MIN_SPOKEN_WORDS or not looks_like_speech(self._last_text or ""):
            logger.warning(
                "live: turn ended after %.1fs without usable speech: %r",
                time.monotonic() - started,
                (self._last_text or "")[:120],
            )
            return SpeakResult("failed")
        self._history.append((history_cue(cue), text))
        return SpeakResult("spoken", text)

    async def _cut(self) -> None:
        try:
            await self._session.interrupt(force=True)
        except Exception:
            logger.debug("live: interrupt raised", exc_info=True)
        await asyncio.sleep(_INTERRUPT_SETTLE_S)

    async def _load_history(self) -> None:
        """Replace the agent's chat context with the last fully spoken turns."""
        ctx = llm.ChatContext.empty()
        for cue, reply in self._history:
            ctx.add_message(role="user", content=cue)
            ctx.add_message(role="assistant", content=reply)
        await self._agent.update_chat_ctx(ctx)


async def _await_quietly(handle: Any) -> None:
    try:
        await handle
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("live: speech handle raised", exc_info=True)


class PoolLiveStore:
    """``LiveStore`` backed by Postgres plus the room's ``live_state`` topic."""

    def __init__(self, pool: asyncpg.Pool, session_id: str, room: rtc.Room) -> None:
        self._pool = pool
        self._session_id = session_id
        self._room = room

    async def list_products(self) -> list[LiveProduct]:
        async with self._pool.acquire() as conn:
            rows = await studio.list_products(conn, self._session_id)
        return [
            LiveProduct(
                id=row.id,
                status=row.status,
                facts=ProductFacts(
                    name=row.name,
                    price=row.price,
                    currency=row.currency,
                    description=row.description,
                    highlights=row.highlights,
                    script=row.script,
                ),
            )
            for row in rows
        ]

    async def set_product_status(self, product_id: str, status: ProductStatus) -> None:
        async with self._pool.acquire() as conn:
            await studio.set_product_status(conn, product_id, status)

    async def reset_products(self) -> None:
        async with self._pool.acquire() as conn:
            await studio.reset_product_statuses(conn, self._session_id)

    async def publish_state(
        self, *, mode: Mode, current_product_id: str | None, last_line: str | None
    ) -> None:
        async with self._pool.acquire() as conn:
            state = await studio.write_live_state(
                conn,
                self._session_id,
                mode=mode,
                current_product_id=current_product_id,
                last_line=last_line,
            )
        try:
            await self._room.local_participant.send_text(
                state.model_dump_json(), topic=LIVE_STATE_TOPIC
            )
        except Exception:
            logger.debug("live: state broadcast failed", exc_info=True)

    async def is_live(self) -> bool:
        async with self._pool.acquire() as conn:
            session = await studio.get_session(conn, self._session_id)
        return session is not None and session.status == "live"


# ------------------------------------------------------------------ wiring


def _build_tts(voice: str) -> Any:
    from soshop.agent.edge_voice import EdgeTTS

    return EdgeTTS(default_voice=voice, voice_id=voice)


def _build_session(voice: str) -> AgentSession:
    from livekit.agents.voice import AgentSession
    from livekit.agents.voice.agent_session import SessionConnectOptions
    from livekit.plugins import groq  # type: ignore[attr-defined]

    chat_llm = groq.LLM(
        model=settings.GROQ_LLM_MODEL,
        api_key=settings.GROQ_API_KEY or "",
        # A turn is 2 to 4 sentences. Vietnamese costs more tokens per word
        # than English, so the ceiling is generous enough to never cut a
        # sentence yet still stops a runaway monologue.
        max_completion_tokens=320,
    )
    return AgentSession(
        llm=chat_llm,
        tts=_build_tts(voice),
        # A livestream must survive a burst of provider errors (rate limits).
        # The director backs off and retries instead of the session closing.
        conn_options=SessionConnectOptions(max_unrecoverable_errors=1_000_000),
    )


def _parse_control(packet: rtc.DataPacket) -> Command | None:
    """Decode a studio command from an ``agent_control`` data packet."""
    # Only the API may steer the host: server-sent packets carry no participant.
    if packet.topic != AGENT_CONTROL_TOPIC or packet.participant is not None:
        return None
    try:
        message = json.loads(packet.data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(message, dict):
        return None
    return Command(
        action=str(message.get("action") or ""),
        product_id=message.get("product_id") or None,
        text=message.get("text") or None,
    )


def _install_handlers(ctx: JobContext, director: LiveDirector) -> None:
    def _on_data(packet: rtc.DataPacket) -> None:
        command = _parse_control(packet)
        if command is not None and not director.submit(command):
            logger.info("live: unknown control action %r", command.action)

    def _on_chat(reader: rtc.TextStreamReader, participant_identity: str) -> None:
        async def _read() -> None:
            try:
                payload = json.loads(await reader.read_all())
            except Exception:
                return
            if isinstance(payload, dict):
                director.add_comment(str(payload.get("name") or ""), str(payload.get("text") or ""))

        asyncio.create_task(_read())

    ctx.room.on("data_received", _on_data)
    ctx.room.register_text_stream_handler(LIVE_CHAT_TOPIC, _on_chat)


async def _start_avatar(
    ctx: JobContext, session: AgentSession, model: studio.ModelRow
) -> tuple[Any | None, bool]:
    """Start the motion avatar when its assets exist.

    Returns ``(avatar, owns_audio)``. The lip-sync avatar publishes audio in
    sync with its own video, so the session must not publish a second audio
    track. Must run after ``ctx.connect()`` and before ``session.start()``.
    """
    # Resolved dynamically: the lip-sync package pulls optional heavy deps and
    # the live host must still run (with the still image) where it is absent.
    try:
        lipsync = importlib.import_module("soshop.agent.lipsync")
        avatar_cls = lipsync.LipSyncAvatar
        assets_ready = lipsync.avatar_assets_ready
    except (ImportError, AttributeError):
        return (None, False)
    assets = studio_assets.avatar_dir(model.id)
    if not assets_ready(assets):
        return (None, False)
    try:
        avatar = avatar_cls(ctx.room, assets_dir=assets, models_dir=studio_assets.models_dir())
        await avatar.start(session)
    except Exception:
        logger.exception("live: lip-sync avatar failed to start; falling back to still image")
        return (None, False)
    ctx.add_shutdown_callback(avatar.aclose)
    logger.info("live: lip-sync avatar started", extra={"model_id": model.id})
    return (avatar, True)


async def _start_still_avatar(ctx: JobContext, session: AgentSession, image: str | None) -> None:
    from soshop.agent.still_avatar import StillAvatar

    avatar = StillAvatar(ctx.room)
    try:
        await avatar.start(session, image_url=image)
    except Exception:
        logger.exception("live: still avatar failed to start; continuing audio-only")
        return
    ctx.add_shutdown_callback(avatar.aclose)


async def _wait_for_viewer(room: rtc.Room) -> None:
    """Hold the greeting until someone is in the room to hear it."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _VIEWER_WAIT_S
    while not room.remote_participants and loop.time() < deadline:
        await asyncio.sleep(0.25)


async def run_live_session(ctx: JobContext, metadata: DispatchMetadata) -> None:
    from livekit.agents.voice.room_io import RoomOptions

    session_id = metadata.live_session_id or ""
    pool = await get_pool()
    async with pool.acquire() as conn:
        live = await studio.get_session(conn, session_id)
        model = await studio.get_model(conn, live.model_id) if live and live.model_id else None
        products = await studio.list_products(conn, session_id)
    if live is None or model is None or live.status != "live":
        logger.error("live: session %s is not runnable; shutting down", session_id)
        ctx.shutdown(reason="live session not runnable")
        return

    profile = HostProfile(
        name=model.name,
        intro=model.intro,
        personality=model.personality,
        language=model.language,
        notes=live.notes,
    )
    session = _build_session(model.voice)
    await ctx.connect()

    _avatar, avatar_owns_audio = await _start_avatar(ctx, session, model)
    facts = [
        ProductFacts(p.name, p.price, p.currency, p.description, p.highlights, p.script)
        for p in products
    ]
    agent = LiveHostAgent(instructions=build_system_prompt(profile, facts))
    await session.start(
        agent,
        room=ctx.room,
        room_options=RoomOptions(
            text_input=False,
            audio_input=False,
            video_input=False,
            audio_output=not avatar_owns_audio,
            close_on_disconnect=False,
        ),
    )
    if not avatar_owns_audio:
        await _start_still_avatar(
            ctx, session, studio_assets.image_source_for_agent(model.image_url)
        )

    director = LiveDirector(
        speaker=SessionSpeaker(session, agent),
        store=PoolLiveStore(pool, session_id, ctx.room),
        profile=profile,
    )
    _install_handlers(ctx, director)
    logger.info(
        "live: session on air",
        extra={
            "live_session_id": session_id,
            "model_id": model.id,
            "language": model.language,
            "voice": model.voice,
            "lipsync": avatar_owns_audio,
            "products": len(products),
        },
    )

    await _wait_for_viewer(ctx.room)
    try:
        await director.run()
    finally:
        logger.info("live: session finished", extra={"live_session_id": session_id})
        ctx.shutdown(reason="live session ended")
