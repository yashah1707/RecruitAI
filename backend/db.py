"""Engine and session handling."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend import settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None) -> Engine:
    url = url or settings.DATABASE_URL
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        # One connection shared across threads is what FastAPI's TestClient
        # and an in-memory database both need.
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url == "sqlite://":
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        # SQLite ignores foreign keys unless told otherwise; PostgreSQL never does.
        @event.listens_for(engine, "connect")
        def _enforce_foreign_keys(dbapi_connection, _record):  # pragma: no cover - trivial
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = make_engine()
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """FastAPI dependency for work that outlives the request (the reading
    queue): it opens its own sessions instead of borrowing the request's."""
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, rolled back on error."""
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
