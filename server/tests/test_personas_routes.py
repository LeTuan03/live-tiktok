"""API tests for `/personas/*` and `/favorites/*` (consumer surface).

The profile/favorites/chat-dispatch flows hit these routes. The DB
connection and LiveKit client are replaced with in-memory fakes so the
tests run without a Postgres or LiveKit service. The chat-dispatch path
is covered here as part of the per-client (1-to-1) room lifecycle —
`test_room_lifecycle.py` exercises the underlying helpers directly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from soshop.api.routes import favorites as favorites_module  # noqa: F401
from soshop.api.routes import personas as personas_module
from soshop.api.routes.favorites import router as favorites_router
from soshop.api.routes.feed import (
    UserContext,
    get_livekit_api,
    require_user,
)
from soshop.api.routes.personas import router
from soshop_server.api.deps import get_db_conn
from soshop_server.core.config import settings

USER_ID = "user_personas_test"


class FakePersonasConn:
    """Tiny in-memory store for the persona/favorites SQL surface.

    Implements only the queries the routes issue: a personas dict by id, a
    persona_scripts dict by persona, and a favorites set keyed by
    (user, persona).
    """

    def __init__(self) -> None:
        self.personas: dict[UUID, dict[str, Any]] = {}
        self.scripts_by_persona: dict[UUID, list[dict[str, Any]]] = {}
        self.favorites: set[tuple[str, UUID]] = set()

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "FROM personas" in query and "id, name" in query:
            (pid,) = args
            return self.personas.get(pid)
        if "FROM persona_scripts" in query:
            (pid,) = args
            scripts = sorted(
                self.scripts_by_persona.get(pid, []),
                key=lambda s: s["weight"],
                reverse=True,
            )
            return scripts[0] if scripts else None
        return None

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "SELECT 1 FROM personas" in query:
            (pid,) = args
            return 1 if pid in self.personas else None
        if "SELECT 1 FROM favorites" in query:
            user_id, pid = args
            return 1 if (user_id, pid) in self.favorites else None
        return None

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM favorites f" in query and "JOIN personas p" in query:
            (user_id,) = args
            out: list[dict[str, Any]] = []
            for u, pid in self.favorites:
                if u != user_id:
                    continue
                p = self.personas.get(pid)
                if p is None:
                    continue
                out.append(
                    {
                        "id": pid,
                        "name": p["name"],
                        "avatar_image_url": p.get("avatar_image_url"),
                        "created_at": None,
                    }
                )
            return out
        return []

    async def execute(self, query: str, *args: Any) -> str:
        if "INSERT INTO favorites" in query:
            user_id, pid = args
            self.favorites.add((user_id, pid))
            return "INSERT 0 1"
        if "DELETE FROM favorites" in query:
            user_id, pid = args
            self.favorites.discard((user_id, pid))
            return "DELETE 1"
        return "OK"


class FakeAgentDispatch:
    def __init__(self) -> None:
        self.created: list[Any] = []

    async def create_dispatch(self, request: Any) -> Any:
        self.created.append(request)
        d = MagicMock()
        d.id = f"AD_{len(self.created):04d}"
        return d


class FakeLiveKitAPI:
    def __init__(self) -> None:
        self.agent_dispatch = FakeAgentDispatch()


@pytest.fixture
def fake_conn() -> FakePersonasConn:
    return FakePersonasConn()


@pytest.fixture
def fake_lk() -> FakeLiveKitAPI:
    return FakeLiveKitAPI()


@pytest.fixture
def app(
    fake_conn: FakePersonasConn,
    fake_lk: FakeLiveKitAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.include_router(favorites_router)

    async def override_db() -> Any:
        yield fake_conn

    app.dependency_overrides[get_db_conn] = override_db
    app.dependency_overrides[get_livekit_api] = lambda: fake_lk
    app.dependency_overrides[require_user] = lambda: UserContext(user_id=USER_ID)

    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", "k", raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", "s", raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_URL", "wss://lk.example", raising=False)
    monkeypatch.setattr(
        personas_module,
        "mint_room_access_token",
        lambda **_: "chat.jwt.fake",  # noqa: S106 — test fixture
    )
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _seed_persona(
    fake_conn: FakePersonasConn,
    *,
    pid: UUID | None = None,
    name: str = "Alex",
    backstory: str = "Sales rep",
    traits: str = "warm,confident",
    avatar_image_url: str | None = "https://cdn.example/p.png",
    with_script: bool = True,
) -> UUID:
    pid = pid or uuid4()
    fake_conn.personas[pid] = {
        "id": pid,
        "name": name,
        "avatar_image_url": avatar_image_url,
        "backstory": backstory,
        "traits": traits,
    }
    if with_script:
        fake_conn.scripts_by_persona[pid] = [{"id": uuid4(), "product_id": uuid4(), "weight": 100}]
    return pid


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


def test_get_persona_profile_happy_path(client: TestClient, fake_conn: FakePersonasConn) -> None:
    pid = _seed_persona(fake_conn)
    resp = client.get(f"/personas/{pid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(pid)
    assert body["display_name"] == "Alex"
    assert body["traits"] == ["warm", "confident"]
    assert body["is_favorited"] is False


def test_get_persona_profile_404_when_missing(client: TestClient) -> None:
    resp = client.get(f"/personas/{uuid4()}")
    assert resp.status_code == 404


def test_get_persona_profile_open_access(
    fake_conn: FakePersonasConn, fake_lk: FakeLiveKitAPI
) -> None:
    """Open access: the persona profile route is reachable without a token
    (soft auth resolves an anonymous viewer), so an unknown persona is a
    404 rather than a 401/403."""
    app = FastAPI()
    app.include_router(router)

    async def override_db() -> Any:
        yield fake_conn

    app.dependency_overrides[get_db_conn] = override_db
    app.dependency_overrides[get_livekit_api] = lambda: fake_lk
    bare = TestClient(app)
    resp = bare.get(f"/personas/{uuid4()}")
    assert resp.status_code not in (401, 403)


# ---------------------------------------------------------------------------
# Clips
# ---------------------------------------------------------------------------


def test_list_persona_clips_returns_empty_envelope(
    client: TestClient, fake_conn: FakePersonasConn
) -> None:
    pid = _seed_persona(fake_conn)
    resp = client.get(f"/personas/{pid}/clips")
    assert resp.status_code == 200
    assert resp.json() == {"clips": []}


def test_list_persona_clips_404_when_missing(client: TestClient) -> None:
    resp = client.get(f"/personas/{uuid4()}/clips")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Favorite toggle
# ---------------------------------------------------------------------------


def test_set_favorite_inserts_then_deletes(client: TestClient, fake_conn: FakePersonasConn) -> None:
    pid = _seed_persona(fake_conn)
    resp = client.post(f"/personas/{pid}/favorite", json={"favorited": True})
    assert resp.status_code == 204
    assert (USER_ID, pid) in fake_conn.favorites

    resp = client.post(f"/personas/{pid}/favorite", json={"favorited": False})
    assert resp.status_code == 204
    assert (USER_ID, pid) not in fake_conn.favorites


def test_set_favorite_404_when_missing(client: TestClient) -> None:
    resp = client.post(
        f"/personas/{uuid4()}/favorite",
        json={"favorited": True},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 1-to-1 chat dispatch — fresh room per call
# ---------------------------------------------------------------------------


def test_chat_dispatch_creates_unique_room_per_call(
    client: TestClient, fake_conn: FakePersonasConn, fake_lk: FakeLiveKitAPI
) -> None:
    """Each persona-chat dispatch must mint a fresh `chat-<hex>` room. This
    is the per-client (1-to-1) room provisioning path — the inverse of the
    shared (1-to-many) feed-session room reused across viewers."""
    pid = _seed_persona(fake_conn)
    body = {"persona_id": str(pid)}

    r1 = client.post(f"/personas/{pid}/chat/dispatch", json=body)
    r2 = client.post(f"/personas/{pid}/chat/dispatch", json=body)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["url"] == "wss://lk.example"
    assert r1.json()["token"] == "chat.jwt.fake"  # noqa: S105

    rooms = {req.room for req in fake_lk.agent_dispatch.created}
    assert len(rooms) == 2  # two calls → two distinct rooms
    for room in rooms:
        assert room.startswith("chat-")


def test_chat_dispatch_503_when_persona_has_no_scripts(
    client: TestClient, fake_conn: FakePersonasConn
) -> None:
    """Without a script the agent has nothing to say — service unavailable."""
    pid = _seed_persona(fake_conn, with_script=False)
    resp = client.post(f"/personas/{pid}/chat/dispatch", json={"persona_id": str(pid)})
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    # `detail` shape varies by error-handler installation; both forms carry the code.
    if isinstance(detail, dict):
        assert detail["code"] == "persona_has_no_scripts"
    else:
        assert "scripts" in detail.lower()


def test_chat_dispatch_400_on_path_body_mismatch(
    client: TestClient, fake_conn: FakePersonasConn
) -> None:
    pid = _seed_persona(fake_conn)
    resp = client.post(
        f"/personas/{pid}/chat/dispatch",
        json={"persona_id": str(uuid4())},  # different from path
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Favorites listing + pin
# ---------------------------------------------------------------------------


def test_list_favorites_returns_user_personas_only(
    client: TestClient, fake_conn: FakePersonasConn
) -> None:
    a = _seed_persona(fake_conn, name="Alex")
    b = _seed_persona(fake_conn, name="Beth")
    fake_conn.favorites.add((USER_ID, a))
    fake_conn.favorites.add(("someone-else", b))  # belongs to another user
    resp = client.get("/favorites")
    assert resp.status_code == 200
    favs = resp.json()["favorites"]
    assert len(favs) == 1
    assert favs[0]["display_name"] == "Alex"
    assert favs[0]["is_pinned"] is False  # v1 schema has no pin column


def test_pin_404_when_not_favorited(client: TestClient, fake_conn: FakePersonasConn) -> None:
    pid = _seed_persona(fake_conn)
    resp = client.post(f"/favorites/{pid}/pin", json={"pinned": True})
    assert resp.status_code == 404


def test_pin_204_on_favorited_persona(client: TestClient, fake_conn: FakePersonasConn) -> None:
    pid = _seed_persona(fake_conn)
    fake_conn.favorites.add((USER_ID, pid))
    resp = client.post(f"/favorites/{pid}/pin", json={"pinned": True})
    assert resp.status_code == 204
