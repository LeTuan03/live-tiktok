"""Pydantic models mirroring the schema in migrations/0001_initial_schema.up.sql.

Use these for typed access to rows fetched via asyncpg
(`Persona.model_validate(dict(row))`) and as building blocks for API
request/response payloads.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enum stubs (mirror the CHECK constraints in the SQL schema)
# ---------------------------------------------------------------------------


class ProductStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class PersonaKind(StrEnum):
    HOST = "host"
    GUEST = "guest"
    EXPERT = "expert"
    CELEBRITY = "celebrity"


class PersonaStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class FeedEventKind(StrEnum):
    IMPRESSION = "impression"
    VIEW = "view"
    VIEW_COMPLETE = "view_complete"
    TAP = "tap"
    ADD_TO_CART = "add_to_cart"
    PURCHASE = "purchase"
    SKIP = "skip"
    FAVORITE = "favorite"
    UNFAVORITE = "unfavorite"
    SHARE = "share"
    MUTE = "mute"
    UNMUTE = "unmute"


class AdStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class ChannelStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"
    RETIRED = "retired"


# ---------------------------------------------------------------------------
# Row models
# ---------------------------------------------------------------------------


class _Row(BaseModel):
    """Base for DB row models — allows extra columns without errors."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")


class Advertiser(_Row):
    id: UUID
    org_id: str
    name: str
    contact_email: str | None = None
    created_at: datetime


class Product(_Row):
    id: UUID
    advertiser_id: UUID
    sku: str
    title: str
    description: str | None = None
    deep_link_url: str | None = None
    landing_url: str | None = None
    price_cents: int | None = None
    currency: str = "USD"
    status: ProductStatus = ProductStatus.DRAFT
    created_at: datetime
    updated_at: datetime


class Campaign(_Row):
    id: UUID
    advertiser_id: UUID
    name: str
    budget_cents: int | None = None
    daily_cap_cents: int | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    targeting: dict[str, Any] = Field(default_factory=dict)
    status: CampaignStatus = CampaignStatus.DRAFT
    created_at: datetime
    updated_at: datetime


class CampaignProduct(_Row):
    campaign_id: UUID
    product_id: UUID
    created_at: datetime


class Persona(_Row):
    id: UUID
    name: str
    avatar_image_url: str | None = None
    voice_id: str | None = None
    voice_pipeline_id: str
    personality: str | None = None
    backstory: str | None = None
    traits: str | None = None
    persona_kind: PersonaKind = PersonaKind.HOST
    status: PersonaStatus = PersonaStatus.DRAFT
    created_at: datetime
    updated_at: datetime


class PersonaScript(_Row):
    id: UUID
    persona_id: UUID
    product_id: UUID | None = None
    script_text: str
    hook: str | None = None
    cta: str | None = None
    weight: int = 1
    created_at: datetime
    updated_at: datetime


class FeedSession(_Row):
    id: UUID
    user_id: str
    room_name: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    device_id: str | None = None
    app_version: str | None = None
    country: str | None = None


class FeedEvent(_Row):
    id: UUID
    session_id: UUID
    persona_id: UUID
    product_id: UUID | None = None
    event_kind: FeedEventKind
    value_ms: int | None = None
    ts: datetime


class Favorite(_Row):
    user_id: str
    persona_id: UUID
    created_at: datetime


class Ad(_Row):
    """One (persona × product × script) tuple in the channel rotation.

    `sequence_order` is the unique 1..N position in the global rotation;
    channels read this ordering and stagger their starting cursor by stride.
    """

    id: UUID
    advertiser_id: UUID
    persona_id: UUID
    product_id: UUID
    script_id: UUID
    duration_ms: int = 45000
    sequence_order: int
    status: AdStatus = AdStatus.ACTIVE
    created_at: datetime
    updated_at: datetime


class Channel(_Row):
    """One always-on LiveKit channel room.

    `id` is the human-readable room name (e.g. ``"channel-0"``), which
    doubles as the LiveKit `room_name`. `current_ad_sequence` is mutable
    and written only by the channel agent loop as it advances through the
    rotation.
    """

    id: str
    ordinal: int
    livekit_room_name: str
    current_ad_sequence: int
    agent_dispatch_id: str | None = None
    status: ChannelStatus = ChannelStatus.STARTING
    last_heartbeat_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    persona_id: UUID | None = None


__all__ = [
    "Ad",
    "AdStatus",
    "Advertiser",
    "Campaign",
    "CampaignProduct",
    "CampaignStatus",
    "Channel",
    "ChannelStatus",
    "Favorite",
    "FeedEvent",
    "FeedEventKind",
    "FeedSession",
    "Persona",
    "PersonaKind",
    "PersonaScript",
    "PersonaStatus",
    "Product",
    "ProductStatus",
]
