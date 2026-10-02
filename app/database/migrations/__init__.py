"""Lightweight, versioned schema migrations for SQLite.

``Base.metadata.create_all`` only creates missing tables; it never alters
existing ones. Every schema change made after the first release must be
registered here as a migration step so that existing user databases are
upgraded in place without data loss.

Each migration is a callable ``(connection) -> None``. Steps must be idempotent
because a crash between applying a step and recording its version would
otherwise leave the database in a half-migrated state.
"""

from __future__ import annotations

from typing import Callable

from loguru import logger
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.database.models.base import Base

Migration = Callable[[Connection], None]


# --------------------------------------------------------------------------- helpers
def table_exists(conn: Connection, table: str) -> bool:
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"), {"t": table}
    ).first()
    return row is not None


def column_exists(conn: Connection, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info('{table}')")).fetchall()
    return any(r[1] == column for r in rows)


def add_column_if_missing(conn: Connection, table: str, column: str, ddl: str) -> None:
    """Add ``column`` to ``table`` when absent. ``ddl`` is the column definition."""
    if not table_exists(conn, table):
        return
    if column_exists(conn, table, column):
        return
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
    logger.info("Migration: added column {}.{}", table, column)


# --------------------------------------------------------------------------- steps
def _m001_baseline(conn: Connection) -> None:
    """Baseline — tables are created by ``create_all`` before migrations run."""


def _m002_platform_fields(conn: Connection) -> None:
    """Add platform/source tracking columns introduced in v1.0.0."""
    add_column_if_missing(conn, "live_sessions", "platform", "VARCHAR(20) DEFAULT 'tiktok'")
    add_column_if_missing(conn, "comment_events", "product_id", "INTEGER")
    add_column_if_missing(conn, "comment_events", "source", "VARCHAR(20) DEFAULT 'manual'")
    add_column_if_missing(conn, "ai_suggestions", "needs_data", "BOOLEAN DEFAULT 0")
    add_column_if_missing(conn, "ai_suggestions", "grounding_note", "TEXT")
    add_column_if_missing(conn, "playlist_items", "notes", "TEXT")
    add_column_if_missing(conn, "generated_audio", "voice_id", "VARCHAR(200)")


MIGRATIONS: list[tuple[int, str, Migration]] = [
    (1, "baseline", _m001_baseline),
    (2, "platform_fields", _m002_platform_fields),
]

CURRENT_SCHEMA_VERSION = MIGRATIONS[-1][0]


# --------------------------------------------------------------------------- runner
def get_schema_version(conn: Connection) -> int:
    if not table_exists(conn, "schema_version"):
        return 0
    row = conn.execute(text("SELECT MAX(version) FROM schema_version")).first()
    return int(row[0]) if row and row[0] is not None else 0


def run_migrations(engine: Engine) -> int:
    """Create missing tables and apply pending migrations. Returns the final version."""
    import app.database.models  # noqa: F401 — register all models on Base.metadata

    Base.metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                "version INTEGER PRIMARY KEY, name VARCHAR(100), "
                "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        current = get_schema_version(conn)

    for version, name, step in MIGRATIONS:
        if version <= current:
            continue
        with engine.begin() as conn:
            logger.info("Applying migration {} ({})...", version, name)
            step(conn)
            conn.execute(
                text("INSERT INTO schema_version (version, name) VALUES (:v, :n)"),
                {"v": version, "n": name},
            )
        current = version

    logger.info("Database schema version: {}", current)
    return current
