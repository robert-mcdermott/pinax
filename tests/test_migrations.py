# SPDX-License-Identifier: Apache-2.0
"""Migrations of the catalog (P7): they make the models' schema on SQLite and PostgreSQL, adopt a catalog made before
migrations, take turns, refuse a catalog newer than Pinax, and leave upgrading to an administrator when asked."""

import sqlite3
import sys
import threading

import pytest
from conftest import sqlite_only
from sqlalchemy import select, text


def test_the_migrations_make_the_models_schema(env):
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    from pinax import migrate
    from pinax.db import Base, get_engine

    with get_engine().connect() as conn:
        assert migrate.state(conn) == ("current", migrate.head())
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


@sqlite_only
def test_a_catalog_made_before_migrations_is_adopted(env):
    """A 0.3.0 catalog has no sign-in columns and few tables: starting Pinax adds what it lacks, with defaults."""
    from pinax import db, migrate
    from pinax.models import Tag, User

    path = env.var_root / "pinax.db"
    db.reset_engine()
    for suffix in ("", "-wal", "-shm"):
        (env.var_root / f"pinax.db{suffix}").unlink(missing_ok=True)
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE "user" (id INTEGER PRIMARY KEY, username VARCHAR(64) UNIQUE, display_name VARCHAR(200), '
                "email VARCHAR(200), title VARCHAR(200), is_admin BOOLEAN, active BOOLEAN, created_at DATETIME)")
    con.execute("CREATE TABLE snapshot (id INTEGER PRIMARY KEY, scan_id VARCHAR(64))")
    con.execute("INSERT INTO \"user\" VALUES (1, 'old', 'Old Timer', 'old@example.org', '', 1, 1, '2026-01-01')")
    con.commit()
    con.close()
    with db.get_engine().connect() as conn:
        assert migrate.state(conn) == ("legacy", None)
    db.init_db()
    with db.session_scope() as s:
        u = s.scalar(select(User).where(User.username == "old"))
        assert (u.password_hash, u.must_change_password, u.failed_logins, u.locked_until) == (None, False, 0, None)
        assert s.scalar(select(Tag).where(Tag.key == "legal_hold")) is not None  # new tables, and the defaults
    with db.get_engine().connect() as conn:
        assert migrate.state(conn) == ("current", migrate.head())


def test_a_catalog_newer_than_pinax_is_refused(env):
    from pinax import db, migrate

    with db.get_engine().begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = 'f00d'"))
    with pytest.raises(migrate.CatalogError, match="newer Pinax"):
        db.init_db()


def test_upgrading_can_be_left_to_an_administrator(env, monkeypatch, capsys):
    from pinax import cli, config, db, migrate

    db.reset_catalog()
    db.reset_engine()
    monkeypatch.setenv("PINAX_DB_AUTO_UPGRADE", "false")
    config.get_settings.cache_clear()
    with pytest.raises(migrate.CatalogError, match="pinax db upgrade"):
        db.init_db()

    def run(*argv) -> int:
        monkeypatch.setattr(sys, "argv", ["pinax", *argv])
        with pytest.raises(SystemExit) as done:
            cli.main()
        return done.value.code

    assert run("db", "status") == 1 and "empty" in capsys.readouterr().out
    assert run("db", "upgrade", "--sql") == 0
    sql = capsys.readouterr().out
    assert "CREATE TABLE audit_event" in sql and "CREATE TABLE alembic_version" in sql
    assert run("db", "upgrade") == 0 and "applied 0001" in capsys.readouterr().out
    db.init_db()  # now up to date
    assert run("db", "status") == 0 and "up to date" in capsys.readouterr().out
    assert run("db", "upgrade", "--sql") == 0 and capsys.readouterr().out == ""


def test_processes_starting_together_take_turns(env):
    """Two upgrades of an empty catalog at once: one creates it, the other finds it done."""
    from pinax import db, migrate

    db.reset_catalog()
    db.reset_engine()
    engines = [db.make_engine(env.db_url) for _ in range(2)]
    applied, errors = [], []

    def go(engine):
        try:
            applied.append(migrate.upgrade(engine))
        except Exception as e:  # noqa: BLE001 - reported below
            errors.append(e)

    threads = [threading.Thread(target=go, args=(e,)) for e in engines]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for e in engines:
        e.dispose()
    every = [f"{n:04d}" for n in range(1, int(migrate.head()) + 1)]  # revisions are numbered in order
    assert errors == [] and sorted(applied) == [[], every]


def test_a_new_revision_holds_what_the_catalog_lacks(env, tmp_path, monkeypatch):
    """`pinax db revision`, in a copy of the migrations: an index the models have and the catalog lacks."""
    import shutil

    from pinax import migrate
    from pinax.db import get_engine

    scripts = tmp_path / "migrations"
    shutil.copytree(migrate.SCRIPTS, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(migrate, "SCRIPTS", scripts)
    migrate._scripts.cache_clear()
    try:
        newest = migrate.head()
        following = f"{int(newest) + 1:04d}"
        with get_engine().begin() as conn:
            conn.execute(text("DROP INDEX ix_audit_event_ts"))
        path = migrate.revision(get_engine(), "Index audit events by time")
        assert path.name == f"{following}_index_audit_events_by_time.py"
        source = path.read_text()
        upgrade = source.split("def upgrade")[1].split("def downgrade")[0]
        assert "create_index(batch_op.f('ix_audit_event_ts'), ['ts']" in upgrade
        assert f"down_revision: str | None = '{newest}'" in source
        assert migrate.head() == following
    finally:
        migrate._scripts.cache_clear()
