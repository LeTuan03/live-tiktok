"""Feed API routes — session creation and cursor pagination.

This module owns two routes:

  POST /feed/session   -- create session + acquire cost slot,
                          optionally dispatch the first persona's agent
                          and mint a LiveKit join token.
  GET  /feed/cursor    -- next batch of `(persona, product?, script_id,
                          is_ad)` items for a session, ranked by a
                          `FeedRankingService`.

`/feed/swipe` (persona swap) and `/feed/event`
(analytics beacon) live in sibling modules `feed_swipe.py` and
`feed_event.py`. Their routers are aggregated into this module's
top-level `router` at the bottom of this file, so callers continue to
see a single `feed.router`.

Shared plumbing — user auth (``soshop.auth.identity``),
LiveKit API singleton (``soshop.livekit.client``) and the
process-local feed-session registry (``_feed_registry``) — was extracted
in #18. The historical re-export block at the bottom of this module
keeps the legacy import paths from ``soshop.api.routes.feed`` working
for existing callers/tests.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import Field

from soshop.agent.schemas import DispatchMetadata, PopupPayload
from soshop.api.routes._feed_registry import (
    PLUG_AGENT_NAME,
    FeedSessionState,
    Registry,
)
from soshop.api.schemas.feed import (
    FeedCursorResponse,
    FeedItemOut,
    PersonaOut,
)
from soshop.auth.identity import (
    OptionalUserCtx,
    anonymous_viewer_id,
    assert_user_matches,
)
from soshop.livekit.client import LkApi
from soshop.livekit.dispatch import (
    create_validated_dispatch,
    mint_room_access_token,
)
from soshop.livekit.swap_mode import SwapMode, generate_sibling_room_name, get_swap_mode
from soshop.repositories.feed import (
    CANDIDATES_SQL,
    RECENT_PERSONAS_SQL,
    FeedRepository,
)
from soshop.services.cost_limits import (
    CostLimitsService,
    cost_limits_from_env,
)
from soshop.services.feed_ranking import (
    FeedCandidate,
    FeedRankingService,
    WeightedRandomFeedRanker,
    decode_cursor,
    encode_cursor,
)
from soshop_server.api.deps import DbConn, FeedRepo, StrictModel
from soshop_server.core.config import settings

logger = logging.getLogger("soshop.api.feed")

router = APIRouter(tags=["feed"])


# ---------------------------------------------------------------------------
# Ranker dependency
# ---------------------------------------------------------------------------

# Module-level default. Tests / future ML rollouts override via FastAPI's
# `app.dependency_overrides[get_feed_ranker] = ...`.
_DEFAULT_RANKER: FeedRankingService = WeightedRandomFeedRanker()


def get_feed_ranker() -> FeedRankingService:
    return _DEFAULT_RANKER


Ranker = Annotated[FeedRankingService, Depends(get_feed_ranker)]


# ---------------------------------------------------------------------------
# Cost-limits dependency
# ---------------------------------------------------------------------------

# Module-level singleton built from env on first use. Tests override via
# `app.dependency_overrides[get_cost_limits] = ...` to inject a fake
# implementation with deterministic counters.
_DEFAULT_COST_LIMITS: CostLimitsService | None = None


def get_cost_limits() -> CostLimitsService:
    global _DEFAULT_COST_LIMITS
    if _DEFAULT_COST_LIMITS is None:
        _DEFAULT_COST_LIMITS = cost_limits_from_env()
    return _DEFAULT_COST_LIMITS


CostLimits = Annotated[CostLimitsService, Depends(get_cost_limits)]


# In v1 every persona is an AI creator. Surface this as a constant so the
# route doesn't need a per-persona DB lookup just to populate the flag —
# when non-AI creators are added, this becomes a column read.
_PERSONA_IS_AI_CREATOR_V1 = True


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------

# SQL constants and per-row helpers now live on `FeedRepository` (see
# `soshop.repositories.feed`). The module-level `_load_*` shims below
# stay so existing tests can import them; route handlers use the repo
# directly via `FeedRepo`.

# Re-exported under the private names that tests have historically
# imported from this module — the SQL is now authoritative on the repo.
_CANDIDATES_SQL = CANDIDATES_SQL
_RECENT_PERSONAS_SQL = RECENT_PERSONAS_SQL

# Session-id cooldown window — keep aligned with the AC ("last 8 swipes").
_COOLDOWN_WINDOW = 8


async def _load_recent_personas(conn: asyncpg.Connection, user_id: str, window: int) -> list[UUID]:
    return await FeedRepository(conn).load_recent_personas(user_id, window)


async def _load_candidates(conn: asyncpg.Connection) -> list[FeedCandidate]:
    return await FeedRepository(conn).load_candidates()


# ---------------------------------------------------------------------------
# /feed/session — cost-guardrails gate + LiveKit token issuance
# ---------------------------------------------------------------------------


class FirstPersonaSpec(StrictModel):
    """Persona/script context for the first slot of a session.

    When the client supplies this on session creation, the server
    dispatches the agent into the first LiveKit room and returns a
    join token in the response. Optional so the cost-only flow
    keeps working for clients that haven't migrated yet.
    """

    persona_id: str = Field(min_length=1)
    script_id: str = Field(min_length=1)
    product_id: str | None = None
    pitch_window_ms: int = Field(default=30_000, ge=0)
    popup_payload: PopupPayload | None = None


class FeedSessionRequest(StrictModel):
    user_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Optional viewer id. When provided, must match the request's resolved user identity."
        ),
    )
    device_id: str | None = Field(default=None, max_length=128)
    app_version: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    first_persona: FirstPersonaSpec | None = None
    dispatch_head: bool = Field(
        default=False,
        description=(
            "When true and `first_persona` is omitted, the server runs "
            "the feed ranker, picks the top-weighted candidate, "
            "dispatches the agent into a fresh LiveKit room, and "
            "returns the chosen slot in `head_slot` along with an "
            "`initial_cursor` whose `shown` set already contains the "
            "head — so a subsequent `/feed/cursor` call does not "
            "re-emit the same persona. Ignored when `first_persona` is "
            "supplied (the client picked the head itself) and when no "
            "eligible candidates exist."
        ),
    )


class FeedSessionCreated(StrictModel):
    session_id: UUID
    avatar_mode: Literal["avatar", "non_avatar"]
    minutes_used: float
    minutes_limit: int
    seconds_until_reset: int
    # The fields below are populated when `first_persona` is supplied
    # on the request. They are `None` for legacy callers
    # that only want the cost-gate behavior.
    room_name: str | None = None
    livekit_url: str | None = None
    livekit_token: str | None = None
    persona_id: str | None = None
    dispatch_id: str | None = None
    swap_mode: Literal["in_place", "single_room", "ping_pong"] | None = None
    # Populated when `dispatch_head=true` chose a candidate or when the
    # client supplied `first_persona` and we resolved the avatar URL.
    # Lets the client pre-seat the active feed slot with the same
    # persona the agent was just dispatched for, instead of waiting on
    # `/feed/cursor` and risking a head/agent mismatch.
    head_slot: FeedItemOut | None = None
    # Cursor token whose `shown` set already includes the head_slot —
    # pass this to the next `/feed/cursor` call so the head is not
    # re-emitted in the queue.
    initial_cursor: str | None = None


async def _resolve_head_slot(
    repo: FeedRepository,
    ranker: FeedRankingService,
    user_id: str,
) -> tuple[FirstPersonaSpec | None, FeedItemOut | None, str | None]:
    """Run the ranker, build the synthesized first-persona spec + head slot.

    Returns ``(spec, head_slot, initial_cursor)`` — all ``None`` when
    no candidates exist. The spec mirrors what a client-supplied
    ``first_persona`` would look like so the dispatch path stays
    path-agnostic.
    """
    candidates = await repo.load_candidates()
    recent = await repo.load_recent_personas(user_id, _COOLDOWN_WINDOW)
    rank_result = ranker.rank(candidates, recent_personas=recent, limit=1, cursor=None)
    if not rank_result.items:
        return None, None, None
    picked = rank_result.items[0]
    spec = FirstPersonaSpec(
        persona_id=str(picked.persona_id),
        script_id=str(picked.script_id),
        product_id=str(picked.product_id) if picked.product_id else None,
    )
    persona_meta = await repo.load_persona_metas([picked.persona_id])
    head_products = await repo.load_products(
        [picked.product_id] if picked.product_id is not None else []
    )
    picked_meta = persona_meta.get(picked.persona_id)
    head_slot = FeedItemOut(
        persona=PersonaOut(
            id=picked.persona_id,
            is_ai_creator=_PERSONA_IS_AI_CREATOR_V1,
            display_name=picked_meta.name if picked_meta else None,
            avatar_url=picked_meta.avatar_url if picked_meta else None,
        ),
        product=picked.product_id,
        product_details=(
            head_products.get(picked.product_id) if picked.product_id is not None else None
        ),
        script_id=picked.script_id,
        is_ad=picked.is_ad,
    )
    return spec, head_slot, encode_cursor(rank_result.next_cursor)


async def _dispatch_first_persona(
    *,
    api_client: LkApi,  # type: ignore[name-defined]
    spec: FirstPersonaSpec,
    room_name: str,
    session_id: UUID,
    user_id: str,
    registry: Registry,  # type: ignore[name-defined]
) -> tuple[str, str]:
    """Dispatch the agent + mint a viewer token, register the session.

    Returns ``(livekit_token, dispatch_id)``.
    """
    metadata = DispatchMetadata(
        persona_id=spec.persona_id,
        product_id=spec.product_id,
        script_id=spec.script_id,
        pitch_window_ms=spec.pitch_window_ms,
        popup_payload=spec.popup_payload,
        session_id=str(session_id),
        user_id=user_id,
    )
    dispatch = await create_validated_dispatch(
        api_client,
        agent_name=PLUG_AGENT_NAME,
        room=room_name,
        metadata=metadata,
    )
    token = mint_room_access_token(
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
        identity=user_id,
        room=room_name,
        metadata=metadata,
    )
    registry.upsert(
        FeedSessionState(
            session_id=str(session_id),
            user_id=user_id,
            room_name=room_name,
            dispatch_id=dispatch.id,
            persona_id=spec.persona_id,
        )
    )
    return token, dispatch.id


@router.post(
    "/feed/session",
    response_model=FeedSessionCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a feed session (cost-gated; mints LiveKit token if first_persona supplied)",
    deprecated=True,
)
async def create_feed_session(
    body: FeedSessionRequest,
    user: OptionalUserCtx,
    conn: DbConn,
    repo: FeedRepo,
    cost: CostLimits,
    registry: Registry,
    api_client: LkApi,
    ranker: Ranker,
    response: Response,
) -> FeedSessionCreated:
    response.headers["X-Deprecated"] = "1"
    # Anonymous browsers can bootstrap a session for the read-only feed.
    # Sign-in is enforced at write time (chat post, Buy / Bid).
    if user is not None:
        assert_user_matches(user.user_id, body.user_id)
        user_id = user.user_id
    else:
        user_id = anonymous_viewer_id()

    # 1. Global concurrent-room cap. Hard 503 with Retry-After when full.
    room_slot = await cost.check_room_slot(conn)
    if not room_slot.granted:
        logger.warning(
            "feed_session_rejected_concurrent_rooms_exhausted",
            extra={
                "event": "feed_session_rejected_concurrent_rooms_exhausted",
                "user_id": user_id,
                "active_count": room_slot.active_count,
                "limit": room_slot.limit,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "concurrent_rooms_exhausted",
                "active_count": room_slot.active_count,
                "limit": room_slot.limit,
            },
            headers={"Retry-After": str(room_slot.retry_after_seconds)},
        )

    # 2. Per-user daily LemonSlice-minute cap. Soft downgrade rather than 503.
    minutes = await cost.check_user_minutes(conn, user_id)
    avatar_mode: Literal["avatar", "non_avatar"] = (
        "avatar" if minutes.avatar_allowed else "non_avatar"
    )

    # 3. Resolve which persona (if any) to dispatch into the first room.
    resolved_first_persona: FirstPersonaSpec | None = body.first_persona
    head_slot: FeedItemOut | None = None
    initial_cursor_token: str | None = None
    if body.first_persona is None and body.dispatch_head:
        resolved_first_persona, head_slot, initial_cursor_token = await _resolve_head_slot(
            repo, ranker, user_id
        )

    # 4. Decide on a room name up front so the DB row records it.
    swap_mode = get_swap_mode()
    room_name: str | None = None
    if resolved_first_persona is not None:
        room_name = (
            generate_sibling_room_name(str(uuid4()))
            if swap_mode is SwapMode.PING_PONG
            else f"feed-{uuid4().hex}"
        )

    session_id = await repo.insert_feed_session(
        user_id=user_id,
        room_name=room_name,
        device_id=body.device_id,
        app_version=body.app_version,
        country=body.country,
    )

    # 5. Dispatch the agent and mint a LiveKit join token when a first
    #    persona is in play. Cost-only callers that omit both
    #    `first_persona` and `dispatch_head` skip this entirely.
    livekit_token: str | None = None
    livekit_url: str | None = None
    persona_id: str | None = None
    dispatch_id: str | None = None
    if resolved_first_persona is not None and room_name is not None:
        livekit_token, dispatch_id = await _dispatch_first_persona(
            api_client=api_client,
            spec=resolved_first_persona,
            room_name=room_name,
            session_id=session_id,
            user_id=user_id,
            registry=registry,
        )
        livekit_url = settings.LIVEKIT_URL
        persona_id = resolved_first_persona.persona_id

    logger.info(
        "feed_session_created",
        extra={
            "event": "feed_session_created",
            "session_id": str(session_id),
            "user_id": user_id,
            "avatar_mode": avatar_mode,
            "active_count": room_slot.active_count,
            "concurrent_limit": room_slot.limit,
            "minutes_used": minutes.minutes_used,
            "minutes_limit": minutes.minutes_limit,
            "room_name": room_name,
            "swap_mode": swap_mode.value if resolved_first_persona else None,
            "dispatch_id": dispatch_id,
            "persona_id": persona_id,
            "auto_dispatched_head": (head_slot is not None and body.first_persona is None),
        },
    )

    return FeedSessionCreated(
        session_id=session_id,
        avatar_mode=avatar_mode,
        minutes_used=minutes.minutes_used,
        minutes_limit=minutes.minutes_limit,
        seconds_until_reset=minutes.seconds_until_utc_midnight,
        room_name=room_name,
        livekit_url=livekit_url,
        livekit_token=livekit_token,
        persona_id=persona_id,
        dispatch_id=dispatch_id,
        swap_mode=swap_mode.value if resolved_first_persona else None,
        head_slot=head_slot,
        initial_cursor=initial_cursor_token,
    )


@router.get(
    "/feed/cursor",
    response_model=FeedCursorResponse,
    summary="Cursor-paginated feed of (persona, product?) pairs",
    deprecated=True,
)
async def get_feed_cursor(
    repo: FeedRepo,
    ranker: Ranker,
    user: OptionalUserCtx,
    response: Response,
    session_id: Annotated[UUID, Query(description="feed_sessions.id for this viewer")],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    cursor: Annotated[str | None, Query(description="opaque token from a previous page")] = None,
) -> FeedCursorResponse:
    response.headers["X-Deprecated"] = "1"
    user_id = await repo.load_session_user(session_id)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="feed session not found",
        )
    # The session's owning user is authoritative. Signed-in callers must
    # match the session's owner so a leaked session_id can't be steered
    # by another logged-in user. Anonymous callers piggyback on the
    # session id alone — sessions for `anon-<hex>` viewers were minted
    # without a Bearer JWT in the first place.
    if user is not None and user_id != user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="session does not belong to this user",
        )

    candidates = await repo.load_candidates()
    recent = await repo.load_recent_personas(user_id, _COOLDOWN_WINDOW)

    parsed_cursor = decode_cursor(cursor)
    result = ranker.rank(
        candidates,
        recent_personas=recent,
        limit=limit,
        cursor=parsed_cursor,
    )

    persona_meta = await repo.load_persona_metas([item.persona_id for item in result.items])
    products = await repo.load_products(
        [item.product_id for item in result.items if item.product_id is not None],
    )

    def _persona_out(persona_id: UUID) -> PersonaOut:
        meta = persona_meta.get(persona_id)
        return PersonaOut(
            id=persona_id,
            is_ai_creator=_PERSONA_IS_AI_CREATOR_V1,
            display_name=meta.name if meta else None,
            avatar_url=meta.avatar_url if meta else None,
        )

    return FeedCursorResponse(
        items=[
            FeedItemOut(
                persona=_persona_out(item.persona_id),
                product=item.product_id,
                product_details=(
                    products.get(item.product_id) if item.product_id is not None else None
                ),
                script_id=item.script_id,
                is_ad=item.is_ad,
            )
            for item in result.items
        ],
        next_cursor=encode_cursor(result.next_cursor) if result.items else None,
    )


# ---------------------------------------------------------------------------
# Backward-compatible re-exports
# ---------------------------------------------------------------------------
#
# Several sibling route modules and historical tests import these names
# from `soshop.api.routes.feed`. The concrete definitions now live in
# `soshop.auth.identity`, `soshop.livekit.client`, and
# `_feed_registry`, but importers keep working unchanged via the aliases
# below.

_anonymous_viewer_id = anonymous_viewer_id
_assert_user_matches = assert_user_matches

from soshop.api.routes._feed_registry import (  # noqa: E402, F401
    FeedSessionRegistry,
    get_feed_session_registry,
)
from soshop.auth.identity import (  # noqa: E402, F401
    UserContext,
    UserCtx,
    maybe_user,
    require_user,
)
from soshop.livekit.client import close_livekit_api, get_livekit_api  # noqa: E402, F401
