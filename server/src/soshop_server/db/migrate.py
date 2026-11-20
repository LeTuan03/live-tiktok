"""Apply pending SQL migrations, then optionally seed an empty database.

The repo has no migration framework: schema changes are numbered
``migrations/000N_<name>.up.sql`` files. This script applies the ones a
database has not seen yet, in order, and records each in a small
``schema_migrations`` table so it is safe to run on every start. The Docker
Compose bundle runs it before the API and the agent worker come up.

Each ``.up.sql`` file manages its own transaction (``BEGIN`` / ``COMMIT``), so
the file is sent as one simple-protocol batch and recorded only after it
succeeds.

It lives in the package rather than in ``scripts/`` so ``python -m`` finds it
in release images too, which ship bytecode only (``Dockerfile.local``).

A database migrated by hand before this script existed has the schema but no
``schema_migrations`` table. Mark what it already has with
``--baseline 0006`` (records 0001 to 0006 without running them).

Usage:
    cd server
    uv run python -m soshop_server.db.migrate [--seed-if-empty]   # reads DATABASE_URL
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

import asyncpg

from soshop_server.core.config import settings

# src/soshop_server/db/migrate.py -> server/
SERVER_DIR = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = SERVER_DIR / "migrations"
# Release images ship scripts as bytecode only (Dockerfile.local, STRIP_SOURCE).
SEED_SCRIPT = next(
    (p for p in (SERVER_DIR / "scripts" / n for n in ("seed_v1.py", "seed_v1.pyc")) if p.exists()),
    SERVER_DIR / "scripts" / "seed_v1.py",
)

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


def migration_files() -> list[tuple[str, Path]]:
    """``(version, path)`` for every ``.up.sql`` file, oldest first."""
    files = sorted(MIGRATIONS_DIR.glob("*.up.sql"))
    return [(f.name.split("_", 1)[0], f) for f in files]


async def _table_exists(conn: asyncpg.Connection, name: str) -> bool:
    found = await conn.fetchval("SELECT to_regclass($1)", f"public.{name}")
    return found is not None


async def _connect(url: str, attempts: int = 30) -> asyncpg.Connection:
    """Connect, retrying while Postgres is still starting up."""
    for attempt in range(attempts):
        try:
            return await asyncpg.connect(url, statement_cache_size=0)
        except (OSError, asyncpg.CannotConnectNowError):
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(2)
    raise RuntimeError("unreachable")


async def migrate(url: str, baseline: str | None) -> None:
    conn = await _connect(url)
    try:
        had_table = await _table_exists(conn, "schema_migrations")
        if not had_table and baseline is None and await _table_exists(conn, "advertisers"):
            sys.exit(
                "This database already has the SoShop schema but no schema_migrations "
                "table. Re-run with --baseline <last applied version>, e.g. --baseline 0006."
            )
        await conn.execute(CREATE_TABLE)
        applied = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}

        for version, path in migration_files():
            if version in applied:
                continue
            if baseline is not None and version <= baseline:
                await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", version)
                print(f"migrate: marked {path.name} as applied (baseline)")
                continue
            print(f"migrate: applying {path.name}")
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", version)
        print("migrate: schema is up to date")
    finally:
        await conn.close()


async def is_empty(url: str) -> bool:
    conn = await _connect(url)
    try:
        return await conn.fetchval("SELECT count(*) FROM personas") == 0
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--seed-if-empty",
        action="store_true",
        help="run scripts/seed_v1.py when the database has no personas yet",
    )
    parser.add_argument(
        "--baseline",
        metavar="VERSION",
        help="record migrations up to VERSION as applied without running them",
    )
    args = parser.parse_args()

    url = settings.DATABASE_URL
    if not url:
        sys.exit("DATABASE_URL is not set")

    asyncio.run(migrate(url, args.baseline))

    if args.seed_if_empty and asyncio.run(is_empty(url)):
        print("migrate: empty database, loading the demo catalog and host")
        subprocess.run([sys.executable, str(SEED_SCRIPT)], check=True)  # noqa: S603 - fixed argv


if __name__ == "__main__":
    main()
