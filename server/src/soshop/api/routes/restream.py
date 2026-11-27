"""Restream settings: where on-air broadcasts are pushed, and per-broadcast switches.

``/stream-destinations`` holds the RTMP ingest points (TikTok Live, Shopee
Live, YouTube Live or any RTMP server): a server URL and a stream key each.
Keys go in and never come back out; responses carry ``key_hint`` instead.

``/broadcasts/{id}/outputs`` lists every destination for one broadcast with
its switch and push state. While the broadcast is live or paused, the API
process pushes it to every destination switched on (``soshop.restream``).
A destination with ``auto_on`` is switched on by itself for the session whose
Start was pressed last.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import Field

from soshop.restream import runner
from soshop.restream.platforms import Platform
from soshop.services import restream
from soshop.services.restream import Destination, Output
from soshop_server.api.deps import DbConn, StrictModel

router = APIRouter(tags=["restream"])


class DestinationsResponse(StrictModel):
    destinations: list[Destination]


class DestinationCreate(StrictModel):
    platform: Platform
    name: str = Field(min_length=1, max_length=80)
    server_url: str = Field(min_length=1, max_length=500)
    stream_key: str = Field(default="", max_length=500)
    auto_on: bool = False
    """Switch it on by itself for each session as Start is pressed."""


class DestinationPatch(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    server_url: str | None = Field(default=None, min_length=1, max_length=500)
    stream_key: str | None = Field(default=None, max_length=500)
    """Leave out to keep the stored key; an empty string removes it."""
    auto_on: bool | None = None


class OutputsResponse(StrictModel):
    available: bool
    """This API process pushes streams (RESTREAM_ENABLED and ffmpeg present)."""
    problem: str | None
    outputs: list[Output]


class OutputSwitch(StrictModel):
    enabled: bool


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except restream.RestreamError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _outputs(outputs: list[Output]) -> OutputsResponse:
    available, problem = runner.availability()
    return OutputsResponse(available=available, problem=problem, outputs=outputs)


# -------------------------------------------------------------- destinations


@router.get("/stream-destinations", response_model=DestinationsResponse)
async def list_destinations(conn: DbConn) -> DestinationsResponse:
    return DestinationsResponse(destinations=await restream.list_destinations(conn))


@router.post(
    "/stream-destinations", response_model=Destination, status_code=status.HTTP_201_CREATED
)
async def create_destination(body: DestinationCreate, conn: DbConn) -> Destination:
    with _errors():
        return await restream.create_destination(conn, body.model_dump())


@router.patch("/stream-destinations/{destination_id}", response_model=Destination)
async def update_destination(
    destination_id: str, body: DestinationPatch, conn: DbConn
) -> Destination:
    with _errors():
        return await restream.update_destination(
            conn, destination_id, body.model_dump(exclude_unset=True)
        )


@router.delete("/stream-destinations/{destination_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_destination(destination_id: str, conn: DbConn) -> Response:
    with _errors():
        await restream.delete_destination(conn, destination_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------- outputs


@router.get("/broadcasts/{broadcast_id}/outputs", response_model=OutputsResponse)
async def list_outputs(broadcast_id: str, conn: DbConn) -> OutputsResponse:
    with _errors():
        return _outputs(await restream.list_outputs(conn, broadcast_id))


@router.put("/broadcasts/{broadcast_id}/outputs/{destination_id}", response_model=OutputsResponse)
async def switch_output(
    broadcast_id: str, destination_id: str, body: OutputSwitch, conn: DbConn
) -> OutputsResponse:
    """Switch a destination on or off for the broadcast (takes effect within a second)."""
    with _errors():
        return _outputs(await restream.set_output(conn, broadcast_id, destination_id, body.enabled))
