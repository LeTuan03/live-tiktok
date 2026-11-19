"""S3 helper unit tests with a mocked aioboto3 client.

`test_assets_smoke.py` already covers the live round-trip path (skipped
when env vars are unset). This file complements it by unit-testing the
helpers' interaction with the S3 client without needing a network.

The strategy: replace the `s3_client()` async-context-manager in
`soshop_server.assets.service` with one that yields a `_FakeS3` recording
the calls. This is *not* mocking the database — the user has been clear
that hides migration breakage — it's mocking an external blob store at
its boundary, which is the safe place to do it.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from soshop_server.assets import service as assets_service
from soshop_server.assets.service import (
    AssetsNotConfiguredError,
    build_object_key,
    generate_presigned_put_url,
    public_url_for_key,
    upload_bytes,
)
from soshop_server.core.config import settings


class _FakeS3:
    """Records the calls the helpers make so tests can assert on them."""

    def __init__(self) -> None:
        self.put_calls: list[dict[str, Any]] = []
        self.presign_calls: list[dict[str, Any]] = []

    async def put_object(self, **kwargs: Any) -> None:
        self.put_calls.append(kwargs)

    async def generate_presigned_url(
        self, op: str, *, Params: dict[str, Any], ExpiresIn: int, HttpMethod: str
    ) -> str:
        self.presign_calls.append(
            {"op": op, "params": Params, "expires_in": ExpiresIn, "method": HttpMethod}
        )
        # Simulate a valid signed URL.
        return f"https://signed.example/{Params['Bucket']}/{Params['Key']}?sig=fake"


@pytest.fixture
def fake_s3(monkeypatch: pytest.MonkeyPatch) -> _FakeS3:
    fake = _FakeS3()

    @asynccontextmanager
    async def _factory() -> Any:
        yield fake

    monkeypatch.setattr(assets_service, "s3_client", _factory)
    monkeypatch.setattr(settings, "BUCKET_NAME", "plug-test", raising=False)
    monkeypatch.setattr(settings, "S3_PUBLIC_URL", "https://cdn.example", raising=False)
    return fake


# ---------------------------------------------------------------------------
# build_object_key
# ---------------------------------------------------------------------------


def test_build_object_key_inserts_uuid_under_prefix() -> None:
    key = build_object_key("products", ".png")
    assert key.startswith("products/")
    assert key.endswith(".png")
    # `prefix.strip('/')` should normalise trailing slashes.
    key2 = build_object_key("avatars/", "jpg")
    assert key2.startswith("avatars/")
    assert key2.endswith(".jpg")


def test_build_object_key_strips_leading_dot_on_extension() -> None:
    """A leading `.` on the extension must not produce `..jpg` paths."""
    key = build_object_key("products", ".jpg")
    assert ".." not in key


def test_build_object_key_unique_per_call() -> None:
    keys = {build_object_key("products", "png") for _ in range(50)}
    assert len(keys) == 50


# ---------------------------------------------------------------------------
# public_url_for_key
# ---------------------------------------------------------------------------


def test_public_url_falls_back_to_endpoint_when_no_cdn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "BUCKET_NAME", "plug-dev", raising=False)
    monkeypatch.setattr(settings, "S3_PUBLIC_URL", None, raising=False)
    monkeypatch.setattr(settings, "AWS_ENDPOINT_URL_S3", "http://localhost:9000", raising=False)
    assert (
        public_url_for_key("products/abc.png") == "http://localhost:9000/plug-dev/products/abc.png"
    )


def test_public_url_falls_back_to_aws_url_with_no_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "BUCKET_NAME", "plug-prod", raising=False)
    monkeypatch.setattr(settings, "S3_PUBLIC_URL", None, raising=False)
    monkeypatch.setattr(settings, "AWS_ENDPOINT_URL_S3", None, raising=False)
    monkeypatch.setattr(settings, "S3_REGION", "us-west-2", raising=False)
    assert (
        public_url_for_key("avatars/x.jpg")
        == "https://plug-prod.s3.us-west-2.amazonaws.com/avatars/x.jpg"
    )


def test_public_url_raises_when_bucket_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "BUCKET_NAME", None, raising=False)
    with pytest.raises(AssetsNotConfiguredError):
        public_url_for_key("k")


# ---------------------------------------------------------------------------
# upload_bytes — passes through the right S3 args
# ---------------------------------------------------------------------------


async def test_upload_bytes_puts_object_with_cache_control(fake_s3: _FakeS3) -> None:
    url = await upload_bytes(key="products/abc.png", data=b"\x89PNG", content_type="image/png")
    assert url == "https://cdn.example/products/abc.png"
    assert len(fake_s3.put_calls) == 1
    call = fake_s3.put_calls[0]
    assert call["Bucket"] == "plug-test"
    assert call["Key"] == "products/abc.png"
    assert call["ContentType"] == "image/png"
    # `upload_bytes`' default `cache_control` was lowered from the
    # "immutable / 1 year" value to a 1-hour TTL so retired persona
    # avatars no longer linger at the edge — Tigris has no on-demand
    # cache invalidation. See the docstring on `assets/service.py`.
    assert call["CacheControl"] == "public, max-age=3600"


async def test_upload_bytes_skips_cache_control_when_none(fake_s3: _FakeS3) -> None:
    await upload_bytes(
        key="products/abc.png",
        data=b"x",
        content_type="image/png",
        cache_control=None,
    )
    assert "CacheControl" not in fake_s3.put_calls[0]


async def test_upload_bytes_raises_when_bucket_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "BUCKET_NAME", None, raising=False)
    with pytest.raises(AssetsNotConfiguredError):
        await upload_bytes(key="k", data=b"x", content_type="image/png")


# ---------------------------------------------------------------------------
# generate_presigned_put_url
# ---------------------------------------------------------------------------


async def test_presigned_put_url_round_trip(fake_s3: _FakeS3) -> None:
    presigned = await generate_presigned_put_url(
        key="avatars/123.png", content_type="image/png", expires_in=600
    )
    assert presigned.upload_url.startswith("https://signed.example/")
    assert presigned.public_url == "https://cdn.example/avatars/123.png"
    assert presigned.expires_in == 600
    # The generate call carried the same key/content-type to S3.
    call = fake_s3.presign_calls[0]
    assert call["params"]["Key"] == "avatars/123.png"
    assert call["params"]["ContentType"] == "image/png"
    assert call["method"] == "PUT"


async def test_presigned_put_url_default_ttl_is_15_minutes(fake_s3: _FakeS3) -> None:
    presigned = await generate_presigned_put_url(key="k", content_type="image/png")
    assert presigned.expires_in == 900
