# SPDX-License-Identifier: Apache-2.0
"""Migrations of the catalog (Alembic, in `pinax/migrations`), on SQLite and PostgreSQL.

`upgrade` brings a catalog to the newest revision: it creates a new one, and adopts one made before migrations
(0.9.0 and earlier, whose tables and columns were added at start-up) by giving it what the first revision has and it
lacks. Processes that start together (the web app and the watcher) take turns: a lock on PostgreSQL, a lock file
beside a SQLite catalog. A catalog already up to date is only checked. `ensure_current` checks without changing
anything (PINAX_DB_AUTO_UPGRADE=false). A catalog newer than this Pinax is refused rather than used.
"""

import fcntl
import io
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, inspect, text
from sqlalchemy.engine import Engine

log = logging.getLogger("pinax.migrate")
SCRIPTS = Path(__file__).parent / "migrations"
LOCK_KEY = 0x70696E6178  # "pinax", for pg_advisory_xact_lock


class CatalogError(Exception):
    """The catalog cannot be used as it is: behind, with automatic upgrades off, or newer than this Pinax."""


def _config(output_buffer=None, **attributes) -> Config:
    cfg = Config(output_buffer=output_buffer)
    cfg.set_main_option("script_location", str(SCRIPTS))
    cfg.attributes.update(attributes)
    return cfg


@cache
def _scripts() -> ScriptDirectory:
    return ScriptDirectory.from_config(_config())


def head() -> str:
    return _scripts().get_current_head()


def current(conn: Connection) -> str | None:
    return MigrationContext.configure(conn).get_current_revision()


def describe(engine: Engine) -> str:
    """The catalog's address without its password, for messages and logs."""
    return engine.url.render_as_string(hide_password=True)


def state(conn: Connection) -> tuple[str, str | None]:
    """`current`, `behind`, `new` (no tables), `legacy` (made before migrations), or `ahead` (a revision this Pinax
    does not know), with the catalog's revision."""
    rev = current(conn)
    if rev is None:
        names = set(inspect(conn).get_table_names())
        return ("legacy" if "snapshot" in names else "new"), None
    if rev == head():
        return "current", rev
    try:
        _scripts().get_revision(rev)
    except Exception:  # noqa: BLE001 - alembic raises several kinds for an unknown revision
        return "ahead", rev
    return "behind", rev


@contextmanager
def _file_turn(engine: Engine) -> Iterator[None]:
    """One process at a time upgrades a SQLite catalog: a lock file beside it, held until the upgrade is committed."""
    path = engine.url.database
    if engine.dialect.name != "sqlite" or not path or path == ":memory:":
        yield
        return
    with open(path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _turn(conn: Connection) -> None:
    """One process at a time upgrades a PostgreSQL catalog: a lock held until this transaction ends."""
    if conn.dialect.name == "postgresql":
        conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_KEY})


def upgrade(engine: Engine) -> list[str]:
    """Bring the catalog to the newest revision; returns the revisions applied (none if it was up to date)."""
    with engine.connect() as conn:
        found, _rev = state(conn)
    if found == "current":
        return []
    if found == "ahead":
        raise CatalogError(_ahead(engine, _rev))
    with _file_turn(engine), engine.begin() as conn:  # committed before the file lock is let go
        _turn(conn)
        found, rev = state(conn)  # again, now that it is this process's turn
        if found == "ahead":
            raise CatalogError(_ahead(engine, rev))
        if found == "current":
            return []
        if found == "legacy":
            log.info("Adopting a catalog made before migrations (Pinax 0.9.0 or earlier): %s", describe(engine))
        applied = [s.revision for s in reversed(list(_scripts().iterate_revisions(head(), rev or "base")))]
        command.upgrade(_config(connection=conn), "head")
    log.info("Upgraded the catalog to revision %s (applied %s): %s", head(), ", ".join(applied), describe(engine))
    return applied


def ensure_current(engine: Engine) -> None:
    with engine.connect() as conn:
        found, rev = state(conn)
    if found == "current":
        return
    if found == "ahead":
        raise CatalogError(_ahead(engine, rev))
    what = {"new": "is empty", "legacy": "was made before migrations",
            "behind": f"is at revision {rev}"}[found]
    raise CatalogError(f"The catalog ({describe(engine)}) {what}, and this Pinax needs revision {head()}. "
                       "PINAX_DB_AUTO_UPGRADE is false, so run `pinax db upgrade` (back the catalog up first).")


def _ahead(engine: Engine, rev: str | None) -> str:
    return (f"The catalog ({describe(engine)}) is at revision {rev}, which this Pinax does not know: a newer Pinax "
            f"has upgraded it. Run that release, or restore a backup made before the upgrade.")


def sql(engine: Engine) -> str:
    """The SQL that `upgrade` would run, for a database administrator to read or apply (Alembic's offline mode)."""
    with engine.connect() as conn:
        found, rev = state(conn)
    if found == "ahead":
        raise CatalogError(_ahead(engine, rev))
    if found == "legacy":
        raise CatalogError("A catalog made before migrations is adopted by `pinax db upgrade` itself, which adds "
                           "only what it lacks; its SQL depends on what it has.")
    if found == "current":
        return ""
    out = io.StringIO()
    command.upgrade(_config(output_buffer=out, url=engine.url), "head" if rev is None else f"{rev}:head", sql=True)
    return out.getvalue()


def revision(engine: Engine, message: str) -> Path:
    """For developers: a new revision from the difference between the models and this catalog (which must be up to
    date), numbered after the newest. Alembic cannot tell a rename from a drop and an add: read it before keeping it."""
    with engine.begin() as conn:
        found, _rev = state(conn)
        if found != "current":
            raise CatalogError("Bring the catalog up to date first (`pinax db upgrade`), so the revision holds only "
                               "the models' changes")
        script = command.revision(_config(connection=conn), message=message, autogenerate=True,
                                  rev_id=f"{int(head()) + 1:04d}")
    _scripts.cache_clear()
    return Path(script.path)
