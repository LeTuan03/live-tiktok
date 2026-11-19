"""Check what the host actually says in a Flow clip against the script.

Veo and Omni speak the line from the prompt, but not always word for word.
Each downloaded clip is transcribed with Whisper on Groq; the share of the
scripted words heard, in order, becomes the shot's ``speech_match``, and the
speech span times the captions.
"""

from __future__ import annotations

import difflib
import logging
import re
import subprocess
import unicodedata
from pathlib import Path

import httpx
from pydantic import BaseModel

from soshop.promo.assemble import ffmpeg
from soshop_server.core.config import settings

logger = logging.getLogger("soshop.promo.qa")

_GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_WORD_RE = re.compile(r"\w+", re.UNICODE)

MATCH_WARN_BELOW = 0.6
"""Below this share of scripted words heard, the studio flags the shot."""


class Transcript(BaseModel):
    text: str
    speech_start: float | None = None
    speech_end: float | None = None


_NUMBER_WORDS = frozenset(
    "không một mốt hai ba bốn tư năm lăm sáu bảy tám chín mười mươi trăm nghìn ngàn "
    "triệu tỷ lẻ linh phẩy zero one two three four five six seven eight nine ten eleven "
    "twelve twenty thirty forty fifty sixty seventy eighty ninety hundred thousand "
    "million point".split()
)
"""Scripts spell prices out; Whisper writes digits. Neither side is scored."""
_FUZZY_RATIO = 0.8
_LOOKAHEAD = 4


def words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _fold(word: str) -> str:
    """Lower-case without tone marks: Whisper mixes up nhé/nhá, đó/đấy."""
    decomposed = unicodedata.normalize("NFD", word.replace("đ", "d"))
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _same(a: str, b: str) -> bool:
    if a == b:
        return True
    return len(a) >= 4 and difflib.SequenceMatcher(a=a, b=b).ratio() >= _FUZZY_RATIO


def match_score(expected: str, heard: str) -> float:
    """Share of the expected words found, in order, in what was heard (0 to 1).

    Number words and digits are left out (``năm mươi chín nghìn`` vs
    ``59.000``), tone marks are ignored, and a long word may differ by a letter
    (``Labello`` vs ``Labelo``), so the score reflects the line, not Whisper's
    spelling.
    """
    want = [_fold(w) for w in words(expected) if w not in _NUMBER_WORDS and not w.isdigit()]
    got = [_fold(w) for w in words(heard) if not w.isdigit()]
    if not want:
        return 1.0
    matched, cursor = 0, 0
    for word in want:
        window = got[cursor : cursor + _LOOKAHEAD]
        hit = next((i for i, candidate in enumerate(window) if _same(word, candidate)), None)
        if hit is not None:
            matched += 1
            cursor += hit + 1
    return round(matched / len(want), 3)


def _extract_audio(clip: Path) -> Path:
    wav = clip.with_suffix(".wav")
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            ffmpeg(),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(clip),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(wav),
        ],
        check=True,
        capture_output=True,
    )
    return wav


async def transcribe(clip: Path, language: str) -> Transcript | None:
    """Whisper transcript of ``clip``; ``None`` when it cannot be checked."""
    if not settings.GROQ_API_KEY:
        return None
    try:
        wav = _extract_audio(clip)
    except (subprocess.CalledProcessError, OSError):
        logger.info("no audio track to transcribe in %s", clip.name)
        return None
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                _GROQ_TRANSCRIBE_URL,
                headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
                data={
                    "model": settings.PROMO_TRANSCRIBE_MODEL,
                    "language": language,
                    "response_format": "verbose_json",
                },
                files={"file": (wav.name, wav.read_bytes(), "audio/wav")},
            )
    except httpx.HTTPError as exc:
        logger.info("transcription failed: %s", exc)
        return None
    finally:
        wav.unlink(missing_ok=True)
    if resp.status_code != 200:
        logger.info("transcription error %s: %s", resp.status_code, resp.text[:200])
        return None
    data = resp.json()
    segments = [s for s in data.get("segments") or [] if str(s.get("text", "")).strip()]
    return Transcript(
        text=str(data.get("text") or "").strip(),
        speech_start=float(segments[0]["start"]) if segments else None,
        speech_end=float(segments[-1]["end"]) if segments else None,
    )
