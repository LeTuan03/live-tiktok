"""Livestream sessions played from pre-rendered product videos.

Flow: ``POST /broadcasts`` -> add finished promo videos (or uploaded files)
with ``POST /broadcasts/{id}/items``, order them, set each one's time on air
and the loop mode -> ``POST /broadcasts/{id}/control {"action": "start"}``.
Every response carries ``playback`` (status, playhead, server time); players
derive the on-air position from it locally and follow changes through
``GET /broadcasts/{id}/events``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import Field

from soshop.broadcast import media
from soshop.broadcast.control import Action
from soshop.broadcast.hub import hub
from soshop.broadcast.timeline import LoopMode
from soshop.services import broadcasts
from soshop.services.broadcasts import Broadcast, BroadcastSummary, ItemRole, ItemSource
from soshop.services.studio import to_uuid
from soshop_server.api.deps import DbConn, StrictModel
from soshop_server.db.pool import get_pool

router = APIRouter(prefix="/broadcasts", tags=["broadcasts"])

_STREAM_SECONDS = 25.0
"""An event stream ends after this; EventSource reconnects on its own."""
_HEARTBEAT_SECONDS = 10.0

# -------------------------------------------------------------------- shapes


class BroadcastsResponse(StrictModel):
    broadcasts: list[BroadcastSummary]


class BroadcastCreate(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)
    loop_mode: LoopMode | None = None
    loop_count: int | None = Field(default=None, ge=1, le=1000)


class BroadcastPatch(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)
    loop_mode: LoopMode | None = None
    loop_count: int | None = Field(default=None, ge=1, le=1000)


class ItemIn(StrictModel):
    role: ItemRole = "product"
    source: ItemSource
    source_ref: str = Field(min_length=1, max_length=300)
    duration_s: int | None = Field(default=None, ge=5, le=3600)
    label: str = Field(default="", max_length=120)
    product_id: str | None = Field(default=None, max_length=64)


class ItemsAdd(StrictModel):
    items: list[ItemIn] = Field(min_length=1, max_length=broadcasts.MAX_PRODUCT_ITEMS)


class ItemPatch(StrictModel):
    duration_s: int | None = Field(default=None, ge=5, le=3600)
    """Seconds on air; send ``null`` to play the video once."""
    label: str | None = Field(default=None, max_length=120)


class ItemsOrder(StrictModel):
    item_ids: list[str] = Field(max_length=broadcasts.MAX_PRODUCT_ITEMS)


class ControlIn(StrictModel):
    action: Action
    item_id: str | None = Field(default=None, max_length=64)
    """For ``jump``, and optionally ``start``: the item to play."""


class UploadedVideo(StrictModel):
    url: str
    thumb_url: str | None
    duration_s: float
    width: int
    height: int
    name: str


# ------------------------------------------------------------------- helpers


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except broadcasts.BroadcastError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy phiên livestream."
    )


async def _snapshot(broadcast_id: str) -> str | None:
    """The broadcast as JSON, on a pooled connection held only for the read."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        broadcast = await broadcasts.get_broadcast(conn, broadcast_id)
    return broadcast.model_dump_json() if broadcast is not None else None


# --------------------------------------------------------------------- crud


@router.get("", response_model=BroadcastsResponse)
async def list_broadcasts(conn: DbConn) -> BroadcastsResponse:
    return BroadcastsResponse(broadcasts=await broadcasts.list_broadcasts(conn))


@router.post("", response_model=Broadcast, status_code=status.HTTP_201_CREATED)
async def create_broadcast(body: BroadcastCreate, conn: DbConn) -> Broadcast:
    with _errors():
        return await broadcasts.create_broadcast(conn, body.model_dump())


@router.post("/uploads", response_model=UploadedVideo, status_code=status.HTTP_201_CREATED)
async def upload_video(file: Annotated[UploadFile, File()]) -> UploadedVideo:
    """Store an intro, outro or product video made elsewhere (MP4 H.264, up to 500 MB)."""
    try:
        saved = await asyncio.to_thread(media.save_upload, file.file, file.filename or "")
    except media.UploadRejectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except media.AssembleError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return UploadedVideo(**asdict(saved))


@router.get("/{broadcast_id}", response_model=Broadcast)
async def get_broadcast(broadcast_id: str, conn: DbConn) -> Broadcast:
    broadcast = await broadcasts.get_broadcast(conn, broadcast_id)
    if broadcast is None:
        raise _not_found()
    return broadcast


@router.patch("/{broadcast_id}", response_model=Broadcast)
async def update_broadcast(broadcast_id: str, body: BroadcastPatch, conn: DbConn) -> Broadcast:
    with _errors():
        return await broadcasts.update_broadcast(
            conn, broadcast_id, body.model_dump(exclude_unset=True)
        )


@router.delete("/{broadcast_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_broadcast(broadcast_id: str, conn: DbConn) -> Response:
    with _errors():
        await broadcasts.delete_broadcast(conn, broadcast_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -------------------------------------------------------------------- items


@router.post("/{broadcast_id}/items", response_model=Broadcast)
async def add_items(broadcast_id: str, body: ItemsAdd, conn: DbConn) -> Broadcast:
    """Append product videos. An intro or outro replaces the current one."""
    specs = [broadcasts.ItemSpec(**item.model_dump()) for item in body.items]
    with _errors():
        return await broadcasts.add_items(conn, broadcast_id, specs)


@router.put("/{broadcast_id}/items/order", response_model=Broadcast)
async def reorder_items(broadcast_id: str, body: ItemsOrder, conn: DbConn) -> Broadcast:
    with _errors():
        return await broadcasts.reorder_items(conn, broadcast_id, body.item_ids)


@router.patch("/{broadcast_id}/items/{item_id}", response_model=Broadcast)
async def update_item(broadcast_id: str, item_id: str, body: ItemPatch, conn: DbConn) -> Broadcast:
    with _errors():
        return await broadcasts.update_item(
            conn, broadcast_id, item_id, body.model_dump(exclude_unset=True)
        )


@router.delete("/{broadcast_id}/items/{item_id}", response_model=Broadcast)
async def remove_item(broadcast_id: str, item_id: str, conn: DbConn) -> Broadcast:
    with _errors():
        return await broadcasts.remove_item(conn, broadcast_id, item_id)


# ------------------------------------------------------------------ playback


@router.post("/{broadcast_id}/control", response_model=Broadcast)
async def control(broadcast_id: str, body: ControlIn, conn: DbConn) -> Broadcast:
    """start, pause, resume, next, prev, jump (``item_id``), finish (outro, then end), stop."""
    with _errors():
        return await broadcasts.control(conn, broadcast_id, body.action, body.item_id)


@router.get("/{broadcast_id}/events", response_class=StreamingResponse)
async def broadcast_events(broadcast_id: str, request: Request) -> StreamingResponse:
    """Server-sent events: the broadcast now, then again whenever it changes.

    A stream ends after about 25 s and the browser's EventSource reconnects by
    itself, so an open player never holds up a server shutdown for long and a
    change this process was not told about still arrives on the next stream.
    """
    bid = to_uuid(broadcast_id)
    first = await _snapshot(broadcast_id) if bid is not None else None
    if bid is None or first is None:
        raise _not_found()
    key = str(bid)

    async def stream() -> AsyncIterator[str]:
        deadline = time.monotonic() + _STREAM_SECONDS
        snapshot: str | None = first
        yield "retry: 1000\n\n"
        while snapshot is not None:
            yield f"data: {snapshot}\n\n"
            remaining = deadline - time.monotonic()
            if remaining <= 0 or await request.is_disconnected():
                return
            await hub.wait(key, min(_HEARTBEAT_SECONDS, remaining))
            snapshot = await _snapshot(key)
        yield "event: gone\ndata: {}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
