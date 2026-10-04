"""Structured script segment service — handles segments with cue, shot, emphasis, and JSON validation."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from app.services.voice_service import strip_stage_directions


# Standard Cues (§3.2)
VALID_CUES: Set[str] = {
    "IDLE_TALK",
    "WAVE_GREET",
    "HOLD_PRODUCT",
    "SHOW_DETAIL",
    "POINT_PRICE",
    "TRY_ON",
    "CTA_POINT_DOWN",
    "NOD_LISTEN",
    "SURPRISE",
}

# Standard Camera Shots (§3.2)
VALID_SHOTS: Set[str] = {
    "WIDE_MODEL",
    "MEDIUM_MODEL",
    "CLOSEUP_PRODUCT",
    "SPLIT",
    "INSET_PRODUCT",
    "TRYON_WIDE",
}


@dataclass
class ScriptSegment:
    """A single segment of a presentation script with independent gesture cue and camera shot."""

    text: str
    cue: str = "IDLE_TALK"
    shot: str = "MEDIUM_MODEL"
    emphasis: str = "normal"  # low, normal, high
    duration_seconds: Optional[float] = None
    audio_path: Optional[str] = None

    def clean_text_for_tts(self) -> str:
        """Return safe text for TTS synthesis (guaranteed to strip all stage directions)."""
        return strip_stage_directions(self.text)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StructuredScript:
    """Full structured script containing environmental set_id and ordered segments."""

    set_id: str = "studio_gradient"
    segments: List[ScriptSegment] = field(default_factory=list)

    @property
    def total_text(self) -> str:
        return " ".join(s.clean_text_for_tts() for s in self.segments)

    @property
    def total_duration(self) -> float:
        return sum(s.duration_seconds or 0.0 for s in self.segments)

    def to_json(self) -> str:
        return json.dumps(
            {
                "set_id": self.set_id,
                "segments": [s.to_dict() for s in self.segments],
            },
            ensure_ascii=False,
            indent=2,
        )


def normalize_cue(cue_str: str) -> str:
    """Normalize cue string to one of standard VALID_CUES."""
    c = (cue_str or "").strip().upper()
    if c in VALID_CUES:
        return c
    # Fuzzy mapping
    if any(k in c for k in ("CHÀO", "VẪY", "WAVE", "GREET", "HELLO")):
        return "WAVE_GREET"
    if any(k in c for k in ("GIƠ", "CẦM", "HOLD", "SHOW")):
        return "HOLD_PRODUCT"
    if any(k in c for k in ("CHI TIẾT", "CẬN", "DETAIL", "CHỈ", "POINT")):
        return "SHOW_DETAIL"
    if any(k in c for k in ("GIÁ", "PRICE", "DEAL", "TIỀN", "SALE")):
        return "POINT_PRICE"
    if any(k in c for k in ("MẶC", "ĐEO", "THỬ", "TRY")):
        return "TRY_ON"
    if any(k in c for k in ("GIỎ", "CART", "MUA", "CTA", "DOWN", "XUỐNG")):
        return "CTA_POINT_DOWN"
    if any(k in c for k in ("NGHE", "GẬT", "LISTEN", "NOD")):
        return "NOD_LISTEN"
    if any(k in c for k in ("SỐC", "SURPRISE", "NGẠC NHIÊN")):
        return "SURPRISE"
    return "IDLE_TALK"


def normalize_shot(shot_str: str) -> str:
    """Normalize shot string to one of standard VALID_SHOTS."""
    s = (shot_str or "").strip().upper()
    if s in VALID_SHOTS:
        return s
    # Fuzzy mapping
    if any(k in s for k in ("TOÀN", "WIDE", "FULL")):
        return "WIDE_MODEL"
    if any(k in s for k in ("CẬN", "CLOSE", "MACRO")):
        return "CLOSEUP_PRODUCT"
    if any(k in s for k in ("SPLIT", "CHIA", "BẢNG")):
        return "SPLIT"
    if any(k in s for k in ("INSET", "GÓC", "Ô")):
        return "INSET_PRODUCT"
    if any(k in s for k in ("TRYON", "MẶC")):
        return "TRYON_WIDE"
    return "MEDIUM_MODEL"


def parse_and_validate_script_json(
    raw_text: str,
    default_set_id: str = "studio_gradient",
) -> StructuredScript:
    """Parse JSON or convert raw script text into a validated StructuredScript with cues and shots.

    Follows strict safety rule:
    Segment.text -> never includes stage directions (clean for TTS).
    Segment.cue -> standard normalized cue.
    Segment.shot -> standard normalized camera shot.
    """
    cleaned = (raw_text or "").strip()

    # 1. Try stripping markdown code fences e.g. ```json ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
    if match:
        cleaned = match.group(1).strip()

    # 2. Try JSON parsing
    parsed_dict: Optional[dict] = None
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            parsed_dict = data
        elif isinstance(data, list):
            parsed_dict = {"set_id": default_set_id, "segments": data}
    except Exception:
        parsed_dict = None

    if parsed_dict and "segments" in parsed_dict and isinstance(parsed_dict["segments"], list):
        set_id = str(parsed_dict.get("set_id") or default_set_id).strip()
        segments: List[ScriptSegment] = []
        for item in parsed_dict["segments"]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            # Strip stage directions from text as a primary rule (§3.2)
            safe_text = strip_stage_directions(text)
            cue = normalize_cue(str(item.get("cue", "IDLE_TALK")))
            shot = normalize_shot(str(item.get("shot", "MEDIUM_MODEL")))
            emphasis = str(item.get("emphasis", "normal")).lower()
            if emphasis not in ("low", "normal", "high"):
                emphasis = "normal"

            segments.append(
                ScriptSegment(
                    text=safe_text,
                    cue=cue,
                    shot=shot,
                    emphasis=emphasis,
                    duration_seconds=float(item.get("duration_seconds", 0.0)) or None,
                )
            )

        if segments:
            return StructuredScript(set_id=set_id, segments=segments)

    # 3. Fallback: Parse raw plain text into natural review segments
    return fallback_parse_raw_text(raw_text, default_set_id)


def fallback_parse_raw_text(raw_text: str, default_set_id: str = "studio_gradient") -> StructuredScript:
    """Split raw unstructured script text into standard review rhythm segments."""
    cleaned = strip_stage_directions(raw_text)
    # Split by double newline or sentence boundaries
    raw_lines = [line.strip() for line in re.split(r"\n\s*\n|(?<=[.!?])\s+", cleaned) if line.strip()]
    if not raw_lines:
        return StructuredScript(set_id=default_set_id, segments=[])

    # Assign standard review rhythm sequence (§4.4)
    # 1. WAVE_GREET (WIDE_MODEL)
    # 2. HOLD_PRODUCT (MEDIUM_MODEL)
    # 3. SHOW_DETAIL (CLOSEUP_PRODUCT)
    # 4. POINT_PRICE (SPLIT)
    # 5. CTA_POINT_DOWN (MEDIUM_MODEL)
    rhythm_presets = [
        ("WAVE_GREET", "WIDE_MODEL"),
        ("HOLD_PRODUCT", "MEDIUM_MODEL"),
        ("SHOW_DETAIL", "CLOSEUP_PRODUCT"),
        ("SHOW_DETAIL", "INSET_PRODUCT"),
        ("POINT_PRICE", "SPLIT"),
        ("CTA_POINT_DOWN", "MEDIUM_MODEL"),
    ]

    segments: List[ScriptSegment] = []
    for i, line in enumerate(raw_lines):
        if i == 0:
            cue, shot = ("WAVE_GREET", "WIDE_MODEL")
        elif i == len(raw_lines) - 1:
            cue, shot = ("CTA_POINT_DOWN", "MEDIUM_MODEL")
        else:
            preset_idx = min(i, len(rhythm_presets) - 2)
            cue, shot = rhythm_presets[preset_idx]

        segments.append(
            ScriptSegment(
                text=line,
                cue=cue,
                shot=shot,
                emphasis="high" if cue in ("POINT_PRICE", "WAVE_GREET", "CTA_POINT_DOWN") else "normal",
            )
        )

    return StructuredScript(set_id=default_set_id, segments=segments)
