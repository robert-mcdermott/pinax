# SPDX-License-Identifier: Apache-2.0
"""Catalog database engine and session factory.

SQLite (WAL mode) for the proof of concept. Point PINAX_DATABASE_URL at PostgreSQL for production.
The catalog holds people, labs, folder mappings, filesystems, snapshots, and folder history; the
per-file data lives in each snapshot's DuckDB index and Parquet files (see index.py).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        return engine
    return create_engine(url, pool_pre_ping=True)


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        settings.ensure_dirs()
        _engine = _make_engine(settings.db_url)
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
    from . import models  # noqa: F401 - register tables

    engine = get_engine()
    Base.metadata.create_all(engine)
    add_missing_columns(engine)
    from .tags import ensure_defaults

    ensure_defaults()


def add_missing_columns(engine: Engine) -> list[str]:
    """Add columns that a newer version of a table has and the catalog's copy lacks (create_all adds only whole
    tables), with their server defaults, so an existing catalog keeps working after an upgrade. Until migrations
    arrive (Alembic, with PostgreSQL), new columns are nullable or have a server default."""
    added = []
    have_tables = set(inspect(engine).get_table_names())
    prep = engine.dialect.identifier_preparer
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in have_tables:
                continue
            have = {c["name"] for c in inspect(conn).get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = f"ALTER TABLE {prep.quote(table.name)} ADD COLUMN {prep.quote(col.name)} {col.type.compile(engine.dialect)}"
                if col.server_default is not None:
                    arg = col.server_default.arg
                    default = arg if isinstance(arg, str) else arg.compile(dialect=engine.dialect)
                    ddl += f" DEFAULT {default}"
                conn.execute(text(ddl))
                added.append(f"{table.name}.{col.name}")
    return added
