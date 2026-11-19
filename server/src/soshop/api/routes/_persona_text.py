"""Pure text helpers for the consumer persona profile shape.

Extracted from ``personas.py`` so that routes module stays under the
project's length threshold. These are dependency-free string
normalizers (no FastAPI, no DB), kept together so the persona surface
has a single home for its display-text munging.
"""

from __future__ import annotations


def split_traits(traits: str | None) -> list[str]:
    """`personas.traits` is a free-form TEXT column. Treat commas / pipes
    / newlines as separators so the UI can render chips."""
    if not traits:
        return []
    out: list[str] = []
    for part in traits.replace("|", ",").replace("\n", ",").split(","):
        cleaned = part.strip()
        if cleaned:
            out.append(cleaned)
    return out


def persona_handle_from_name(name: str) -> str:
    """The schema has no `handle` column, so we synthesize one from
    `name` for the profile shape until a column lands."""
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or "persona"
