"""Favorites listing + pin endpoints (consumer surface).

Split out of ``soshop.api.routes.personas`` so the persona module
stays focused on persona-centric surfaces (profile, clips, chat
dispatch, favorite toggle). The favorite *toggle* — which mutates a
specific persona's favorite state — remains in ``personas.py`` because
it's keyed by ``persona_id``; the listing and pin operations here are
keyed by the caller.

Endpoints (mounted at ``/``):

  GET  /favorites                         → { favorites: [FavoriteResponse] }
  POST /favorites/{persona_id}/pin         { pinned }

Pinning is currently a write-through best-effort: the v1 schema's
``favorites`` table has no ``pinned_at`` column, so the pin POST is
accepted and the favorite is left in place; the response shape carries
``is_pinned``/``pinned_at`` so the client doesn't have to branch on a
missing field. Adding a real ``pinned_at`` column is a follow-up.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, status

from soshop.auth.identity import UserCtx
from soshop_server.api.deps import DbConn, StrictModel

logger = logging.getLogger("soshop.api.favorites")

router = APIRouter(tags=["favorites"])


def _persona_handle_from_name(name: str) -> str:
    """The schema has no `handle` column, so we synthesize one from
    `name` for the profile shape until a column lands."""
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or "persona"


class FavoriteResponse(StrictModel):
    id: UUID
    display_name: str
    handle: str
    avatar_url: str | None
    is_pinned: bool
    pinned_at: datetime | None


class FavoritesListResponse(StrictModel):
    favorites: list[FavoriteResponse]


@router.get(
    "/favorites",
    response_model=FavoritesListResponse,
    summary="List the caller's favorited personas",
)
async def list_favorites(
    user: UserCtx,
    conn: DbConn,
) -> FavoritesListResponse:
    rows = await conn.fetch(
        """
        SELECT p.id, p.name, p.avatar_image_url, f.created_at
        FROM favorites f
        JOIN personas p ON p.id = f.persona_id
        WHERE f.user_id = $1
        ORDER BY f.created_at DESC
        """,
        user.user_id,
    )
    favorites = [
        FavoriteResponse(
            id=r["id"],
            display_name=r["name"],
            handle=_persona_handle_from_name(r["name"]),
            avatar_url=r["avatar_image_url"],
            # Pinning persistence isn't in the schema yet; surface the
            # field so a client can bind to it without branching on
            # missing keys.
            is_pinned=False,
            pinned_at=None,
        )
        for r in rows
    ]
    return FavoritesListResponse(favorites=favorites)


class PinRequest(StrictModel):
    pinned: bool


@router.post(
    "/favorites/{persona_id}/pin",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Pin or unpin a favorite (best-effort; persistence pending)",
)
async def set_favorite_pin(
    body: PinRequest,
    user: UserCtx,
    conn: DbConn,
    persona_id: Annotated[UUID, Path()],
) -> None:
    favorited = await conn.fetchval(
        "SELECT 1 FROM favorites WHERE user_id = $1 AND persona_id = $2",
        user.user_id,
        persona_id,
    )
    if favorited is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="favorite not found",
        )
    # No `pinned_at` column in v1; accept and log so the API is
    # contract-correct. A follow-up adds the column + write path.
    logger.info(
        "favorite_pin_pending_persistence",
        extra={
            "event": "favorite_pin_pending_persistence",
            "user_id": user.user_id,
            "persona_id": str(persona_id),
            "pinned": body.pinned,
        },
    )


__all__ = ["router"]
