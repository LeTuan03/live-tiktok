"""Product catalog and Google Flow promo videos.

Flow: ``GET /catalog/products`` -> pick one -> ``POST /promo/videos`` writes
the AI script (a draft, editable, no credits spent) -> ``POST
/promo/videos/{id}/render`` has Google Flow render every shot in the user's
own Chrome through the SoShop Flow Bridge extension, then stitches the
video -> ``GET /promo/videos/{id}/file``.

Jobs live in Postgres (``soshop.promo.jobs``) and their files in the media
store: the file routes serve local files directly and redirect to the CDN
when the store is an S3 bucket.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response, WebSocket, status
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import Field

from soshop.promo import jobs, runner
from soshop.promo.bridge import BridgeError, bridge
from soshop.promo.flow import FLOW_MODELS, FlowModel, credit_ceiling, find_model
from soshop.promo.script import (
    MAX_SHOTS,
    MIN_SHOTS,
    PromoHost,
    PromoProduct,
    PromoScript,
    ScriptError,
    generate_script,
    validate_edit,
)
from soshop.services import broadcasts, catalog, studio
from soshop_server import storage
from soshop_server.api.deps import DbConn, StrictModel
from soshop_server.core.config import settings

logger = logging.getLogger("soshop.api.promo")

router = APIRouter(tags=["promo"])

# -------------------------------------------------------------------- shapes


class ProductsResponse(StrictModel):
    products: list[catalog.CatalogProduct]


class ProductCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    brand: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=120)
    price: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    description: str | None = Field(default=None, max_length=4000)
    highlights: list[Annotated[str, Field(max_length=300)]] | None = Field(
        default=None, max_length=12
    )
    usage: str | None = Field(default=None, max_length=1000)
    talking_points: str | None = Field(default=None, max_length=2000)
    image_url: str | None = Field(default=None, max_length=2000)


class ProductPatch(ProductCreate):
    name: str | None = Field(default=None, min_length=1, max_length=200)  # type: ignore[assignment]


class FlowStatus(StrictModel):
    connected: bool
    extension_version: str | None
    flow_tabs: int | None
    running_job_id: str | None
    default_model: str
    models: list[FlowModel]


class VideoCreate(StrictModel):
    product_id: str
    model_id: str
    shots: int = Field(default=3, ge=MIN_SHOTS, le=MAX_SHOTS)
    language: Literal["vi", "en"] | None = None
    model: str | None = Field(default=None, max_length=60)


class ScriptUpdate(StrictModel):
    script: PromoScript


class VideoOut(jobs.PromoJob):
    running: bool
    has_video: bool
    has_thumb: bool
    credits_estimate: int


class VideosResponse(StrictModel):
    videos: list[VideoOut]


# ------------------------------------------------------------------- helpers


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Không tìm thấy {what}.")


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)


def _out(job: jobs.PromoJob) -> VideoOut:
    clips = {shot.index for shot in job.shots if shot.clip_key}
    remaining = sum(1 for i in range(job.shot_count) if i not in clips)
    return VideoOut(
        **job.model_dump(),
        running=runner.is_running(job.id),
        has_video=job.status == "ready" and job.video_key is not None,
        has_thumb=job.thumb_key is not None,
        credits_estimate=remaining * credit_ceiling(job.flow_model),
    )


async def _require_job(conn: DbConn, job_id: str) -> jobs.PromoJob:
    job = await jobs.load(conn, job_id)
    if job is None:
        raise _not_found("video")
    return job


def _require_idle(job: jobs.PromoJob) -> None:
    if runner.is_running(job.id) or job.busy:
        raise _conflict("Video đang được tạo, hãy đợi hoặc hủy trước.")


def _promo_product(product: catalog.CatalogProduct) -> PromoProduct:
    return PromoProduct(
        id=product.id,
        name=product.name,
        brand=product.brand,
        category=product.category,
        price=product.price,
        currency=product.currency,
        description=product.description,
        highlights=product.highlights,
        usage=product.usage,
        talking_points=product.talking_points,
        image_url=product.image_url,
    )


def _promo_host(model: studio.ModelRow) -> PromoHost:
    return PromoHost(
        id=model.id,
        name=model.name,
        language=model.language,
        image_url=model.image_url,
        personality=model.personality,
    )


def _file(key: str | None, media_type: str, missing: str) -> Response:
    """The stored file: served from local disk, or a redirect to the bucket's CDN URL."""
    if not key:
        raise _not_found(missing)
    store = storage.get_store()
    path = store.local_path(key)
    if path is not None:
        return FileResponse(path, media_type=media_type)
    if store.kind == "local":
        raise _not_found(missing)
    return RedirectResponse(store.url(key), status_code=status.HTTP_307_TEMPORARY_REDIRECT)


# ------------------------------------------------------------------- catalog


@router.get("/catalog/products", response_model=ProductsResponse)
async def list_catalog(conn: DbConn) -> ProductsResponse:
    return ProductsResponse(products=await catalog.list_products(conn))


@router.get("/catalog/products/{product_id}", response_model=catalog.CatalogProduct)
async def get_catalog_product(product_id: str, conn: DbConn) -> catalog.CatalogProduct:
    product = await catalog.get_product(conn, product_id)
    if product is None:
        raise _not_found("sản phẩm")
    return product


@router.post(
    "/catalog/products",
    response_model=catalog.CatalogProduct,
    status_code=status.HTTP_201_CREATED,
)
async def create_catalog_product(body: ProductCreate, conn: DbConn) -> catalog.CatalogProduct:
    return await catalog.create_product(conn, body.model_dump())


@router.patch("/catalog/products/{product_id}", response_model=catalog.CatalogProduct)
async def patch_catalog_product(
    product_id: str, body: ProductPatch, conn: DbConn
) -> catalog.CatalogProduct:
    product = await catalog.update_product(conn, product_id, body.model_dump(exclude_unset=True))
    if product is None:
        raise _not_found("sản phẩm")
    return product


@router.delete("/catalog/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_catalog_product(product_id: str, conn: DbConn) -> Response:
    if not await catalog.archive_product(conn, product_id):
        raise _not_found("sản phẩm")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------- flow


@router.websocket("/promo/flow/bridge")
async def flow_bridge(websocket: WebSocket) -> None:
    """The SoShop Flow Bridge extension dials in here.

    Only an extension may connect: browsers stamp the ``Origin`` header and a
    web page cannot claim ``chrome-extension://``, so no site the user visits
    can pose as the bridge.
    """
    if not websocket.headers.get("origin", "").startswith("chrome-extension://"):
        await websocket.close(code=1008)
        return
    await bridge.serve(websocket)


@router.get("/promo/flow/status", response_model=FlowStatus)
async def flow_status() -> FlowStatus:
    tabs: int | None = None
    if bridge.connected:
        try:
            tabs = len(await bridge.call("tabs.list", timeout=5.0))
        except BridgeError:
            tabs = None
    return FlowStatus(
        connected=bridge.connected,
        extension_version=(bridge.hello or {}).get("version"),
        flow_tabs=tabs,
        running_job_id=runner.running_job_id(),
        default_model=settings.PROMO_FLOW_MODEL,
        models=FLOW_MODELS,
    )


# -------------------------------------------------------------------- videos


@router.get("/promo/videos", response_model=VideosResponse)
async def list_videos(
    conn: DbConn,
    product_id: Annotated[str | None, Query()] = None,
) -> VideosResponse:
    return VideosResponse(videos=[_out(job) for job in await jobs.list_all(conn, product_id)])


@router.post("/promo/videos", response_model=VideoOut, status_code=status.HTTP_201_CREATED)
async def create_video(body: VideoCreate, conn: DbConn) -> VideoOut:
    """Create a draft: the AI writes the script; nothing is sent to Flow yet."""
    product = await catalog.get_product(conn, body.product_id)
    if product is None:
        raise _not_found("sản phẩm")
    if product.image_url is None:
        raise _conflict("Sản phẩm chưa có ảnh. Hãy tải ảnh sản phẩm lên trước.")
    model = await studio.get_model(conn, body.model_id)
    if model is None:
        raise _not_found("người mẫu")
    if not model.image_url:
        raise _conflict("Người mẫu chưa có ảnh chân dung.")
    flow_model = find_model(body.model or settings.PROMO_FLOW_MODEL)
    if flow_model is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Model Flow không hợp lệ.",
        )
    host = _promo_host(model)
    language = body.language or host.language
    promo_product = _promo_product(product)
    try:
        script = await generate_script(promo_product, host, body.shots, language)
    except ScriptError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    job = jobs.new_job(
        product=promo_product,
        host=host,
        language=language,
        shot_count=body.shots,
        flow_model=flow_model.label,
    )
    job.script = script
    job.shots = [jobs.ShotState(index=i) for i in range(len(script.shots))]
    await jobs.insert(conn, job)
    return _out(job)


@router.get("/promo/videos/{video_id}", response_model=VideoOut)
async def get_video(video_id: str, conn: DbConn) -> VideoOut:
    return _out(await _require_job(conn, video_id))


@router.post("/promo/videos/{video_id}/script", response_model=VideoOut)
async def regenerate_script(video_id: str, conn: DbConn) -> VideoOut:
    job = await _require_job(conn, video_id)
    _require_idle(job)
    try:
        script = await generate_script(job.product, job.host, job.shot_count, job.language)
    except ScriptError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    await _replace_script(conn, job, script)
    return _out(job)


@router.put("/promo/videos/{video_id}/script", response_model=VideoOut)
async def update_script(video_id: str, body: ScriptUpdate, conn: DbConn) -> VideoOut:
    job = await _require_job(conn, video_id)
    _require_idle(job)
    if len(body.script.shots) != job.shot_count:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Kịch bản phải có đúng {job.shot_count} cảnh.",
        )
    try:
        script = validate_edit(body.script, job.language)
    except ScriptError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    await _replace_script(conn, job, script)
    return _out(job)


async def _replace_script(conn: DbConn, job: jobs.PromoJob, script: PromoScript) -> None:
    """Swap the script; clips of shots whose staging changed must be re-rendered."""
    old = job.script
    previous = {s.index: s for s in job.shots}
    states: list[jobs.ShotState] = []
    dropped: list[str | None] = []
    for index, shot in enumerate(script.shots):
        unchanged = (
            old is not None
            and index < len(old.shots)
            and old.shots[index] == shot
            and old.setting == script.setting
            and old.product_look == script.product_look
        )
        kept = previous.get(index)
        if unchanged and kept is not None and kept.clip_key:
            states.append(kept)
            continue
        if kept is not None:
            dropped.append(kept.drop_clip())
        states.append(jobs.ShotState(index=index))
    dropped += [s.clip_key for i, s in previous.items() if i >= len(script.shots)]
    dropped.append(job.video_key)
    job.script = script
    job.shots = states
    job.status = "draft"
    job.error = None
    job.video_key = None
    await jobs.save(conn, job)
    await asyncio.to_thread(jobs.delete_keys, dropped)


@router.post("/promo/videos/{video_id}/render", response_model=VideoOut)
async def render_video(video_id: str, conn: DbConn) -> VideoOut:
    """Send the missing shots to Google Flow, then stitch the video."""
    job = await _require_job(conn, video_id)
    _require_idle(job)
    try:
        await runner.start(conn, job.id)
    except runner.RunnerBusyError as exc:
        raise _conflict(str(exc)) from exc
    except BridgeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise _conflict(str(exc)) from exc
    return _out(await _require_job(conn, video_id))


@router.post("/promo/videos/{video_id}/shots/{index}/regenerate", response_model=VideoOut)
async def regenerate_shot(video_id: str, index: int, conn: DbConn) -> VideoOut:
    """Drop one shot's clip and render it again (Veo gives a new take)."""
    job = await _require_job(conn, video_id)
    _require_idle(job)
    if not 0 <= index < job.shot_count:
        raise _not_found("cảnh")
    shot = next((s for s in job.shots if s.index == index), None)
    dropped = shot.drop_clip() if shot is not None else None
    await jobs.save(conn, job)
    await asyncio.to_thread(jobs.delete_keys, [dropped])
    return await render_video(video_id, conn)


@router.post("/promo/videos/{video_id}/cancel", response_model=VideoOut)
async def cancel_video(video_id: str, conn: DbConn) -> VideoOut:
    job = await _require_job(conn, video_id)
    if not await runner.cancel(job.id) and job.busy:
        job.status, job.error, job.stage = "failed", "Đã hủy.", ""
        await jobs.save(conn, job)
    return _out(await _require_job(conn, video_id))


@router.delete("/promo/videos/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_video(video_id: str, conn: DbConn) -> Response:
    job = await _require_job(conn, video_id)
    _require_idle(job)
    used_by = await broadcasts.promo_video_usage(conn, job.id)
    if used_by:
        raise _conflict(
            "Video đang nằm trong phiên livestream: "
            + ", ".join(used_by)
            + ". Hãy gỡ video khỏi phiên trước khi xóa."
        )
    await jobs.delete(conn, job.id)
    try:
        await asyncio.to_thread(jobs.remove_files, job.id)
    except Exception:
        logger.warning("could not delete the files of promo video %s", job.id, exc_info=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/promo/videos/{video_id}/file", response_class=FileResponse)
async def video_file(video_id: str, conn: DbConn) -> Response:
    job = await _require_job(conn, video_id)
    return _file(job.video_key, "video/mp4", "video")


@router.get("/promo/videos/{video_id}/thumb", response_class=FileResponse)
async def video_thumb(video_id: str, conn: DbConn) -> Response:
    job = await _require_job(conn, video_id)
    return _file(job.thumb_key, "image/jpeg", "ảnh xem trước")


@router.get("/promo/videos/{video_id}/captions", response_class=FileResponse)
async def video_captions(video_id: str, conn: DbConn) -> Response:
    """Served by the API itself on any storage: a ``<track>`` must be same-origin or CORS."""
    job = await _require_job(conn, video_id)
    if not job.captions_key:
        raise _not_found("phụ đề")
    data = await asyncio.to_thread(storage.get_store().get_bytes, job.captions_key)
    if data is None:
        raise _not_found("phụ đề")
    return Response(content=data, media_type="text/vtt")


@router.get("/promo/videos/{video_id}/shots/{index}/clip", response_class=FileResponse)
async def shot_clip(video_id: str, index: int, conn: DbConn) -> Response:
    job = await _require_job(conn, video_id)
    shot = next((s for s in job.shots if s.index == index), None)
    return _file(shot.clip_key if shot else None, "video/mp4", "clip")


@router.get("/promo/videos/{video_id}/debug", response_class=FileResponse)
async def debug_screenshot(video_id: str, conn: DbConn) -> Response:
    """The Flow tab as it looked when the last step failed."""
    job = await _require_job(conn, video_id)
    return _file(job.debug_key, "image/png", "ảnh chụp lỗi")
