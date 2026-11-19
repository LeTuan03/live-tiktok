"""Asset upload routes.

Exposes a single endpoint that issues a presigned PUT URL so the client can
upload an image (product photo, avatar) directly to the bucket. The actual
upload bytes never pass through this server.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from soshop.api.routes.feed import UserCtx
from soshop_server.assets import (
    AssetsNotConfiguredError,
    build_object_key,
    generate_presigned_put_url,
)

router = APIRouter(prefix="/assets", tags=["assets"])

_ALLOWED_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
_PREFIXES = frozenset({"products", "avatars"})


class UploadUrlRequest(BaseModel):
    prefix: str = Field(
        description="Object-key prefix; one of `products`, `avatars`.",
    )
    content_type: str = Field(description="MIME type of the file to upload.")
    expires_in: int = Field(default=900, ge=60, le=3600)


class UploadUrlResponse(BaseModel):
    upload_url: str
    # `public_url` and `object_url` are the same value — kept for API
    # consistency with `/products/{id}/upload-url`, which historically
    # used `object_url`. New consumers should prefer `public_url`.
    public_url: str
    object_url: str
    key: str
    expires_in: int


@router.post(
    "/upload-url",
    response_model=UploadUrlResponse,
    status_code=status.HTTP_200_OK,
)
async def create_upload_url(body: UploadUrlRequest, user: UserCtx) -> UploadUrlResponse:
    # Require any authenticated user. The advertiser-scoped
    # `/products/{id}/upload-url` is the org-bound variant; this generic
    # endpoint stays available for cases where a product id isn't yet
    # known, but it must not be an anonymous presigned-PUT sink.
    _ = user.user_id
    if body.prefix not in _PREFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"prefix must be one of {sorted(_PREFIXES)}",
        )
    if body.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"content_type must be one of {sorted(_ALLOWED_CONTENT_TYPES)}",
        )

    extension = body.content_type.split("/", 1)[1]
    key = build_object_key(body.prefix, extension)

    try:
        presigned = await generate_presigned_put_url(
            key=key,
            content_type=body.content_type,
            expires_in=body.expires_in,
        )
    except AssetsNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    return UploadUrlResponse(
        upload_url=presigned.upload_url,
        public_url=presigned.public_url,
        object_url=presigned.public_url,
        key=presigned.key,
        expires_in=presigned.expires_in,
    )
