"""Process-wide LiveKit API client lifecycle.

Owns the lazy `LiveKitAPI` singleton previously defined in
``soshop.api.routes.feed``. The client wraps an aiohttp session and
is reused across requests via FastAPI's dependency system. The companion
``close_livekit_api`` coroutine is wired into the FastAPI lifespan
shutdown branch so the underlying aiohttp session is released cleanly.

Importing ``livekit.api`` is deferred to first use so the module remains
importable in unit tests that mock the LiveKit client out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends

from soshop_server.core.config import settings

if TYPE_CHECKING:
    from livekit.api import LiveKitAPI

_LK_API: LiveKitAPI | None = None


async def get_livekit_api() -> LiveKitAPI:
    """Return a process-wide `LiveKitAPI` client built from settings.

    The client owns an aiohttp session; we keep one per process and let
    FastAPI's lifespan reuse it across requests. Must be async because
    `LiveKitAPI.__init__` constructs an `aiohttp.ClientSession`, which
    requires a running event loop — running this in a threadpool (the
    default for sync FastAPI dependencies) raises ``RuntimeError``. A
    plain double-check is sufficient: FastAPI runs a single event loop
    per worker, so concurrent requests cannot race the assignment.
    """
    global _LK_API
    if _LK_API is None:
        from livekit.api import LiveKitAPI

        _LK_API = LiveKitAPI(
            url=settings.LIVEKIT_URL,
            api_key=settings.LIVEKIT_API_KEY,
            api_secret=settings.LIVEKIT_API_SECRET,
        )
    return _LK_API


async def close_livekit_api() -> None:
    """Close the cached LiveKitAPI client. Idempotent.

    Wired into the FastAPI lifespan shutdown branch so the underlying
    aiohttp session is released cleanly — prevents `Unclosed client
    session` warnings during pytest teardown and bounded FD use during
    rolling deploys.
    """
    global _LK_API
    if _LK_API is not None:
        await _LK_API.aclose()
        _LK_API = None


LkApi = Annotated["LiveKitAPI", Depends(get_livekit_api)]
