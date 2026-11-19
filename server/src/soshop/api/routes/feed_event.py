"""Feed analytics beacon for `/feed/event`.

Idempotent client beacon for popup, deep-link, and conversion events.
The endpoint is keyed on a client-supplied `event_id` so retries and
double-taps write the row at most once.

Shared session/auth plumbing lives in `feed.py`; this module is wired
into the top-level feed router via `include_router`.
"""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import Field

from soshop.api.routes.feed import (
    UserCtx,
)
from soshop_server.api.deps import FeedRepo, StrictModel

logger = logging.getLogger("soshop.api.feed")

router = APIRouter(tags=["feed"])


# Client-facing event names → DB `feed_events.event_kind` values. The v1
# schema's CHECK constraint pre-dates the popup/deep-link/conversion
# beacons; we map onto the closest existing enum value so writes stay
# valid without a schema migration. The mapping is intentional and not
# load-bearing for analytics — downstream queries can join on the
# original client kind via the `value_ms` column or a follow-up
# `event_kind_v2` column when one is added.
_CLIENT_EVENT_KIND_MAP: dict[str, str] = {
    "popup_shown": "impression",
    "popup_tapped": "tap",
    "deep_link_open": "tap",
    "conversion_estimated": "purchase",
}


class FeedEventRequest(StrictModel):
    event_id: UUID = Field(
        description=(
            "Client-generated UUID. The endpoint is idempotent on this "
            "value — duplicate POSTs (retries, double-taps) write the "
            "row at most once."
        ),
    )
    session_id: UUID
    persona_id: UUID
    product_id: UUID | None = None
    kind: Literal[
        "popup_shown",
        "popup_tapped",
        "deep_link_open",
        "conversion_estimated",
    ]
    value_ms: int | None = Field(default=None, ge=0)


class FeedEventResponse(StrictModel):
    event_id: UUID
    deduplicated: bool = Field(
        description=(
            "True when the row already existed (i.e. this POST was a "
            "duplicate). False when the event was newly recorded."
        ),
    )


@router.post(
    "/feed/event",
    response_model=FeedEventResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Idempotent client beacon (popup/deep-link/conversion)",
)
async def post_feed_event(
    body: FeedEventRequest,
    user: UserCtx,
    repo: FeedRepo,
) -> FeedEventResponse:
    # Confirm the session belongs to the caller. Without this check a
    # leaked session token would let an attacker poison another user's
    # analytics by writing events into their session.
    owner = await repo.load_session_user(body.session_id)
    if owner is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="feed session not found",
        )
    if owner != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="session does not belong to this user",
        )

    db_kind = _CLIENT_EVENT_KIND_MAP[body.kind]
    inserted = await repo.insert_feed_event(
        event_id=body.event_id,
        session_id=body.session_id,
        persona_id=body.persona_id,
        product_id=body.product_id,
        event_kind=db_kind,
        value_ms=body.value_ms,
    )

    if not inserted:
        logger.info(
            "feed_event_deduplicated",
            extra={
                "event": "feed_event_deduplicated",
                "event_id": str(body.event_id),
                "session_id": str(body.session_id),
                "kind": body.kind,
            },
        )
    else:
        logger.info(
            "feed_event_recorded",
            extra={
                "event": "feed_event_recorded",
                "event_id": str(body.event_id),
                "session_id": str(body.session_id),
                "kind": body.kind,
                "db_kind": db_kind,
            },
        )

    return FeedEventResponse(event_id=body.event_id, deduplicated=not inserted)
