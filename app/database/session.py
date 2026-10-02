"""Database session management with SQLAlchemy 2.x."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from loguru import logger
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Module-level engine and session factory
_engine = None
_SessionFactory = None


def init_database(db_url: str, echo: bool = False) -> None:
    """Initialize the database engine, create tables and run migrations.

    Args:
        db_url: SQLAlchemy database URL (e.g., sqlite:///path/to/db).
        echo: If True, log all SQL statements.
    """
    global _engine, _SessionFactory

    is_sqlite = db_url.startswith("sqlite")
    is_memory = is_sqlite and (":memory:" in db_url or db_url.rstrip("/") == "sqlite://")

    engine_kwargs: dict = {"echo": echo, "pool_pre_ping": True}
    if is_sqlite:
        engine_kwargs["connect_args"] = {"check_same_thread": False}
        if is_memory:
            # Share one in-memory connection across threads/sessions (tests).
            engine_kwargs["poolclass"] = StaticPool

    _engine = create_engine(db_url, **engine_kwargs)

    # Enable WAL mode and foreign keys for SQLite
    if is_sqlite:

        @event.listens_for(_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            if not is_memory:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)

    from app.database.migrations import run_migrations

    run_migrations(_engine)
    logger.info("Database initialized: {}", db_url)


def get_engine():
    """Get the current database engine."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _engine


def is_initialized() -> bool:
    return _engine is not None


def get_session() -> Session:
    """Create a new database session."""
    if _SessionFactory is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _SessionFactory()


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """Context manager for database sessions with auto-commit/rollback."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def close_database() -> None:
    """Close the database engine and dispose connections."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
        logger.info("Database connection closed")
    _engine = None
    _SessionFactory = None
