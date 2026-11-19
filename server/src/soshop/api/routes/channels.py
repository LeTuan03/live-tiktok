"""Public consumer API for the always-on channel rotation.

Three routes, mounted at the app root:

* ``GET /channels`` — anonymous; one entry per running channel with a
  freshly-minted **viewer** LiveKit token (subscribe-only). The
  channel-ring feed opens N rooms in parallel and only subscribes
  to the active one, so each request returns N tokens, one per
  channel, pre-paired with the channel's ``livekit_url``.
* ``GET /channels/{id}/current`` — anonymous; the ad currently airing
  on a channel, used by the shop CTA overlay.
* ``POST /admin/channels/reconcile`` — org-admin gated; triggers
  :func:`soshop.livekit.channels.reconcile_channels` against
  ``settings.CHANNEL_COUNT`` so ``N`` can be tuned at runtime without
  redeploying.

The viewer-token grant is intentionally narrower than the
``mint_room_access_token`` helper used by ``/feed/session`` and
``/live/{room}/viewer-token``: those grant ``can_publish=True`` so a
user can publish audio into a 1:1 pitch room. Channels are
broadcast-only for media (``can_publish=False``), but anonymous
viewers still need ``can_publish_data=True`` so they can fan-out
chat messages on the ``global_chat`` text-stream topic to the rest
of the room. The data permission is the chat-only seam — viewers
cannot publish media tracks, only data messages.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, status

from soshop.api.routes._channels_queries import (
    CHANNEL_CURRENT_AD_SQL,
    RUNNING_CHANNELS_WITH_AD_SQL,
    AdSnapshot,
    row_to_ad_snapshot,
)
from soshop.auth.identity import OrgContext, require_org_admin
from soshop.livekit.channels import reconcile_channels
from soshop_server.api.deps import DbConn, StrictModel
from soshop_server.core.config import settings
from soshop_server.db.models import Channel

logger = logging.getLogger("soshop.api.channels")

router = APIRouter(tags=["channels"])


OrgAdmin = Annotated[OrgContext, Depends(require_org_admin)]


# Viewer tokens outlive a casual browsing session. 6 hours is the floor
# from the spec; long enough for a user to put a phone in their
# pocket and come back to the same room without re-fetching.
VIEWER_TOKEN_TTL_SECONDS = 6 * 60 * 60


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ChannelOut(StrictModel):
    id: str
    ordinal: int
    livekit_url: str
    livekit_token: str
    current_ad: AdSnapshot | None


class CurrentAdResponse(StrictModel):
    ad: AdSnapshot | None


class ReconcileResponse(StrictModel):
    """Result of the admin reconcile call — same channel rows as DB."""

    channels: list[Channel]


# ---------------------------------------------------------------------------
# Token mint helper (anon viewer)
# ---------------------------------------------------------------------------


def mint_anon_viewer_token(
    *,
    api_key: str,
    api_secret: str,
    room: str,
    ttl_seconds: int = VIEWER_TOKEN_TTL_SECONDS,
) -> tuple[str, str]:
    """Mint a subscribe-only LiveKit JWT for an anonymous channel viewer.

    Mirrors :func:`soshop.livekit.dispatch.mint_room_access_token` but
    grants ``can_publish=False`` (no audio/video tracks) and attaches no
    ``DispatchMetadata``: the channel agent already carries the rotation
    context on its own dispatch. ``can_publish_data=True`` is granted so
    the viewer can send text-stream chat messages on the ``global_chat``
    topic — the SFU still rejects any attempt to publish a media track.

    Returns ``(identity, token)``. The identity is the synthesized
    ``anon-<uuid>`` string so downstream telemetry on the LiveKit side
    sees a stable participant id.
    """
    if not api_key or not api_secret:
        raise ValueError("api_key and api_secret are required to mint a token")
    if not room:
        raise ValueError("room is required")

    from livekit.api import AccessToken, VideoGrants

    identity = f"anon-{uuid4().hex}"
    grants = VideoGrants(
        room=room,
        room_join=True,
        can_publish=False,
        can_publish_data=True,
        can_subscribe=True,
    )
    token = (
        AccessToken(api_key=api_key, api_secret=api_secret)
        .with_identity(identity)
        .with_grants(grants)
        .with_ttl(timedelta(seconds=ttl_seconds))
    ).to_jwt()
    return identity, token


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/channels",
    response_model=list[ChannelOut],
    summary="List running channels with viewer tokens and the current ad",
)
async def list_channels(conn: DbConn) -> list[ChannelOut]:
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

    rows = await conn.fetch(RUNNING_CHANNELS_WITH_AD_SQL)

    out: list[ChannelOut] = []
    for row in rows:
        room_name = row["livekit_room_name"]
        _, token = mint_anon_viewer_token(
            api_key=settings.LIVEKIT_API_KEY,
            api_secret=settings.LIVEKIT_API_SECRET,
            room=room_name,
        )
        out.append(
            ChannelOut(
                id=row["channel_id"],
                ordinal=int(row["ordinal"]),
                livekit_url=settings.LIVEKIT_URL,
                livekit_token=token,
                current_ad=row_to_ad_snapshot(row),
            )
        )

    logger.info(
        "channels_listed",
        extra={
            "event": "channels_listed",
            "channel_count": len(out),
        },
    )
    return out


@router.get(
    "/channels/{channel_id}/current",
    response_model=CurrentAdResponse,
    summary="Return the ad currently airing on a channel",
)
async def get_channel_current_ad(
    conn: DbConn,
    channel_id: Annotated[str, Path(min_length=1, max_length=128)],
) -> CurrentAdResponse:
    row = await conn.fetchrow(CHANNEL_CURRENT_AD_SQL, channel_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "channel_not_found", "message": "channel does not exist"},
        )
    return CurrentAdResponse(ad=row_to_ad_snapshot(row))


@router.post(
    "/admin/channels/reconcile",
    response_model=ReconcileResponse,
    summary="Admin-only: reconcile the channel set to settings.CHANNEL_COUNT",
)
async def admin_reconcile_channels(
    ctx: OrgAdmin,
) -> ReconcileResponse:
    # `reconcile_channels` falls back to the process-wide LiveKitAPI
    # singleton (via `feed.get_livekit_api`) and acquires its own pool
    # connection when neither is supplied — we lean on that here so the
    # admin path runs without holding a request-scoped DB connection
    # across the LiveKit round-trips.
    channels = await reconcile_channels(settings.CHANNEL_COUNT)
    logger.info(
        "channels_reconciled_via_admin",
        extra={
            "event": "channels_reconciled_via_admin",
            "user_id": ctx.user_id,
            "org_id": ctx.org_id,
            "target_count": settings.CHANNEL_COUNT,
            "result_count": len(channels),
        },
    )
    return ReconcileResponse(channels=channels)
