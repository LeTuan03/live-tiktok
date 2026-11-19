"""Asyncpg connection pool for Neon Postgres.

The connection string is read from the ``DATABASE_URL`` environment variable
(see ``soshop_server.core.config.Settings``). Use Neon's *pooled* URL — the
one whose host ends in ``-pooler.<region>.aws.neon.tech`` — so we go through
Neon's pgbouncer in transaction mode.

Pool defaults are chosen for serverless Postgres / pgbouncer:

* ``statement_cache_size=0`` — pgbouncer in transaction-pooling mode does not
  guarantee that a prepared statement created on one backend is visible on the
  next, so asyncpg's prepared-statement cache must be disabled.
* ``min_size=1`` / ``max_size=10`` — keep idle connections low; Neon's free
  tier has a small connection cap and the pooler multiplexes us anyway.
* ``timeout=10`` / ``command_timeout=30`` — fail fast rather than hang on
  cold-start branch wakeups.
"""

from __future__ import annotations

import asyncio
import weakref

import asyncpg

from soshop_server.core.config import settings

# One pool per event loop. An asyncpg pool is bound to the loop that created
# it, and the agent worker can run several jobs in one process, each on its
# own thread and loop (livekit-agents' default executor on Windows). A single
# process-wide pool handed to a second loop fails with "another operation is
# in progress" or "Executor shutdown has been called". The API server has one
# loop, so it still sees exactly one pool.
_POOLS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncpg.Pool] = (
    weakref.WeakKeyDictionary()
)


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when ``DATABASE_URL`` is not set in the environment."""


async def get_pool() -> asyncpg.Pool:
    """Return the asyncpg pool for the running event loop, creating it on first call."""
    loop = asyncio.get_running_loop()
    pool = _POOLS.get(loop)
    if pool is None:
        if not settings.DATABASE_URL:
            raise DatabaseNotConfiguredError(
                "DATABASE_URL is not configured. Set it in server/.env "
                "(use Neon's pooled connection string)."
            )
        pool = await asyncpg.create_pool(
            settings.DATABASE_URL,
            min_size=1,
            max_size=10,
            timeout=10,
            command_timeout=30,
            statement_cache_size=0,
        )
        _POOLS[loop] = pool
    return pool


async def close_pool() -> None:
    """Close the running loop's pool. Safe to call when it was never created."""
    pool = _POOLS.pop(asyncio.get_running_loop(), None)
    if pool is not None:
        await pool.close()
