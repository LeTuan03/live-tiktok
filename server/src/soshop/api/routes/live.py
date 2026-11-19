"""Live-room viewer-token endpoint.

Mints the viewer-scoped LiveKit token the web consumer feed uses to join
a public live room.

`POST /live/{room_id}/viewer-token` is soft-auth: it accepts a bearer
token when present (so signed-in viewers carry their user_id into
LiveKit) but also issues an anonymous viewer identity when no
Authorization header is supplied. Browsing live shopping streams is a
read-only surface; sign-in is gated at write-time (Buy / Bid) so a casual
viewer does not have to authenticate just to watch.

Emits the canonical error envelope from `soshop_server.api.errors`.

Endpoint (mounted at `/`):

  POST /live/{room_id}/viewer-token   -> { url, token, room_state }

`room_state` is computed best-effort from the LiveKit room metadata; in
the absence of a configured backend (tests, local dev) the server returns
``"live"`` so the client UI can still render. In-room chat is delivered
over the LiveKit data channel directly (the web client publishes via text
streams), so there is no server chat endpoint.
"""

from __future__ import annotations

import logging
import re
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import Field

from soshop.agent.schemas import DispatchMetadata
from soshop.api.routes.feed import OptionalUserCtx
from soshop.livekit.dispatch import mint_room_access_token
from soshop_server.api.deps import StrictModel
from soshop_server.core.config import settings

logger = logging.getLogger("soshop.api.live")

router = APIRouter(tags=["live"])


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

# LiveKit room names must be url-safe; this matches the regex used by
# `feed.create_feed_session` (lower-case slug or hex). Reject anything
# weirder to keep the path predictable for ops.
_ROOM_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,127}$")


def _validate_room_id(room_id: str) -> str:
    if not _ROOM_ID_PATTERN.match(room_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_room_id", "message": "room_id is malformed"},
        )
    return room_id


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


# Wire format is snake_case to match the rest of the API. Web's old
# camelCase contract (`roomState`, `clientId`, `createdAt`) is replaced
# in this same change set.
LiveRoomState = Literal["scheduled", "live", "ended"]


class ViewerTokenRequest(StrictModel):
    role: Literal["viewer"] = Field(
        default="viewer",
        description="Reserved for future role-based grants. Only `viewer` is accepted today.",
    )


class ViewerTokenResponse(StrictModel):
    url: str
    token: str
    room_state: LiveRoomState


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/live/{room_id}/viewer-token",
    response_model=ViewerTokenResponse,
    summary="Mint a viewer-scoped LiveKit token for a public live room",
)
async def issue_viewer_token(
    body: ViewerTokenRequest,
    user: OptionalUserCtx,
    room_id: str = Path(min_length=1, max_length=128),
) -> ViewerTokenResponse:
    room = _validate_room_id(room_id)
    # `role` is reserved for future use; the typed Literal already
    # constrains the value, but we touch it so static analysis sees
    # the field as consumed.
    _ = body.role

    if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "livekit_not_configured",
                "message": "LiveKit credentials are not configured on this server.",
            },
        )
    if not settings.LIVEKIT_URL:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "livekit_not_configured",
                "message": "LIVEKIT_URL is not configured on this server.",
            },
        )

    # Anonymous viewers get an `anon-<uuid>` identity; signed-in viewers
    # are tagged with their user id so the agent can correlate
    # interactions back to a session. The viewing surface itself has no
    # write access — sign-in is enforced at purchase / bid / chat time.
    viewer_id = user.user_id if user is not None else f"anon-{uuid4().hex[:8]}"
    # Viewer token has no persona/product context — the dispatch metadata
    # is optional from the agent's perspective, so we send a minimal
    # placeholder document that satisfies the type while not pretending
    # the viewer is the owner of any pitch.
    metadata = DispatchMetadata(
        persona_id="viewer",
        script_id="viewer",
        session_id=f"viewer-{viewer_id}",
        user_id=viewer_id,
        pitch_window_ms=0,
    )
    token = mint_room_access_token(
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
        identity=f"viewer-{viewer_id}-{uuid4().hex[:8]}",
        room=room,
        metadata=metadata,
    )
    logger.info(
        "live_viewer_token_issued",
        extra={
            "event": "live_viewer_token_issued",
            "room": room,
            "user_id": user.user_id if user is not None else None,
            "anonymous": user is None,
        },
    )
    return ViewerTokenResponse(
        url=settings.LIVEKIT_URL,
        token=token,
        # The server has no live-room state store yet; "live" is the
        # safe default once a token has been minted. Future work may
        # consult the LiveKit room API to surface "scheduled"/"ended".
        room_state="live",
    )
