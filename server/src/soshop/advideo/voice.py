"""The voice track: each beat spoken on its own, then laid out with natural pauses.

Speaking beat by beat costs a few extra TTS requests but gives exact sentence
boundaries, which the director needs for gestures and the edit needs for cuts.
Word timings for captions are estimated inside each sentence from syllable
lengths (Vietnamese writes one syllable per word), which is accurate to about
a tenth of a second on TTS speech: enough for captions shown a few words at
a time.
"""

from __future__ import annotations

import logging
import math
import wave
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from soshop.advideo import media
from soshop.advideo.script import AdScript

RATE = 24000
LEAD_IN_S = 0.55
TAIL_S = 1.0
GAP_S = 0.3
GAP_AFTER_QUESTION_S = 0.45
_SILENCE_DB = -42.0
_EDGE_PAD_S = 0.03

logger = logging.getLogger("soshop.advideo")

Int16Array = npt.NDArray[np.int16]
Synthesize = Callable[[str], Awaitable[Int16Array]]


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class SpokenBeat:
    index: int
    text: str
    start: float
    end: float
    words: tuple[Word, ...]


@dataclass(frozen=True)
class VoiceTrack:
    samples: Int16Array
    rate: int
    beats: tuple[SpokenBeat, ...]

    @property
    def duration(self) -> float:
        return len(self.samples) / self.rate

    def beat_at(self, seconds: float) -> SpokenBeat | None:
        for beat in self.beats:
            if beat.start <= seconds < beat.end:
                return beat
        return None

    def write_wav(self, path: Path) -> None:
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(self.rate)
            out.writeframes(self.samples.tobytes())


def decode_audio(data: bytes, rate: int = RATE) -> Int16Array:
    """Decode any audio ffmpeg reads (MP3 from TTS) to mono 16-bit PCM."""
    pcm = media.run(["-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", str(rate), "pipe:1"], data)
    return np.frombuffer(pcm, dtype=np.int16).copy()


def trim_silence(samples: Int16Array, rate: int = RATE) -> Int16Array:
    """Cut leading and trailing silence, keeping a few ms so onsets stay soft."""
    if samples.size == 0:
        return samples
    hop = rate // 100
    frames = len(samples) // hop
    if frames == 0:
        return samples
    chunks = samples[: frames * hop].astype(np.float32).reshape(frames, hop) / 32768.0
    db = 10 * np.log10(np.mean(chunks**2, axis=1) + 1e-12)
    loud = np.flatnonzero(db > _SILENCE_DB)
    if loud.size == 0:
        return samples[:0]
    pad = int(_EDGE_PAD_S * rate)
    start = max(0, int(loud[0]) * hop - pad)
    end = min(len(samples), (int(loud[-1]) + 1) * hop + pad)
    return samples[start:end]


def _syllable_weight(token: str) -> float:
    letters = sum(ch.isalnum() for ch in token)
    weight = 1.0 + 0.12 * letters
    if token[-1:] in ",;:":
        weight += 0.8  # a short breath after a comma
    elif token[-1:] in ".!?":
        weight += 0.4
    return weight


def word_timings(text: str, start: float, end: float) -> tuple[Word, ...]:
    """Spread the words of ``text`` over ``[start, end]`` by syllable weight."""
    tokens = text.split()
    if not tokens:
        return ()
    weights = np.array([_syllable_weight(t) for t in tokens])
    edges = start + (end - start) * np.concatenate([[0.0], np.cumsum(weights) / weights.sum()])
    return tuple(
        Word(token, float(edges[i]), float(edges[i + 1])) for i, token in enumerate(tokens)
    )


def assemble(texts: Sequence[str], clips: Sequence[Int16Array], rate: int = RATE) -> VoiceTrack:
    """Lay trimmed clips end to end with pauses and record where each one sits."""
    pieces: list[Int16Array] = [np.zeros(int(LEAD_IN_S * rate), dtype=np.int16)]
    cursor = len(pieces[0])
    beats: list[SpokenBeat] = []
    for index, (text, clip) in enumerate(zip(texts, clips, strict=True)):
        if index > 0:
            gap = GAP_AFTER_QUESTION_S if texts[index - 1].rstrip().endswith("?") else GAP_S
            pieces.append(np.zeros(int(gap * rate), dtype=np.int16))
            cursor += len(pieces[-1])
        start = cursor / rate
        pieces.append(clip)
        cursor += len(clip)
        end = cursor / rate
        beats.append(SpokenBeat(index, text, start, end, word_timings(text, start, end)))
    pieces.append(np.zeros(int(TAIL_S * rate), dtype=np.int16))
    return VoiceTrack(np.concatenate(pieces), rate, tuple(beats))


async def speak(script: AdScript, synthesize: Synthesize, rate: int = RATE) -> VoiceTrack:
    """Speak every beat with ``synthesize`` (text to PCM at ``rate``) and assemble."""
    texts = [beat.text for beat in script.beats]
    clips = [trim_silence(await synthesize(text), rate) for text in texts]
    empty = [i for i, clip in enumerate(clips) if clip.size == 0]
    if empty:
        raise RuntimeError(f"text-to-speech returned silence for beat(s) {empty}")
    return assemble(texts, clips, rate)


def keyless_synthesizer(voice: str, language: str = "vi", rate: int = RATE) -> Synthesize:
    """``Synthesize`` backed by the keyless voices (``google-<lang>`` or Edge).

    Edge voices fail often from some networks. A beat that fails in ``voice``
    is retried once and then spoken by ``google-<language>``, so one flaky
    request does not cost a whole render.
    """
    from soshop.agent.edge_voice import synthesize_mp3

    fallback = f"google-{language}"

    async def _run(text: str) -> Int16Array:
        voices = [voice, voice] if voice == fallback else [voice, voice, fallback]
        error: Exception | None = None
        for attempt in voices:
            try:
                return decode_audio(await synthesize_mp3(text, attempt), rate)
            except Exception as exc:  # network, provider or decode failure
                error = exc
                logger.warning("tts failed with %s: %s", attempt, exc)
        raise RuntimeError(f"text-to-speech failed for {text[:40]!r}") from error

    return _run


def frame_count(track: VoiceTrack, fps: int) -> int:
    return math.ceil(track.duration * fps)
