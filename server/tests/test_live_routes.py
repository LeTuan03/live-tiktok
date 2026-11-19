"""API tests for the `/live/{room_id}/*` endpoints (web live UI).

`/live/*` is the shared (1-to-many) live-room surface the web client hits
when joining a public room. The token mint helper is monkey-patched so we
don't need real LiveKit credentials at test time; the route's job is auth
gating, validation, and dispatch metadata shape, which is what we assert.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from soshop.api.routes import live as live_route
from soshop.api.routes.feed import UserContext, maybe_user
from soshop.api.routes.live import router
from soshop_server.core.config import settings

USER_ID = "user_live_test"


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    # Viewer-token uses the soft-auth `maybe_user` dependency. The
    # signed-in test path overrides it to return the fixture user so
    # assertions about `viewer-<USER_ID>-…` identities hold.
    app.dependency_overrides[maybe_user] = lambda: UserContext(user_id=USER_ID)

    captured: dict[str, Any] = {}

    def fake_mint(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "live.jwt.fake"  # noqa: S105 — test fixture, not a real token

    monkeypatch.setattr(live_route, "mint_room_access_token", fake_mint)
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", "k", raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", "s", raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_URL", "wss://lk.example", raising=False)
    app.state._captured = captured  # type: ignore[attr-defined]
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_viewer_token_happy_path(client: TestClient, app: FastAPI) -> None:
    resp = client.post("/live/show-42/viewer-token", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["url"] == "wss://lk.example"
    assert body["token"] == "live.jwt.fake"  # noqa: S105
    # Without a real backend the route returns "live" as a safe default.
    assert body["room_state"] == "live"

    # The mint helper saw the right room and identity-shape (viewer-<user>-<8hex>).
    captured: dict[str, Any] = app.state._captured  # type: ignore[attr-defined]
    assert captured["room"] == "show-42"
    assert captured["identity"].startswith(f"viewer-{USER_ID}-")
    # Viewer dispatch metadata uses `viewer-<user>` for the session id.
    assert captured["metadata"].session_id == f"viewer-{USER_ID}"


def test_viewer_token_rejects_malformed_room_id(client: TestClient) -> None:
    """Room ids must be url-safe slugs; e.g. spaces / `..` are rejected."""
    resp = client.post("/live/has space/viewer-token", json={})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_room_id"


def test_viewer_token_503_when_livekit_unconfigured(
    monkeypatch: pytest.MonkeyPatch, app: FastAPI
) -> None:
    """Missing API key → 503 with a stable error code so the UI can branch."""
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", "", raising=False)
    client = TestClient(app)
    resp = client.post("/live/show-42/viewer-token", json={})
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "livekit_not_configured"


def test_viewer_token_allows_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live viewing is a public surface — no auth required.

    Sign-in gates the *write* surfaces (future Buy / Bid) instead. Anon
    viewers receive a server-assigned `anon-<hex>` identity so dispatch
    metadata is still well-formed.
    """
    app = FastAPI()
    app.include_router(router)
    captured: dict[str, Any] = {}

    def fake_mint(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "live.jwt.anon"  # noqa: S105 — test fixture

    monkeypatch.setattr(live_route, "mint_room_access_token", fake_mint)
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", "k", raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", "s", raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_URL", "wss://lk.example", raising=False)

    bare = TestClient(app)
    resp = bare.post("/live/show-42/viewer-token", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["token"] == "live.jwt.anon"  # noqa: S105
    assert captured["identity"].startswith("viewer-anon-")
    assert captured["metadata"].session_id.startswith("viewer-anon-")
