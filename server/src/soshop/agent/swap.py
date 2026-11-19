"""In-place persona-swap handler for the running SoShop agent."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from livekit.agents import llm

from soshop.agent.loaders import (
    load_persona_voice_id as _db_load_persona_voice_id,
)
from soshop.agent.runner import (
    OPENING_LINE_INSTRUCTIONS,
    OPENING_LINE_USER_CUE,
    PERSONA_SWAP_TOPIC,
    AgentRunner,
    _session_running,
    build_runtime_context,
)
from soshop.agent.schemas import DispatchMetadata
from soshop.livekit.lemonslice_control import (
    LemonsliceControlError,
    lemonslice_update_image,
)
from soshop_server.core.config import settings
from soshop_server.db.pool import get_pool

logger = logging.getLogger("soshop.agent")


async def _swap_avatar_image(
    runner: AgentRunner, *, new_avatar_url: str | None, session_id: str
) -> bool:
    """Swap the LemonSlice avatar image in place. Returns False on failure.

    Best-effort: a control-endpoint outage degrades to "old image, new
    voice/script" rather than aborting the whole swap.
    """
    if runner.still_avatar is not None:
        try:
            await runner.still_avatar.update_image(new_avatar_url)
        except Exception:
            logger.exception("persona_swap: still avatar update_image raised")
            return False
        return True
    if not (new_avatar_url and settings.LEMONSLICE_API_KEY):
        return True
    if not runner.lemonslice_session_id:
        # The avatar never started (no API key or no boot-time
        # `avatar_image_url`), so there is no LemonSlice session to
        # mutate. Surface as a warning so the silent no-op is visible
        # in dashboards instead of masquerading as success.
        logger.warning(
            "persona_swap_avatar_skipped: lemonslice_session_id unavailable",
            extra={
                "event": "persona_swap_avatar_skipped",
                "session_id": session_id,
                "new_avatar_url": new_avatar_url,
            },
        )
        return True
    try:
        await lemonslice_update_image(
            runner.lemonslice_session_id,
            new_avatar_url,
            api_key=settings.LEMONSLICE_API_KEY,
        )
    except LemonsliceControlError as exc:
        logger.warning(
            "persona_swap_avatar_update_failed",
            extra={
                "event": "persona_swap_avatar_update_failed",
                "session_id": session_id,
                "lemonslice_session_id": runner.lemonslice_session_id,
                "status_code": exc.status_code,
            },
        )
        return False
    except Exception:
        logger.exception("persona_swap: lemonslice update_image raised")
        return False
    return True


async def _swap_avatar_and_announce(
    runner: AgentRunner,
    next_metadata: DispatchMetadata,
    new_avatar_url: str | None,
) -> bool:
    """Run the LemonSlice image swap and the `persona.swapped` publish.

    Bundled into a single helper so `_execute_swap_persona` stays under
    Ruff's McCabe ceiling. Returns ``True`` when the avatar update
    succeeded (or was a no-op due to missing config); ``False`` only
    when the LemonSlice control endpoint actually failed. The announce
    publish is best-effort and never demotes the return value — a
    missed envelope is covered by the client's fallback settle timer.
    """
    avatar_ok = await _swap_avatar_image(
        runner,
        new_avatar_url=new_avatar_url,
        session_id=next_metadata.session_id,
    )
    if avatar_ok:
        await _announce_persona_swap(runner, next_metadata)
    return avatar_ok


async def _announce_persona_swap(runner: AgentRunner, next_metadata: DispatchMetadata) -> bool:
    """Publish the `persona.swapped` envelope on the `persona_swap` topic.

    Called by `_execute_swap_persona` once `lemonslice_update_image` has
    resolved. The web consumer feed listens for this envelope and
    crossfades from the persona still to the live video the moment it
    arrives; without it the client falls back to a generous settle
    timer covering LemonSlice's documented ~5s ceiling.

    Returns ``True`` when the publish succeeded so the caller can fold
    the result into ``partial_failure`` tracking without an extra
    try/except inflating the swap function's complexity past Ruff's
    McCabe threshold.
    """
    try:
        await runner.ctx.room.local_participant.send_text(
            json.dumps(
                {
                    "type": "swapped",
                    "persona_id": next_metadata.persona_id,
                    "session_id": next_metadata.session_id,
                }
            ),
            topic=PERSONA_SWAP_TOPIC,
        )
    except Exception:
        logger.exception("persona_swap: announce envelope failed")
        return False
    return True


def _swap_tts_voice(runner: AgentRunner, new_voice_id: str | None) -> bool:
    """Apply a fresh voice to the running TTS plugin.

    ``session.tts`` is typed as the abstract ``TTS`` base, which doesn't
    declare ``update_options``; the ElevenLabs plugin instance built in
    ``build_agent_session`` does. We tolerate the plugin being swapped
    under us by reading via ``getattr`` rather than asserting the type.
    Returns ``False`` on a real plugin error so the caller can mark
    ``partial_failure``; a missing ``update_options`` is treated as
    success (the channel rotation falls back to the dispatch voice).
    """
    if not new_voice_id:
        return True
    tts = runner.session.tts
    update_options = getattr(tts, "update_options", None) if tts is not None else None
    if not callable(update_options):
        logger.warning("persona_swap: tts has no update_options; skipping voice swap")
        return True
    try:
        update_options(voice_id=new_voice_id)
    except Exception:
        logger.exception("persona_swap: tts.update_options failed")
        return False
    return True


async def _interrupt_current_speech(runner: AgentRunner) -> bool:
    """Cut the previous persona's in-flight speech. Returns False on a
    failure that should mark the swap as ``partial_failure``. A closed
    session is treated as success — there is nothing to interrupt."""
    if not _session_running(runner.session):
        return True
    try:
        await runner.session.interrupt(force=True)
    except Exception:
        logger.exception("persona_swap: interrupt failed; continuing")
        return False
    return True


async def _reset_chat_history(runner: AgentRunner) -> bool:
    """Drop the previous segment's conversation from the agent's chat context.

    Without this, the new persona's first turn carries the prior
    segment's chat items — including unresolved ``signal_pitch_end``
    tool calls and references to the old ``product_id``. Groq's LLM
    surfaces that as ``APIError: Failed to call a function`` (which
    livekit-agents re-raises as a fatal ``APIConnectionError`` and
    crashes the inference task), and even when it doesn't crash, the
    LLM goes on to call ``signal_pitch_end`` with the old / hallucinated
    product id that ``PitchTools`` then rejects with a mismatch warning.

    ``Agent.update_chat_ctx`` with an empty ``ChatContext`` resets the
    conversation and re-inserts the current system prompt
    (``add_if_missing=True`` in the SDK's update_instructions helper),
    so the new persona starts from a clean slate.
    """
    update_chat_ctx = getattr(runner.agent, "update_chat_ctx", None)
    if not callable(update_chat_ctx):
        logger.warning("persona_swap: agent has no update_chat_ctx; skipping chat history reset")
        return True
    try:
        await update_chat_ctx(llm.ChatContext.empty())
    except Exception:
        logger.exception("persona_swap: chat history reset failed")
        return False
    return True


def _kickoff_opening_line(runner: AgentRunner) -> tuple[bool, Any | None]:
    """Schedule the new persona's opening turn.

    Returns ``(ok, handle)``:

    * ``ok`` is ``False`` when the kickoff failed (session closed
      mid-swap, or the SDK raised inside ``generate_reply``) — the
      swap marks itself ``partial_failure``.
    * ``handle`` is the ``SpeechHandle`` produced by ``generate_reply``,
      which the channel rotation orchestrator awaits to detect natural
      pitch completion. Stashed on ``runner.current_pitch_handle`` so
      the orchestrator can read it without re-entering swap.py.
    """
    if not _session_running(runner.session):
        logger.info("persona_swap: session closed before opening line; skipping")
        return (False, None)
    try:
        # `tool_choice="none"` keeps the LLM to a single turn for this
        # pitch. With tools enabled, the SDK auto-launches another LLM
        # step after every tool call (see
        # ``livekit.agents.voice.agent_activity._mark_generation_done``
        # → multi-step chain inside ``_pipeline_reply_task``). That
        # was the root cause of the "2-3 pitches per product" bug —
        # the LLM would speak the pitch, call ``signal_product_pitch``,
        # then the SDK would launch a fresh step where the LLM elaborated
        # or re-pitched. The popup show/dismiss is now fully
        # server-owned (``PitchTools.schedule_auto_show`` /
        # ``emit_dismiss_auto``), so the LLM has no reason to call
        # tools and we explicitly disallow it.
        handle = runner.session.generate_reply(
            instructions=OPENING_LINE_INSTRUCTIONS,
            user_input=OPENING_LINE_USER_CUE,
            tool_choice="none",
        )
    except Exception:
        logger.exception("persona_swap: generate_reply failed")
        return (False, None)
    return (True, handle)


async def _execute_swap_persona(
    runner: AgentRunner,
    next_metadata: DispatchMetadata,
    *,
    source: str,
) -> None:
    """Mutate the running agent session to a new persona in place.

    Each step is wrapped so a failure in one (e.g. LemonSlice control
    timeout) doesn't roll back earlier successful mutations — the
    overall ``persona_swap_completed`` log carries a ``partial_failure``
    flag so dashboards can flag degraded swaps. The swap is aborted
    cleanly only when the new persona's data can't be loaded from the
    database; in that case the running session keeps its current state.
    """
    async with runner.swap_lock:
        started = time.monotonic()
        prev_persona_id = runner.runtime.metadata.persona_id

        # Step 0: bail before we do any work if the session has already
        # closed (e.g. participant_disconnected). Swapping persona on a
        # dead session would just stack a wall of `RuntimeError:
        # AgentSession isn't running` failures every loop iteration.
        if not _session_running(runner.session):
            logger.info(
                "persona_swap_skipped: session closed",
                extra={
                    "event": "persona_swap_skipped",
                    "session_id": next_metadata.session_id,
                    "previous_persona_id": prev_persona_id,
                    "next_persona_id": next_metadata.persona_id,
                    "source": source,
                },
            )
            return

        # Step 1: load new context from DB. If this fails the swap is
        # aborted — we don't want to mutate any field if we can't get
        # a coherent picture of the next persona.
        try:
            pool = await get_pool()
            new_runtime = await build_runtime_context(next_metadata, pool=pool)
            new_voice_id, new_avatar_url = await _db_load_persona_voice_id(
                pool, next_metadata.persona_id
            )
        except Exception:
            logger.exception(
                "persona_swap_failed: db reload",
                extra={
                    "event": "persona_swap_failed",
                    "session_id": next_metadata.session_id,
                    "previous_persona_id": prev_persona_id,
                    "next_persona_id": next_metadata.persona_id,
                    "source": source,
                },
            )
            return

        partial_failure = False

        # Fast path: when the channel-locked seller stays the
        # same (only the product is rotating), skip the expensive
        # voice/avatar mutations. The voice id and LemonSlice image are
        # persona-scoped, so they would be set to their existing values
        # — and the avatar /control round-trip costs ~300 ms plus a
        # crossfade flicker on the client. We still update instructions
        # (the prompt embeds the new product context), reset chat
        # history (so old product references and pending tool calls
        # don't leak), and rebind tools (the product id changed).
        is_product_only_swap = prev_persona_id == next_metadata.persona_id

        # Step 2: cut current TTS so the old utterance doesn't bleed
        # into the new opening line.
        if not await _interrupt_current_speech(runner):
            partial_failure = True

        # Step 3: swap the LLM system prompt. Propagates immediately
        # to the running LLM connection (Agent.update_instructions
        # records an AgentConfigUpdate in chat history).
        try:
            await runner.agent.update_instructions(new_runtime.persona_prompt)
        except Exception:
            logger.exception("persona_swap: update_instructions failed")
            partial_failure = True

        # Step 3b: clear chat history so the new persona's first turn
        # starts from a clean conversation. The previous segment's
        # unresolved tool calls and product_id references would
        # otherwise leak into the next turn and either crash the LLM
        # stream (Groq "Failed to call a function") or produce a
        # signal_pitch_end mismatch warning.
        if not await _reset_chat_history(runner):
            partial_failure = True

        # Step 4: swap the TTS voice for the next utterance — only
        # when the persona actually changed.
        if not is_product_only_swap and not _swap_tts_voice(runner, new_voice_id):
            partial_failure = True

        # Step 5: rebind the pitch tools and refresh the LLM's tool view.
        try:
            runner.pitch_tools.rebind(
                product_id=next_metadata.product_id,
                popup_payload=next_metadata.popup_payload,
            )
            await runner.agent.update_tools(list(llm.find_function_tools(runner.pitch_tools)))
        except Exception:
            logger.exception("persona_swap: tool rebind failed")
            partial_failure = True

        # Step 6: swap the LemonSlice avatar image (best-effort) and
        # announce the swap on the `persona_swap` topic so the
        # consumer feed can crossfade from the still to the live
        # video the moment the new avatar is on stream. Skipped when
        # the persona is unchanged (same avatar, no announce needed).
        if not is_product_only_swap and not await _swap_avatar_and_announce(
            runner, next_metadata, new_avatar_url
        ):
            partial_failure = True

        # Step 7: replace runner state so subsequent swaps see fresh.
        runner.runtime = new_runtime

        # Step 8: kick off the new opening line. The ``SpeechHandle``
        # runs in the background; we stash it on ``runner`` so the
        # channel rotation orchestrator can await its completion (the
        # "swap when the LLM finishes" trigger). We pass the same
        # directive used at session boot (``OPENING_LINE_INSTRUCTIONS``);
        # a tersely-worded instruction here was producing silence
        # because the persona prompt's guardrails default to "do not
        # speak" unless a specific pitch beat is asked for.
        ok, handle = _kickoff_opening_line(runner)
        runner.current_pitch_handle = handle
        if not ok:
            partial_failure = True

        # Queue the deterministic product-card show for this segment.
        # `PitchTools.rebind` (Step 5) reset the timing state; this
        # schedules a fresh ~2.5s auto-show so the card materialises
        # mid-pitch on every channel rotation. Safe to call regardless
        # of kickoff status — the auto-show task is a no-op when no
        # popup is configured, and a failed kickoff means the next
        # rotation tick will rebind + reschedule before the timer
        # would have fired anyway. See `tools.py` for the
        # behavioral-psychology timing contract.
        runner.pitch_tools.schedule_auto_show()

        swap_duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "persona_swap_completed",
            extra={
                "event": "persona_swap_completed",
                "swap_mode": "in_place",
                "session_id": next_metadata.session_id,
                "previous_persona_id": prev_persona_id,
                "new_persona_id": next_metadata.persona_id,
                "swap_duration_ms": swap_duration_ms,
                "partial_failure": partial_failure,
                "source": source,
            },
        )
