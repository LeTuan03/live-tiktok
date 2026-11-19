"""Upload / signed-URL helpers for product images and avatar assets.

Two flows are supported:

1. **Server-side upload** — :func:`upload_bytes` puts an in-memory blob
   straight into the bucket. Useful for trusted-server flows (e.g. resizing
   an uploaded image, then re-uploading the variant).
2. **Client-direct upload** — :func:`generate_presigned_put_url` returns a
   short-lived signed URL so the browser/native client can ``PUT`` to the
   bucket without round-tripping bytes through the API.

Public read URLs are always built from ``S3_PUBLIC_URL`` so a CDN can be put
in front of the bucket later without a code change.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from soshop_server.assets.client import s3_client
from soshop_server.core.config import settings


class AssetsNotConfiguredError(RuntimeError):
    """Raised when the S3 env vars are missing at request time."""


@dataclass(frozen=True)
class PresignedUpload:
    """Bundle returned to clients that need to ``PUT`` direct-to-S3."""

    upload_url: str
    public_url: str
    key: str
    expires_in: int


def _require_bucket() -> str:
    if not settings.BUCKET_NAME:
        raise AssetsNotConfiguredError("BUCKET_NAME is not set")
    return settings.BUCKET_NAME


def public_url_for_key(key: str) -> str:
    """Build the public read URL for ``key``.

    Prefers ``S3_PUBLIC_URL`` (CDN-friendly base, e.g. ``https://cdn.soshop.tv``)
    and falls back to ``<endpoint>/<bucket>/<key>`` so local dev against MinIO
    works without extra config.
    """
    bucket = _require_bucket()
    if settings.S3_PUBLIC_URL:
        return f"{settings.S3_PUBLIC_URL.rstrip('/')}/{key}"
    if settings.AWS_ENDPOINT_URL_S3:
        return f"{settings.AWS_ENDPOINT_URL_S3.rstrip('/')}/{bucket}/{key}"
    # Standard AWS S3 virtual-hosted-style URL.
    region = settings.S3_REGION if settings.S3_REGION != "auto" else "us-east-1"
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"


def build_object_key(prefix: str, extension: str) -> str:
    """Return a collision-free object key under ``prefix``."""
    ext = extension.lstrip(".")
    return f"{prefix.strip('/')}/{uuid4()}.{ext}"


async def upload_bytes(
    *,
    key: str,
    data: bytes,
    content_type: str,
    cache_control: str | None = "public, max-age=3600",
    acl: str | None = None,
) -> str:
    """Upload ``data`` to ``key`` and return the public URL.

    ``cache_control`` defaults to ``public, max-age=3600`` — Tigris's
    recommended TTL for public-bucket assets. The previous "immutable,
    1 year" value was nice for bandwidth but meant ``delete_object``
    didn't actually purge the edge cache (Tigris has no on-demand
    cache invalidation API at the time of writing), so retiring a
    persona avatar left the bytes publicly fetchable until the TTL
    expired. Pass an explicit value when callers know they're writing
    truly content-addressed (UUID-keyed) bytes that will never need
    a server-side purge.

    ``acl`` is optional. Pass ``"public-read"`` to make the object readable
    over the public endpoint without a presigned URL — needed when serving
    persona avatars (or other consumer-feed assets) directly from Tigris,
    since the bucket itself is not publicly listable. Default is ``None``
    (Tigris/AWS apply the bucket-default ACL — typically private).
    """
    bucket = _require_bucket()
    extra: dict[str, str] = {"ContentType": content_type}
    if cache_control is not None:
        extra["CacheControl"] = cache_control
    if acl is not None:
        extra["ACL"] = acl
    async with s3_client() as s3:
        await s3.put_object(Bucket=bucket, Key=key, Body=data, **extra)
    return public_url_for_key(key)


async def generate_presigned_put_url(
    *,
    key: str,
    content_type: str,
    expires_in: int = 900,
) -> PresignedUpload:
    """Issue a presigned PUT URL the client can upload to directly.

    ``expires_in`` defaults to 15 minutes, which is plenty for an interactive
    upload but too short to be worth re-using. The returned ``public_url`` is
    what the client should persist after the upload completes.
    """
    bucket = _require_bucket()
    async with s3_client() as s3:
        upload_url = await s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_in,
            HttpMethod="PUT",
        )
    return PresignedUpload(
        upload_url=upload_url,
        public_url=public_url_for_key(key),
        key=key,
        expires_in=expires_in,
    )
