"""Bearer-token auth for the public-test gate.

The web preview is gated behind a single password (``settings.SITE_PASSWORD``).
The web's ``/api/agents/session`` proxy forwards that password as a
Bearer token to the FastAPI side; this dependency validates it.

Mirrors the shape of the bearer-token parsing in :mod:`soshop.auth.identity` but
does not call any external auth provider — it is a constant-time string compare against
the env-configured password.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status

from soshop_server.core.config import settings


def require_site_password(request: Request) -> None:
    if not settings.SITE_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "site_password_not_configured",
                "message": "SITE_PASSWORD is not configured",
            },
        )
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing authorization header",
        )
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="malformed authorization header",
        )
    if not hmac.compare_digest(parts[1].strip(), settings.SITE_PASSWORD):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid site password",
        )
