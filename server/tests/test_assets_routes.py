"""API tests for the `/assets/upload-url` route.

The route is the generic, advertiser-unscoped presigned-PUT issuer (the
advertiser portal uses `/products/{id}/upload-url` instead). Both clients
hit it indirectly when they need a key without a product context.

The S3 helper is monkey-patched so the test does not need a live bucket;
the route logic (auth, validation, error envelope) is what's under test
here. Live S3 round-trips are exercised separately in
`test_s3_integration.py` and `test_assets_smoke.py`.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from soshop.api.routes.feed import UserContext, require_user
from soshop_server.api.routes import assets as assets_route
from soshop_server.api.routes.assets import router
from soshop_server.assets import AssetsNotConfiguredError, PresignedUpload

USER_ID = "user_test_assets"


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_user] = lambda: UserContext(user_id=USER_ID)

    async def fake_presign(**kwargs: Any) -> PresignedUpload:
        key = kwargs["key"]
        return PresignedUpload(
            upload_url=f"https://signed.example/{key}?X-Amz-Sig=fake",
            public_url=f"https://cdn.example/{key}",
            key=key,
            expires_in=kwargs.get("expires_in", 900),
        )

    monkeypatch.setattr(assets_route, "generate_presigned_put_url", fake_presign)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_upload_url_happy_path_products(client: TestClient) -> None:
    resp = client.post(
        "/assets/upload-url",
        json={"prefix": "products", "content_type": "image/jpeg"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key"].startswith("products/")
    assert body["key"].endswith(".jpeg")
    assert body["upload_url"].startswith("https://signed.example/")
    # `public_url` and `object_url` are intentional aliases.
    assert body["public_url"] == body["object_url"]
    assert body["expires_in"] == 900


def test_upload_url_happy_path_avatars(client: TestClient) -> None:
    resp = client.post(
        "/assets/upload-url",
        json={
            "prefix": "avatars",
            "content_type": "image/png",
            "expires_in": 600,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["key"].startswith("avatars/")
    assert body["key"].endswith(".png")
    assert body["expires_in"] == 600


def test_upload_url_rejects_unknown_prefix(client: TestClient) -> None:
    """Only `products` and `avatars` are valid; anything else is 400."""
    resp = client.post(
        "/assets/upload-url",
        json={"prefix": "evil", "content_type": "image/png"},
    )
    assert resp.status_code == 400
    assert "prefix" in resp.json()["detail"].lower()


def test_upload_url_rejects_disallowed_content_type(client: TestClient) -> None:
    """Non-image MIME types are rejected to keep the bucket image-only."""
    resp = client.post(
        "/assets/upload-url",
        json={"prefix": "products", "content_type": "application/pdf"},
    )
    assert resp.status_code == 400
    assert "content_type" in resp.json()["detail"].lower()


def test_upload_url_returns_503_when_assets_unconfigured(
    monkeypatch: pytest.MonkeyPatch, app: FastAPI
) -> None:
    """If the bucket isn't configured the helper raises and we surface 503."""

    async def boom(**_: Any) -> PresignedUpload:
        raise AssetsNotConfiguredError("BUCKET_NAME is not set")

    monkeypatch.setattr(assets_route, "generate_presigned_put_url", boom)
    client = TestClient(app)
    resp = client.post(
        "/assets/upload-url",
        json={"prefix": "products", "content_type": "image/png"},
    )
    assert resp.status_code == 503
    assert "BUCKET_NAME" in resp.json()["detail"]


def test_upload_url_open_access() -> None:
    """Open access: with no external auth provider the upload-url route is
    reachable without a token, so it no longer rejects with 401/403. Here it
    reaches the handler (which 503s because BUCKET_NAME is unset in tests)."""
    app = FastAPI()
    app.include_router(router)
    bare = TestClient(app)
    resp = bare.post(
        "/assets/upload-url",
        json={"prefix": "products", "content_type": "image/png"},
    )
    assert resp.status_code not in (401, 403)
