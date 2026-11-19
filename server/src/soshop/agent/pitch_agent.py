"""Custom ``Agent`` subclass that hardens the TTS boundary.

Two concerns, both handled in :meth:`PitchAgent.tts_node`:

1. **Tool-call leak filtering** (see :mod:`soshop.agent.tts_filter`). Groq's
   Llama 3.3 70B occasionally emits the pitch tools' function-call markup as
   plain text instead of through the structured function-calling channel;
   ElevenLabs would otherwise read it aloud.

2. **Dead-voice fallback.** A persona's ``voice_id`` can become unusable under
   the runtime ElevenLabs key: a Voice Library voice that was unshared or
   deleted, or a persona created against a different ElevenLabs account (the
   ``/customize`` picker surfaces shared-library voices that aren't in *our*
   account's "My Voices"). ElevenLabs rejects it with ``voice_id_does_not_exist``
   carrying a *null* ``contextId``, so the plugin can't attach it to the
   in-flight synthesis and surfaces only a generic, retryable ``"connection
   closed"`` (the specific code lands in the log, never in the exception). The
   synthesis then retry-storms and the persona stays silent for the whole
   session. We can't match on the specific error and we can't pre-validate the
   voice (a usable shared-library voice 404s on ``GET /v1/voices/{id}`` exactly
   like a dead one), so the only reliable oracle is the synthesis itself: on a
   first-audio failure we switch the live TTS to the plugin default voice and
   replay the turn, so the persona speaks in *some* voice rather than none.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterable
from typing import TYPE_CHECKING

from livekit import rtc
from livekit.agents import APIStatusError
from livekit.agents.voice import Agent
from livekit.plugins.elevenlabs.tts import DEFAULT_VOICE_ID

from soshop.agent.tts_filter import filter_tool_call_text

if TYPE_CHECKING:
    from livekit.agents import ModelSettings

__all__ = ["PitchAgent"]

logger = logging.getLogger("soshop.agent")


def _switch_tts_to_default_voice(agent: Agent) -> str | None:
    """Point the running ElevenLabs TTS at the plugin default voice.

    Returns the previous ``voice_id`` (``""`` when it can't be read) on a
    successful switch, or ``None`` when nothing was switched: no live TTS, the
    plugin exposes no ``update_options``, or the default voice is already
    active. That last case is deliberate: if the default itself is failing the
    problem is a real ElevenLabs outage or a bad key, and papering over it by
    re-switching to the same voice would just loop. Best-effort throughout, so
    the caller can fall back to re-raising the original synthesis error.

    Mirrors :func:`soshop.agent.swap._swap_tts_voice`: ``session.tts`` is typed
    as the abstract base, which declares neither ``update_options`` nor
    ``_opts``, so we read both through ``getattr`` rather than assert the
    concrete plugin type.
    """
    try:
        session = agent.session
    except Exception:
        return None
    tts = getattr(session, "tts", None)
    update_options = getattr(tts, "update_options", None) if tts is not None else None
    if not callable(update_options):
        return None
    current = getattr(getattr(tts, "_opts", None), "voice_id", None)
    if current == DEFAULT_VOICE_ID:
        return None
    try:
        update_options(voice_id=DEFAULT_VOICE_ID)
    except Exception:
        logger.exception("tts_node: failed to switch to the default voice")
        return None
    return current if isinstance(current, str) else ""


class PitchAgent(Agent):
    """Drop-in ``Agent`` that filters tool-call markup and survives a dead voice."""

    async def tts_node(
        self,
        text: AsyncIterable[str],
        model_settings: ModelSettings,
    ) -> AsyncIterable[rtc.AudioFrame]:
        # Record the filtered turn text as it streams so it can be replayed on
        # the fallback voice if the configured one can't synthesize. The happy
        # path still streams chunk-by-chunk (the buffer is only read back on
        # failure), so time-to-first-audio is unchanged.
        source = filter_tool_call_text(text)
        recorded: list[str] = []

        async def _record() -> AsyncIterable[str]:
            async for chunk in source:
                recorded.append(chunk)
                yield chunk

        produced = False
        try:
            async for frame in Agent.default.tts_node(self, _record(), model_settings):
                produced = True
                yield frame
            return
        except APIStatusError:
            # Only a failure that produced *no* audio looks like a bad voice; a
            # mid-stream drop already played frames and must not be re-spoken.
            # If we can't fall back (already on the default voice, or no live
            # TTS), re-raise so the original retry/error handling stands.
            if produced:
                raise
            previous = _switch_tts_to_default_voice(self)
            if previous is None:
                raise
            logger.warning(
                "tts_node: voice %r failed to synthesize; fell back to default "
                "voice %r and replayed the turn",
                previous,
                DEFAULT_VOICE_ID,
            )

        async def _replay() -> AsyncIterable[str]:
            for chunk in recorded:
                yield chunk

        async for frame in Agent.default.tts_node(self, _replay(), model_settings):
            yield frame
