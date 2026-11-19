"""Smoke test for S3 / S3-compatible asset hosting.

Two layers:

* A pure unit test of the public-URL builder (always runs).
* Live round-trip tests against the configured bucket (skip when the S3 env
  vars are unset, so the default ``uv run pytest`` stays hermetic).

Run the live tests against MinIO (see ``server/README.md``) or a real
bucket:

    uv run pytest tests/test_assets_smoke.py -v
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from soshop_server.assets import (
    build_object_key,
    generate_presigned_put_url,
    public_url_for_key,
    upload_bytes,
)
from soshop_server.core.config import settings

# Smallest valid PNG: 1x1 transparent pixel.
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d4944415478da6300010000000500010d0a2db40000000049454e44"
    "ae426082"
)


_S3_CONFIGURED = bool(
    settings.BUCKET_NAME and settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY
)


def test_public_url_uses_s3_public_url_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``S3_PUBLIC_URL`` overrides the endpoint URL — that's what makes a CDN
    swap a config change rather than a code change."""
    monkeypatch.setattr(settings, "BUCKET_NAME", "plug-dev", raising=False)
    monkeypatch.setattr(settings, "S3_PUBLIC_URL", "https://cdn.soshop.tv", raising=False)
    assert public_url_for_key("products/abc.png") == "https://cdn.soshop.tv/products/abc.png"


@pytest.mark.skipif(
    not _S3_CONFIGURED,
    reason="S3 env vars not set — skipping live asset smoke test",
)
async def test_upload_bytes_round_trip() -> None:
    # Tigris (the prod backend) does not expose objects publicly by
    # default — the bucket is private and individual objects opt in via
    # the `public-read` ACL. The smoke test sets that ACL explicitly so
    # the GET below resolves; this matches how the seed script and the
    # partner-portal upload flows mark consumer-feed assets.
    key = build_object_key("smoke-test", "png")
    public_url = await upload_bytes(
        key=key,
        data=_TINY_PNG,
        content_type="image/png",
        acl="public-read",
    )

    assert public_url == public_url_for_key(key)

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(public_url)

    assert resp.status_code == 200, (
        f"GET {public_url} returned {resp.status_code}; "
        "is the bucket reachable / is S3_PUBLIC_URL correct?"
    )
    assert resp.content == _TINY_PNG


@pytest.mark.skipif(
    not _S3_CONFIGURED,
    reason="S3 env vars not set — skipping live asset smoke test",
)
async def test_presigned_put_url_round_trip() -> None:
    key = f"smoke-test/{uuid.uuid4()}.png"
    presigned = await generate_presigned_put_url(
        key=key,
        content_type="image/png",
        expires_in=300,
    )

    assert presigned.key == key
    assert presigned.public_url == public_url_for_key(key)

    async with httpx.AsyncClient(timeout=10.0) as client:
        # The presigned PUT URL was minted without an ACL clause, so
        # the upload lands as the bucket default (private). For the
        # follow-up read to succeed we either need a public bucket
        # policy OR we have to attach the ACL header on PUT — the
        # SigV4 signature includes Content-Type but not arbitrary
        # headers, so we resign with `x-amz-acl=public-read` baked
        # into the URL signature when we want public reads. The
        # smoke test below uses a presigned GET so we don't depend
        # on a bucket policy that may not exist.
        put_resp = await client.put(
            presigned.upload_url,
            content=_TINY_PNG,
            headers={"Content-Type": "image/png"},
        )
        assert put_resp.status_code in (200, 204), put_resp.text

        from soshop_server.assets.client import s3_client

        async with s3_client() as s3:
            get_url = await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.BUCKET_NAME, "Key": key},
                ExpiresIn=300,
            )
        get_resp = await client.get(get_url)
        assert get_resp.status_code == 200
        assert get_resp.content == _TINY_PNG
