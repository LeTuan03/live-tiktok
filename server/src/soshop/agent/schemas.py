"""Pydantic schemas for the LiveKit agent dispatch metadata payload.

The SoShop agent receives a JSON-encoded `DispatchMetadata` document on the
LiveKit `Job.metadata` field. The agent process decodes and validates it on
startup so it knows which persona, product, and script to run, and what
popup payload (if any) to deliver mid-pitch.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

__all__ = [
    "DispatchMetadata",
    "PopupPayload",
    "ProductMediaItem",
    "DispatchMetadataValidationError",
    "parse_dispatch_metadata",
]


class DispatchMetadataValidationError(ValueError):
    """Raised when dispatch metadata cannot be parsed or fails validation."""


class ProductMediaItem(BaseModel):
    """One slot in a product's ordered media gallery.

    Mirrors a row of ``product_media`` (see ``migrations/0001_initial_schema``).
    The client renders these as a paginated horizontal gallery in the
    product-focus surface; the existing ``image_url`` on ``PopupPayload``
    remains the single hero (position 0) for the compact popup card.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    position: int = Field(ge=0)
    kind: str = Field(min_length=1)
    url: str = Field(min_length=1)


class PopupPayload(BaseModel):
    """Mid-pitch popup payload delivered to the client.

    Fired when the LLM calls ``signal_product_pitch`` 2–4 seconds into
    delivering the pitch body.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    image_url: str = Field(min_length=1)
    cta: str = Field(min_length=1)
    deep_link_url: str = Field(min_length=1)
    landing_url: str = Field(min_length=1)
    media: list[ProductMediaItem] = Field(default_factory=list)
    """Full ordered gallery (image + video slots). Empty when the product
    has no ``product_media`` rows; clients fall back to ``image_url``."""


class DispatchMetadata(BaseModel):
    """Metadata passed to the agent process at dispatch time.

    Schema drift vs. `soshop/`: SoShop adds `channel_id` and
    `ad_start_index` so the agent worker can distinguish a 1:1 companion
    dispatch (no `channel_id`) from a channel-broadcast dispatch
    (`channel_id` set, `ad_start_index` required). Per CLAUDE.md, this is a
    breaking change relative to the soshop repo — only consumers inside
    this repo see it.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    persona_id: str = Field(min_length=1)
    product_id: str | None = None
    script_id: str = Field(min_length=1)
    pitch_window_ms: int = Field(ge=0)
    """Advisory field retained for transport compatibility. The channel
    rotation no longer reads this — pacing is completion-driven off the
    LLM's ``SpeechHandle``. Channel dispatches set it to ``0``; the 1:1
    companion path may still log it. New consumers should use the
    word-count band documented in ``server/seed/personas/README.md``."""
    popup_payload: PopupPayload | None = None
    session_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    channel_id: str | None = Field(default=None, min_length=1)
    ad_start_index: int | None = Field(default=None, ge=0)
    live_session_id: str | None = Field(default=None, min_length=1)
    """Set for a studio livestream dispatch (``live_sessions.id``). The worker
    then runs the live host (free talk plus the ordered product queue) instead
    of the pitch pipeline. ``script_id`` carries the placeholder ``"live"``."""
    await_viewer_ready: bool = False
    """When true, the 1:1 companion entrypoint holds the opening pitch until the
    viewer's browser signals ``viewer_ready`` on the ``agent_control`` topic
    (i.e. it has unlocked audio playback via a user gesture). Set by the public
    ``/customize`` preview so its single one-shot pitch is never delivered to a
    still-muted client. Every other dispatch leaves it false and speaks as soon
    as the avatar is ready."""

    @model_validator(mode="after")
    def _require_ad_start_index_with_channel(self) -> DispatchMetadata:
        if self.channel_id is not None and self.ad_start_index is None:
            raise ValueError("ad_start_index is required when channel_id is set")
        return self

    def to_metadata_string(self) -> str:
        """Serialize for the LiveKit dispatch `metadata` field."""
        return self.model_dump_json()


def parse_dispatch_metadata(raw: str) -> DispatchMetadata:
    """Parse a raw JSON dispatch metadata string into a `DispatchMetadata`.

    Raises `DispatchMetadataValidationError` on malformed JSON or schema
    violations so callers can handle both failure modes uniformly.
    """
    if not raw:
        raise DispatchMetadataValidationError("dispatch metadata is empty")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DispatchMetadataValidationError(
            f"dispatch metadata is not valid JSON: {exc}"
        ) from exc
    try:
        return DispatchMetadata.model_validate(data)
    except ValidationError as exc:
        raise DispatchMetadataValidationError(str(exc)) from exc
