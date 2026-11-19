"""Room provisioning lifecycle tests for LiveKit dispatch helpers.

The acceptance criteria require coverage of both:

* **Shared (1-to-many)** — `swap_persona_in_room` keeps a single room
  alive across persona swaps; many viewers can be subscribed at once.
  Used by `/feed/swipe` in `single_room` mode.
* **Per-client (1-to-1)** — `prewarm_room` mints a fresh sibling room and
  `dispose_room` tears it down. Used by `/feed/swipe` in `ping_pong`
  mode and by `/personas/{id}/chat/dispatch`. Each call yields
  a unique room name so concurrent users do not collide.

The tests run against fakes for `livekit.api.LiveKitAPI` so we don't need
a LiveKit service. The fakes implement just the methods the helpers call
(`agent_dispatch.create_dispatch`, `delete_dispatch`,
`room.list_participants`, `room.delete_room`).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from soshop.agent.schemas import DispatchMetadata
from soshop.livekit.dispatch import (
    AgentLeaveTimeout,
    dispose_room,
    prewarm_room,
    request_agent_leave,
    swap_persona_in_room,
    wait_for_room_agent_empty,
)
from soshop.livekit.swap_mode import generate_sibling_room_name

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeAgentDispatch:
    def __init__(self) -> None:
        self.created: list[Any] = []
        self.deleted: list[tuple[str, str]] = []

    async def create_dispatch(self, request: Any) -> Any:
        self.created.append(request)
        d = MagicMock()
        d.id = f"AD_{len(self.created):04d}"
        return d

    async def delete_dispatch(self, *, dispatch_id: str, room_name: str) -> None:
        self.deleted.append((dispatch_id, room_name))


class _FakeRoom:
    def __init__(
        self,
        *,
        agents: list[str] | None = None,
        participants_per_call: list[list[str]] | None = None,
    ) -> None:
        # `participants_per_call`: each entry is the agent identities to
        # report on the i-th `list_participants` call. Used to simulate a
        # room that becomes empty after a poll or two.
        self._calls = 0
        self._sequence = participants_per_call or [agents or []]
        self.deleted: list[str] = []

    async def list_participants(self, request: Any) -> Any:
        idx = min(self._calls, len(self._sequence) - 1)
        self._calls += 1
        ids = self._sequence[idx]
        from livekit.protocol.models import ParticipantInfo

        resp = MagicMock()
        resp.participants = [MagicMock(identity=i, kind=ParticipantInfo.AGENT) for i in ids]
        return resp

    async def delete_room(self, request: Any) -> None:
        self.deleted.append(request.room)


class _FakeLK:
    def __init__(
        self,
        *,
        agents: list[str] | None = None,
        participants_per_call: list[list[str]] | None = None,
    ) -> None:
        self.agent_dispatch = _FakeAgentDispatch()
        self.room = _FakeRoom(agents=agents, participants_per_call=participants_per_call)


def _md(session_id: str = "sess_lifecycle", persona_id: str = "persona_1") -> DispatchMetadata:
    return DispatchMetadata(
        persona_id=persona_id,
        product_id=None,
        script_id="script_1",
        pitch_window_ms=10_000,
        popup_payload=None,
        session_id=session_id,
        user_id="user_lifecycle",
    )


# ---------------------------------------------------------------------------
# Shared (1-to-many) — swap_persona_in_room
# ---------------------------------------------------------------------------


async def test_swap_persona_in_room_keeps_same_room_across_personas() -> None:
    """1-to-many path: one room, many personas dispatched in sequence."""
    lk = _FakeLK(agents=[])
    room = "feed-shared-1"
    result1 = await swap_persona_in_room(
        lk,  # type: ignore[arg-type]
        agent_name="plug-agent",
        room=room,
        next_metadata=_md(persona_id="persona_alex"),
        current_dispatch_id=None,
    )
    result2 = await swap_persona_in_room(
        lk,  # type: ignore[arg-type]
        agent_name="plug-agent",
        room=room,
        next_metadata=_md(persona_id="persona_bea"),
        current_dispatch_id=result1.new_dispatch.id,
    )

    assert result1.room == room
    assert result2.room == room
    # Two dispatches, both pointed at the same room.
    rooms_used = [r.room for r in lk.agent_dispatch.created]
    assert rooms_used == [room, room]
    # The second swap asked the first agent to leave first.
    assert lk.agent_dispatch.deleted == [(result1.new_dispatch.id, room)]


async def test_swap_persona_in_room_propagates_metadata() -> None:
    lk = _FakeLK(agents=[])
    md = _md(persona_id="persona_with_product")
    md = md.model_copy(update={"product_id": "prod_zzz"})
    result = await swap_persona_in_room(
        lk,  # type: ignore[arg-type]
        agent_name="plug-agent",
        room="feed-shared-2",
        next_metadata=md,
        current_dispatch_id=None,
    )
    assert result.new_dispatch.id.startswith("AD_")
    import json as _json

    payload = _json.loads(lk.agent_dispatch.created[0].metadata)
    assert payload["persona_id"] == "persona_with_product"
    assert payload["product_id"] == "prod_zzz"


async def test_wait_for_room_agent_empty_raises_on_timeout() -> None:
    """If the previous agent stays put past the budget, raise to surface it."""
    lk = _FakeLK(participants_per_call=[["agent-stuck"]] * 3)
    with pytest.raises(AgentLeaveTimeout):
        await wait_for_room_agent_empty(
            lk,  # type: ignore[arg-type]
            room="feed-shared-stuck",
            timeout_s=0.05,
            poll_interval_s=0.01,
        )


async def test_wait_for_room_agent_empty_polls_until_clear() -> None:
    """If the agent leaves on a later poll the helper resolves cleanly."""
    lk = _FakeLK(participants_per_call=[["agent-leaving"], []])
    await wait_for_room_agent_empty(
        lk,  # type: ignore[arg-type]
        room="feed-shared-clearing",
        timeout_s=1.0,
        poll_interval_s=0.0,
    )
    # Two `list_participants` calls — initial + post-leave confirmation.
    assert lk.room._calls >= 2


async def test_request_agent_leave_swallows_errors() -> None:
    """`delete_dispatch` must be best-effort so a missed leave doesn't fail
    the swipe — the wait-for-empty poll is the source of truth."""

    class _Boom:
        async def delete_dispatch(self, **_: Any) -> None:
            raise RuntimeError("already gone")

    fake = MagicMock()
    fake.agent_dispatch = _Boom()
    # Should not raise.
    await request_agent_leave(fake, room="r", dispatch_id="AD_1")


# ---------------------------------------------------------------------------
# Per-client (1-to-1) — prewarm_room + dispose_room
# ---------------------------------------------------------------------------


async def test_prewarm_room_creates_unique_rooms_per_call() -> None:
    """1-to-1 path: every prewarm yields a fresh room name."""
    lk = _FakeLK()
    rooms = set()
    for _ in range(5):
        result = await prewarm_room(
            lk,  # type: ignore[arg-type]
            agent_name="plug-agent",
            session_id="sess_xyz",
            metadata=_md(session_id="sess_xyz"),
        )
        rooms.add(result.room)
    assert len(rooms) == 5
    # Five dispatches, each into its own unique room.
    assert len(lk.agent_dispatch.created) == 5


async def test_prewarm_room_metadata_session_must_match() -> None:
    """`metadata.session_id` and `session_id` argument disagree → ValueError."""
    lk = _FakeLK()
    with pytest.raises(ValueError, match="session_id"):
        await prewarm_room(
            lk,  # type: ignore[arg-type]
            agent_name="plug-agent",
            session_id="sess_a",
            metadata=_md(session_id="sess_b"),
        )


async def test_prewarm_room_honors_explicit_room_name() -> None:
    """Tests pass a deterministic name so assertions are exact."""
    lk = _FakeLK()
    result = await prewarm_room(
        lk,  # type: ignore[arg-type]
        agent_name="plug-agent",
        session_id="sess_explicit",
        metadata=_md(session_id="sess_explicit"),
        room="custom-room-name",
    )
    assert result.room == "custom-room-name"


async def test_dispose_room_calls_delete_dispatch_and_delete_room() -> None:
    lk = _FakeLK()
    await dispose_room(
        lk,  # type: ignore[arg-type]
        room="some-room",
        dispatch_id="AD_x",
    )
    assert lk.agent_dispatch.deleted == [("AD_x", "some-room")]
    assert lk.room.deleted == ["some-room"]


async def test_dispose_room_skip_delete_when_flag_off() -> None:
    """A future call may want to keep the room alive (e.g. drained) — the
    `delete_room=False` path avoids the LiveKit DELETE."""
    lk = _FakeLK()
    await dispose_room(
        lk,  # type: ignore[arg-type]
        room="some-room",
        dispatch_id="AD_x",
        delete_room=False,
    )
    assert lk.agent_dispatch.deleted == [("AD_x", "some-room")]
    assert lk.room.deleted == []


async def test_dispose_room_swallows_delete_errors() -> None:
    """Cleanup must never raise — the caller has already returned to the
    user and reraising would orphan a request."""

    class _BoomRoom:
        async def delete_room(self, _req: Any) -> None:
            raise RuntimeError("unreachable")

    class _BoomLK:
        def __init__(self) -> None:
            self.agent_dispatch = _FakeAgentDispatch()
            self.room = _BoomRoom()

    lk = _BoomLK()
    # Should not raise.
    await dispose_room(lk, room="some-room", dispatch_id="AD_x")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Sibling-room name generation
# ---------------------------------------------------------------------------


def test_generate_sibling_room_name_unique_per_call() -> None:
    names = {generate_sibling_room_name("sess_a") for _ in range(50)}
    assert len(names) == 50
    for n in names:
        assert n.startswith("feed-sess_a-")


def test_generate_sibling_room_name_requires_session_id() -> None:
    with pytest.raises(ValueError):
        generate_sibling_room_name("")


# ---------------------------------------------------------------------------
# Concurrent prewarm — races must not produce duplicate room names
# ---------------------------------------------------------------------------


async def test_concurrent_prewarms_yield_distinct_rooms() -> None:
    """Many parallel chat-dispatch calls must all land in different rooms."""
    lk = _FakeLK()
    md = _md(session_id="sess_concurrent")
    results = await asyncio.gather(
        *[
            prewarm_room(
                lk,  # type: ignore[arg-type]
                agent_name="plug-agent",
                session_id="sess_concurrent",
                metadata=md,
            )
            for _ in range(20)
        ]
    )
    rooms = {r.room for r in results}
    assert len(rooms) == 20
