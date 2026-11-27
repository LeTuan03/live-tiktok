"""FastAPI lifespan wiring for the SoShop API.

The FastAPI app factory in :mod:`soshop_server.api.app` registers the
:func:`lifespan` context manager defined here. The lifespan is now
on-demand: agents are OFF at boot (the public preview is gated behind
LemonSlice avatar cost). The :class:`AgentSessionManager` singleton in
:mod:`soshop.agent.session` is the only source of truth for the
target channel count; ``POST /agents/session`` flips it on for a
configurable window.

The 5-minute periodic reconciler stays as a safety net: it consults
``AgentSessionManager.reconcile_to_target`` so it picks up the current
target instead of hardcoding ``settings.CHANNEL_COUNT`` — this keeps a
process restart that occurred mid-session from leaking stale agents
once the original expiry passes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("soshop_server.main")

# Interval (seconds) between background safety-net reconciles. The
# session manager's wind-down task is the primary teardown path; this
# loop just rescues sessions that lost their wind-down task (e.g. a
# process restart between the original POST and the expiry) and
# re-reconciles when an agent process dies mid-session.
_CHANNEL_RECONCILE_INTERVAL_S = 300.0


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Construct the AgentSessionManager (agents off by default), run a
    periodic safety-net reconcile, and clean up on shutdown."""
    from soshop.agent.session import get_session_manager

    mgr = get_session_manager()
    # Reconcile to 0 on boot so a previously-running channel set is torn
    # down — agents are off by default until ``POST /agents/session``.
    try:
        await mgr.reconcile_to_target()
    except Exception:
        logger.exception("startup reconcile_to_target failed; continuing")
    reconciler_task = asyncio.create_task(
        _periodic_reconcile_channels(_CHANNEL_RECONCILE_INTERVAL_S),
        name="channel-reconciler",
    )
    # Promo videos render inside this process; any left "rendering" belonged
    # to a previous run and can never finish.
    from soshop.promo.runner import recover_orphans

    try:
        await recover_orphans()
    except Exception:
        logger.exception("promo job recovery failed; continuing")
    # Pushes on-air broadcasts to TikTok Live, Shopee Live and other RTMP
    # destinations (soshop.restream). Off when RESTREAM_ENABLED is false.
    from soshop.restream import runner as restream_runner

    restream_task = restream_runner.start()
    try:
        yield
    finally:
        if restream_task is not None:
            restream_task.cancel()
            try:
                await restream_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("restreamer raised during shutdown")
        reconciler_task.cancel()
        try:
            await reconciler_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("channel reconciler task raised during shutdown")
        try:
            await mgr.stop()
        except Exception:
            logger.exception("agent session stop failed during shutdown")
        await _shutdown_close_livekit_api()


async def _periodic_reconcile_channels(interval_s: float) -> None:
    """Periodically defer to the AgentSessionManager's reconcile target.

    The manager owns the desired channel count (``CHANNEL_COUNT`` while
    a session is active, ``0`` otherwise). Errors are logged and
    swallowed — a single failed reconcile must not kill the loop.
    """
    from soshop.agent.session import get_session_manager

    mgr = get_session_manager()
    while True:
        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            raise
        try:
            await mgr.reconcile_to_target()
        except Exception:
            logger.exception("periodic agent-session reconcile failed; will retry")


async def _shutdown_close_livekit_api() -> None:
    """Release the process-wide LiveKitAPI aiohttp session on shutdown.

    Importing lazily keeps the FastAPI lifespan free of heavy module-level
    imports and matches the lazy-init in `soshop.api.routes.feed`.
    """
    try:
        from soshop.api.routes.feed import close_livekit_api

        await close_livekit_api()
    except Exception:
        logger.exception("livekit api close failed during shutdown")
