# SPDX-License-Identifier: Apache-2.0
"""Catalog database engine and session factory.

SQLite (WAL mode) by default, a file in VAR_ROOT with nothing to install; PostgreSQL for production
(PINAX_DATABASE_URL). Migrations (migrate.py) create the catalog and keep it up to date on both.
The catalog holds people, labs, folder mappings, filesystems, snapshots, and folder history; the
per-file data lives in each snapshot's DuckDB index and Parquet files (see index.py).
"""

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import MetaData, create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

# Constraints and indexes named by rule, so a migration can name what it changes on every database.
NAMING = {"ix": "ix_%(column_0_label)s", "uq": "uq_%(table_name)s_%(column_0_N_name)s",
          "ck": "ck_%(table_name)s_%(constraint_name)s", "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
          "pk": "pk_%(table_name)s"}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA busy_timeout=30000")
            # Turning a new catalog to WAL needs it to itself, and SQLite does not wait for that: processes that
            # start together (the web app and the watcher) try again for a few seconds.
            for attempt in range(100):
                try:
                    cur.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as e:
                    if "locked" not in str(e) or attempt == 99:
                        raise
                    time.sleep(0.05)
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    connect_args = {}
    if url.startswith("postgresql") and "application_name" not in url:
        connect_args["application_name"] = "pinax"  # so the database's own views say who is connected
    pool = get_settings().database_pool_size
    try:
        return create_engine(url, pool_pre_ping=True, pool_size=pool, max_overflow=pool, connect_args=connect_args)
    except ModuleNotFoundError as e:
        raise RuntimeError(f"The catalog's database needs a driver that is not installed ({e.name}); for PostgreSQL, "
                           "install Pinax with its postgres extra: uv sync --extra postgres") from e


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        settings.ensure_dirs()
        _engine = make_engine(settings.db_url)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine (used by tests and `pinax seed`)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def SessionLocal() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    """A session that commits on success and rolls back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    """Bring the catalog up to date (or, with PINAX_DB_AUTO_UPGRADE=false, check that it is), then add the default
    tags if they were never added."""
    from . import migrate

    if get_settings().db_auto_upgrade:
        migrate.upgrade(get_engine())
    else:
        migrate.ensure_current(get_engine())
    from .tags import ensure_defaults

    ensure_defaults()


def reset_catalog() -> None:
    """Delete everything in the catalog (`pinax seed`, which runs only in the demo environment)."""
    from . import models  # noqa: F401 - register tables

    engine = get_engine()
    if engine.dialect.name == "sqlite":
        path = engine.url.database
        reset_engine()
        if path and path != ":memory:":
            for suffix in ("", "-wal", "-shm", ".lock"):
                Path(path + suffix).unlink(missing_ok=True)
        return
    with engine.begin() as conn:
        Base.metadata.drop_all(conn)
        if inspect(conn).has_table("alembic_version"):
            conn.execute(text("DROP TABLE alembic_version"))
