"""Engine and session lifecycle.

Everything here is dialect-neutral on purpose: the same code has to work against
in-memory SQLite in the test suite and managed Postgres in production, or the
green test run stops meaning anything.
"""

import logging
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base. Models arrive in Phase 2."""


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def normalize_database_url(url: str) -> str:
    """Coerce the URLs platforms hand out into ones SQLAlchemy 2.x accepts.

    DigitalOcean exposes `postgresql://...`, and some providers still emit the
    legacy `postgres://`. SQLAlchemy 2 needs an explicit driver, and we install
    psycopg 3, so both are rewritten to `postgresql+psycopg://`.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def init_engine(database_url: str) -> Engine:
    """Build the engine and session factory. Called once from the app lifespan."""
    global _engine, _session_factory

    url = normalize_database_url(database_url)
    kwargs: dict[str, object] = {"pool_pre_ping": True, "future": True}

    if url.startswith("sqlite"):
        # Handlers are sync and run in a threadpool, so a connection can be
        # touched by a thread other than the one that opened it.
        kwargs["connect_args"] = {"check_same_thread": False}
        if _is_memory_sqlite(url):
            # Each connection to an in-memory SQLite gets its OWN empty database.
            # Without a shared single connection the test suite would create
            # tables on one connection and query another that has none.
            kwargs["poolclass"] = StaticPool
        else:
            _ensure_sqlite_parent_dir(url)
    else:
        # Managed Postgres drops idle connections; pre-ping plus a recycle well
        # under the server's idle timeout keeps the first request after a lull
        # from failing on a stale socket.
        kwargs.update(pool_size=5, max_overflow=10, pool_recycle=1800)

    _engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):
        _enable_sqlite_pragmas(_engine)

    _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    logger.info("database engine initialised", extra={"dialect": _engine.dialect.name})
    return _engine


def _is_memory_sqlite(url: str) -> bool:
    return url in ("sqlite://", "sqlite:///:memory:")


def _ensure_sqlite_parent_dir(url: str) -> None:
    path = url.split("///", 1)[-1]
    if path and path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)


def _enable_sqlite_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Engine not initialised; init_engine() runs in the app lifespan.")
    return _engine


def create_all() -> None:
    """Create tables from the declarative metadata.

    Fine for a greenfield single-table service. Alembic is the first thing added
    once the schema has to change without dropping data.
    """
    Base.metadata.create_all(bind=get_engine())


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one Session per request, never a shared global."""
    if _session_factory is None:
        raise RuntimeError("Session factory not initialised.")
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()


def check_database() -> bool:
    """Cheap liveness probe for the DB, used by /readyz.

    Deliberately does not go through get_session: if the pool itself cannot hand
    out a connection, that is exactly the condition we need to report, not an
    exception raised before the handler is entered.
    """
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.warning("database readiness check failed", exc_info=True)
        return False


def dispose_engine() -> None:
    """Close pooled connections on shutdown so a SIGTERM does not leave sockets open."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
        logger.info("database engine disposed")
    _engine = None
    _session_factory = None
