"""Persona profile endpoints (consumer surface).

Backs the consumer persona-profile surfaces. All payloads are
snake_case on the wire, matching the rest of the consumer API. Auth is
the user session (bearer token), the same dependency used by
``/feed/*``, so any client shares one auth dialect.

Endpoints (mounted at ``/``):

  GET  /personas/{persona_id}                  → PersonaProfileResponse
  GET  /personas/{persona_id}/clips            → { clips: [ClipResponse] }
  POST /personas/{persona_id}/favorite         { favorited }
  POST /personas/{persona_id}/chat/dispatch    → { url, token }

The favorites listing (``GET /favorites``) and pin endpoints
(``POST /favorites/{persona_id}/pin``) live in the sibling module
``soshop.api.routes.favorites``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Path, status

from soshop.agent.schemas import DispatchMetadata
from soshop.api.routes._persona_text import persona_handle_from_name, split_traits
from soshop.api.routes.feed import PLUG_AGENT_NAME, LkApi, UserCtx
from soshop.livekit.dispatch import (
    create_validated_dispatch,
    mint_room_access_token,
)
from soshop_server.api.deps import DbConn, StrictModel
from soshop_server.core.config import settings

logger = logging.getLogger("soshop.api.personas")

router = APIRouter(tags=["personas"])


# ---------------------------------------------------------------------------
# Persona profile
# ---------------------------------------------------------------------------


class PersonaProfileResponse(StrictModel):
    id: UUID
    display_name: str
    handle: str
    backstory: str
    traits: list[str]
    avatar_url: str | None
    is_favorited: bool


@router.get(
    "/personas/{persona_id}",
    response_model=PersonaProfileResponse,
    summary="Get persona profile (consumer)",
)
async def get_persona_profile(
    user: UserCtx,
    conn: DbConn,
    persona_id: Annotated[UUID, Path()],
) -> PersonaProfileResponse:
    row = await conn.fetchrow(
        """
        SELECT id, name, avatar_image_url, backstory, traits
        FROM personas
        WHERE id = $1
        """,
        persona_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="persona not found",
        )

    favorited = await conn.fetchval(
        "SELECT 1 FROM favorites WHERE user_id = $1 AND persona_id = $2",
        user.user_id,
        persona_id,
    )

    return PersonaProfileResponse(
        id=row["id"],
        display_name=row["name"],
        handle=persona_handle_from_name(row["name"]),
        backstory=row["backstory"] or "",
        traits=split_traits(row["traits"]),
        avatar_url=row["avatar_image_url"],
        is_favorited=favorited is not None,
    )


# ---------------------------------------------------------------------------
# Persona clips
# ---------------------------------------------------------------------------


class ClipResponse(StrictModel):
    id: str
    recorded_at: datetime
    duration_seconds: float
    transcript: str | None
    audio_url: str | None


class ClipsListResponse(StrictModel):
    clips: list[ClipResponse]


@router.get(
    "/personas/{persona_id}/clips",
    response_model=ClipsListResponse,
    summary="List the user's recorded clips for this persona",
)
async def list_persona_clips(
    user: UserCtx,
    conn: DbConn,
    persona_id: Annotated[UUID, Path()],
) -> ClipsListResponse:
    # The schema has no `clips` table yet; this route is forward-looking
    # and tolerates an empty list. Confirm the persona exists so a typo'd
    # id surfaces a 404 rather than silently returning [].
    exists = await conn.fetchval("SELECT 1 FROM personas WHERE id = $1", persona_id)
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="persona not found",
        )
    # Touch `user` so unused-arg lint is happy and so the dependency
    # actually runs; the empty response is per-user even though no row
    # store exists yet.
    _ = user.user_id
    return ClipsListResponse(clips=[])


# ---------------------------------------------------------------------------
# Favorite toggle
# ---------------------------------------------------------------------------


class FavoriteRequest(StrictModel):
    favorited: bool


@router.post(
    "/personas/{persona_id}/favorite",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Favorite or un-favorite a persona for the caller",
)
async def set_persona_favorite(
    body: FavoriteRequest,
    user: UserCtx,
    conn: DbConn,
    persona_id: Annotated[UUID, Path()],
) -> None:
    exists = await conn.fetchval("SELECT 1 FROM personas WHERE id = $1", persona_id)
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="persona not found",
        )
    if body.favorited:
        await conn.execute(
            """
            INSERT INTO favorites (user_id, persona_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            user.user_id,
            persona_id,
        )
    else:
        await conn.execute(
            "DELETE FROM favorites WHERE user_id = $1 AND persona_id = $2",
            user.user_id,
            persona_id,
        )


# ---------------------------------------------------------------------------
# Persona chat dispatch — mints a fresh 1:1 LiveKit room
# ---------------------------------------------------------------------------


class ChatDispatchRequest(StrictModel):
    persona_id: UUID


class ChatDispatchResponse(StrictModel):
    url: str
    token: str


@router.post(
    "/personas/{persona_id}/chat/dispatch",
    response_model=ChatDispatchResponse,
    summary="Dispatch a fresh 1:1 LiveKit room for chatting with this persona",
)
async def dispatch_persona_chat(
    body: ChatDispatchRequest,
    user: UserCtx,
    api_client: LkApi,
    conn: DbConn,
    persona_id: Annotated[UUID, Path()],
) -> ChatDispatchResponse:
    if body.persona_id != persona_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path persona_id does not match body persona_id",
        )

    exists = await conn.fetchval("SELECT 1 FROM personas WHERE id = $1", persona_id)
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="persona not found",
        )

    if not (settings.LIVEKIT_API_KEY and settings.LIVEKIT_API_SECRET and settings.LIVEKIT_URL):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "livekit_not_configured",
                "message": "LiveKit credentials are not configured on this server.",
            },
        )

    # Pick the persona's first script as the dispatch script. Empty
    # script catalogue → 503 because the agent needs *something* to say.
    script_row = await conn.fetchrow(
        """
        SELECT id, product_id
        FROM persona_scripts
        WHERE persona_id = $1
        ORDER BY weight DESC
        LIMIT 1
        """,
        persona_id,
    )
    if script_row is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "persona_has_no_scripts",
                "message": "Persona has no scripts; cannot dispatch a chat agent.",
            },
        )

    room = f"chat-{uuid4().hex}"
    metadata = DispatchMetadata(
        persona_id=str(persona_id),
        product_id=str(script_row["product_id"]) if script_row["product_id"] else None,
        script_id=str(script_row["id"]),
        session_id=f"chat-{user.user_id}-{uuid4().hex[:8]}",
        user_id=user.user_id,
        pitch_window_ms=0,
    )
    await create_validated_dispatch(
        api_client,
        agent_name=PLUG_AGENT_NAME,
        room=room,
        metadata=metadata,
    )
    token = mint_room_access_token(
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
        identity=user.user_id,
        room=room,
        metadata=metadata,
    )
    logger.info(
        "persona_chat_dispatched",
        extra={
            "event": "persona_chat_dispatched",
            "persona_id": str(persona_id),
            "room": room,
            "user_id": user.user_id,
        },
    )
    return ChatDispatchResponse(url=settings.LIVEKIT_URL, token=token)


__all__ = ["router"]
