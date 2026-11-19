"""Shared FastAPI dependencies for SoShop route modules.

Centralizes the bits route modules share: DB connection acquisition,
the org-context dependency, and the canonical strict-model base
class, so no router has to pull DB plumbing through a sibling route
module.

Exports:

* :data:`DbConn` — ``Annotated[asyncpg.Connection, Depends(get_db_conn)]``
* :data:`OrgCtx` — ``Annotated[OrgContext, Depends(require_org)]``
* :class:`StrictModel` — pydantic ``BaseModel`` with
  ``extra='forbid'`` + ``str_strip_whitespace=True``

The DB connection is acquired from :func:`soshop_server.db.pool.get_pool`
so there is exactly one asyncpg pool in the process (matching the agent
worker). The pool is configured with ``statement_cache_size=0`` because
Neon's pgbouncer runs in transaction-pool mode.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

import asyncpg
from fastapi import Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from soshop.auth.identity import OrgContext, require_org
from soshop.repositories import AdvertiserRepository, FeedRepository
from soshop_server.db.pool import DatabaseNotConfiguredError, get_pool

__all__ = [
    "AdvertiserRepo",
    "OrgCtx",
    "DbConn",
    "FeedRepo",
    "StrictModel",
    "get_advertiser_repo",
    "get_db_conn",
    "get_feed_repo",
]


async def get_db_conn() -> AsyncIterator[asyncpg.Connection]:
    """Yield a Postgres connection from the canonical pool.

    Translates :class:`DatabaseNotConfiguredError` into a 500 so routes
    that depend on this don't need their own try/except wrapper.
    """
    try:
        pool = await get_pool()
    except DatabaseNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DATABASE_URL is not configured",
        ) from exc
    async with pool.acquire() as conn:
        yield conn


DbConn = Annotated[asyncpg.Connection, Depends(get_db_conn)]
OrgCtx = Annotated[OrgContext, Depends(require_org)]


async def get_feed_repo(
    conn: Annotated[asyncpg.Connection, Depends(get_db_conn)],
) -> FeedRepository:
    """Build a `FeedRepository` over the per-request DB connection.

    Reads from `get_db_conn` so test suites that override `get_db_conn`
    with a fake asyncpg connection don't need to add a second override
    for the repo dependency.
    """
    return FeedRepository(conn)


async def get_advertiser_repo(
    conn: Annotated[asyncpg.Connection, Depends(get_db_conn)],
) -> AdvertiserRepository:
    """Build an `AdvertiserRepository` over the per-request DB connection.

    See :func:`get_feed_repo` for the test-override rationale.
    """
    return AdvertiserRepository(conn)


FeedRepo = Annotated[FeedRepository, Depends(get_feed_repo)]
AdvertiserRepo = Annotated[AdvertiserRepository, Depends(get_advertiser_repo)]


class StrictModel(BaseModel):
    """Canonical request/response model base.

    * ``extra='forbid'`` — unknown fields are a 422, not a silent drop.
    * ``str_strip_whitespace=True`` — leading/trailing whitespace is
      stripped on str values so optional UI padding doesn't leak into
      the DB or trip ``min_length`` constraints.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
