"""Database engine and session management."""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from persistence.models import Base

log = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None
_url: str | None = None


def configure(url: str) -> None:
    global _engine, _SessionLocal, _url
    if not url:
        raise RuntimeError("AGENT_DATABASE_URL is not configured")
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url.endswith("sqlite://"):
            kwargs["poolclass"] = StaticPool
    _engine = create_engine(url, **kwargs)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    _url = url


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database not configured; call configure() first")
    return _engine


def init_db(retries: int = 30, delay: float = 2.0) -> None:
    engine = get_engine()
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            Base.metadata.create_all(engine)
            log.info("database ready (attempt %s)", attempt)
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("database not ready (attempt %s/%s): %s", attempt, retries, exc)
            time.sleep(delay)
    raise RuntimeError(f"database unavailable after {retries} attempts: {last}")


@contextmanager
def session_scope() -> Iterator[Session]:
    if _SessionLocal is None:
        raise RuntimeError("Database not configured")
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_connection() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
