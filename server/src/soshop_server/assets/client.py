"""S3 client factory for the assets module.

Centralises construction of the ``aioboto3`` session + client so callers don't
have to repeat the credential / endpoint plumbing. Works against AWS S3,
Tigris, and any other S3-compatible service (MinIO for local dev).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aioboto3
from botocore.config import Config as BotoConfig

from soshop_server.core.config import settings


@asynccontextmanager
async def s3_client() -> AsyncIterator[Any]:
    """Yield an async S3 client configured from ``settings``.

    SigV4 is forced because Tigris/MinIO both require it for presigned URLs.
    """
    session = aioboto3.Session(
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.S3_REGION,
    )
    async with session.client(
        "s3",
        endpoint_url=settings.AWS_ENDPOINT_URL_S3,
        config=BotoConfig(signature_version="s3v4"),
    ) as client:
        yield client
