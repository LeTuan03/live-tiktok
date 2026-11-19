"""Always-on channel reconciler.

Ensures exactly ``N`` LiveKit rooms (one per channel ordinal) exist, each
with a single ``plug-agent`` dispatch that tells the worker which slot of
the global ad rotation to start at. Reconciliation runs on FastAPI startup
and is re-runnable from the admin endpoint to scale ``N`` at
runtime.

Stride math (must stay in sync with the channel-feed clients,
which assume the same formula when prefetching upcoming ads)::

    ad_start_index = i * floor(M / N) + 1

where:

* ``i`` is the channel ordinal (``0..N-1``)
* ``N`` is the target channel count (``settings.CHANNEL_COUNT``)
* ``M`` is the count of active ads (``SELECT count(*) FROM ads WHERE status='active'``)

The rotation is 1-indexed because ``ads.sequence_order`` is 1-indexed in
the schema. Channel ordinals are 0-indexed; channel IDs are derived as
``f"channel-{i}"``.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from livekit.api import TwirpError

from soshop.agent.schemas import DispatchMetadata
from soshop.livekit.dispatch import build_dispatch_request
from soshop_server.core.config import settings
from soshop_server.db.models import Channel
from soshop_server.db.pool import get_pool

if TYPE_CHECKING:
    import asyncpg
    from livekit.api import LiveKitAPI
    from livekit.protocol.agent_dispatch import AgentDispatch

__all__ = [
    "AGENT_PARTICIPANT_KIND",
    "PLUG_AGENT_NAME",
    "ROOM_EMPTY_TIMEOUT_S",
    "compute_channel_assignments",
    "reconcile_channels",
]


def _agent_participant_kind() -> int:
    """Return ``ParticipantInfo.Kind.AGENT`` from the LiveKit proto enum.

    Resolved lazily so the channel reconciler doesn't pay the
    ``livekit.protocol.models`` import cost at module load — only
    callers of ``_room_has_running_agent`` need the value, and they
    only run inside the reconcile loop. Importing live (rather than
    hardcoding ``4``) means a renumbered or re-ordered ``Kind`` enum
    in a future LiveKit release surfaces as an import error rather
    than silent miscompare.
    """
    from livekit.protocol.models import ParticipantInfo

    return int(ParticipantInfo.Kind.AGENT)


# Public constant for callers that need to compare against the AGENT
# participant kind (e.g. tests). Resolved at import-time so module-level
# `from ... import AGENT_PARTICIPANT_KIND` keeps working unchanged.
AGENT_PARTICIPANT_KIND = _agent_participant_kind()
"""``ParticipantInfo.Kind.AGENT`` from the LiveKit proto enum."""

logger = logging.getLogger("soshop.livekit.channels")

PLUG_AGENT_NAME = settings.AGENT_NAME
"""Name registered by the channel-rotation agent worker. Reads
`settings.AGENT_NAME` so it stays in lockstep with the worker registration
and can be overridden per environment."""

ROOM_EMPTY_TIMEOUT_S = 86_400
"""Seconds LiveKit keeps an empty room alive.

The agent staying connected is the real permanence guarantee; this value
is a safety net for the window between an agent crash and the next
reconcile call.
"""


def compute_channel_assignments(
    target_count: int, total_active_ads: int
) -> list[tuple[int, str, int]]:
    """Return ``(ordinal, channel_id, ad_start_index)`` tuples.

    Pure function — exposed for unit-testing the stride math without
    spinning up a LiveKit fake or DB connection. The returned list has
    one entry per ordinal in ``[0, target_count)``; when
    ``target_count <= 0`` the result is empty.

    ``ad_start_index = i * floor(M / N) + 1``. When ``total_active_ads``
    is 0 every channel starts at ``1`` (the rotation is empty but the
    channel row still exists).
    """
    if target_count <= 0:
        return []
    stride = total_active_ads // target_count
    return [(i, f"channel-{i}", i * stride + 1) for i in range(target_count)]


def _channel_dispatch_metadata(channel_id: str, ad_start_index: int) -> DispatchMetadata:
    """Build the ``DispatchMetadata`` payload for a channel-rotation agent.

    ``DispatchMetadata`` was originally a per-pitch schema; channel
    dispatches share the same envelope so the agent worker has a single
    validated shape to parse on startup. The channel agent recognizes
    channel mode via ``channel_id is not None`` and ignores the
    per-pitch fields (which are filled with stable sentinels here so the
    schema's ``min_length=1`` constraints pass).
    """
    return DispatchMetadata(
        persona_id="channel-rotation",
        product_id=None,
        script_id="channel-rotation",
        pitch_window_ms=0,
        popup_payload=None,
        session_id=channel_id,
        user_id="channel-rotation",
        channel_id=channel_id,
        ad_start_index=ad_start_index,
    )


async def _count_active_ads(conn: asyncpg.Connection) -> int:
    value = await conn.fetchval(
        "SELECT count(*) FROM ads WHERE status = 'active'",
    )
    return int(value or 0)


async def _load_channels(conn: asyncpg.Connection) -> list[Channel]:
    rows = await conn.fetch(
        """
        SELECT id, ordinal, livekit_room_name, current_ad_sequence,
               agent_dispatch_id, status, last_heartbeat_at,
               created_at, updated_at, persona_id
        FROM channels
        ORDER BY ordinal
        """
    )
    return [Channel.model_validate(dict(r)) for r in rows]


async def _upsert_channel(
    conn: asyncpg.Connection,
    *,
    channel_id: str,
    ordinal: int,
    ad_start_index: int,
    dispatch_id: str | None,
    persona_id: str | None,
) -> Channel:
    """Insert a channel row or update an existing one.

    On INSERT, ``current_ad_sequence`` starts at ``ad_start_index`` and
    ``persona_id`` is stamped to the value supplied by the reconciler
    (typically the ``ordinal``-th persona ordered by name).
    On UPDATE, ``current_ad_sequence`` and ``persona_id`` are both
    *preserved* — the agent loop advances the cursor as it airs ads,
    and a reconcile must not rewind it; the persona binding is
    intentionally sticky so an operator-set seller stays put across
    reconcile calls. The dispatch id, status, and ordinal are always
    reset to the reconciled values.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO channels (
            id, ordinal, livekit_room_name, current_ad_sequence,
            agent_dispatch_id, status, persona_id
        )
        VALUES ($1, $2, $1, $3, $4, 'running', $5)
        ON CONFLICT (id) DO UPDATE SET
            ordinal           = EXCLUDED.ordinal,
            livekit_room_name = EXCLUDED.livekit_room_name,
            agent_dispatch_id = EXCLUDED.agent_dispatch_id,
            status            = 'running',
            updated_at        = NOW()
        RETURNING id, ordinal, livekit_room_name, current_ad_sequence,
                  agent_dispatch_id, status, last_heartbeat_at,
                  created_at, updated_at
        """,
        channel_id,
        ordinal,
        ad_start_index,
        dispatch_id,
        persona_id,
    )
    if row is None:
        raise RuntimeError(f"upsert returned no row for channel {channel_id!r}")
    return Channel.model_validate(dict(row))


async def _load_persona_ids_ordered(conn: asyncpg.Connection) -> list[str]:
    """Return active persona ids in stable order (by ``name``).

    The reconciler zips this list against channel ordinals to assign a
    fixed seller per slot — channel-0 takes the alphabetically-first
    persona, channel-1 the second, and so on. Name ordering is a
    deliberate choice: it's stable, recoverable from inspection, and
    leaves the *display* order independent of insertion timing (which
    drifts with seed runs). Personas without ``status='active'`` are
    excluded so retired sellers don't claim a channel slot.
    """
    rows = await conn.fetch(
        "SELECT id FROM personas WHERE status = 'active' ORDER BY name",
    )
    return [str(r["id"]) for r in rows]


async def _retire_channel(conn: asyncpg.Connection, *, channel_id: str) -> None:
    await conn.execute(
        """
        UPDATE channels
        SET status = 'retired',
            agent_dispatch_id = NULL,
            updated_at = NOW()
        WHERE id = $1
        """,
        channel_id,
    )


async def _ensure_room(lkapi: LiveKitAPI, *, room_name: str) -> None:
    """Idempotently ensure a LiveKit room exists with the safety-net timeout.

    LiveKit's ``CreateRoom`` is upsert-ish — re-creating an existing room
    is generally fine. We swallow errors here because the next call
    (dispatch creation) is what actually matters for correctness; a
    spurious ``create_room`` failure on a room that already exists must
    not abort reconciliation.
    """
    from livekit.api import CreateRoomRequest

    try:
        await lkapi.room.create_room(
            CreateRoomRequest(
                name=room_name,
                empty_timeout=ROOM_EMPTY_TIMEOUT_S,
            )
        )
    except Exception:
        logger.warning(
            "create_room failed for room=%s; assuming already exists",
            room_name,
            exc_info=True,
        )


def _dispatch_matches(
    dispatch: AgentDispatch,
    *,
    agent_name: str,
    channel_id: str,
    ad_start_index: int,
) -> bool:
    """Return True if the dispatch already has the desired metadata."""
    if dispatch.agent_name != agent_name:
        return False
    try:
        payload = json.loads(dispatch.metadata)
    except (ValueError, TypeError):
        return False
    return (
        payload.get("channel_id") == channel_id and payload.get("ad_start_index") == ad_start_index
    )


async def _room_has_running_agent(lkapi: LiveKitAPI, *, room_name: str) -> bool:
    """Return True iff the room currently has a participant of kind AGENT.

    A dispatch row sticks around on LiveKit's side after the worker that
    accepted it has died (e.g. the worker process was killed and
    restarted). ``list_dispatch`` will still surface that stale record,
    so ``_ensure_dispatch``'s "matching dispatch already exists" branch
    would short-circuit and we'd leave the channel without an actual
    agent in the room. Treat the room participants as the authoritative
    signal: if no AGENT-kind participant is present, the dispatch is
    stale regardless of what ``list_dispatch`` claims.
    """
    from livekit.api import ListParticipantsRequest

    try:
        resp = await lkapi.room.list_participants(ListParticipantsRequest(room=room_name))
    except Exception:
        # If we can't tell, assume the agent is present and let the
        # reconciler reuse the existing dispatch. The room may not yet
        # exist on a first-ever reconcile; the subsequent create_room
        # call handles that path.
        logger.warning(
            "list_participants failed for room=%s; assuming agent present",
            room_name,
            exc_info=True,
        )
        return True
    return any(p.kind == AGENT_PARTICIPANT_KIND for p in resp.participants)


async def _ensure_dispatch(
    lkapi: LiveKitAPI,
    *,
    room_name: str,
    channel_id: str,
    ad_start_index: int,
) -> str:
    """Ensure exactly one ``plug-agent`` dispatch with a *live* worker.

    Returns the dispatch id. If an existing dispatch matches the desired
    metadata **and** an agent participant is actually present in the
    room, the dispatch is reused. Otherwise (metadata mismatch *or*
    dispatch orphaned by a dead worker) all existing dispatches are
    deleted and a fresh one is created.

    ``create_dispatch`` may raise on transient conflicts; the caller
    surfaces such errors but the happy-path on a re-run is a no-op
    because the matching dispatch short-circuits at the top.
    """
    existing = await _list_dispatches(lkapi, room_name=room_name)
    matching = next(
        (
            d
            for d in existing
            if _dispatch_matches(
                d,
                agent_name=PLUG_AGENT_NAME,
                channel_id=channel_id,
                ad_start_index=ad_start_index,
            )
        ),
        None,
    )

    # An existing matching dispatch is only honored when an agent is
    # actually in the room. Without that liveness check a restart that
    # leaves stale dispatch rows on LiveKit's side would never re-launch
    # the channel agents, and viewers would join empty rooms.
    if matching is not None and not await _room_has_running_agent(lkapi, room_name=room_name):
        logger.info(
            "channel_dispatch_stale_no_agent_in_room",
            extra={
                "event": "channel_dispatch_stale_no_agent_in_room",
                "room": room_name,
                "channel_id": channel_id,
                "dispatch_id": matching.id,
            },
        )
        matching = None

    stale = [d for d in existing if d is not matching]

    for d in stale:
        try:
            await lkapi.agent_dispatch.delete_dispatch(dispatch_id=d.id, room_name=room_name)
        except Exception:
            logger.warning(
                "delete_dispatch failed for stale dispatch=%s room=%s",
                d.id,
                room_name,
                exc_info=True,
            )

    if matching is not None:
        return matching.id

    metadata = _channel_dispatch_metadata(channel_id, ad_start_index)
    request = build_dispatch_request(
        agent_name=PLUG_AGENT_NAME,
        room=room_name,
        metadata=metadata,
    )
    try:
        created = await lkapi.agent_dispatch.create_dispatch(request)
        return created.id
    except Exception:
        logger.warning(
            "create_dispatch raised for room=%s; re-listing to reconcile",
            room_name,
            exc_info=True,
        )
        # Re-list and accept whatever matching dispatch now exists.
        for d in await _list_dispatches(lkapi, room_name=room_name):
            if _dispatch_matches(
                d,
                agent_name=PLUG_AGENT_NAME,
                channel_id=channel_id,
                ad_start_index=ad_start_index,
            ):
                return d.id
        raise


async def _list_dispatches(lkapi: LiveKitAPI, *, room_name: str) -> list[AgentDispatch]:
    try:
        return await lkapi.agent_dispatch.list_dispatch(room_name=room_name)
    except TwirpError as e:
        if e.code == "not_found":
            # Room aged out (empty_timeout) or never existed: trivially zero dispatches.
            logger.debug("list_dispatch: room=%s absent; no dispatches", room_name)
            return []
        logger.warning(
            "list_dispatch failed for room=%s; assuming no dispatches",
            room_name,
            exc_info=True,
        )
        return []
    except Exception:
        logger.warning(
            "list_dispatch failed for room=%s; assuming no dispatches",
            room_name,
            exc_info=True,
        )
        return []


async def _delete_room_dispatches(lkapi: LiveKitAPI, *, room_name: str) -> None:
    for d in await _list_dispatches(lkapi, room_name=room_name):
        try:
            await lkapi.agent_dispatch.delete_dispatch(dispatch_id=d.id, room_name=room_name)
        except Exception:
            logger.warning(
                "delete_dispatch failed for retire dispatch=%s room=%s",
                d.id,
                room_name,
                exc_info=True,
            )


async def reconcile_channels(
    target_count: int,
    *,
    lkapi: LiveKitAPI | None = None,
    conn: asyncpg.Connection | None = None,
) -> list[Channel]:
    """Ensure exactly ``target_count`` channels exist; retire the rest.

    Idempotent: calling twice with the same arguments produces no LiveKit
    side-effects on the second call (no new rooms or dispatches), and the
    DB rows are upserted into the same state.

    For each ordinal ``i ∈ [0, target_count)``:

    1. Compute ``ad_start_index = i * floor(M / N) + 1``.
    2. ``lkapi.room.create_room`` with ``empty_timeout=86400`` (idempotent).
    3. Reconcile a ``plug-agent`` dispatch carrying the channel's
       ``{channel_id, ad_start_index}`` (kept if it already matches).
    4. Upsert the ``channels`` row with the dispatch id; on INSERT
       seed ``current_ad_sequence`` to ``ad_start_index``.

    Existing channels with ``ordinal >= target_count`` are marked
    ``retired`` and have their dispatches deleted (rooms are left
    alone — LiveKit's ``empty_timeout`` will reap them once the agent
    disconnects).

    Args:
        target_count: Desired number of channels (``>= 0``). When ``0``
            every existing channel is retired.
        lkapi: LiveKit API client. Defaults to the process-wide singleton.
        conn: An asyncpg connection. Defaults to one acquired from the
            soshop_server pool.

    Returns:
        The reconciled list of ``Channel`` rows (one per ordinal in
        ``[0, target_count)``), in ordinal order.
    """
    if target_count < 0:
        raise ValueError("target_count must be >= 0")

    if lkapi is None:
        from soshop.api.routes.feed import get_livekit_api

        lkapi = await get_livekit_api()

    if conn is not None:
        return await _reconcile_with_conn(target_count, lkapi=lkapi, conn=conn)

    pool = await get_pool()
    async with pool.acquire() as acquired:
        return await _reconcile_with_conn(target_count, lkapi=lkapi, conn=acquired)


async def _reconcile_with_conn(
    target_count: int,
    *,
    lkapi: LiveKitAPI,
    conn: asyncpg.Connection,
) -> list[Channel]:
    total_active_ads = await _count_active_ads(conn)
    assignments = compute_channel_assignments(target_count, total_active_ads)
    desired_ids = {channel_id for _, channel_id, _ in assignments}
    # Each channel is locked to one seller. The reconciler stamps
    # the persona binding on fresh INSERTs only — existing rows keep
    # whatever an operator (or seed_v1.bind_channel_personas) set, so a
    # `CHANNEL_COUNT` bump never reassigns a seller that's already on
    # air. Personas beyond the channel count are simply unused.
    persona_ids_by_ordinal = await _load_persona_ids_ordered(conn)

    existing = await _load_channels(conn)
    for channel in existing:
        if channel.id in desired_ids:
            continue
        await _delete_room_dispatches(lkapi, room_name=channel.livekit_room_name)
        await _retire_channel(conn, channel_id=channel.id)

    reconciled: list[Channel] = []
    for ordinal, channel_id, ad_start_index in assignments:
        await _ensure_room(lkapi, room_name=channel_id)
        dispatch_id = await _ensure_dispatch(
            lkapi,
            room_name=channel_id,
            channel_id=channel_id,
            ad_start_index=ad_start_index,
        )
        persona_id_for_slot = (
            persona_ids_by_ordinal[ordinal] if ordinal < len(persona_ids_by_ordinal) else None
        )
        channel = await _upsert_channel(
            conn,
            channel_id=channel_id,
            ordinal=ordinal,
            ad_start_index=ad_start_index,
            dispatch_id=dispatch_id,
            persona_id=persona_id_for_slot,
        )
        reconciled.append(channel)
        logger.info(
            "channel_reconciled",
            extra={
                "event": "channel_reconciled",
                "channel_id": channel_id,
                "ordinal": ordinal,
                "ad_start_index": ad_start_index,
                "dispatch_id": dispatch_id,
                "total_active_ads": total_active_ads,
                "target_count": target_count,
            },
        )

    return reconciled
