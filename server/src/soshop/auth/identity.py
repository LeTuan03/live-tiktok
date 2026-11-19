"""Open-access identity dependencies for the SoShop API.

The demo runs without an external authentication provider. The consumer
feed is anonymous, and the advertiser portal resolves to a single default
organization. Identity is read from optional request headers and falls
back to synthesized or default values, so every endpoint is reachable
without a bearer token:

* ``X-Org-Id``  — advertiser org for the portal routes. Defaults to
  ``settings.DEFAULT_ORG_ID`` (the primary seeded org).
* ``X-User-Id`` — viewer/user identity for user-scoped routes (favorites,
  feed events, chat). Defaults to a stable demo user so those rows persist
  across requests. The soft-auth feed routes treat a missing header as
  "anonymous" and mint an ``anon-<hex>`` id instead.

The dependency *shapes* (:class:`OrgContext` / :class:`UserContext` plus
the ``Depends`` wrappers) mirror the auth layer this replaced, so route
signatures and the existing dependency overrides in the test suite keep
working unchanged. Org-admin gating is still enforced structurally
(:func:`require_org_admin`); in open access the default role is admin, so
real callers pass while a non-admin context still fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, HTTPException, Request, status

from soshop_server.core.config import settings

__all__ = [
    "OptionalUserCtx",
    "OrgContext",
    "OrgCtx",
    "OrgRole",
    "UserContext",
    "UserCtx",
    "anonymous_viewer_id",
    "assert_user_matches",
    "maybe_user",
    "require_org",
    "require_org_admin",
    "require_user",
]

# Optional request headers used to override the default open-access identity.
ORG_ID_HEADER = "X-Org-Id"
USER_ID_HEADER = "X-User-Id"

# Stable fallback user for user-scoped routes when no `X-User-Id` is sent, so
# favorites and feed events persist across requests in the demo.
DEFAULT_USER_ID = "demo-user"


class OrgRole:
    ADMIN = "org:admin"
    MEMBER = "org:member"


@dataclass(frozen=True)
class OrgContext:
    """Resolved advertiser-org identity for the portal routes.

    Routes scope every read/write by ``org_id``; the value comes from the
    optional ``X-Org-Id`` header or ``settings.DEFAULT_ORG_ID``.
    """

    user_id: str
    org_id: str
    org_role: str = OrgRole.ADMIN
    org_slug: str | None = None
    raw_claims: dict[str, Any] = field(default_factory=dict)

    @property
    def is_admin(self) -> bool:
        return self.org_role == OrgRole.ADMIN


@dataclass(frozen=True)
class UserContext:
    """Resolved viewer/user identity for user-scoped routes."""

    user_id: str


def _header(request: Request, name: str) -> str | None:
    value = request.headers.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def require_org(request: Request) -> OrgContext:
    """Resolve the advertiser org for portal routes (open access).

    The org id comes from the optional ``X-Org-Id`` header and otherwise
    defaults to ``settings.DEFAULT_ORG_ID``. Routes still resolve the
    advertiser by ``ctx.org_id`` and never trust an org/advertiser id from
    the request body or URL.
    """
    org_id = _header(request, ORG_ID_HEADER) or settings.DEFAULT_ORG_ID
    user_id = _header(request, USER_ID_HEADER) or DEFAULT_USER_ID
    return OrgContext(user_id=user_id, org_id=org_id, org_role=OrgRole.ADMIN)


def require_org_admin(
    ctx: Annotated[OrgContext, Depends(require_org)],
) -> OrgContext:
    """Like :func:`require_org`, but additionally requires ``org:admin``."""
    if not ctx.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="org admin role required",
        )
    return ctx


def require_user(request: Request) -> UserContext:
    """Resolve a user identity for user-scoped routes (open access).

    Reads the optional ``X-User-Id`` header, falling back to a stable demo
    user so favorites / events persist across requests.
    """
    user_id = _header(request, USER_ID_HEADER) or DEFAULT_USER_ID
    return UserContext(user_id=user_id)


def maybe_user(request: Request) -> UserContext | None:
    """Soft-auth variant: return the header identity when present, else None.

    Read-mostly consumer surfaces (the feed, the live-room viewer token)
    use this so an anonymous browser stays anonymous — callers mint an
    ``anon-<hex>`` id via :func:`anonymous_viewer_id` when this returns
    ``None``.
    """
    user_id = _header(request, USER_ID_HEADER)
    return UserContext(user_id=user_id) if user_id else None


OrgCtx = Annotated[OrgContext, Depends(require_org)]
UserCtx = Annotated[UserContext, Depends(require_user)]
OptionalUserCtx = Annotated[UserContext | None, Depends(maybe_user)]


def assert_user_matches(resolved_user_id: str, body_user_id: str | None) -> None:
    """Reject a request whose body declares a different user than resolved.

    Retained for transport compatibility: legacy clients may echo the user
    id in the request body. When present it must match the resolved
    identity, so a forged body can't write into another user's session.
    """
    if body_user_id is not None and body_user_id != resolved_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="body user_id does not match the request user",
        )


def anonymous_viewer_id() -> str:
    """Synthesize an anonymous viewer identity.

    Anonymous browsers of the feed get a stable-looking ``anon-<hex>`` id
    that downstream code (DB writes, dispatch metadata) can treat like any
    other ``user_id``. The full 128-bit hex keeps the collision-resistance
    margin wide because the anon id doubles as the write handle on the
    soft-auth feed routes.
    """
    return f"anon-{uuid4().hex}"
