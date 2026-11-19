"""On-demand agent-session control plane.

``POST /agents/session`` — start (or extend) an agent session for
``settings.AGENT_SESSION_DURATION_MINUTES``. Idempotent: a second POST
resets the wind-down clock.

``GET /agents/session`` — current :class:`SessionStatus`.

Both routes are gated by the ``SITE_PASSWORD`` bearer token. The
endpoint is intentionally not org-gated so the public preview gate
can drive it without bringing portal auth into the trigger path.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from soshop.agent.session import SessionStatus, get_session_manager
from soshop.auth.site_password import require_site_password
from soshop_server.core.config import settings

logger = logging.getLogger("soshop.api.agents")

router = APIRouter(
    prefix="/agents",
    tags=["agents"],
    dependencies=[Depends(require_site_password)],
)


@router.post(
    "/session",
    response_model=SessionStatus,
    summary="Start (or extend) an agent session",
)
async def start_agent_session() -> SessionStatus:
    mgr = get_session_manager()
    status_ = await mgr.start(settings.AGENT_SESSION_DURATION_MINUTES)
    logger.info(
        "agent_session_post",
        extra={
            "event": "agent_session_post",
            "expires_at": status_.expires_at.isoformat() if status_.expires_at else None,
        },
    )
    return status_


@router.get(
    "/session",
    response_model=SessionStatus,
    summary="Current agent-session status",
)
async def get_agent_session() -> SessionStatus:
    return get_session_manager().status()
