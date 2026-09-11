"""Database engine and session management.

The engine is created lazily so that the application can boot (and serve
`/health`) even when PostgreSQL is not yet reachable -- the extension then shows
a degraded state instead of failing to start.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings
from app.database.models import Base

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _create_engine(url: str) -> Engine:
    connect_args: dict = {}
    kwargs: dict = {"echo": settings.db_echo, "pool_pre_ping": True, "future": True}

    if url.startswith("sqlite"):
        # Used by the test-suite; allow use across the TestClient's threads.
        connect_args["check_same_thread"] = False
        kwargs.pop("pool_pre_ping")
    elif settings.is_serverless:
        # One pool per process is fine on a long-lived server and ruinous on a
        # serverless host, where dozens of concurrent invocations each hold
        # their own. NullPool opens and closes per checkout instead, which is
        # the right trade when the process itself is short-lived.
        kwargs["poolclass"] = NullPool
        kwargs.pop("pool_pre_ping")

    return create_engine(url, connect_args=connect_args, **kwargs)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _create_engine(settings.database_url)
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _SessionLocal


def configure_engine(url: str) -> None:
    """Rebind the engine to another URL. Used by the test-suite."""
    global _engine, _SessionLocal
    dispose_engine()
    _engine = _create_engine(url)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def dispose_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """Enforce foreign keys on SQLite, which disables them by default."""
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager for use outside the request cycle (agent loop)."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all() -> None:
    """Create tables directly.

    Production uses Alembic; this exists for tests and for the SQLite
    fallback so a developer without PostgreSQL can still run the app.
    """
    Base.metadata.create_all(bind=get_engine())


def check_connection() -> tuple[bool, str | None]:
    """Return (ok, error) for the health endpoint."""
    from sqlalchemy import text

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # noqa: BLE001 - surfaced as a health string only
        logger.warning("Database health check failed: %s", exc)
        return False, type(exc).__name__
