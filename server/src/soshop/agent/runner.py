"""Per-dispatch runtime state and pipeline construction for the SoShop agent."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import asyncpg
from livekit.agents import llm

from soshop.agent.lifecycle import LifecycleHooks
from soshop.agent.loaders import (
    load_persona_profile as _db_load_persona_profile,
)
from soshop.agent.loaders import (
    load_product_context as _db_load_product_context,
)
from soshop.agent.loaders import (
    load_script_body as _db_load_script_body,
)
from soshop.agent.schemas import DispatchMetadata
from soshop.agent.tools import PRODUCT_PITCH_TOPIC, PitchTools
from soshop.livekit.lemonslice_control import lemonslice_start
from soshop_server.core.config import settings
from soshop_server.db.pool import get_pool

if TYPE_CHECKING:
    from livekit.agents import JobContext
    from livekit.agents.voice import Agent, AgentSession

logger = logging.getLogger("soshop.agent")

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_PERSONA_TEMPLATE_PATH = _PROMPTS_DIR / "persona_template.md"

PERSONA_SWAP_TOPIC = "persona_swap"
"""LiveKit text-stream topic the agent uses to announce that an in-place
persona swap has been accepted by LemonSlice and the new avatar is
about to render. The consumer client listens for ``{"type": "swapped",
"persona_id": ...}`` envelopes here so it can crossfade from the still
to the live video as soon as the new avatar arrives, instead of
waiting on its fallback settle timer (set generously to LemonSlice's
documented ~5s ceiling). Keep this string stable — the web client
hardcodes the same value in ``web/lib/feed/PersonaSwapEventSource.ts``.
"""


OPENING_LINE_INSTRUCTIONS = (
    "Open the segment in character. Greet the viewer in one short "
    "sentence, deliver the pitch in the prompt (the current script), then "
    "land the verbatim call-to-action. Stop speaking after the CTA — "
    "the rotation handoff is immediate. Do not call any tools."
)
"""Directive used for every persona's opening turn, both at session
boot and after an in-place swap. The swap path previously passed a
much terser instruction (``"Greet the viewer and start the new
pitch."``) which the LLM frequently honored by emitting nothing
audible at all — the new persona's prompt is full of guardrails that
suppress speech unless an explicit pitch beat is requested. Mirroring
the entrypoint's instruction keeps the swap-time opening behavior
identical to the first turn of a fresh dispatch.

The "Do not call any tools" tail is belt-and-suspenders: the kickoff
call sites pass ``tool_choice="none"`` to ``generate_reply`` so the
SDK actively suppresses tool emission, but instructing the LLM
through the prompt as well keeps the chat-context history tidy
(no spurious "I tried to call a tool" tokens) and survives the
hypothetical case where ``tool_choice`` is dropped from a future
refactor."""


OPENING_LINE_USER_CUE = "You're live. Go ahead."
"""Synthetic user turn sent with every opening line. The kickoff used to
carry only system instructions, and some chat templates on Groq (Qwen among
them) reject a request with no user message ("No user query found in
messages"). The chat history is reset on every swap, so the cue never
accumulates."""


def _session_running(session: AgentSession) -> bool:
    """Best-effort liveness check for an ``AgentSession``.

    The SDK doesn't expose a public ``is_running``; both ``interrupt``
    and ``generate_reply`` raise ``RuntimeError`` when the internal
    ``_activity`` slot is ``None`` (the canonical "session has closed"
    state — see ``livekit/agents/voice/agent_session.py``). We probe the
    same slot so the channel loop can short-circuit calls into a dead
    session instead of catching the exception every iteration.
    """
    return getattr(session, "_activity", None) is not None


@dataclass(frozen=True)
class AgentRuntimeContext:
    """Resolved context the agent runs with for a single dispatch."""

    metadata: DispatchMetadata
    persona_prompt: str
    product_context: dict | None


@dataclass
class AgentRunner:
    """Mutable runtime state for an agent process across in-place persona swaps.

    A single SoShop agent process spans one *feed session* — many personas.
    The control handler reads / mutates these fields when a
    ``swap_persona`` control message arrives, while the LLM tool
    dispatch path reads ``pitch_tools`` directly. ``swap_lock``
    serializes rapid swipes so two concurrent swaps cannot interleave
    half-mutated state.
    """

    ctx: JobContext
    session: AgentSession
    agent: Agent
    pitch_tools: PitchTools
    runtime: AgentRuntimeContext
    lemonslice_session_id: str | None
    still_avatar: Any | None = None
    """``StillAvatar`` publisher used when LemonSlice is not configured. The
    swap handler calls its ``update_image`` in place of the LemonSlice
    control endpoint."""
    swap_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    viewer_ready: asyncio.Event = field(default_factory=asyncio.Event)
    """Set when the browser signals `viewer_ready` on the agent_control topic
    (it has unlocked audio playback). The 1:1 companion entrypoint awaits this
    before the opening pitch when ``metadata.await_viewer_ready`` is set, so a
    one-shot customize preview is never spoken to a still-muted client."""
    current_pitch_handle: Any | None = None
    """``SpeechHandle`` returned by the most recent
    ``session.generate_reply`` invoked from the swap flow. The channel
    rotation reads this after each swap so it can await the new
    persona's pitch to completion (the "drive swaps off the LLM, not a
    timer" model). Reset to ``None`` only when explicit kickoff fails;
    a viewer-initiated swap overwrites it with the new turn's handle."""


def _format_product_context(product_context: dict | None) -> str:
    if not product_context:
        return "none"
    return "\n".join(f"- {key}: {value}" for key, value in product_context.items())


async def load_persona_profile(
    persona_id: str, *, pool: asyncpg.Pool | None = None
) -> dict[str, str]:
    """Resolve the per-persona identity fields used by the prompt template.

    DB-backed. Falls back to the placeholder dict on missing rows so
    the prompt template's `str.format(...)` never raises.
    """
    pool = pool or await get_pool()
    return await _db_load_persona_profile(pool, persona_id)


async def load_script_body(script_id: str, *, pool: asyncpg.Pool | None = None) -> str:
    """Resolve the script the LLM should deliver this segment.

    DB-backed. Returns ``"none"`` for missing rows / freestyle scripts —
    the prompt template treats that as "drop into freestyle mode".
    """
    pool = pool or await get_pool()
    return await _db_load_script_body(pool, script_id)


def _strip_template_header(template: str) -> str:
    """Drop the human-facing doc preamble that precedes the first ``---``.

    The template begins with a short note for editors describing how it's
    rendered. That preamble is not part of the system prompt and may itself
    contain literal ``{var}`` examples that would break ``str.format``.
    """
    parts = template.split("\n---\n", 1)
    return parts[1].lstrip() if len(parts) == 2 else template


async def load_persona_prompt(
    metadata: DispatchMetadata,
    product_context: dict | None,
    *,
    pool: asyncpg.Pool | None = None,
) -> str:
    """Render the persona system prompt for this dispatch.

    Fills the markdown template under ``prompts/persona_template.md`` using
    ``str.format``. Single-brace ``{var}`` placeholders only; optional
    fields fall back to the literal string ``"none"``.
    """
    pool = pool or await get_pool()
    body = _strip_template_header(_PERSONA_TEMPLATE_PATH.read_text(encoding="utf-8"))
    profile = await _db_load_persona_profile(pool, metadata.persona_id)
    script = await _db_load_script_body(pool, metadata.script_id)
    return body.format(
        name=profile["name"],
        backstory=profile["backstory"],
        traits=profile["traits"],
        voice_tone=profile["voice_tone"],
        product_context=_format_product_context(product_context),
        current_script=script,
    )


async def load_product_context(
    product_id: str | None, *, pool: asyncpg.Pool | None = None
) -> dict | None:
    """Load product context (title, description, price) for the pitch.

    DB-backed. Returns ``None`` when no product is bound (freestyle).
    """
    if product_id is None:
        return None
    pool = pool or await get_pool()
    return await _db_load_product_context(pool, product_id)


async def build_runtime_context(
    metadata: DispatchMetadata, *, pool: asyncpg.Pool | None = None
) -> AgentRuntimeContext:
    """Resolve persona + product context from validated dispatch metadata."""
    pool = pool or await get_pool()
    product_context = await load_product_context(metadata.product_id, pool=pool)
    persona_prompt = await load_persona_prompt(metadata, product_context, pool=pool)
    return AgentRuntimeContext(
        metadata=metadata,
        persona_prompt=persona_prompt,
        product_context=product_context,
    )


def build_pitch_tools(
    ctx: JobContext,
    runtime: AgentRuntimeContext,
) -> PitchTools:
    """Construct a single ``PitchTools`` instance bound to this room.

    The instance is reused across personas in the same feed session —
    the in-place swap handler calls ``PitchTools.rebind`` to retarget
    it. The ``send_text`` closure depends only on ``ctx.room``, which
    does not change across swaps.
    """

    async def send_text(payload: str, topic: str) -> None:
        try:
            await ctx.room.local_participant.send_text(payload, topic=topic)
        except Exception:
            logger.exception("failed to send text-stream message on topic %s", topic)

    return PitchTools(
        product_id=runtime.metadata.product_id,
        popup_payload=runtime.metadata.popup_payload,
        send_text=send_text,
    )


def build_agent_tools(
    ctx: JobContext,
    runtime: AgentRuntimeContext,
    pitch_tools: PitchTools | None = None,
) -> list[llm.FunctionTool | llm.RawFunctionTool]:
    """Build the LLM tool list for this dispatch.

    Currently registers the pitch popup tools. Future tools (product Q&A,
    catalog lookups, etc.) get added here. Accepts an optional
    pre-constructed ``PitchTools`` so the entrypoint can hold the same
    instance and rebind it across in-place persona swaps.
    """
    tools = pitch_tools if pitch_tools is not None else build_pitch_tools(ctx, runtime)
    return list(llm.find_function_tools(tools))


def build_lifecycle_hooks(
    ctx: JobContext,
    runtime: AgentRuntimeContext,
) -> LifecycleHooks:
    """Wire the lifecycle hooks for a single dispatch.

    The LLM-nudge, audio-fade, and next-persona dispatch hooks land their
    full implementations alongside the AgentSession single-room
    re-dispatch work. Until those land, the wrap nudge and fade are
    structured-logged so the timing is observable end-to-end and the
    integration points are explicit. Popup dismissal and event recording
    work today.
    """
    metadata = runtime.metadata

    async def prompt_wrapup() -> None:
        # AgentSession.generate_reply is wired by the pipeline work.
        # Until then, log the trigger so we can verify timing in dev.
        logger.info(
            "lifecycle: prompt_wrapup (LLM nudge)",
            extra={
                "session_id": metadata.session_id,
                "persona_id": metadata.persona_id,
            },
        )

    async def fade_audio() -> None:
        # TTS fade-out lands with the AgentSession wiring; log here so the
        # timing is observable today.
        logger.info(
            "lifecycle: fade_audio",
            extra={"session_id": metadata.session_id},
        )

    async def dismiss_popup() -> None:
        # No-op if no popup was ever shown; a client treats an unmatched
        # dismiss as a no-op (see PitchTools.signal_pitch_end).
        if metadata.product_id is None:
            return
        message = json.dumps({"type": "dismiss", "product_id": metadata.product_id})
        try:
            await ctx.room.local_participant.send_text(message, topic=PRODUCT_PITCH_TOPIC)
        except Exception:
            logger.exception(
                "lifecycle: dismiss_popup failed",
                extra={"session_id": metadata.session_id},
            )

    async def record_dwell(reason: str) -> None:
        logger.info(
            "persona_dwelled",
            extra={
                "event": "persona_dwelled",
                "session_id": metadata.session_id,
                "persona_id": metadata.persona_id,
                "user_id": metadata.user_id,
                "reason": reason,
            },
        )

    async def dispatch_next() -> None:
        # The single-room re-dispatch flow owns this; this hook will call into
        # that flow once it lands. Logged today so the trigger is visible.
        logger.info(
            "lifecycle: dispatch_next (single-room handoff)",
            extra={
                "session_id": metadata.session_id,
                "persona_id": metadata.persona_id,
            },
        )

    return LifecycleHooks(
        prompt_wrapup=prompt_wrapup,
        fade_audio=fade_audio,
        dismiss_popup=dismiss_popup,
        record_dwell=record_dwell,
        dispatch_next=dispatch_next,
    )


async def build_agent_session(
    ctx: JobContext,
    runtime: AgentRuntimeContext,
    *,
    voice_id: str | None,
) -> AgentSession:
    """Construct an `AgentSession` with the full LLM/STT/TTS/VAD stack.

    Plugin choices:
      - VAD: silero (`livekit-plugins-silero`)
      - STT: groq Whisper turbo
      - LLM: groq, model from ``settings.GROQ_LLM_MODEL``
      - TTS: ElevenLabs when ``ELEVEN_API_KEY`` is set, otherwise the keyless
        ``EdgeTTS`` fallback. ElevenLabs pins ``model="eleven_turbo_v2_5"`` for low
        time-to-first-audio (~250 ms; matches the persona row's
        ``voice_pipeline_id``). Pinning insulates the agent from a
        future livekit-plugins-elevenlabs default change. ``voice_id``
        comes from the persona row, falling back to the plugin default
        when missing so a misconfigured persona still speaks rather
        than crashing the dispatch.

    Tools are registered on the `Agent` (not the session) so that
    `Agent.update_tools` during an in-place persona swap fully replaces
    the LLM's tool view. LiveKit's `AgentActivity.tools` concatenates
    `session.tools + agent.tools`, so holding tools in both places
    causes a `duplicate function name` `ValueError` on the next LLM
    turn after a swap.
    """
    from livekit.agents.voice import AgentSession
    from livekit.plugins import elevenlabs, groq, silero  # type: ignore[attr-defined]

    vad = silero.VAD.load()
    stt = groq.STT(model="whisper-large-v3-turbo", api_key=settings.GROQ_API_KEY or "")
    # `max_completion_tokens` is a hard ceiling on response length. The
    # rotation targets ~30-45 spoken words per pitch (~15s at ElevenLabs'
    # ~150 WPM), and Llama 3.3 70B without a ceiling will pad to several
    # hundred words per turn — the LLM emits a hook, then a long body,
    # then the CTA, then a "you might also like..." aside, until the
    # 45 s safety ceiling on the orchestrator force-rotates and cuts
    # the audio mid-sentence. ~120 tokens is roughly 80-90 words at the
    # plain-English token-to-word ratio (1.3-1.5×), giving the LLM headroom
    # for hook + script + CTA + a short greeting without leaving room for
    # a second pitch attempt within the same turn.
    chat_llm = groq.LLM(
        model=settings.GROQ_LLM_MODEL,
        api_key=settings.GROQ_API_KEY or "",
        max_completion_tokens=120,
    )
    tts: Any
    if settings.ELEVEN_API_KEY:
        tts_kwargs: dict[str, object] = {
            "api_key": settings.ELEVEN_API_KEY,
            "model": "eleven_turbo_v2_5",
        }
        if voice_id:
            tts_kwargs["voice_id"] = voice_id
        tts = elevenlabs.TTS(**tts_kwargs)  # type: ignore[arg-type]
    else:
        # No ElevenLabs key: fall back to the keyless Edge voices so the
        # demo still speaks. See ``soshop.agent.edge_voice``.
        from soshop.agent.edge_voice import EdgeTTS

        tts = EdgeTTS(default_voice=settings.EDGE_TTS_VOICE, voice_id=voice_id)
        logger.info("tts fallback: edge voice %s (ELEVEN_API_KEY unset)", tts.voice)

    session: AgentSession = AgentSession(
        vad=vad,
        stt=stt,
        llm=chat_llm,
        tts=tts,
    )
    return session


async def maybe_start_avatar(
    ctx: JobContext,
    session: AgentSession,
    *,
    avatar_image_url: str | None,
    idle_timeout: int | None = None,
) -> str | None:
    """Start the LemonSlice avatar publisher when configured.

    The avatar plugin publishes the full-screen video track that the
    client renders. Without it the agent still speaks (audio-only) so a
    missing image url or LemonSlice key degrades gracefully rather
    than blocking the dispatch.

    ``idle_timeout`` is forwarded to the plugin when supplied. The 1:1
    companion path leaves it ``None`` so the plugin's default (a few
    minutes of silence and the session expires) applies. The channel
    broadcast loop passes ``settings.LEMONSLICE_IDLE_TIMEOUT`` (``-1``)
    so the avatar never expires while waiting for the next ad.

    Returns the LemonSlice session id when the avatar is started, or
    ``None`` when avatar publishing is disabled (missing API key or
    image url). Callers store the value on
    ``AgentRunner.lemonslice_session_id`` so a subsequent in-place
    persona swap can target the running session via
    :func:`lemonslice_update_image`.
    """
    if not (settings.LEMONSLICE_API_KEY and avatar_image_url):
        logger.info(
            "avatar disabled: LEMONSLICE_API_KEY=%s avatar_image_url=%s",
            bool(settings.LEMONSLICE_API_KEY),
            bool(avatar_image_url),
        )
        return None
    return await lemonslice_start(
        ctx,
        session,
        avatar_image_url=avatar_image_url,
        api_key=settings.LEMONSLICE_API_KEY,
        idle_timeout=idle_timeout,
    )


async def maybe_start_still_avatar(
    ctx: JobContext,
    session: AgentSession,
    *,
    avatar_image_url: str | None,
) -> Any | None:
    """Publish the persona image as video when LemonSlice is not configured.

    Must run after ``ctx.connect()`` (it publishes on the agent's own
    participant). Returns ``None`` when LemonSlice owns the video track.
    A failure here degrades to audio-only rather than failing the dispatch.
    """
    if settings.LEMONSLICE_API_KEY:
        return None
    from soshop.agent.still_avatar import StillAvatar

    avatar = StillAvatar(ctx.room)
    try:
        await avatar.start(session, image_url=avatar_image_url)
    except Exception:
        logger.exception("still avatar failed to start; continuing audio-only")
        return None
    ctx.add_shutdown_callback(avatar.aclose)
    return avatar
