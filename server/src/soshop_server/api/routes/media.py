"""``/media/<key>``: media files by storage key (see :mod:`soshop_server.storage`).

Local storage serves the file with HTTP range support (browsers need it to
seek a video). Only the key folders in ``storage.PREFIXES`` are reachable, so
avatar assets, model weights and scratch files under ``DATA_DIR`` stay private.

S3 storage answers with a redirect to the public bucket URL, so a URL saved
while the app ran on local storage keeps working after the switch.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, FastAPI, HTTPException, status
from fastapi.responses import RedirectResponse
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from soshop_server import storage


class MediaFiles(StaticFiles):
    """``StaticFiles`` limited to valid media keys."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        key = path.replace(os.sep, "/")
        if not storage.valid_key(key):
            return Response(status_code=status.HTTP_404_NOT_FOUND)
        response = await super().get_response(path, scope)
        if response.status_code == status.HTTP_200_OK:
            response.headers.setdefault("Cache-Control", storage.CACHE_CONTROL)
        return response


redirect_router = APIRouter()


@redirect_router.get("/media/{key:path}", include_in_schema=False)
async def media_redirect(key: str) -> RedirectResponse:
    if not storage.valid_key(key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return RedirectResponse(storage.get_store().url(key), status_code=307)


def install_media(app: FastAPI) -> None:
    store = storage.get_store()
    if store.kind == "local":
        root = storage.data_root()
        root.mkdir(parents=True, exist_ok=True)
        app.mount("/media", MediaFiles(directory=root), name="media")
    else:
        app.include_router(redirect_router)
