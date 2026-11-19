"""Shared spoken-word band for a pitch — one source of truth.

The channel rotation swaps personas the moment the current pitch finishes
speaking, so each pitch's *spoken* footprint (hook + script_text + cta, the
three fields the LLM actually delivers) sets the segment length. Authors —
and the public ``/customize`` page — target ``WORD_COUNT_TARGET`` words, which
lands near ~15s of TTS at ElevenLabs' ~150 WPM default.

Both the seed-data gate (``tests/seed/test_persona_word_count.py``) and the
customize service import these constants so the band can never drift between
the two enforcement points.
"""

from __future__ import annotations

WORD_COUNT_MIN = 30
WORD_COUNT_MAX = 45
WORD_COUNT_TARGET = 37


def count_words(text: str | None) -> int:
    """Whitespace word count for a single field (``None`` counts as zero)."""
    return len((text or "").split())


def count_pitch_words(*parts: str | None) -> int:
    """Combined word count across the spoken parts (hook, body, cta).

    Mirrors how :func:`soshop.agent.loaders.load_script_body` joins the
    fields the LLM speaks back-to-back, so the count matches the real
    spoken footprint.
    """
    combined = " ".join(p.strip() for p in parts if p and p.strip())
    return len(combined.split())
