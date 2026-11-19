"""Seed-data gate: every authored pitch lands in the spoken-word band.

The channel rotation swaps personas the moment the current pitch finishes
speaking, so each pitch's *spoken* footprint (hook + script_text + cta,
the three fields the LLM actually delivers) determines the segment
length. Authors target ``WORD_COUNT_TARGET ± a few`` words, which lands
near ~15s of TTS at ElevenLabs' ~150 WPM default. This test fences the
band so a drift on either side fails CI before it ships.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from soshop.agent.pitch_timing import WORD_COUNT_MAX, WORD_COUNT_MIN, count_pitch_words

_SEED_DIR = Path(__file__).resolve().parents[2] / "seed" / "personas"


def _collect_scripts() -> list[tuple[str, int, str, int]]:
    """Return ``(slug, idx, sku, word_count)`` for every authored script.

    Walks ``server/seed/personas/*.json`` and combines each script's
    ``hook``, ``script_text``, and ``cta`` into one stream — the LLM
    speaks all three back-to-back, so the band must cover the union.
    Empty / missing fields contribute zero words (freestyle scripts get
    counted on whatever is present).
    """
    out: list[tuple[str, int, str, int]] = []
    paths = sorted(_SEED_DIR.glob("*.json"))
    if not paths:
        # Fresh clone: only the committed `.json.example` template exists.
        paths = sorted(_SEED_DIR.glob("*.json.example"))
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        slug = data["slug"]
        for idx, script in enumerate(data.get("scripts", [])):
            count = count_pitch_words(
                script.get("hook"),
                script.get("script_text"),
                script.get("cta"),
            )
            sku = script.get("product_sku") or "freestyle"
            out.append((slug, idx, sku, count))
    return out


_SCRIPTS = _collect_scripts()


def test_seed_persona_scripts_exist() -> None:
    """Smoke check: the seed has at least one script (catches a botched glob)."""
    assert _SCRIPTS, f"no scripts found under {_SEED_DIR}"


@pytest.mark.parametrize(("slug", "idx", "sku", "count"), _SCRIPTS)
def test_persona_script_word_count_in_band(slug: str, idx: int, sku: str, count: int) -> None:
    """Every script's combined hook + body + CTA lands in [30, 45] words.

    On failure the message names the persona slug, script index, SKU, and
    current count so the author can find the offending entry in seconds.
    """
    assert WORD_COUNT_MIN <= count <= WORD_COUNT_MAX, (
        f"{slug}[{idx}] ({sku}) is {count} words; "
        f"target {WORD_COUNT_MIN}-{WORD_COUNT_MAX} (≈15s at 150 WPM)"
    )
