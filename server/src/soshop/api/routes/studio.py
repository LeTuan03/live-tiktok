"""Live studio API: AI models, live sessions, ordered products, on-air control.

Open access like the rest of the app. The studio page creates a session,
fills its product list, then starts it: that creates a LiveKit room,
dispatches the agent worker in live-host mode and returns a join token. While
on air, ``/control`` relays director commands to the agent over the room's
``agent_control`` data topic and the agent reports state back into
``live_sessions.live_state``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import uuid4

import asyncpg
from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import Field

from soshop.agent.schemas import DispatchMetadata
from soshop.livekit.channels import AGENT_PARTICIPANT_KIND, _delete_room_dispatches
from soshop.livekit.client import get_livekit_api
from soshop.livekit.control import send_agent_control
from soshop.livekit.dispatch import create_validated_dispatch
from soshop.services import studio, studio_assets, studio_videos
from soshop.services.studio_script import ScriptGenerationError, generate_product_script
from soshop_server.api.deps import DbConn, StrictModel
from soshop_server.core.config import settings
from soshop_server.db.pool import get_pool

logger = logging.getLogger("soshop.api.studio")

router = APIRouter(prefix="/studio", tags=["studio"])

# The agent refreshes its state every few seconds. A live session whose state
# has gone quiet this long has lost its agent (worker crash, LiveKit restart).
_AGENT_SILENT_AFTER = timedelta(seconds=75)
# Grace for the agent to say goodbye before the room is torn down.
_END_GRACE_S = 12.0
_TOKEN_TTL = timedelta(hours=12)
# How long to wait for the agent to appear in the room after a dispatch, and
# how many dispatches to try. A healthy worker joins in about two seconds.
_AGENT_JOIN_WAIT_S = 8.0
_DISPATCH_ATTEMPTS = 3

ControlAction = Literal[
    "start_products", "pause", "resume", "skip", "present", "stop_products", "say"
]


# -------------------------------------------------------------------- shapes


class ModelOut(studio.ModelRow):
    avatar_status: studio_assets.AvatarStatusName
    avatar_progress: float | None
    avatar_error: str | None


class SessionOut(studio.SessionRow):
    model: ModelOut | None
    products: list[studio.ProductRow]


class ModelsResponse(StrictModel):
    models: list[ModelOut]


class VoicesResponse(StrictModel):
    voices: list[studio.VoiceOption]


class SessionsResponse(StrictModel):
    sessions: list[SessionOut]


class ProductsResponse(StrictModel):
    products: list[studio.ProductRow]


class ModelPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    intro: str | None = Field(default=None, max_length=2000)
    personality: str | None = Field(default=None, max_length=1000)
    language: studio.Language | None = None
    voice: str | None = None


class SessionCreate(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    model_id: str | None = None


class SessionPatch(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    model_id: str | None = None
    notes: str | None = Field(default=None, max_length=4000)


class ProductCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    price: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    description: str | None = Field(default=None, max_length=4000)
    highlights: str | None = Field(default=None, max_length=4000)
    image_url: str | None = Field(default=None, max_length=2000)
    script: str | None = Field(default=None, max_length=6000)


class ProductPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    price: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    description: str | None = Field(default=None, max_length=4000)
    highlights: str | None = Field(default=None, max_length=4000)
    image_url: str | None = Field(default=None, max_length=2000)
    script: str | None = Field(default=None, max_length=6000)


class ProductOrder(StrictModel):
    product_ids: list[str]


class UploadResponse(StrictModel):
    url: str


class JoinInfo(StrictModel):
    livekit_url: str
    token: str
    room_name: str


class StartResponse(JoinInfo):
    session: SessionOut


class ControlRequest(StrictModel):
    action: ControlAction
    product_id: str | None = None
    text: str | None = Field(default=None, max_length=500)


class OkResponse(StrictModel):
    ok: bool = True


class PublicModel(StrictModel):
    name: str
    image_url: str | None


class PublicProduct(StrictModel):
    id: str
    position: int
    name: str
    price: float | None
    currency: str
    description: str
    image_url: str | None
    status: studio.ProductStatus


class PublicSession(StrictModel):
    id: str
    title: str
    status: studio.SessionStatus
    model: PublicModel | None
    live: studio.LiveState | None
    products: list[PublicProduct]


class VideoCreate(StrictModel):
    model_id: str
    product_id: str


class VideosResponse(StrictModel):
    videos: list[studio_videos.VideoJob]


# ------------------------------------------------------------------- helpers


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Không tìm thấy {what}.")


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)


def _model_out(model: studio.ModelRow) -> ModelOut:
    avatar = studio_assets.read_avatar_status(model.id)
    return ModelOut(
        **model.model_dump(),
        avatar_status=avatar.status,
        avatar_progress=avatar.progress,
        avatar_error=avatar.error,
    )


async def _expire_if_agent_gone(
    conn: asyncpg.Connection, session: studio.SessionRow
) -> studio.SessionRow:
    """End a live session whose agent stopped reporting.

    Without this a crashed worker would leave the studio showing "live"
    forever with no way to start again.
    """
    if session.status != "live":
        return session
    last_seen = session.live.updated_at if session.live else session.started_at
    if last_seen is None or datetime.now(UTC) - last_seen < _AGENT_SILENT_AFTER:
        return session
    logger.warning("live session %s lost its agent; marking ended", session.id)
    await studio.mark_session_ended(conn, session.id)
    refreshed = await studio.get_session(conn, session.id)
    return refreshed or session


async def _session_out(conn: asyncpg.Connection, session: studio.SessionRow) -> SessionOut:
    session = await _expire_if_agent_gone(conn, session)
    model = await studio.get_model(conn, session.model_id) if session.model_id else None
    products = await studio.list_products(conn, session.id)
    return SessionOut(
        **session.model_dump(),
        model=_model_out(model) if model else None,
        products=products,
    )


async def _require_session(conn: asyncpg.Connection, session_id: str) -> studio.SessionRow:
    session = await studio.get_session(conn, session_id)
    if session is None:
        raise _not_found("phiên livestream")
    return session


async def _require_live(conn: asyncpg.Connection, session_id: str) -> studio.SessionRow:
    session = await _expire_if_agent_gone(conn, await _require_session(conn, session_id))
    if session.status != "live" or not session.room_name:
        raise _conflict("Phiên livestream chưa bắt đầu hoặc đã kết thúc.")
    return session


def _room_name(session_id: str) -> str:
    return f"live-{session_id}"


def _mint_token(room: str, *, identity: str, name: str) -> str:
    from livekit.api import AccessToken, VideoGrants

    grants = VideoGrants(
        room=room,
        room_join=True,
        can_publish=False,
        can_subscribe=True,
        can_publish_data=True,
    )
    return (
        AccessToken(api_key=settings.LIVEKIT_API_KEY, api_secret=settings.LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(name)
        .with_grants(grants)
        .with_ttl(_TOKEN_TTL)
        .to_jwt()
    )


def _join_info(room: str, role: str, name: str | None) -> JoinInfo:
    display = (name or "").strip()[:40] or ("Chủ phòng" if role == "host" else "Khán giả")
    return JoinInfo(
        livekit_url=settings.LIVEKIT_URL,
        token=_mint_token(room, identity=f"{role}-{uuid4().hex[:12]}", name=display),
        room_name=room,
    )


async def _dispatch_until_joined(room: str, metadata: DispatchMetadata) -> bool:
    """Dispatch the agent and confirm it actually joined the room.

    ``create_dispatch`` succeeds even when no worker takes the job (worker
    down, or reporting full load), which used to look like a started stream
    that never showed a host. A dropped job is not retried by LiveKit, so
    re-dispatch a couple of times before reporting failure.
    """
    api = await get_livekit_api()
    for _attempt in range(_DISPATCH_ATTEMPTS):
        await _delete_room_dispatches(api, room_name=room)
        await create_validated_dispatch(
            api, agent_name=settings.AGENT_NAME, room=room, metadata=metadata
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _AGENT_JOIN_WAIT_S
        while loop.time() < deadline:
            await asyncio.sleep(0.5)
            if await _agent_in_room(room):
                return True
        logger.warning("live dispatch for room %s was not picked up; retrying", room)
    return False


async def _agent_in_room(room: str) -> bool:
    from livekit.api import ListParticipantsRequest

    api = await get_livekit_api()
    resp = await api.room.list_participants(ListParticipantsRequest(room=room))
    return any(p.kind == AGENT_PARTICIPANT_KIND for p in resp.participants)


async def _delete_room(room: str) -> None:
    from livekit.api import DeleteRoomRequest

    try:
        api = await get_livekit_api()
        await api.room.delete_room(DeleteRoomRequest(room=room))
    except Exception:
        # Twirp returns not_found when the room is already gone: nothing to do.
        logger.debug("delete_room(%s) failed or room absent", room, exc_info=True)


async def _delete_room_later(room: str, session_id: str) -> None:
    """Tear the room down after the goodbye, unless the session was restarted."""
    await asyncio.sleep(_END_GRACE_S)
    pool = await get_pool()
    async with pool.acquire() as conn:
        session = await studio.get_session(conn, session_id)
    if session is not None and session.status == "live":
        return
    await _delete_room(room)


_background_tasks: set[asyncio.Task[None]] = set()


def _spawn(coro: Coroutine[None, None, None]) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# -------------------------------------------------------------------- models


@router.get("/voices", response_model=VoicesResponse)
async def list_voices() -> VoicesResponse:
    return VoicesResponse(voices=studio.VOICES)


@router.get("/models", response_model=ModelsResponse)
async def list_models(conn: DbConn) -> ModelsResponse:
    return ModelsResponse(models=[_model_out(m) for m in await studio.list_models(conn)])


@router.post("/models", response_model=ModelOut, status_code=status.HTTP_201_CREATED)
async def create_model(
    conn: DbConn,
    name: Annotated[str, Form(min_length=1, max_length=80)],
    image: Annotated[UploadFile, File()],
    intro: Annotated[str, Form(max_length=2000)] = "",
    personality: Annotated[str, Form(max_length=1000)] = "",
    language: Annotated[studio.Language, Form()] = "vi",
    voice: Annotated[str, Form()] = "",
) -> ModelOut:
    try:
        image_url = studio_assets.save_image_upload(await image.read())
    except studio_assets.UploadRejectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    model = await studio.create_model(
        conn,
        name=name.strip(),
        intro=intro.strip(),
        personality=personality.strip(),
        language=language,
        voice=studio.resolve_voice(voice, language),
        image_url=image_url,
    )
    # Kick off the motion avatar right away so it is ready by showtime. A
    # missing builder is not an error here: the model still works with the
    # still-image fallback and the build can be started later.
    try:
        studio_assets.start_avatar_build(model.id, model.image_url)
    except (studio_assets.AvatarBuilderUnavailableError, studio_assets.UploadRejectedError):
        logger.info("avatar build not started for model %s", model.id)
    return _model_out(model)


@router.patch("/models/{model_id}", response_model=ModelOut)
async def patch_model(model_id: str, body: ModelPatch, conn: DbConn) -> ModelOut:
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if "voice" in fields or "language" in fields:
        current = await studio.get_model(conn, model_id)
        if current is None:
            raise _not_found("người mẫu")
        language = fields.get("language", current.language)
        # Switching language without picking a voice moves to that language's
        # default voice: a Vietnamese script read by an English voice is unusable.
        keep = current.voice if language == current.language else None
        fields["voice"] = studio.resolve_voice(fields.get("voice", keep), language)
    model = await studio.update_model(conn, model_id, fields)
    if model is None:
        raise _not_found("người mẫu")
    return _model_out(model)


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(model_id: str, conn: DbConn) -> Response:
    if await studio.model_in_live_session(conn, model_id):
        raise _conflict("Người mẫu đang trong một phiên livestream.")
    model = await studio.get_model(conn, model_id)
    try:
        deleted = await studio.delete_model(conn, model_id)
    except asyncpg.ForeignKeyViolationError as exc:
        raise _conflict("Người mẫu đang được dùng ở nơi khác nên không thể xóa.") from exc
    if not deleted or model is None:
        raise _not_found("người mẫu")
    studio_assets.remove_model_files(model.id, model.image_url)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/models/{model_id}/avatar", response_model=ModelOut)
async def build_model_avatar(model_id: str, conn: DbConn) -> ModelOut:
    model = await studio.get_model(conn, model_id)
    if model is None:
        raise _not_found("người mẫu")
    try:
        studio_assets.start_avatar_build(model.id, model.image_url)
    except studio_assets.AvatarBuilderUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bộ dựng avatar chuyển động chưa được cài trên server.",
        ) from exc
    except studio_assets.UploadRejectedError as exc:
        raise _conflict(str(exc)) from exc
    return _model_out(model)


@router.post("/uploads", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_image(file: Annotated[UploadFile, File()]) -> UploadResponse:
    try:
        return UploadResponse(url=studio_assets.save_image_upload(await file.read()))
    except studio_assets.UploadRejectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


# ------------------------------------------------------------- review videos


@router.get("/videos", response_model=VideosResponse)
async def list_videos() -> VideosResponse:
    return VideosResponse(videos=studio_videos.list_videos())


@router.post("/videos", response_model=studio_videos.VideoJob, status_code=status.HTTP_201_CREATED)
async def create_video(body: VideoCreate, conn: DbConn) -> studio_videos.VideoJob:
    """Render a TikTok-style review of a product, hosted by a model (10 to 20 min)."""
    model = await studio.get_model(conn, body.model_id)
    if model is None:
        raise _not_found("người mẫu")
    product = await studio.get_product(conn, body.product_id)
    if product is None:
        raise _not_found("sản phẩm")
    try:
        return studio_videos.start_video(model, product)
    except studio_videos.VideoBusyError as exc:
        raise _conflict(str(exc)) from exc
    except studio_videos.VideoUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get("/videos/{video_id}", response_model=studio_videos.VideoJob)
async def get_video(video_id: str) -> studio_videos.VideoJob:
    job = studio_videos.get_video(video_id)
    if job is None:
        raise _not_found("video")
    return job


@router.get("/videos/{video_id}/file", response_class=FileResponse)
async def download_video(video_id: str) -> FileResponse:
    path = studio_videos.video_file(video_id)
    if path is None:
        raise _not_found("video")
    return FileResponse(path, media_type="video/mp4", filename=f"review-{video_id[:8]}.mp4")


@router.get("/videos/{video_id}/thumb", response_class=FileResponse)
async def video_thumbnail(video_id: str) -> FileResponse:
    path = studio_videos.thumb_file(video_id)
    if path is None:
        raise _not_found("ảnh xem trước")
    return FileResponse(path, media_type="image/jpeg")


@router.delete("/videos/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_video(video_id: str) -> Response:
    try:
        deleted = studio_videos.delete_video(video_id)
    except studio_videos.VideoBusyError as exc:
        raise _conflict(str(exc)) from exc
    if not deleted:
        raise _not_found("video")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ sessions


@router.get("/sessions", response_model=SessionsResponse)
async def list_sessions(conn: DbConn) -> SessionsResponse:
    sessions = await studio.list_sessions(conn)
    return SessionsResponse(sessions=[await _session_out(conn, s) for s in sessions])


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(body: SessionCreate, conn: DbConn) -> SessionOut:
    if body.model_id and await studio.get_model(conn, body.model_id) is None:
        raise _not_found("người mẫu")
    session = await studio.create_session(conn, title=body.title.strip(), model_id=body.model_id)
    return await _session_out(conn, session)


@router.get("/sessions/{session_id}", response_model=SessionOut)
async def get_session(session_id: str, conn: DbConn) -> SessionOut:
    return await _session_out(conn, await _require_session(conn, session_id))


@router.patch("/sessions/{session_id}", response_model=SessionOut)
async def patch_session(session_id: str, body: SessionPatch, conn: DbConn) -> SessionOut:
    current = await _require_session(conn, session_id)
    fields = body.model_dump(exclude_unset=True)
    if fields.get("title") is None:
        fields.pop("title", None)
    if fields.get("notes") is None:
        fields.pop("notes", None)
    if "model_id" in fields:
        if current.status == "live" and fields["model_id"] != current.model_id:
            raise _conflict("Không thể đổi người mẫu khi đang livestream.")
        if fields["model_id"] and await studio.get_model(conn, fields["model_id"]) is None:
            raise _not_found("người mẫu")
    session = await studio.update_session(conn, session_id, fields)
    if session is None:
        raise _not_found("phiên livestream")
    return await _session_out(conn, session)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: str, conn: DbConn) -> Response:
    session = await _require_session(conn, session_id)
    if session.status == "live":
        raise _conflict("Hãy kết thúc livestream trước khi xóa phiên.")
    await studio.delete_session(conn, session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sessions/{session_id}/public", response_model=PublicSession)
async def get_public_session(session_id: str, conn: DbConn) -> PublicSession:
    out = await _session_out(conn, await _require_session(conn, session_id))
    return PublicSession(
        id=out.id,
        title=out.title,
        status=out.status,
        model=PublicModel(name=out.model.name, image_url=out.model.image_url)
        if out.model
        else None,
        live=out.live,
        products=[
            PublicProduct(**p.model_dump(include=set(PublicProduct.model_fields)))
            for p in out.products
        ],
    )


# ------------------------------------------------------------------ products


@router.post(
    "/sessions/{session_id}/products",
    response_model=studio.ProductRow,
    status_code=status.HTTP_201_CREATED,
)
async def create_product(session_id: str, body: ProductCreate, conn: DbConn) -> studio.ProductRow:
    await _require_session(conn, session_id)
    fields = body.model_dump()
    fields["name"] = body.name.strip()
    return await studio.create_product(conn, session_id, fields)


@router.patch("/products/{product_id}", response_model=studio.ProductRow)
async def patch_product(product_id: str, body: ProductPatch, conn: DbConn) -> studio.ProductRow:
    fields = body.model_dump(exclude_unset=True)
    # Text columns are NOT NULL: a null clears to empty. Price and image may be null.
    for key in ("description", "highlights", "script"):
        if key in fields and fields[key] is None:
            fields[key] = ""
    for key in ("name", "currency"):
        if key in fields and fields[key] is None:
            fields.pop(key)
    product = await studio.update_product(conn, product_id, fields)
    if product is None:
        raise _not_found("sản phẩm")
    return product


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(product_id: str, conn: DbConn) -> Response:
    if not await studio.delete_product(conn, product_id):
        raise _not_found("sản phẩm")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/sessions/{session_id}/products/order", response_model=ProductsResponse)
async def reorder_products(session_id: str, body: ProductOrder, conn: DbConn) -> ProductsResponse:
    await _require_session(conn, session_id)
    products = await studio.reorder_products(conn, session_id, body.product_ids)
    if products is None:
        raise _conflict("Danh sách sản phẩm đã thay đổi. Hãy tải lại rồi sắp xếp lại.")
    return ProductsResponse(products=products)


@router.post("/products/{product_id}/script", response_model=studio.ProductRow)
async def generate_script(product_id: str, conn: DbConn) -> studio.ProductRow:
    product = await studio.get_product(conn, product_id)
    if product is None:
        raise _not_found("sản phẩm")
    session = await studio.get_session(conn, product.session_id)
    model = await studio.get_model(conn, session.model_id) if session and session.model_id else None
    try:
        script = await generate_product_script(product, model.language if model else "vi")
    except ScriptGenerationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    updated = await studio.update_product(conn, product_id, {"script": script})
    if updated is None:
        raise _not_found("sản phẩm")
    return updated


# ------------------------------------------------------------------- on air


@router.post("/sessions/{session_id}/start", response_model=StartResponse)
async def start_session(session_id: str, conn: DbConn) -> StartResponse:
    session = await _expire_if_agent_gone(conn, await _require_session(conn, session_id))
    if session.status == "live":
        raise _conflict("Phiên livestream này đang phát.")
    if not session.model_id or await studio.get_model(conn, session.model_id) is None:
        raise _conflict("Hãy chọn người mẫu AI trước khi bắt đầu livestream.")
    if not settings.GROQ_API_KEY:
        raise _conflict("Server chưa cấu hình GROQ_API_KEY nên người mẫu không thể nói.")

    room = _room_name(session.id)
    api = await get_livekit_api()
    # A leftover room from a previous run may still hold a stale agent.
    await _delete_room(room)
    await studio.mark_session_live(conn, session.id, room)
    metadata = DispatchMetadata(
        persona_id=session.model_id,
        script_id="live",
        pitch_window_ms=0,
        session_id=session.id,
        user_id="studio-host",
        live_session_id=session.id,
    )
    try:
        from livekit.api import CreateRoomRequest

        await api.room.create_room(CreateRoomRequest(name=room, empty_timeout=600))
        joined = await _dispatch_until_joined(room, metadata)
    except Exception as exc:
        logger.exception("live start failed for session %s", session.id)
        await studio.mark_session_ended(conn, session.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Không kết nối được máy chủ livestream (LiveKit). Kiểm tra LiveKit đang chạy.",
        ) from exc
    if not joined:
        await studio.mark_session_ended(conn, session.id)
        await _delete_room(room)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Người mẫu AI không vào được phòng live. Hãy kiểm tra agent worker đang chạy "
                "(khởi động lại worker nếu LiveKit vừa được khởi động lại) rồi thử lại."
            ),
        )

    refreshed = await _require_session(conn, session.id)
    join = _join_info(room, "host", None)
    return StartResponse(**join.model_dump(), session=await _session_out(conn, refreshed))


@router.post("/sessions/{session_id}/end", response_model=SessionOut)
async def end_session(session_id: str, conn: DbConn) -> SessionOut:
    session = await _require_session(conn, session_id)
    if session.status == "live" and session.room_name:
        try:
            await send_agent_control(
                await get_livekit_api(), room=session.room_name, payload={"action": "end"}
            )
        except Exception:
            logger.warning("end signal not delivered; the room will be deleted anyway")
        await studio.mark_session_ended(conn, session.id)
        _spawn(_delete_room_later(session.room_name, session.id))
    return await _session_out(conn, await _require_session(conn, session_id))


@router.post("/sessions/{session_id}/control", response_model=OkResponse)
async def control_session(session_id: str, body: ControlRequest, conn: DbConn) -> OkResponse:
    session = await _require_live(conn, session_id)
    payload: dict[str, str] = {"action": body.action}
    if body.action == "present":
        product = await studio.get_product(conn, body.product_id or "")
        if product is None or product.session_id != session.id:
            raise _not_found("sản phẩm")
        payload["product_id"] = product.id
    if body.action == "say":
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Hãy nhập nội dung muốn người mẫu nói.",
            )
        payload["text"] = text
    try:
        await send_agent_control(
            await get_livekit_api(), room=session.room_name or "", payload=payload
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Không gửi được lệnh tới người mẫu. Kiểm tra kết nối LiveKit.",
        ) from exc
    return OkResponse()


@router.get("/sessions/{session_id}/token", response_model=JoinInfo)
async def get_token(
    session_id: str,
    conn: DbConn,
    role: Annotated[Literal["host", "viewer"], Query()] = "viewer",
    name: Annotated[str | None, Query(max_length=40)] = None,
) -> JoinInfo:
    session = await _require_live(conn, session_id)
    return _join_info(session.room_name or "", role, name)
