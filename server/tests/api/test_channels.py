"""API tests for the /channels routes.

Covers the three routes: list, single-channel current ad, and
the admin reconcile — with a hermetic in-memory stand-in for the
joined channels/channel_ad_log/ads/products/personas/advertisers
queries. The token mint helper is monkey-patched so we don't need
LiveKit credentials at test time; the routes' job is to assemble the
right payload + run auth gating, which is what we assert.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from soshop.api.routes import channels as channels_route
from soshop.api.routes.channels import router
from soshop.auth.identity import (
    OrgContext,
    OrgRole,
    require_org,
    require_org_admin,
)
from soshop_server.api.deps import get_db_conn
from soshop_server.core.config import settings
from soshop_server.db.models import Channel, ChannelStatus

ORG_ID = "org_channels_test"
USER_ID = "user_channels_test"


# ---------------------------------------------------------------------------
# In-memory fake — only models the joined SQL the channels route emits.
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _make_channel_row(
    *,
    channel_id: str,
    ordinal: int,
    ad: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the joined row shape produced by `_RUNNING_CHANNELS_WITH_AD_SQL`."""
    base: dict[str, Any] = {
        "channel_id": channel_id,
        "ordinal": ordinal,
        "livekit_room_name": channel_id,
        "ad_started_at": None,
        "ad_id": None,
        "ad_duration_ms": None,
        "product_id": None,
        "product_title": None,
        "product_price_cents": None,
        "product_currency": None,
        "product_deep_link_url": None,
        "product_landing_url": None,
        "product_image_url": None,
        # `product_media_json` mirrors the json_agg output from the
        # SQL lateral aggregator. Tests can override this with a JSON
        # string (asyncpg's wire shape) or a pre-parsed list.
        "product_media_json": None,
        "persona_id": None,
        "persona_name": None,
        "persona_avatar_url": None,
        "advertiser_id": None,
        "advertiser_name": None,
    }
    if ad is not None:
        base.update(ad)
    return base


class FakeChannelsConn:
    """asyncpg-shaped fake honoring just the SQL the channels route runs."""

    def __init__(self) -> None:
        self.channel_rows: list[dict[str, Any]] = []
        # Map channel_id -> joined row (or None for "not found").
        self.single_rows: dict[str, dict[str, Any]] = {}
        # Recorded queries for diagnostics if a test fails.
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.queries.append((query, args))
        if "FROM channels c" in query and "c.status = 'running'" in query:
            return list(self.channel_rows)
        raise NotImplementedError(f"fetch not handled: {query!r}")

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.queries.append((query, args))
        if "FROM channels c" in query and "WHERE c.id = $1" in query:
            (channel_id,) = args
            return self.single_rows.get(channel_id)
        raise NotImplementedError(f"fetchrow not handled: {query!r}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_conn() -> FakeChannelsConn:
    return FakeChannelsConn()


@pytest.fixture
def app(fake_conn: FakeChannelsConn, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    async def override_db() -> Any:
        yield fake_conn

    app.dependency_overrides[get_db_conn] = override_db

    captured_mints: list[dict[str, Any]] = []

    def fake_mint(**kwargs: Any) -> tuple[str, str]:
        captured_mints.append(kwargs)
        return f"anon-{uuid4().hex}", "channel.jwt.fake"

    monkeypatch.setattr(channels_route, "mint_anon_viewer_token", fake_mint)
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", "k", raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", "s", raising=False)
    monkeypatch.setattr(settings, "LIVEKIT_URL", "wss://lk.example", raising=False)
    monkeypatch.setattr(settings, "CHANNEL_COUNT", 2, raising=False)

    app.state._captured_mints = captured_mints  # type: ignore[attr-defined]
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _admin_ctx() -> OrgContext:
    return OrgContext(
        user_id=USER_ID,
        org_id=ORG_ID,
        org_role=OrgRole.ADMIN,
        org_slug="test-org",
        raw_claims={"sub": USER_ID, "org_id": ORG_ID},
    )


def _member_ctx() -> OrgContext:
    return OrgContext(
        user_id=USER_ID,
        org_id=ORG_ID,
        org_role=OrgRole.MEMBER,
        org_slug="test-org",
        raw_claims={"sub": USER_ID, "org_id": ORG_ID},
    )


# ---------------------------------------------------------------------------
# GET /channels — anonymous happy path + token shape
# ---------------------------------------------------------------------------


def test_list_channels_returns_one_token_per_channel_anonymously(
    client: TestClient, fake_conn: FakeChannelsConn, app: FastAPI
) -> None:
    """One token minted per channel per request; no Bearer header sent."""
    ad_started = datetime(2026, 5, 12, 9, 0, tzinfo=UTC)
    product_id, persona_id, advertiser_id, ad_id = (uuid4() for _ in range(4))

    fake_conn.channel_rows = [
        _make_channel_row(channel_id="channel-0", ordinal=0),
        _make_channel_row(
            channel_id="channel-1",
            ordinal=1,
            ad={
                "ad_started_at": ad_started,
                "ad_id": ad_id,
                "ad_duration_ms": 15000,
                "product_id": product_id,
                "product_title": "Acme Cap",
                "product_price_cents": 1999,
                "product_currency": "USD",
                "product_deep_link_url": "soshop://acme/cap",
                "product_landing_url": "https://acme.test/cap",
                "product_image_url": "https://cdn.acme.test/cap.jpg",
                "persona_id": persona_id,
                "persona_name": "Vivian Vibe",
                "persona_avatar_url": "https://cdn.acme.test/vivian.jpg",
                "advertiser_id": advertiser_id,
                "advertiser_name": "Acme",
            },
        ),
    ]

    resp = client.get("/channels")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    captured: list[dict[str, Any]] = app.state._captured_mints  # type: ignore[attr-defined]

    _assert_channel_envelope(body, captured)
    _assert_acme_cap_ad_snapshot(body[1]["current_ad"], ad_id=ad_id)


def _assert_channel_envelope(body: list[dict[str, Any]], captured: list[dict[str, Any]]) -> None:
    """Shape assertions for the two-channel list response (channel-0, channel-1)."""
    assert [c["id"] for c in body] == ["channel-0", "channel-1"]
    assert [c["ordinal"] for c in body] == [0, 1]
    # Every channel echoes the same configured LiveKit URL.
    assert {c["livekit_url"] for c in body} == {"wss://lk.example"}
    # Each channel gets its own freshly-minted viewer token; one mint per
    # channel — the AC mandates one viewer token per request per channel.
    assert [bool(c["livekit_token"]) for c in body] == [True, True]
    assert [m["room"] for m in captured] == ["channel-0", "channel-1"]
    # The channel without a live ad surfaces current_ad=None, not absent.
    assert body[0]["current_ad"] is None


def _assert_acme_cap_ad_snapshot(ad: dict[str, Any], *, ad_id: UUID) -> None:
    """The channel-1 ad surfaces the full nested snapshot."""
    assert ad == {
        **ad,
        "id": str(ad_id),
        "deep_link_url": "soshop://acme/cap",
        "duration_ms": 15000,
        "product": {
            **ad["product"],
            "title": "Acme Cap",
            "price_cents": 1999,
            "image_url": "https://cdn.acme.test/cap.jpg",
        },
        "persona": {**ad["persona"], "display_name": "Vivian Vibe"},
        "advertiser": {**ad["advertiser"], "name": "Acme"},
    }


def test_list_channels_503_when_livekit_unconfigured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing LiveKit credentials → 503 with a stable error code."""
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", "", raising=False)
    resp = client.get("/channels")
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "livekit_not_configured"


def test_list_channels_empty_when_none_running(
    client: TestClient, fake_conn: FakeChannelsConn
) -> None:
    """No running channels → empty list, not an error."""
    fake_conn.channel_rows = []
    resp = client.get("/channels")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /channels/{id}/current — anonymous happy path + 404
# ---------------------------------------------------------------------------


def test_get_channel_current_returns_ad_snapshot(
    client: TestClient, fake_conn: FakeChannelsConn
) -> None:
    ad_started = datetime(2026, 5, 12, 10, 0, tzinfo=UTC)
    product_id, persona_id, advertiser_id, ad_id = (uuid4() for _ in range(4))

    fake_conn.single_rows["channel-0"] = _make_channel_row(
        channel_id="channel-0",
        ordinal=0,
        ad={
            "ad_started_at": ad_started,
            "ad_id": ad_id,
            "ad_duration_ms": 12000,
            "product_id": product_id,
            "product_title": "Plush Hoodie",
            "product_price_cents": 4999,
            "product_currency": "USD",
            "product_deep_link_url": "soshop://plush/hoodie",
            "product_landing_url": "https://plush.test/hoodie",
            "product_image_url": "https://cdn.plush.test/hoodie.jpg",
            "persona_id": persona_id,
            "persona_name": "Plush Pete",
            "persona_avatar_url": None,
            "advertiser_id": advertiser_id,
            "advertiser_name": "Plush Co",
        },
    )

    resp = client.get("/channels/channel-0/current")
    assert resp.status_code == 200, resp.text
    ad = resp.json()["ad"]
    assert ad["id"] == str(ad_id)
    assert ad["product"]["title"] == "Plush Hoodie"
    assert ad["product"]["deep_link_url"] == "soshop://plush/hoodie"
    assert ad["deep_link_url"] == "soshop://plush/hoodie"
    assert ad["duration_ms"] == 12000
    assert ad["persona"]["avatar_url"] is None


def test_get_channel_current_returns_null_ad_when_no_log_row(
    client: TestClient, fake_conn: FakeChannelsConn
) -> None:
    """Channel exists but no ad has been logged yet → ad is null, not 404."""
    fake_conn.single_rows["channel-0"] = _make_channel_row(
        channel_id="channel-0", ordinal=0, ad=None
    )
    resp = client.get("/channels/channel-0/current")
    assert resp.status_code == 200
    assert resp.json() == {"ad": None}


def test_get_channel_current_404_when_unknown(
    client: TestClient, fake_conn: FakeChannelsConn
) -> None:
    resp = client.get("/channels/channel-does-not-exist/current")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "channel_not_found"


# ---------------------------------------------------------------------------
# POST /admin/channels/reconcile — admin gate + delegate to reconcile_channels
# ---------------------------------------------------------------------------


def _channel_row(ordinal: int, ad_seq: int) -> Channel:
    now = _now()
    cid = f"channel-{ordinal}"
    return Channel(
        id=cid,
        ordinal=ordinal,
        livekit_room_name=cid,
        current_ad_sequence=ad_seq,
        agent_dispatch_id=f"AD_{ordinal:04d}",
        status=ChannelStatus.RUNNING,
        last_heartbeat_at=None,
        created_at=now,
        updated_at=now,
    )


def test_admin_reconcile_calls_reconcile_channels_with_settings_count(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.dependency_overrides[require_org] = _admin_ctx
    app.dependency_overrides[require_org_admin] = _admin_ctx

    captured_args: list[tuple[Any, ...]] = []

    async def fake_reconcile(target_count: int, *args: Any, **kwargs: Any):
        captured_args.append((target_count, args, kwargs))
        return [_channel_row(0, 1), _channel_row(1, 21)]

    monkeypatch.setattr(channels_route, "reconcile_channels", fake_reconcile)

    bearer = TestClient(app)
    resp = bearer.post("/admin/channels/reconcile")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [c["id"] for c in body["channels"]] == ["channel-0", "channel-1"]
    # Reconciler must be called with the configured CHANNEL_COUNT (=2 in the
    # fixture), not a magic number.
    assert captured_args == [(2, (), {})]


def test_admin_reconcile_403_when_org_member_not_admin(
    app: FastAPI,
) -> None:
    """org:member is not enough; only org:admin is permitted."""
    app.dependency_overrides[require_org] = _member_ctx
    # Reset the admin override to default so the production gate runs.
    app.dependency_overrides.pop(require_org_admin, None)

    member_client = TestClient(app)
    resp = member_client.post("/admin/channels/reconcile")
    assert resp.status_code == 403


def test_admin_reconcile_open_access_no_auth_header(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open access: with no external auth provider an anonymous request
    resolves to the default admin org and reaches the reconciler, so it is
    no longer rejected with 401/403."""
    app.dependency_overrides.pop(require_org, None)
    app.dependency_overrides.pop(require_org_admin, None)

    async def fake_reconcile(target_count: int, *args: Any, **kwargs: Any):
        return [_channel_row(0, 1)]

    monkeypatch.setattr(channels_route, "reconcile_channels", fake_reconcile)

    anon_client = TestClient(app)
    resp = anon_client.post("/admin/channels/reconcile")
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Token mint helper — viewer TTL ≥ 6h, grants are subscribe-only
# ---------------------------------------------------------------------------


def test_mint_anon_viewer_token_grants_are_subscribe_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The AC requires can_publish=False, can_publish_data=True (chat fan-out),
    can_subscribe=True, anon identity."""
    captured: dict[str, Any] = {}

    class FakeVideoGrants:
        def __init__(self, **kwargs: Any) -> None:
            captured["grants"] = kwargs

    class FakeAccessToken:
        def __init__(self, *, api_key: str, api_secret: str) -> None:
            captured["api_key"] = api_key
            captured["api_secret"] = api_secret

        def with_identity(self, identity: str) -> FakeAccessToken:
            captured["identity"] = identity
            return self

        def with_grants(self, grants: Any) -> FakeAccessToken:
            captured["grants_obj"] = grants
            return self

        def with_ttl(self, ttl: Any) -> FakeAccessToken:
            captured["ttl"] = ttl
            return self

        def to_jwt(self) -> str:
            return "viewer.jwt.fake"  # noqa: S105 — test fixture

    import livekit.api as livekit_api  # type: ignore[import-not-found]

    monkeypatch.setattr(livekit_api, "AccessToken", FakeAccessToken)
    monkeypatch.setattr(livekit_api, "VideoGrants", FakeVideoGrants)

    identity, token = channels_route.mint_anon_viewer_token(
        api_key="k",
        api_secret="s",  # noqa: S106 — test fixture, not a real secret
        room="channel-0",
    )

    assert token == "viewer.jwt.fake"  # noqa: S105
    assert identity.startswith("anon-")
    # Subscribe-only for media (``can_publish=False``) but allowed to send
    # data messages so the anonymous viewer can broadcast chat on the
    # `global_chat` text-stream topic.
    assert captured["grants"] == {
        "room": "channel-0",
        "room_join": True,
        "can_publish": False,
        "can_publish_data": True,
        "can_subscribe": True,
    }
    # TTL ≥ 6h — the floor.
    assert captured["ttl"].total_seconds() >= 6 * 60 * 60


def test_mint_anon_viewer_token_rejects_missing_credentials() -> None:
    with pytest.raises(ValueError, match="api_key and api_secret"):
        channels_route.mint_anon_viewer_token(
            api_key="",
            api_secret="s",  # noqa: S106 — test fixture, not a real secret
            room="channel-0",
        )


def test_mint_anon_viewer_token_rejects_blank_room() -> None:
    with pytest.raises(ValueError, match="room is required"):
        channels_route.mint_anon_viewer_token(
            api_key="k",
            api_secret="s",  # noqa: S106 — test fixture, not a real secret
            room="",
        )


# ---------------------------------------------------------------------------
# Channel id validation
# ---------------------------------------------------------------------------


def test_get_channel_current_rejects_blank_id(client: TestClient) -> None:
    # An empty path segment routes to /channels//current which FastAPI
    # treats as a 404 at the router level — the typed Path min_length=1
    # never fires because the path isn't matched.
    resp = client.get("/channels//current")
    assert resp.status_code == 404


def test_get_channel_current_rejects_oversize_id(client: TestClient) -> None:
    too_long = "c" * 200
    resp = client.get(f"/channels/{too_long}/current")
    assert resp.status_code == 422


def _unused_session_id() -> UUID:
    return uuid4()
