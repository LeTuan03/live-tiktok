"""Response schemas for `/feed/cursor`.

Two flags here exist for FTC Endorsement Guides compliance and are not
optional:

- `FeedItemOut.is_ad`: ``true`` when the slot carries a paid placement
  (i.e. `product_id` is not null). The client renders an ``(Ad)`` chip
  whenever this is set so paid endorsements are clearly disclosed.
- `PersonaOut.is_ai_creator`: ``true`` for every persona since all
  creators in this demo are AI. Surfaced per-persona so the disclosure
  travels with the entity even if non-AI creators are added later.

See `server/legal/disclosure.md` for the policy these flags
implement.

Each slot also bundles the advertised product so consumer clients can
render the product image, price, and shop CTA without an extra fetch.
Every AI seller in v1 advertises a product (the feed ranker filters out
freestyle scripts); ``product_details`` is therefore expected to be
populated whenever ``product`` is set.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "FeedCursorResponse",
    "FeedItemOut",
    "PersonaOut",
    "ProductMediaOut",
    "ProductOut",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PersonaOut(_StrictModel):
    """A persona reference embedded in a feed slot.

    `is_ai_creator` is mandatory and currently always ``true``; it stays
    a per-persona field rather than a feed-level constant so future
    non-AI creators can opt out without changing the response shape.

    `avatar_url` mirrors `personas.avatar_image_url` and is consumed by
    clients as the still-frame placeholder shown while the LiveKit
    avatar track is connecting. May be ``null`` for personas without an
    uploaded avatar — clients should fall back to the gradient identity
    in that case.
    """

    id: UUID = Field(description="persona_id")
    is_ai_creator: bool = Field(
        description=(
            "True when the persona is an AI creator. Always true in v1; "
            "required by FTC Endorsement Guides so clients can render an "
            "AI-creator disclosure."
        ),
    )
    display_name: str | None = Field(
        default=None,
        description=(
            "Human-readable display name from `personas.name`. Optional "
            "for backwards-compatibility — older clients fall back to "
            "the persona id."
        ),
    )
    avatar_url: str | None = Field(
        default=None,
        description=(
            "Public URL of the persona's still avatar image. Rendered "
            "as a placeholder while the live avatar track is connecting."
        ),
    )


class ProductMediaOut(_StrictModel):
    """One ordered media item attached to a product.

    Position 0 is the feature image, position 1 is the feature video
    when present, positions ≥2 are gallery images. Clients should
    treat the list as already sorted by position.
    """

    position: int = Field(ge=0)
    kind: str = Field(description="'image' or 'video'")
    url: str


class ProductOut(_StrictModel):
    """Product details embedded in a feed slot.

    Bundles the catalog fields a consumer surface needs to render the
    product gallery and the Shop CTA without an extra fetch. Sourced
    from ``products`` + ``product_media``. The deep-link and landing
    URLs travel in parallel so a native client can use the deep link
    while web falls back to the public landing URL.

    Clients iterate ``media`` (already sorted by position) for image
    + video assets. Position 0 is the feature image, position 1 is
    the feature video (when present), positions ≥2 are gallery
    images.
    """

    id: UUID
    sku: str
    title: str
    description: str | None = None
    price_cents: int | None = None
    currency: str | None = None
    deep_link_url: str | None = None
    landing_url: str | None = None
    media: list[ProductMediaOut] = Field(
        default_factory=list,
        description=(
            "Ordered media items for the product gallery. Position 0 is "
            "the feature image, position 1 the feature video (if any), "
            "remainder are additional images. Empty list for products "
            "without any product_media rows."
        ),
    )


class FeedItemOut(_StrictModel):
    """A single ranked slot in the cursor feed."""

    persona: PersonaOut
    product: UUID | None = Field(default=None, description="product_id, null for comedy slots")
    product_details: ProductOut | None = Field(
        default=None,
        description=(
            "Catalog snapshot for the advertised product. Populated when "
            "`product` is set so the client can render the PiP product "
            "image, price, and Shop CTA without a second fetch."
        ),
    )
    script_id: UUID
    is_ad: bool = Field(
        description=(
            "True when the slot is a paid placement (product_id is not "
            "null). Required by FTC Endorsement Guides so clients can "
            "render an (Ad) chip."
        ),
    )


class FeedCursorResponse(_StrictModel):
    items: list[FeedItemOut]
    next_cursor: str | None
