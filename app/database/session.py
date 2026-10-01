"""Database session management with SQLAlchemy 2.x."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from loguru import logger
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.database.models.base import Base

# Module-level engine and session factory
_engine = None
_SessionFactory = None


def init_database(db_url: str, echo: bool = False) -> None:
    """Initialize the database engine and create all tables.

    Args:
        db_url: SQLAlchemy database URL (e.g., sqlite:///path/to/db).
        echo: If True, log all SQL statements.
    """
    global _engine, _SessionFactory

    _engine = create_engine(
        db_url,
        echo=echo,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False} if "sqlite" in db_url else {},
    )

    # Enable WAL mode and foreign keys for SQLite
    if "sqlite" in db_url:
        @event.listens_for(_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)

    # Create all tables
    # Import all models to ensure they're registered
    import app.database.models  # noqa: F401

    Base.metadata.create_all(_engine)
    logger.info("Database initialized: {}", db_url)


def get_engine():
    """Get the current database engine."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _engine


def get_session() -> Session:
    """Create a new database session.

    Returns:
        A new SQLAlchemy Session.
    """
    if _SessionFactory is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _SessionFactory()


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """Context manager for database sessions with auto-commit/rollback.

    Usage:
        with get_db() as db:
            db.add(product)
            # Auto-committed on exit, rolled back on exception
    """
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
