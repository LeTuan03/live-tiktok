"""Unit tests for the channel reconciler.

The reconciler is tested with a stateful in-memory fake for both
``lkapi`` and the asyncpg connection. The DB fake models just the SQL
shapes the reconciler issues (an ads count, the channels SELECT, and
the ON CONFLICT UPSERT / retire UPDATE) — it does not pretend to be a
general Postgres mock. The "no mocked DB" rule in ``server/CLAUDE.md``
is about preventing migration drift in integration tests; here we are
unit-testing the reconciler's *coordination* (which dispatches it
creates, which channel rows it upserts), and the SQL itself is exercised
by integration tests / migration runs against a real Neon when present.

Covers the three acceptance bullets:

* ``reconcile_channels(2)`` with 40 active ads yields ``ad_start_index``
  ``[1, 21]`` on ordinals ``[0, 1]``.
* A second call with identical inputs creates no new dispatches and no
  new rooms (idempotent).
* ``reconcile_channels(3)`` after ``(2)`` re-shuffles dispatches to
  ``[1, 14, 27]``, deleting the stale ordinal-1 dispatch.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from livekit.api import TwirpError

from soshop.livekit.channels import (
    AGENT_PARTICIPANT_KIND,
    PLUG_AGENT_NAME,
    ROOM_EMPTY_TIMEOUT_S,
    _list_dispatches,
    compute_channel_assignments,
    reconcile_channels,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeAgentDispatchService:
    """Stateful fake for ``LiveKitAPI.agent_dispatch``.

    On ``create_dispatch`` the fake also seeds an AGENT-kind participant
    into the linked ``_FakeRoomService`` so the reconciler's liveness
    check (``_room_has_running_agent``) sees the agent as present —
    mirroring what happens in production when a worker accepts the
    dispatch and joins the room.
    """

    def __init__(self, room_service: _FakeRoomService) -> None:
        self._room = room_service
        self._dispatches: dict[str, Any] = {}
        self._counter = 0
        self.create_calls: list[Any] = []
        self.delete_calls: list[tuple[str, str]] = []
        self.list_calls: list[str] = []

    async def create_dispatch(self, request: Any) -> Any:
        self.create_calls.append(request)
        self._counter += 1
        dispatch = SimpleNamespace(
            id=f"AD_{self._counter:04d}",
            agent_name=request.agent_name,
            room=request.room,
            metadata=request.metadata,
        )
        self._dispatches[dispatch.id] = dispatch
        self._room.add_agent(request.room, identity=f"agent-{dispatch.id}")
        return dispatch

    async def delete_dispatch(self, *, dispatch_id: str, room_name: str) -> None:
        self.delete_calls.append((dispatch_id, room_name))
        self._dispatches.pop(dispatch_id, None)

    async def list_dispatch(self, *, room_name: str) -> list[Any]:
        self.list_calls.append(room_name)
        return [d for d in self._dispatches.values() if d.room == room_name]


class _FakeRoomService:
    """Stateful fake for ``LiveKitAPI.room``."""

    def __init__(self) -> None:
        self.create_calls: list[Any] = []
        self.rooms: set[str] = set()
        self.list_participants_calls: list[str] = []
        # Map room name -> list of ``SimpleNamespace(kind=int, identity=str)``.
        self._participants: dict[str, list[Any]] = {}

    async def create_room(self, request: Any) -> Any:
        self.create_calls.append(request)
        self.rooms.add(request.name)
        return SimpleNamespace(name=request.name)

    async def list_participants(self, request: Any) -> Any:
        self.list_participants_calls.append(request.room)
        return SimpleNamespace(participants=list(self._participants.get(request.room, [])))

    def add_agent(self, room: str, *, identity: str) -> None:
        """Test helper: place an AGENT-kind participant in a room."""
        self._participants.setdefault(room, []).append(
            SimpleNamespace(kind=AGENT_PARTICIPANT_KIND, identity=identity)
        )

    def clear_participants(self, room: str) -> None:
        """Test helper: simulate the worker leaving (e.g. process crash)."""
        self._participants.pop(room, None)


class _FakeLkApi:
    """Top-level LiveKit API fake the reconciler operates against."""

    def __init__(self) -> None:
        self.room = _FakeRoomService()
        self.agent_dispatch = _FakeAgentDispatchService(self.room)


class _FakeChannelsDB:
    """Stateful in-memory stand-in for the channels table + ads count.

    Honors just the SQL shapes the reconciler emits:

    * ``SELECT count(*) FROM ads WHERE status = 'active'``
    * ``SELECT … FROM channels ORDER BY ordinal``
    * ``INSERT INTO channels (…) … ON CONFLICT (id) DO UPDATE … RETURNING …``
    * ``UPDATE channels SET status = 'retired' … WHERE id = $1``

    Any other query raises ``NotImplementedError`` so a future SQL change
    in the reconciler fails loudly in tests rather than silently.
    """

    def __init__(self, *, active_ads_count: int = 0) -> None:
        self.active_ads_count = active_ads_count
        self.rows: dict[str, dict[str, Any]] = {}
        # Personas the reconciler will discover when looking up the
        # ordered seller list. Tests that care about persona binding
        # can populate this; the default empty list keeps existing
        # specs (which don't assert on persona_id) green.
        self.persona_ids: list[str] = []

    async def fetchval(self, query: str, *args: Any) -> Any:
        q = " ".join(query.lower().split())
        if "select count(*) from ads where status = 'active'" in q:
            return self.active_ads_count
        raise NotImplementedError(f"fetchval not handled: {query!r}")

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        q = " ".join(query.lower().split())
        if "from channels" in q and "order by ordinal" in q:
            return [dict(r) for r in sorted(self.rows.values(), key=lambda r: r["ordinal"])]
        if "from personas" in q and "order by name" in q:
            return [{"id": pid} for pid in self.persona_ids]
        raise NotImplementedError(f"fetch not handled: {query!r}")

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        q = " ".join(query.lower().split())
        if q.startswith("insert into channels"):
            channel_id, ordinal, ad_start_index, dispatch_id, persona_id = args
            now = datetime.now(UTC)
            existing = self.rows.get(channel_id)
            if existing is None:
                row = {
                    "id": channel_id,
                    "ordinal": ordinal,
                    "livekit_room_name": channel_id,
                    "current_ad_sequence": ad_start_index,
                    "agent_dispatch_id": dispatch_id,
                    "status": "running",
                    "last_heartbeat_at": None,
                    "created_at": now,
                    "updated_at": now,
                    "persona_id": persona_id,
                }
                self.rows[channel_id] = row
            else:
                # UPDATE path: preserve current_ad_sequence + persona_id,
                # reset the rest. The real ON CONFLICT clause does the
                # same (persona_id is intentionally sticky).
                existing.update(
                    {
                        "ordinal": ordinal,
                        "livekit_room_name": channel_id,
                        "agent_dispatch_id": dispatch_id,
                        "status": "running",
                        "updated_at": now,
                    }
                )
                row = existing
            return dict(row)
        raise NotImplementedError(f"fetchrow not handled: {query!r}")

    async def execute(self, query: str, *args: Any) -> None:
        q = " ".join(query.lower().split())
        if "update channels" in q and "'retired'" in q:
            (channel_id,) = args
            if channel_id in self.rows:
                self.rows[channel_id]["status"] = "retired"
                self.rows[channel_id]["agent_dispatch_id"] = None
            return
        raise NotImplementedError(f"execute not handled: {query!r}")


# ---------------------------------------------------------------------------
# Pure stride math
# ---------------------------------------------------------------------------


def test_compute_channel_assignments_2_channels_40_ads() -> None:
    assert compute_channel_assignments(2, 40) == [
        (0, "channel-0", 1),
        (1, "channel-1", 21),
    ]


def test_compute_channel_assignments_3_channels_40_ads() -> None:
    # floor(40 / 3) == 13 → starts at 1, 14, 27
    assert compute_channel_assignments(3, 40) == [
        (0, "channel-0", 1),
        (1, "channel-1", 14),
        (2, "channel-2", 27),
    ]


def test_compute_channel_assignments_zero_target() -> None:
    assert compute_channel_assignments(0, 40) == []


def test_compute_channel_assignments_zero_ads() -> None:
    # Empty rotation: every channel starts at 1 (the channel still exists,
    # the agent just has nothing to air yet).
    assert compute_channel_assignments(2, 0) == [
        (0, "channel-0", 1),
        (1, "channel-1", 1),
    ]


# ---------------------------------------------------------------------------
# reconcile_channels — acceptance bullets
# ---------------------------------------------------------------------------


async def test_reconcile_channels_2_yields_ordinals_and_starts_1_21() -> None:
    lk = _FakeLkApi()
    db = _FakeChannelsDB(active_ads_count=40)

    result = await reconcile_channels(2, lkapi=lk, conn=db)  # type: ignore[arg-type]

    assert [c.ordinal for c in result] == [0, 1]
    assert [c.id for c in result] == ["channel-0", "channel-1"]
    assert [c.current_ad_sequence for c in result] == [1, 21]

    # Two rooms created, both with the 24h empty-timeout safety net.
    assert [r.name for r in lk.room.create_calls] == ["channel-0", "channel-1"]
    assert {r.empty_timeout for r in lk.room.create_calls} == {ROOM_EMPTY_TIMEOUT_S}

    # Two dispatches created carrying the channel_id + ad_start_index.
    metadatas = [json.loads(req.metadata) for req in lk.agent_dispatch.create_calls]
    assert [m["channel_id"] for m in metadatas] == ["channel-0", "channel-1"]
    assert [m["ad_start_index"] for m in metadatas] == [1, 21]
    assert {req.agent_name for req in lk.agent_dispatch.create_calls} == {PLUG_AGENT_NAME}


async def test_reconcile_channels_is_idempotent_on_re_run() -> None:
    lk = _FakeLkApi()
    db = _FakeChannelsDB(active_ads_count=40)

    first = await reconcile_channels(2, lkapi=lk, conn=db)  # type: ignore[arg-type]
    creates_after_first = list(lk.agent_dispatch.create_calls)
    deletes_after_first = list(lk.agent_dispatch.delete_calls)

    second = await reconcile_channels(2, lkapi=lk, conn=db)  # type: ignore[arg-type]

    # Idempotency is about *effect*, not API-call count: LiveKit's
    # CreateRoom is itself idempotent server-side, but no *new* dispatch
    # should be created or deleted on the second pass.
    assert lk.agent_dispatch.create_calls == creates_after_first
    assert lk.agent_dispatch.delete_calls == deletes_after_first

    # Channel rows look identical between runs (current_ad_sequence
    # preserved on the UPDATE-path upsert).
    assert [c.id for c in first] == [c.id for c in second]
    assert [c.current_ad_sequence for c in first] == [c.current_ad_sequence for c in second]
    assert [c.agent_dispatch_id for c in first] == [c.agent_dispatch_id for c in second]


async def test_reconcile_channels_3_reshuffles_starts_to_1_14_27() -> None:
    lk = _FakeLkApi()
    db = _FakeChannelsDB(active_ads_count=40)

    await reconcile_channels(2, lkapi=lk, conn=db)  # type: ignore[arg-type]
    result = await reconcile_channels(3, lkapi=lk, conn=db)  # type: ignore[arg-type]

    assert [c.id for c in result] == ["channel-0", "channel-1", "channel-2"]
    assert [c.ordinal for c in result] == [0, 1, 2]

    # Final dispatch state — exactly one dispatch per channel with the
    # expected `ad_start_index`. channel-0's dispatch is preserved from
    # the first run (start unchanged at 1); channel-1's old dispatch is
    # replaced (start moved from 21 → 14); channel-2 is freshly created.
    dispatches_by_room: dict[str, list[Any]] = {}
    for d in lk.agent_dispatch._dispatches.values():
        dispatches_by_room.setdefault(d.room, []).append(d)

    expected_starts = {"channel-0": 1, "channel-1": 14, "channel-2": 27}
    for room, expected_start in expected_starts.items():
        room_dispatches = dispatches_by_room.get(room, [])
        assert len(room_dispatches) == 1, (
            f"expected one dispatch on {room}, got {len(room_dispatches)}"
        )
        meta = json.loads(room_dispatches[0].metadata)
        assert meta["channel_id"] == room
        assert meta["ad_start_index"] == expected_start

    # The stale channel-1 (start=21) dispatch must have been explicitly
    # deleted before its replacement was created.
    assert any(room == "channel-1" for _, room in lk.agent_dispatch.delete_calls)


# ---------------------------------------------------------------------------
# Retirement path — channels with ordinal >= target_count
# ---------------------------------------------------------------------------


async def test_reconcile_channels_retires_dropped_ordinals() -> None:
    """Scaling 3 → 2 must mark channel-2 retired and delete its dispatch."""
    lk = _FakeLkApi()
    db = _FakeChannelsDB(active_ads_count=40)

    await reconcile_channels(3, lkapi=lk, conn=db)  # type: ignore[arg-type]
    creates_after_first = len(lk.agent_dispatch.create_calls)

    result = await reconcile_channels(2, lkapi=lk, conn=db)  # type: ignore[arg-type]

    # Only the surviving two channels are returned.
    assert [c.id for c in result] == ["channel-0", "channel-1"]

    # channel-2's row is marked retired.
    assert db.rows["channel-2"]["status"] == "retired"
    assert db.rows["channel-2"]["agent_dispatch_id"] is None

    # channel-2's dispatch is gone from the LiveKit fake.
    rooms_with_dispatch = {d.room for d in lk.agent_dispatch._dispatches.values()}
    assert "channel-2" not in rooms_with_dispatch

    # Going from (3) to (2), only channel-1's start changes (21 → 21? no,
    # at N=3 start was 14, at N=2 start is 21). So channel-1 gets a new
    # dispatch; channel-0 keeps its dispatch; channel-2 is deleted with
    # no replacement.
    new_creates = len(lk.agent_dispatch.create_calls) - creates_after_first
    assert new_creates == 1  # only the replacement for channel-1


# ---------------------------------------------------------------------------
# Stale-dispatch recovery — agent worker died between reconciles
# ---------------------------------------------------------------------------


async def test_reconcile_recreates_dispatch_when_no_agent_in_room() -> None:
    """A matching dispatch with no AGENT participant must be replaced.

    Reproduces the production bug where an API-server restart would
    find dispatches lingering on LiveKit's side from the prior run,
    treat them as already-reconciled, and never re-launch the channel
    agents — leaving viewers connected to empty rooms.
    """
    lk = _FakeLkApi()
    db = _FakeChannelsDB(active_ads_count=40)

    await reconcile_channels(2, lkapi=lk, conn=db)  # type: ignore[arg-type]
    creates_after_first = len(lk.agent_dispatch.create_calls)

    # Simulate the agent worker processes dying — dispatches remain
    # listed on LiveKit but no AGENT participants are in the rooms.
    lk.room.clear_participants("channel-0")
    lk.room.clear_participants("channel-1")

    await reconcile_channels(2, lkapi=lk, conn=db)  # type: ignore[arg-type]

    # Both stale dispatches were deleted and two fresh ones created.
    new_creates = len(lk.agent_dispatch.create_calls) - creates_after_first
    assert new_creates == 2
    deleted_rooms = [room for _, room in lk.agent_dispatch.delete_calls]
    assert sorted(deleted_rooms) == ["channel-0", "channel-1"]

    # The replacement dispatches re-seed agent participants so a third
    # reconcile in a row is once again a no-op.
    creates_after_second = len(lk.agent_dispatch.create_calls)
    await reconcile_channels(2, lkapi=lk, conn=db)  # type: ignore[arg-type]
    assert len(lk.agent_dispatch.create_calls) == creates_after_second


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


async def test_reconcile_channels_rejects_negative_target() -> None:
    lk = _FakeLkApi()
    db = _FakeChannelsDB()
    with pytest.raises(ValueError, match="target_count"):
        await reconcile_channels(-1, lkapi=lk, conn=db)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _list_dispatches — LiveKit TwirpError handling
# ---------------------------------------------------------------------------


def _lkapi_listing(raises: Exception) -> Any:
    """Minimal lkapi whose ``agent_dispatch.list_dispatch`` always raises."""

    async def _list_dispatch(*, room_name: str) -> list[Any]:
        raise raises

    return SimpleNamespace(agent_dispatch=SimpleNamespace(list_dispatch=_list_dispatch))


async def test_list_dispatches_swallows_not_found_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A `not_found` TwirpError (room aged out / never existed) is benign:
    zero dispatches, and it must NOT surface as a warning."""
    lk = _lkapi_listing(TwirpError("not_found", "room missing", status=404))

    with caplog.at_level("WARNING"):
        result = await _list_dispatches(lk, room_name="channel-0")

    assert result == []
    assert "list_dispatch failed" not in caplog.text


async def test_list_dispatches_warns_on_other_twirp_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Any other TwirpError code still returns no dispatches but is logged
    as a warning so the failure stays visible."""
    lk = _lkapi_listing(TwirpError("unavailable", "livekit down", status=503))

    with caplog.at_level("WARNING"):
        result = await _list_dispatches(lk, room_name="channel-0")

    assert result == []
    assert "list_dispatch failed for room=channel-0" in caplog.text
