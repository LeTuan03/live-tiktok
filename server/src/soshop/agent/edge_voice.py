"""Keyless TTS for the agent: Microsoft Edge read-aloud and Google Translate voices.

Used by ``build_agent_session`` and the live host when ``ELEVEN_API_KEY`` is
not configured, so the demo can speak without a paid TTS account. Both
services are unofficial endpoints with no SLA, which is fine for a local demo
and wrong for production.

Voice ids:

* Edge voices by name, e.g. ``en-US-AriaNeural`` or ``vi-VN-HoaiMyNeural``
  (the ``edge-tts`` package, streamed MP3).
* ``google-<lang>``, e.g. ``google-vi``: the Google Translate voice for that
  language (plain HTTPS GET, MP3, at most ~200 characters per request).

Persona rows may carry ElevenLabs voice ids. Those mean nothing here, so any
``voice_id`` that is not one of the forms above is ignored and the configured
default voice is used instead.

Why two services. For Vietnamese the Edge voices sound the most natural, but
the endpoint is unreliable for them: measured from one machine, between a
third and nearly all requests came back with no audio (each taking about 3 s
to fail), and sending more requests in parallel made it worse, not better.
The Edge multilingual voices are reliable but read Vietnamese with a foreign
accent. Google's Vietnamese voice is native, answered every request in about
0.4 s, and is therefore the default for Vietnamese. An Edge Vietnamese voice
can still be chosen: a sentence it fails is spoken by its fallback, and after
two failures in a row the fallback takes over for a few minutes so the pacing
does not pay a failed attempt on every sentence.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import AsyncIterator

from livekit.agents import APIConnectionError, APIConnectOptions, tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

logger = logging.getLogger("soshop.agent.edge_voice")

SAMPLE_RATE = 24000
NUM_CHANNELS = 1

GOOGLE_VOICE_PREFIX = "google-"
_GOOGLE_TTS_URL = "https://translate.google.com/translate_tts"
_GOOGLE_MAX_CHARS = 180

# Voices known to be flaky on their endpoint, mapped to the voice that speaks
# a sentence they fail. Same language and gender where one exists.
FALLBACK_VOICES: dict[str, str] = {
    "vi-VN-HoaiMyNeural": "google-vi",
    "vi-VN-NamMinhNeural": "en-US-BrianMultilingualNeural",
}
# How long to wait for the first audio of a request. A flaky voice either
# starts within about half a second or reports failure near 3 s; the slow
# successes after that are not worth holding the stream for.
_FLAKY_FIRST_AUDIO_S = 3.2
_FIRST_AUDIO_S = 10.0
_BREAKER_FAILURES = 2
_BREAKER_OPEN_S = 300.0
_RETRY_DELAY_S = 0.25
# Fragments this short that no voice will read are treated as silence.
_TRIVIAL_TEXT_CHARS = 3
_SILENCE = bytes(SAMPLE_RATE * NUM_CHANNELS * 2 // 10)  # 100 ms of 16-bit PCM
# At least one letter or digit: something a voice can actually pronounce.
_SPEAKABLE_RE = re.compile(r"[^\W_]")

_EDGE_VOICE_RE = re.compile(r"^[a-z]{2,3}-[A-Za-z]{2,4}(-[A-Za-z]+)?-[A-Za-z]+Neural$")
_GOOGLE_VOICE_RE = re.compile(r"^google-[a-z]{2,3}(-[A-Za-z]{2,4})?$")

Attempt = tuple[str, float]
"""One synthesis attempt: (voice, seconds to wait for its first audio)."""


def is_edge_voice(voice_id: str | None) -> bool:
    """True when ``voice_id`` looks like an Edge voice name, not a provider id."""
    return bool(voice_id and _EDGE_VOICE_RE.match(voice_id))


def is_keyless_voice(voice_id: str | None) -> bool:
    """True for any voice this module can speak with (Edge or ``google-<lang>``)."""
    return is_edge_voice(voice_id) or bool(voice_id and _GOOGLE_VOICE_RE.match(voice_id))


class EdgeTTS(tts.TTS):
    """Non-streaming keyless TTS. The agent pipeline wraps it per sentence."""

    def __init__(self, *, default_voice: str, voice_id: str | None = None) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        self._default_voice = default_voice
        self._voice = default_voice
        self._primary_failures = 0
        self._fallback_until = 0.0
        self.update_options(voice_id=voice_id)

    @property
    def model(self) -> str:
        return "keyless-read-aloud"

    @property
    def provider(self) -> str:
        return "Google" if self._voice.startswith(GOOGLE_VOICE_PREFIX) else "Edge"

    @property
    def voice(self) -> str:
        return self._voice

    def update_options(self, *, voice_id: str | None = None) -> None:
        """Mirror the ElevenLabs plugin's signature used by the persona swap."""
        self._voice = voice_id if voice_id and is_keyless_voice(voice_id) else self._default_voice
        self._primary_failures = 0
        self._fallback_until = 0.0

    def voice_plan(self) -> list[Attempt]:
        """Attempts for the next sentence, in order."""
        fallback = FALLBACK_VOICES.get(self._voice)
        if fallback is None:
            return [(self._voice, _FIRST_AUDIO_S)] * 3
        primary = (self._voice, _FLAKY_FIRST_AUDIO_S)
        backup = (fallback, _FIRST_AUDIO_S)
        if time.monotonic() < self._fallback_until:
            return [backup, backup, primary]
        if self._fallback_until:
            # The breaker just closed: the primary gets one trial sentence, and
            # a single failure puts the fallback back in charge.
            self._fallback_until = 0.0
            self._primary_failures = _BREAKER_FAILURES - 1
        return [primary, backup, backup]

    def report(self, used_voice: str) -> None:
        """Track primary-voice health and open the breaker when it keeps failing."""
        if used_voice == self._voice:
            self._primary_failures = 0
            return
        if time.monotonic() < self._fallback_until:
            return
        self._primary_failures += 1
        if self._primary_failures >= _BREAKER_FAILURES:
            self._primary_failures = 0
            self._fallback_until = time.monotonic() + _BREAKER_OPEN_S
            logger.warning(
                "voice %s keeps failing; using %s for the next %.0f s",
                self._voice,
                used_voice,
                _BREAKER_OPEN_S,
            )

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> _EdgeChunkedStream:
        return _EdgeChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class _PartialAudioError(RuntimeError):
    """The stream died after some audio had already been forwarded."""


# ------------------------------------------------------------------ services


def _edge_audio(text: str, voice: str) -> AsyncIterator[bytes]:
    import edge_tts

    async def _chunks() -> AsyncIterator[bytes]:
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk["type"] == "audio":
                yield chunk["data"]

    return _chunks()


def split_for_google(text: str, limit: int = _GOOGLE_MAX_CHARS) -> list[str]:
    """Cut ``text`` into pieces the Google endpoint accepts.

    Breaks at the last punctuation mark inside the limit, else at the last
    space, so a pause in the audio always falls where a speaker would pause.
    """
    pieces: list[str] = []
    rest = text.strip()
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind(mark) for mark in (". ", "! ", "? ", ", ", "; ", ": "))
        cut = cut + 1 if cut > 0 else window.rfind(" ")
        if cut <= 0:
            cut = limit
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        pieces.append(rest)
    return pieces


def _google_audio(text: str, voice: str) -> AsyncIterator[bytes]:
    import httpx

    language = voice[len(GOOGLE_VOICE_PREFIX) :]

    async def _chunks() -> AsyncIterator[bytes]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for piece in split_for_google(text):
                response = await client.get(
                    _GOOGLE_TTS_URL,
                    params={"ie": "UTF-8", "tl": language, "client": "tw-ob", "q": piece},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if response.status_code != 200 or not response.content:
                    raise RuntimeError(f"google tts returned {response.status_code}")
                # Each reply is bare MPEG audio frames, so replies concatenate
                # into one valid MP3 stream.
                yield response.content

    return _chunks()


async def synthesize_mp3(text: str, voice: str) -> bytes:
    """The whole MP3 for ``text`` in a keyless ``voice``, for offline renders."""
    return b"".join([chunk async for chunk in _voice_audio(text, voice)])


def _voice_audio(text: str, voice: str) -> AsyncIterator[bytes]:
    """MP3 chunks for ``text`` in ``voice``. Module-level so tests can replace it."""
    if voice.startswith(GOOGLE_VOICE_PREFIX):
        return _google_audio(text, voice)
    return _edge_audio(text, voice)


# -------------------------------------------------------------------- policy


class _Request:
    """One synthesis request, buffering its audio until it is forwarded."""

    def __init__(self, text: str, voice: str) -> None:
        self.chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.has_audio = asyncio.Event()
        self.error: Exception | None = None
        self.task = asyncio.create_task(self._run(text, voice))

    async def _run(self, text: str, voice: str) -> None:
        try:
            async for data in _voice_audio(text, voice):
                self.chunks.put_nowait(data)
                self.has_audio.set()
        except Exception as exc:
            self.error = exc
        finally:
            self.chunks.put_nowait(None)

    async def wait_for_audio(self, timeout: float) -> bool:
        """True once the first audio arrives, False on failure or timeout."""
        waiter = asyncio.ensure_future(self.has_audio.wait())
        try:
            await asyncio.wait(
                [waiter, self.task], timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            waiter.cancel()
        return self.has_audio.is_set()

    async def cancel(self) -> None:
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self.task


async def deliver(text: str, plan: list[Attempt], output_emitter: tts.AudioEmitter) -> str | None:
    """Speak one sentence into ``output_emitter``, trying ``plan`` in order.

    Returns the voice that spoke it, or ``None`` when the text was a fragment
    no voice can read and a beat of silence was played instead. Kept apart
    from the stream class so the policy can be tested without livekit's
    stream machinery, which starts running on construction.
    """
    text = text.strip()
    if _SPEAKABLE_RE.search(text):
        voice = await _try_plan(text, plan, output_emitter)
        if voice is not None:
            output_emitter.flush()
            return voice
        if len(text) > _TRIVIAL_TEXT_CHARS:
            raise APIConnectionError("keyless tts failed: no voice returned audio")
    # A stray dash, an ellipsis, "mm": the services return nothing for these,
    # which would otherwise fail the whole turn.
    output_emitter.initialize(
        request_id=utils.shortuuid(),
        sample_rate=SAMPLE_RATE,
        num_channels=NUM_CHANNELS,
        mime_type="audio/pcm",
    )
    output_emitter.push(_SILENCE)
    output_emitter.flush()
    return None


async def _try_plan(text: str, plan: list[Attempt], output_emitter: tts.AudioEmitter) -> str | None:
    for index, (voice, first_audio_timeout) in enumerate(plan):
        if index and voice == plan[index - 1][0]:
            await asyncio.sleep(_RETRY_DELAY_S)
        request = _Request(text, voice)
        try:
            if await request.wait_for_audio(first_audio_timeout):
                await _forward(request, output_emitter)
                return voice
            logger.info("tts voice %s attempt %d failed: %s", voice, index + 1, request.error)
        except _PartialAudioError as exc:
            # Audio already reached the listener: another attempt would replay
            # the start of the sentence, so surface the failure instead.
            raise APIConnectionError(f"keyless tts stream broke: {exc}") from exc
        finally:
            await request.cancel()
    return None


async def _forward(request: _Request, output_emitter: tts.AudioEmitter) -> None:
    """Send a request's audio to the listener as it arrives."""
    output_emitter.initialize(
        request_id=utils.shortuuid(),
        sample_rate=SAMPLE_RATE,
        num_channels=NUM_CHANNELS,
        mime_type="audio/mp3",
    )
    while True:
        data = await request.chunks.get()
        if data is None:
            break
        output_emitter.push(data)
    if request.error is not None:
        raise _PartialAudioError(str(request.error))


class _EdgeChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: EdgeTTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._keyless = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        used = await deliver(self._input_text, self._keyless.voice_plan(), output_emitter)
        if used is not None:
            self._keyless.report(used)
