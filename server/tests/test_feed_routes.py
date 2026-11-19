"""Unit tests for the feed write endpoints.

Covers the acceptance criteria:

* The write endpoints are reachable without an external auth provider.
* `/feed/swipe` exercises both the new in-place control-message path
  and the legacy single-room redispatch fallback (mocked LiveKit
  client).
* `/feed/event` is idempotent on the client-supplied `event_id`.

The tests run against a FastAPI app that mounts only the feed router
and overrides every external dependency (DB, LiveKit, cost limits,
auth) with in-memory fakes — no Postgres, no LiveKit, no JWKS
network call.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from soshop.api.routes import feed as feed_module
from soshop.api.routes import feed_event, feed_swipe
from soshop.api.routes.feed import (
    PLUG_AGENT_NAME,
    FeedSessionRegistry,
    FeedSessionState,
    UserContext,
    get_cost_limits,
    get_feed_session_registry,
    get_livekit_api,
    maybe_user,
    require_user,
    router,
)
from soshop.services.cost_limits import RoomSlotDecision, UserMinutesDecision
from soshop_server.api.deps import get_db_conn

USER_ID = "user_test_abc"
OTHER_USER_ID = "user_other_xyz"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeConn:
    """Records executes/fetchrows so tests can assert on them.

    Implements just enough of asyncpg's `Connection` API for the
    routes:

    * `INSERT INTO feed_sessions ... RETURNING id` — generates a UUID.
    * `INSERT INTO feed_events ... ON CONFLICT (id) DO NOTHING` —
      emulates uniqueness on the id column.
    * `SELECT user_id FROM feed_sessions WHERE id = $1` —
      returns the user that owns the inserted session.
    """

    def __init__(self) -> None:
        self.sessions: dict[UUID, dict[str, Any]] = {}
        self.events: dict[UUID, dict[str, Any]] = {}
        # Optional fixtures consulted by `fetch()` so individual tests
        # can drive the auto-dispatch ranker path without standing up a
        # real Postgres. Empty by default — `_load_candidates` returns
        # an empty list and the route falls back to the cost-only flow.
        self.candidates: list[dict[str, Any]] = []
        self.persona_avatars: dict[UUID, str | None] = {}

    async def execute(self, query: str, *args: Any) -> str:
        if "INSERT INTO feed_events" in query:
            event_id, session_id, persona_id, product_id, event_kind, value_ms = args
            if event_id in self.events:
                return "INSERT 0 0"
            self.events[event_id] = {
                "id": event_id,
                "session_id": session_id,
                "persona_id": persona_id,
                "product_id": product_id,
                "event_kind": event_kind,
                "value_ms": value_ms,
            }
            return "INSERT 0 1"
        return "OK"

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "INSERT INTO feed_sessions" in query:
            user_id, room_name, device_id, app_version, country = args
            session_id = uuid4()
            self.sessions[session_id] = {
                "id": session_id,
                "user_id": user_id,
                "room_name": room_name,
                "device_id": device_id,
                "app_version": app_version,
                "country": country,
            }
            return {"id": session_id}
        if "SELECT user_id FROM feed_sessions" in query:
            (sid,) = args
            row = self.sessions.get(sid)
            return {"user_id": row["user_id"]} if row else None
        return None

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        # `_load_candidates` — selects active scripts whose product (if
        # any) is on a non-paused campaign. Tests pre-seed
        # `self.candidates`; we ignore the WHERE clause and return them
        # verbatim.
        if "FROM persona_scripts ps" in query:
            return list(self.candidates)
        # `_load_recent_personas` — cooldown lookup. Empty by default
        # (no prior sessions for the test user).
        if "FROM feed_events fe" in query:
            return []
        # `_load_avatar_urls` — `WHERE id = ANY($1::uuid[])`.
        if "SELECT id, name, avatar_image_url FROM personas" in query:
            (persona_ids,) = args
            return [
                {
                    "id": pid,
                    "name": None,
                    "avatar_image_url": self.persona_avatars.get(pid),
                }
                for pid in persona_ids
            ]
        # `_load_products` — feed slot product details.
        if "FROM products" in query and "ANY($1::uuid[])" in query:
            return []
        return []


class FakeAgentDispatchService:
    def __init__(self) -> None:
        self.created: list[Any] = []
        self.deleted: list[Any] = []

    async def create_dispatch(self, request: Any) -> Any:
        self.created.append(request)
        dispatch = MagicMock()
        dispatch.id = f"AD_{len(self.created):04d}"
        return dispatch

    async def delete_dispatch(self, *, dispatch_id: str, room_name: str) -> None:
        self.deleted.append((dispatch_id, room_name))


class FakeRoomService:
    def __init__(self) -> None:
        self.list_calls = 0
        self.send_data_calls: list[dict[str, Any]] = []

    async def list_participants(self, request: Any) -> Any:
        # Always return an empty room — the previous agent has already
        # left, so `wait_for_room_agent_empty` resolves on the first poll.
        self.list_calls += 1
        resp = MagicMock()
        resp.participants = []
        return resp

    async def send_data(self, request: Any) -> Any:
        # Capture the room / topic / decoded body so tests can assert on
        # the in-place swap-control message the route layer sends.
        self.send_data_calls.append(
            {
                "room": request.room,
                "topic": request.topic,
                "data": bytes(request.data),
                "destination_identities": list(request.destination_identities),
            }
        )
        return MagicMock()


class FakeLiveKitAPI:
    def __init__(self) -> None:
        self.agent_dispatch = FakeAgentDispatchService()
        self.room = FakeRoomService()


class FakeCostLimits:
    """Always-grant cost limits implementation used in tests."""

    async def check_room_slot(self, conn: Any) -> RoomSlotDecision:
        return RoomSlotDecision(granted=True, active_count=0, limit=50, retry_after_seconds=30)

    async def check_user_minutes(self, conn: Any, user_id: str) -> UserMinutesDecision:
        return UserMinutesDecision(
            avatar_allowed=True,
            minutes_used=0.0,
            minutes_limit=5,
            seconds_until_utc_midnight=3600,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_conn() -> FakeConn:
    return FakeConn()


@pytest.fixture
def fake_lk() -> FakeLiveKitAPI:
    return FakeLiveKitAPI()


@pytest.fixture
def registry() -> FeedSessionRegistry:
    """Fresh, isolated registry per test."""
    return FeedSessionRegistry()


@pytest.fixture
def app(fake_conn: FakeConn, fake_lk: FakeLiveKitAPI, registry: FeedSessionRegistry) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.include_router(feed_swipe.router)
    app.include_router(feed_event.router)

    async def override_db() -> Any:
        yield fake_conn

    def override_lk() -> Any:
        return fake_lk

    def override_registry() -> Any:
        return registry

    def override_cost() -> Any:
        return FakeCostLimits()

    def override_user() -> Any:
        return UserContext(user_id=USER_ID)

    app.dependency_overrides[get_db_conn] = override_db
    app.dependency_overrides[get_livekit_api] = override_lk
    app.dependency_overrides[get_feed_session_registry] = override_registry
    app.dependency_overrides[get_cost_limits] = override_cost
    app.dependency_overrides[require_user] = override_user
    # Soft-auth dependency used by `/feed/session`, `/feed/cursor`, and
    # `/feed/swipe`. Existing test cases assume the signed-in path, so
    # return the same fixture user as `require_user`. The
    # anonymous path is exercised explicitly via `_app_without_auth`.
    app.dependency_overrides[maybe_user] = override_user
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_swap_mode() -> Iterator[None]:
    """Tests that mutate FEED_SWAP_MODE clean up after themselves."""
    saved = os.environ.get("FEED_SWAP_MODE")
    yield
    if saved is None:
        os.environ.pop("FEED_SWAP_MODE", None)
    else:
        os.environ["FEED_SWAP_MODE"] = saved


# ---------------------------------------------------------------------------
# Auth — feed-browsing endpoints are soft-auth (anon-OK); /feed/event
# remains hard-gated since it is a write surface (analytics).
# ---------------------------------------------------------------------------


def _app_without_auth(
    fake_conn: FakeConn, fake_lk: FakeLiveKitAPI, registry: FeedSessionRegistry
) -> FastAPI:
    """Build an app that does NOT override `require_user`.

    Used to verify the dependency runs and rejects unauthenticated
    requests. Every other dependency is still overridden so we never hit
    a real DB / LiveKit on the way to the auth check.
    """
    app = FastAPI()
    app.include_router(router)
    app.include_router(feed_swipe.router)
    app.include_router(feed_event.router)

    async def override_db() -> Any:
        yield fake_conn

    app.dependency_overrides[get_db_conn] = override_db
    app.dependency_overrides[get_livekit_api] = lambda: fake_lk
    app.dependency_overrides[get_feed_session_registry] = lambda: registry
    app.dependency_overrides[get_cost_limits] = lambda: FakeCostLimits()
    return app


def test_feed_session_allows_anonymous(
    fake_conn: FakeConn, fake_lk: FakeLiveKitAPI, registry: FeedSessionRegistry
) -> None:
    """Anonymous browsers can bootstrap a feed session.

    The main consumer feed is a public surface — sign-in is gated at
    write time (chat post, Buy / Bid). The server mints an `anon-<hex>`
    user identity so the row in `feed_sessions` is well-formed.
    """
    bare = TestClient(_app_without_auth(fake_conn, fake_lk, registry))
    resp = bare.post("/feed/session", json={})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["avatar_mode"] in {"avatar", "non_avatar"}
    # The synthesized user id was persisted alongside the session.
    [stored] = list(fake_conn.sessions.values())
    assert stored["user_id"].startswith("anon-")


def test_feed_session_dispatch_head_auto_picks_and_dispatches(
    fake_conn: FakeConn, fake_lk: FakeLiveKitAPI, registry: FeedSessionRegistry
) -> None:
    """`dispatch_head=true` runs the ranker and dispatches the agent.

    Web bootstrap path: the client doesn't pre-pick a persona — the
    server does it via the ranker, dispatches the agent into a fresh
    LiveKit room, and returns enough context for the client to:

      * pre-seat the active card with `head_slot`,
      * skip the head on the next `/feed/cursor` call via
        `initial_cursor`,
      * connect to LiveKit with `livekit_url` + `livekit_token`.

    This is the contract the web `FeedRoot` now relies on; if the
    response shape drifts, the active card and the agent's stream
    will desync.
    """
    persona_id = uuid4()
    script_id = uuid4()
    fake_conn.candidates = [
        {
            "persona_id": persona_id,
            "script_id": script_id,
            "product_id": None,
            "weight": 1,
        }
    ]
    fake_conn.persona_avatars = {persona_id: "https://cdn.example/avatars/host.jpg"}

    bare = TestClient(_app_without_auth(fake_conn, fake_lk, registry))
    resp = bare.post("/feed/session", json={"dispatch_head": True})
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # LiveKit fields populated — the agent was dispatched.
    assert body["persona_id"] == str(persona_id)
    assert body["livekit_token"]
    assert body["room_name"]
    assert body["swap_mode"] in {"in_place", "single_room", "ping_pong"}

    # Head slot echoes the picked candidate, including the avatar URL
    # so the client can render the still placeholder immediately.
    assert body["head_slot"]["persona"]["id"] == str(persona_id)
    assert body["head_slot"]["persona"]["avatar_url"] == "https://cdn.example/avatars/host.jpg"
    assert body["head_slot"]["script_id"] == str(script_id)
    assert body["head_slot"]["is_ad"] is False  # comedy slot — no product
    assert body["initial_cursor"]

    # The session is registered so a follow-up /feed/swipe finds it.
    state = registry.get(body["session_id"])
    assert state is not None
    assert state.persona_id == str(persona_id)

    # And a real LiveKit dispatch was created.
    assert len(fake_lk.agent_dispatch.created) == 1


def test_feed_session_dispatch_head_no_candidates_falls_back(
    fake_conn: FakeConn, fake_lk: FakeLiveKitAPI, registry: FeedSessionRegistry
) -> None:
    """When no eligible candidates exist, dispatch_head silently skips.

    The session is still created (cost-only) — the response just omits
    the LiveKit fields. The client falls back to the still placeholder
    and can retry once content is seeded.
    """
    bare = TestClient(_app_without_auth(fake_conn, fake_lk, registry))
    resp = bare.post("/feed/session", json={"dispatch_head": True})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["livekit_token"] is None
    assert body["head_slot"] is None
    assert body["initial_cursor"] is None
    assert len(fake_lk.agent_dispatch.created) == 0


def test_feed_swipe_allows_anonymous(
    fake_conn: FakeConn, fake_lk: FakeLiveKitAPI, registry: FeedSessionRegistry
) -> None:
    """Anonymous viewers can swipe within a session they own.

    The session row carries an `anon-<hex>` user id minted at session
    creation; subsequent swipes use that session id as the correlation
    handle. Sign-in remains required at write time (chat / Buy).
    """
    os.environ["FEED_SWAP_MODE"] = "in_place"
    state = FeedSessionState(
        session_id="sess_anon_1",
        user_id="anon-deadbeef",
        room_name="feed-anon-1",
        dispatch_id="AD_seed",
        persona_id="persona_initial",
    )
    registry.upsert(state)

    bare = TestClient(_app_without_auth(fake_conn, fake_lk, registry))
    resp = bare.post(
        "/feed/swipe",
        json={
            "session_id": state.session_id,
            "user_id": state.user_id,
            "next": {
                "persona_id": "persona_next",
                "script_id": "script_v2",
                "pitch_window_ms": 25_000,
            },
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["persona_id"] == "persona_next"


def test_feed_event_open_access(
    fake_conn: FakeConn, fake_lk: FakeLiveKitAPI, registry: FeedSessionRegistry
) -> None:
    """`/feed/event` is reachable without an auth provider (open access).

    The read-only feed and the analytics write surface both work
    anonymously now; a write against an unknown session is a domain error,
    not an auth rejection (401/403).
    """
    bare = TestClient(_app_without_auth(fake_conn, fake_lk, registry))
    resp = bare.post(
        "/feed/event",
        json={
            "event_id": str(uuid4()),
            "session_id": str(uuid4()),
            "persona_id": str(uuid4()),
            "kind": "popup_shown",
        },
    )
    assert resp.status_code not in (401, 403)


def test_feed_session_rejects_body_user_mismatch(client: TestClient) -> None:
    """`body.user_id` must match the token; otherwise 403."""
    resp = client.post(
        "/feed/session",
        json={"user_id": OTHER_USER_ID},
    )
    assert resp.status_code == 403
    assert "does not match" in resp.json()["detail"]


def test_feed_event_rejects_other_users_session(client: TestClient, fake_conn: FakeConn) -> None:
    """A user cannot post events into a session owned by someone else."""
    foreign_session = uuid4()
    fake_conn.sessions[foreign_session] = {
        "id": foreign_session,
        "user_id": OTHER_USER_ID,
        "room_name": None,
        "device_id": None,
        "app_version": None,
        "country": None,
    }
    resp = client.post(
        "/feed/event",
        json={
            "event_id": str(uuid4()),
            "session_id": str(foreign_session),
            "persona_id": str(uuid4()),
            "kind": "popup_shown",
        },
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# /feed/event idempotency (dedup on event_id)
# ---------------------------------------------------------------------------


def test_feed_event_records_first_post(client: TestClient, fake_conn: FakeConn) -> None:
    session_id = uuid4()
    fake_conn.sessions[session_id] = {
        "id": session_id,
        "user_id": USER_ID,
        "room_name": None,
        "device_id": None,
        "app_version": None,
        "country": None,
    }
    event_id = uuid4()
    resp = client.post(
        "/feed/event",
        json={
            "event_id": str(event_id),
            "session_id": str(session_id),
            "persona_id": str(uuid4()),
            "kind": "popup_tapped",
            "value_ms": 1500,
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["event_id"] == str(event_id)
    assert body["deduplicated"] is False
    assert event_id in fake_conn.events
    # Client `popup_tapped` maps to DB `tap` (the closest enum value
    # available without a schema migration).
    assert fake_conn.events[event_id]["event_kind"] == "tap"


def test_feed_event_dedups_repeat_post(client: TestClient, fake_conn: FakeConn) -> None:
    """A second POST with the same event_id must not write a new row."""
    session_id = uuid4()
    fake_conn.sessions[session_id] = {
        "id": session_id,
        "user_id": USER_ID,
        "room_name": None,
        "device_id": None,
        "app_version": None,
        "country": None,
    }
    event_id = uuid4()
    payload = {
        "event_id": str(event_id),
        "session_id": str(session_id),
        "persona_id": str(uuid4()),
        "kind": "conversion_estimated",
    }

    first = client.post("/feed/event", json=payload)
    assert first.status_code == 202
    assert first.json()["deduplicated"] is False

    second = client.post("/feed/event", json=payload)
    assert second.status_code == 202
    assert second.json()["deduplicated"] is True
    # Still exactly one row in the fake DB.
    assert len(fake_conn.events) == 1


def test_feed_event_kind_mapping_covers_all_client_kinds() -> None:
    """All four client event names must map to a valid DB enum value."""
    valid_db_kinds = {
        "impression",
        "view",
        "view_complete",
        "tap",
        "add_to_cart",
        "purchase",
        "skip",
        "favorite",
        "unfavorite",
        "share",
        "mute",
        "unmute",
    }
    from soshop.api.routes import feed_event

    for client_kind, db_kind in feed_event._CLIENT_EVENT_KIND_MAP.items():
        assert client_kind in {
            "popup_shown",
            "popup_tapped",
            "deep_link_open",
            "conversion_estimated",
        }
        assert db_kind in valid_db_kinds


# ---------------------------------------------------------------------------
# /feed/swipe — single-room dispatch path
# ---------------------------------------------------------------------------


def _seed_active_session(
    registry: FeedSessionRegistry, *, session_id: str = "sess_swipe_1"
) -> FeedSessionState:
    state = FeedSessionState(
        session_id=session_id,
        user_id=USER_ID,
        room_name=f"feed-{session_id}",
        dispatch_id="AD_seed",
        persona_id="persona_initial",
    )
    registry.upsert(state)
    return state


def _assert_in_place_swap_response(body: dict, state: FeedSessionState) -> None:
    assert body == {
        **body,
        "swap_mode": "in_place",
        "room_name": state.room_name,
        "persona_id": "persona_next",
        "dispatch_id": state.dispatch_id,  # dispatch is reused
        "livekit_token": None,  # client keeps existing token
    }


def _assert_in_place_control_message(fake_lk: FakeLiveKitAPI, state: FeedSessionState) -> None:
    # No agent redispatch happened.
    assert fake_lk.agent_dispatch.deleted == []
    assert fake_lk.agent_dispatch.created == []

    # A single control message went out on the agent_control topic.
    assert len(fake_lk.room.send_data_calls) == 1
    sent = fake_lk.room.send_data_calls[0]
    assert sent["room"] == state.room_name
    assert sent["topic"] == "agent_control"

    payload = json.loads(sent["data"].decode("utf-8"))
    assert payload["action"] == "swap_persona"
    assert payload["metadata"] == {
        **payload["metadata"],
        "persona_id": "persona_next",
        "product_id": "prod_xyz",
        "script_id": "script_v2",
        "pitch_window_ms": 25_000,
        "session_id": state.session_id,
        "user_id": USER_ID,
    }


def test_feed_swipe_in_place_sends_control_message(
    client: TestClient,
    fake_lk: FakeLiveKitAPI,
    registry: FeedSessionRegistry,
) -> None:
    """In-place mode sends a swap_persona control message to the running agent.

    No new agent dispatch is created and no leave signal is sent — the
    existing agent process mutates its own session in place.
    """
    os.environ["FEED_SWAP_MODE"] = "in_place"
    state = _seed_active_session(registry)

    resp = client.post(
        "/feed/swipe",
        json={
            "session_id": state.session_id,
            "user_id": USER_ID,
            "next": {
                "persona_id": "persona_next",
                "product_id": "prod_xyz",
                "script_id": "script_v2",
                "pitch_window_ms": 25_000,
            },
        },
    )
    assert resp.status_code == 200, resp.text
    _assert_in_place_swap_response(resp.json(), state)
    _assert_in_place_control_message(fake_lk, state)

    # Registry: same dispatch, new persona.
    updated = registry.get(state.session_id)
    assert updated == FeedSessionState(
        session_id=state.session_id,
        user_id=state.user_id,
        room_name=state.room_name,
        dispatch_id=state.dispatch_id,
        persona_id="persona_next",
    )


def test_feed_swipe_single_room_legacy_redispatch(
    client: TestClient,
    fake_lk: FakeLiveKitAPI,
    registry: FeedSessionRegistry,
) -> None:
    """Legacy single-room fallback: tell the old agent to leave then
    dispatch a new one into the same room."""
    os.environ["FEED_SWAP_MODE"] = "single_room"
    state = _seed_active_session(registry)

    resp = client.post(
        "/feed/swipe",
        json={
            "session_id": state.session_id,
            "user_id": USER_ID,
            "next": {
                "persona_id": "persona_next",
                "product_id": "prod_xyz",
                "script_id": "script_v2",
                "pitch_window_ms": 25_000,
            },
        },
    )
    assert resp.status_code == 200, resp.text
    _assert_single_room_swap_response(resp.json(), state)
    _assert_single_room_redispatch(fake_lk, state)
    _assert_registry_advanced(registry, state)


def _assert_single_room_swap_response(body: dict, state: FeedSessionState) -> None:
    assert body == {
        **body,
        "swap_mode": "single_room",
        "room_name": state.room_name,
        "persona_id": "persona_next",
        "livekit_token": None,
    }


def _assert_single_room_redispatch(fake_lk: FakeLiveKitAPI, state: FeedSessionState) -> None:
    # Old dispatch was asked to leave; new dispatch created.
    assert fake_lk.agent_dispatch.deleted == [(state.dispatch_id, state.room_name)]
    assert len(fake_lk.agent_dispatch.created) == 1
    new_request = fake_lk.agent_dispatch.created[0]
    assert new_request.room == state.room_name
    assert new_request.agent_name == PLUG_AGENT_NAME
    decoded = json.loads(new_request.metadata)
    assert decoded == {**decoded, "persona_id": "persona_next", "product_id": "prod_xyz"}


def _assert_registry_advanced(registry: FeedSessionRegistry, state: FeedSessionState) -> None:
    # Registry advanced to the new dispatch.
    updated = registry.get(state.session_id)
    assert updated is not None
    assert updated.persona_id == "persona_next"
    assert updated.dispatch_id != state.dispatch_id


def test_feed_swipe_returns_404_for_unknown_session(client: TestClient) -> None:
    os.environ["FEED_SWAP_MODE"] = "in_place"
    resp = client.post(
        "/feed/swipe",
        json={
            "session_id": "sess_does_not_exist",
            "user_id": USER_ID,
            "next": {
                "persona_id": "p",
                "script_id": "s",
                "pitch_window_ms": 1000,
            },
        },
    )
    assert resp.status_code == 404


def test_feed_swipe_rejects_other_users_session(
    client: TestClient, registry: FeedSessionRegistry
) -> None:
    """Authenticated user cannot swipe a session belonging to a different user."""
    os.environ["FEED_SWAP_MODE"] = "in_place"
    state = FeedSessionState(
        session_id="sess_owned_by_other",
        user_id=OTHER_USER_ID,
        room_name="feed-other",
        dispatch_id="AD_other",
        persona_id="persona_initial",
    )
    registry.upsert(state)

    resp = client.post(
        "/feed/swipe",
        json={
            "session_id": state.session_id,
            "user_id": USER_ID,
            "next": {
                "persona_id": "p",
                "script_id": "s",
                "pitch_window_ms": 1000,
            },
        },
    )
    assert resp.status_code == 403


def test_feed_swipe_body_user_must_match_token(
    client: TestClient, registry: FeedSessionRegistry
) -> None:
    """`body.user_id` mismatching the resolved request user is 403."""
    os.environ["FEED_SWAP_MODE"] = "in_place"
    state = _seed_active_session(registry, session_id="sess_match_check")

    resp = client.post(
        "/feed/swipe",
        json={
            "session_id": state.session_id,
            "user_id": OTHER_USER_ID,  # different from the token's USER_ID
            "next": {
                "persona_id": "p",
                "script_id": "s",
                "pitch_window_ms": 1000,
            },
        },
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Swap-mode env flag
# ---------------------------------------------------------------------------


def test_swap_mode_default_is_in_place(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FEED_SWAP_MODE", raising=False)
    from soshop.livekit.swap_mode import SwapMode, get_swap_mode

    assert get_swap_mode() is SwapMode.IN_PLACE


def test_swap_mode_honors_single_room(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEED_SWAP_MODE", "single_room")
    from soshop.livekit.swap_mode import SwapMode, get_swap_mode

    assert get_swap_mode() is SwapMode.SINGLE_ROOM


def test_swap_mode_honors_ping_pong(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEED_SWAP_MODE", "ping_pong")
    from soshop.livekit.swap_mode import SwapMode, get_swap_mode

    assert get_swap_mode() is SwapMode.PING_PONG


def test_swap_mode_unknown_falls_back_to_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FEED_SWAP_MODE", "garbage_value")
    from soshop.livekit.swap_mode import SwapMode, get_swap_mode

    assert get_swap_mode() is SwapMode.IN_PLACE


# ---------------------------------------------------------------------------
# /feed/session — identity + LiveKit token issuance
# ---------------------------------------------------------------------------


def test_feed_session_minimal_no_first_persona(
    client: TestClient, fake_conn: FakeConn, fake_lk: FakeLiveKitAPI
) -> None:
    """Without first_persona, the cost-only flow is preserved."""
    resp = client.post("/feed/session", json={"user_id": USER_ID})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert UUID(body["session_id"])
    assert body["livekit_token"] is None
    assert body["dispatch_id"] is None
    # No agent dispatched without a first persona.
    assert fake_lk.agent_dispatch.created == []
    # But the session row is recorded.
    assert len(fake_conn.sessions) == 1


def test_feed_session_with_first_persona_dispatches_and_mints_token(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    fake_conn: FakeConn,
    fake_lk: FakeLiveKitAPI,
    registry: FeedSessionRegistry,
) -> None:
    """When first_persona is supplied, the endpoint dispatches the agent and
    returns a LiveKit token whose metadata carries the persona/product/script
    context required by the acceptance criteria."""

    def fake_mint(**kwargs: Any) -> str:
        # Capture the metadata so we can assert on the JSON payload that
        # would be embedded in the JWT.
        fake_mint.last_kwargs = kwargs  # type: ignore[attr-defined]
        return "fake.jwt.token"

    monkeypatch.setattr(feed_module, "mint_room_access_token", fake_mint)

    resp = client.post(
        "/feed/session",
        json={
            "user_id": USER_ID,
            "device_id": "ios-123",
            "first_persona": {
                "persona_id": "persona_first",
                "script_id": "script_first",
                "product_id": "prod_first",
                "pitch_window_ms": 30_000,
            },
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    _assert_first_persona_response_shape(body)
    _assert_first_persona_dispatch_metadata(fake_lk, body)
    _assert_first_persona_mint_metadata(fake_mint, body)
    _assert_first_persona_registered(registry, body)


_FAKE_JWT_TOKEN = "fake.jwt.token"  # noqa: S105 (test fixture, not a real token)


def _assert_first_persona_response_shape(body: dict) -> None:
    assert body["livekit_token"] == _FAKE_JWT_TOKEN
    assert body["persona_id"] == "persona_first"
    assert body["dispatch_id"].startswith("AD_")
    assert body["room_name"]


def _assert_first_persona_dispatch_metadata(fake_lk: FakeLiveKitAPI, body: dict) -> None:
    """The agent was dispatched with the persona/product/script metadata."""
    assert len(fake_lk.agent_dispatch.created) == 1
    md = json.loads(fake_lk.agent_dispatch.created[0].metadata)
    assert md == {
        **md,
        "persona_id": "persona_first",
        "product_id": "prod_first",
        "script_id": "script_first",
        "session_id": body["session_id"],
        "user_id": USER_ID,
    }


def _assert_first_persona_mint_metadata(fake_mint: Any, body: dict) -> None:
    """Same metadata is also passed to the token mint helper, which is how
    the client surfaces the popup before the agent's audio reaches it."""
    minted_meta = fake_mint.last_kwargs["metadata"]
    assert minted_meta.persona_id == "persona_first"
    assert minted_meta.session_id == body["session_id"]


def _assert_first_persona_registered(registry: FeedSessionRegistry, body: dict) -> None:
    """The session is registered for follow-up swipes."""
    state = registry.get(body["session_id"])
    assert state is not None
    assert state.persona_id == "persona_first"
    assert state.user_id == USER_ID
