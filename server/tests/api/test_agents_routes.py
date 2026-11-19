"""API tests for the on-demand agent-session routes.

Mounts only ``soshop.api.routes.agents`` on a fresh FastAPI app and
swaps the session-manager singleton for a recording fake so the tests
never reach :func:`reconcile_channels`. The site-password gate is
exercised end-to-end (no dependency override) — that is the whole
point of these routes.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from soshop.agent import session as session_module
from soshop.agent.session import SessionStatus, _reset_singleton_for_tests
from soshop.api.routes.agents import router
from soshop_server.core.config import settings

SITE_PASSWORD = "test-site-password"  # noqa: S105 — test fixture, not a real secret


class FakeSessionManager:
    """Minimal stand-in for :class:`AgentSessionManager`.

    Records ``start()``/``stop()``/``status()`` invocations so tests can
    assert on the wire shape without spinning up the full manager (and
    its real ``reconcile_channels`` import).
    """

    def __init__(self, channel_count: int) -> None:
        self.channel_count = channel_count
        self._expires_at: datetime | None = None
        self.start_calls: list[int] = []
        self.stop_calls = 0

    def status(self) -> SessionStatus:
        active = self._expires_at is not None
        remaining = (
            int((self._expires_at - datetime.now(UTC)).total_seconds())
            if active and self._expires_at is not None
            else 0
        )
        return SessionStatus(
            active=active,
            expires_at=self._expires_at,
            seconds_remaining=max(0, remaining),
            target_channel_count=self.channel_count if active else 0,
        )

    async def start(self, duration_minutes: int) -> SessionStatus:
        self.start_calls.append(duration_minutes)
        self._expires_at = datetime.now(UTC) + timedelta(minutes=duration_minutes)
        return self.status()

    async def stop(self) -> SessionStatus:
        self.stop_calls += 1
        self._expires_at = None
        return self.status()


@pytest.fixture
def fake_manager(monkeypatch: pytest.MonkeyPatch) -> FakeSessionManager:
    fake = FakeSessionManager(channel_count=settings.CHANNEL_COUNT)
    _reset_singleton_for_tests()
    monkeypatch.setattr(session_module, "_singleton", fake, raising=False)
    yield fake
    _reset_singleton_for_tests()


@pytest.fixture
def site_password(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setattr(settings, "SITE_PASSWORD", SITE_PASSWORD)
    yield SITE_PASSWORD


@pytest.fixture
def app(fake_manager: FakeSessionManager) -> FastAPI:  # noqa: ARG001
    application = FastAPI()
    application.include_router(router)
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_post_session_missing_bearer_returns_401(
    site_password: str,  # noqa: ARG001
    client: TestClient,
) -> None:
    resp = client.post("/agents/session")
    assert resp.status_code == 401


def test_post_session_wrong_bearer_returns_401(
    site_password: str,  # noqa: ARG001
    client: TestClient,
) -> None:
    resp = client.post("/agents/session", headers={"Authorization": "Bearer not-the-password"})
    assert resp.status_code == 401


def test_get_session_malformed_bearer_returns_401(
    site_password: str,  # noqa: ARG001
    client: TestClient,
) -> None:
    resp = client.get("/agents/session", headers={"Authorization": "TokenOnly"})
    assert resp.status_code == 401


def test_post_session_unset_password_returns_503(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    monkeypatch.setattr(settings, "SITE_PASSWORD", None)
    resp = client.post("/agents/session", headers={"Authorization": "Bearer anything"})
    assert resp.status_code == 503
    body = resp.json()
    assert body["detail"]["code"] == "site_password_not_configured"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_post_session_starts_and_returns_status(
    site_password: str,
    client: TestClient,
    fake_manager: FakeSessionManager,
) -> None:
    resp = client.post("/agents/session", headers={"Authorization": f"Bearer {site_password}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is True
    assert body["target_channel_count"] == settings.CHANNEL_COUNT
    assert body["seconds_remaining"] > 0
    assert body["expires_at"] is not None
    assert fake_manager.start_calls == [settings.AGENT_SESSION_DURATION_MINUTES]


def test_get_session_returns_inactive_by_default(
    site_password: str,
    client: TestClient,
    fake_manager: FakeSessionManager,  # noqa: ARG001
) -> None:
    resp = client.get("/agents/session", headers={"Authorization": f"Bearer {site_password}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "active": False,
        "expires_at": None,
        "seconds_remaining": 0,
        "target_channel_count": 0,
    }


def test_post_then_get_round_trip(
    site_password: str,
    client: TestClient,
) -> None:
    headers = {"Authorization": f"Bearer {site_password}"}
    post_resp = client.post("/agents/session", headers=headers)
    assert post_resp.status_code == 200
    get_resp = client.get("/agents/session", headers=headers)
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["active"] is True
    assert body["target_channel_count"] == settings.CHANNEL_COUNT
