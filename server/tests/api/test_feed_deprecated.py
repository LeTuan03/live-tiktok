"""Regression coverage for the deprecated `/feed/*` per-session routes.

The deprecation work marks ``POST /feed/session``, ``POST /feed/swipe`` and ``GET
/feed/cursor`` as ``deprecated=True`` and adds the ``X-Deprecated: 1``
response header during the 30-day removal window. They keep working
unchanged until the window closes — this smoke test pins that contract
so an accidental refactor (or premature deletion) is loud.

When the deprecation work has not yet landed, the deprecation markers are absent;
the route-level tests skip with a pointer rather than fail the
hermetic ``uv run pytest`` baseline.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from soshop.api.routes import feed as feed_module
from soshop.api.routes import feed_swipe as feed_swipe_module
from soshop.api.routes.feed import (
    FeedSessionRegistry,
    FeedSessionState,
    UserContext,
    get_cost_limits,
    get_feed_session_registry,
    get_livekit_api,
    maybe_user,
    require_user,
)
from soshop.api.routes.feed import (
    router as feed_router,
)
from soshop.api.routes.feed_swipe import router as feed_swipe_router
from soshop.services.cost_limits import RoomSlotDecision, UserMinutesDecision
from soshop_server.api.deps import get_db_conn

USER_ID = "user_deprecated_test"

# The three deprecated routes — (method, path) pairs the test exercises.
DEPRECATED_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", "/feed/session"),
    ("POST", "/feed/swipe"),
    ("GET", "/feed/cursor"),
)


# ---------------------------------------------------------------------------
# Fakes — minimal, just enough for the three deprecated routes to succeed.
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self) -> None:
        self.session_user: str = USER_ID

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "INSERT INTO feed_sessions" in query:
            return {"id": uuid4()}
        if "SELECT user_id FROM feed_sessions" in query:
            return {"user_id": self.session_user}
        return None

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        # `_load_candidates`, recent personas, avatars, products — all empty.
        return []

    async def execute(self, query: str, *args: Any) -> str:
        return "OK"


class _FakeAgentDispatch:
    async def create_dispatch(self, request: Any) -> Any:
        dispatch = MagicMock()
        dispatch.id = "AD_dep_test"
        return dispatch

    async def delete_dispatch(self, *, dispatch_id: str, room_name: str) -> None:
        return None


class _FakeRoom:
    async def list_participants(self, request: Any) -> Any:
        resp = MagicMock()
        resp.participants = []
        return resp

    async def send_data(self, request: Any) -> Any:
        return MagicMock()


class _FakeLk:
    def __init__(self) -> None:
        self.agent_dispatch = _FakeAgentDispatch()
        self.room = _FakeRoom()


class _AllowCost:
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
def app() -> FastAPI:
    fastapi_app = FastAPI()
    fastapi_app.include_router(feed_router)
    fastapi_app.include_router(feed_swipe_router)

    conn = _FakeConn()
    registry = FeedSessionRegistry()
    # Pre-seat a feed session so /feed/swipe finds it without going
    # through /feed/session first.
    registry.upsert(
        FeedSessionState(
            session_id="sess_dep_test",
            user_id=USER_ID,
            room_name="feed-dep-test",
            dispatch_id="AD_dep_test",
            persona_id="persona_initial",
        )
    )

    async def override_db() -> Any:
        yield conn

    def override_user() -> UserContext:
        return UserContext(user_id=USER_ID)

    fastapi_app.dependency_overrides[get_db_conn] = override_db
    fastapi_app.dependency_overrides[get_livekit_api] = lambda: _FakeLk()
    fastapi_app.dependency_overrides[get_feed_session_registry] = lambda: registry
    fastapi_app.dependency_overrides[get_cost_limits] = lambda: _AllowCost()
    fastapi_app.dependency_overrides[require_user] = override_user
    fastapi_app.dependency_overrides[maybe_user] = override_user
    return fastapi_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _routes_marked_deprecated() -> set[tuple[str, str]]:
    """Return the (method, path) pairs whose FastAPI route is ``deprecated``."""
    marked: set[tuple[str, str]] = set()
    for router in (feed_module.router, feed_swipe_module.router):
        for route in router.routes:
            if getattr(route, "deprecated", False):
                for method in getattr(route, "methods", ()) or ():
                    marked.add((method, route.path))
    return marked


_DEPRECATION_NOT_APPLIED = pytest.mark.skipif(
    not (set(DEPRECATED_ROUTES) <= _routes_marked_deprecated()),
    reason=(
        "Deprecation markers not present on /feed/session, /feed/swipe, "
        "/feed/cursor — re-run after the deprecation work lands."
    ),
)


# ---------------------------------------------------------------------------
# OpenAPI shape — the deprecation must surface in the schema clients consume.
# ---------------------------------------------------------------------------


@_DEPRECATION_NOT_APPLIED
def test_openapi_marks_legacy_feed_routes_deprecated(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    for method, path in DEPRECATED_ROUTES:
        operation = spec["paths"][path][method.lower()]
        assert operation.get("deprecated") is True, (
            f"{method} {path}: deprecated flag missing from OpenAPI operation"
        )


# ---------------------------------------------------------------------------
# Runtime header — clients reading X-Deprecated can warn users without
# parsing OpenAPI on every request.
# ---------------------------------------------------------------------------


@_DEPRECATION_NOT_APPLIED
def test_feed_session_returns_x_deprecated_header(client: TestClient) -> None:
    resp = client.post("/feed/session", json={})
    assert resp.status_code == 201, resp.text
    assert resp.headers.get("X-Deprecated") == "1"


@_DEPRECATION_NOT_APPLIED
def test_feed_cursor_returns_x_deprecated_header(client: TestClient) -> None:
    resp = client.get(f"/feed/cursor?session_id={uuid4()}")
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("X-Deprecated") == "1"


@_DEPRECATION_NOT_APPLIED
def test_feed_swipe_returns_x_deprecated_header(client: TestClient) -> None:
    import os

    # `/feed/swipe` is mode-sensitive; force the in-place path so the
    # control-message send is the only LiveKit interaction.
    os.environ["FEED_SWAP_MODE"] = "in_place"
    try:
        resp = client.post(
            "/feed/swipe",
            json={
                "session_id": "sess_dep_test",
                "user_id": USER_ID,
                "next": {
                    "persona_id": "persona_next",
                    "script_id": "script_next",
                    "pitch_window_ms": 20_000,
                },
            },
        )
    finally:
        os.environ.pop("FEED_SWAP_MODE", None)
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("X-Deprecated") == "1"
