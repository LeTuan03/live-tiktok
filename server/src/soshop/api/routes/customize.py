"""Public ``/customize`` routes — the "create your AI host" page backend.

Powers https://soshop.tv/customize. Two endpoints:

* ``GET  /customize/voices`` — return the curated host voices (committed seed).
* ``POST /customize/host``   — accept an uploaded avatar + voice + personality
  + script, persist an ephemeral draft persona, dispatch the LiveKit agent,
  and return a join token so the browser can watch the host deliver the pitch.

Public and anonymous by design (this is the consumer surface), with a small
per-IP rate limit because each ``/host`` call spins up paid LiveKit /
LemonSlice / ElevenLabs / Groq usage. Unlike the org-gated
``/assets/upload-url``, the avatar here is uploaded *through* the server so an
anonymous caller can never get a raw presigned-PUT sink.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

from soshop.auth.identity import anonymous_viewer_id
from soshop.livekit.client import LkApi
from soshop.services import customize as customize_service
from soshop_server.api.deps import DbConn
from soshop_server.assets import AssetsNotConfiguredError, build_object_key, upload_bytes

logger = logging.getLogger("soshop.api.customize")

router = APIRouter(prefix="/customize", tags=["customize"])

# content-type -> file extension. Mirrors the allow-list in the org-gated
# `/assets/upload-url` so both upload paths accept the same image formats.
_ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}
_MAX_AVATAR_BYTES = 10 * 1024 * 1024
_MAX_PERSONALITY_CHARS = 600
_MAX_NAME_CHARS = 80
_DEFAULT_HOST_NAME = "My AI Host"

# Per-IP rate limit: small + in-memory, enough abuse protection for a demo.
# A multi-instance deploy would swap this for a shared store (Redis).
_RATE_MAX = 5
_RATE_WINDOW_S = 60.0
_rate_lock = threading.Lock()
_rate_log: dict[str, list[float]] = {}


def _enforce_rate_limit(client_ip: str) -> None:
    now = time.monotonic()
    with _rate_lock:
        hits = [t for t in _rate_log.get(client_ip, []) if now - t < _RATE_WINDOW_S]
        if len(hits) >= _RATE_MAX:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many hosts created from here. Wait a minute and try again.",
                headers={"Retry-After": str(int(_RATE_WINDOW_S))},
            )
        hits.append(now)
        _rate_log[client_ip] = hits


class CuratedVoicesResponse(BaseModel):
    """The fixed curated host voices shown by the voice picker."""

    voices: list[customize_service.VoiceOut]


class CustomHostResponse(BaseModel):
    """What the browser needs to join the room and watch the pitch."""

    room_name: str
    livekit_url: str
    token: str
    persona_id: str
    session_id: str


@router.get(
    "/voices",
    response_model=CuratedVoicesResponse,
    summary="Return the curated host voices for the customize voice picker",
)
async def list_voices() -> CuratedVoicesResponse:
    # Static, local read of the committed seed: no query params, no upstream
    # round trip, so no 503 path.
    return CuratedVoicesResponse(voices=customize_service.load_curated_voices())


def _validate_avatar(avatar: UploadFile, data: bytes) -> str:
    """Return the file extension for a valid avatar, else raise HTTP 4xx."""
    extension = _ALLOWED_CONTENT_TYPES.get((avatar.content_type or "").lower())
    if extension is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Avatar must be a JPEG, PNG, WebP, or GIF image.",
        )
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Avatar file is empty.",
        )
    if len(data) > _MAX_AVATAR_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Avatar must be 10MB or smaller.",
        )
    return extension


@router.post(
    "/host",
    response_model=CustomHostResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an ephemeral AI host and dispatch it into a live room",
)
async def create_host(
    request: Request,
    conn: DbConn,
    api_client: LkApi,
    avatar: Annotated[UploadFile, File(description="9:16 portrait image")],
    voice_id: Annotated[str, Form(min_length=1, max_length=128)],
    personality: Annotated[str, Form(min_length=1)],
    script: Annotated[str, Form(min_length=1)],
    public_owner_id: Annotated[str | None, Form(max_length=128)] = None,
    name: Annotated[str | None, Form(max_length=_MAX_NAME_CHARS)] = None,
) -> CustomHostResponse:
    _enforce_rate_limit(request.client.host if request.client else "unknown")

    data = await avatar.read()
    extension = _validate_avatar(avatar, data)

    personality = personality.strip()
    if len(personality) > _MAX_PERSONALITY_CHARS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Personality must be {_MAX_PERSONALITY_CHARS} characters or fewer.",
        )

    script = script.strip()
    try:
        customize_service.validate_script(script)
    except customize_service.ScriptOutOfBandError as exc:
        # Literal 422 (not status.HTTP_422_*): Starlette renamed the constant
        # (ENTITY -> CONTENT) and accessing the old name emits a DeprecationWarning,
        # while the new name isn't present on older pins. The int is stable.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    host_name = (name or "").strip() or _DEFAULT_HOST_NAME

    try:
        avatar_url = await upload_bytes(
            key=build_object_key("avatars", extension),
            data=data,
            content_type=avatar.content_type or "image/jpeg",
            acl="public-read",
        )
    except AssetsNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    await customize_service.cleanup_stale_drafts(conn)
    # Copy a Voice Library pick into our account so it's guaranteed usable at
    # dispatch and can't be revoked by the original owner. Premade voices and
    # copy failures fall through to the original id (the agent's TTS fallback
    # still keeps the host audible).
    effective_voice_id = await customize_service.ensure_voice_in_account(
        voice_id.strip(), (public_owner_id or "").strip() or None
    )
    draft = await customize_service.insert_draft_host(
        conn,
        name=host_name,
        voice_id=effective_voice_id,
        avatar_image_url=avatar_url,
        personality=personality,
        script_text=script,
    )

    dispatched = await customize_service.dispatch_custom_host(
        api_client,
        persona_id=draft.persona_id,
        script_id=draft.script_id,
        user_id=anonymous_viewer_id(),
    )
    logger.info(
        "customize_host_created",
        extra={
            "event": "customize_host_created",
            "persona_id": draft.persona_id,
            "room": dispatched.room_name,
            "session_id": dispatched.session_id,
        },
    )
    return CustomHostResponse(
        room_name=dispatched.room_name,
        livekit_url=dispatched.livekit_url,
        token=dispatched.token,
        persona_id=draft.persona_id,
        session_id=dispatched.session_id,
    )
