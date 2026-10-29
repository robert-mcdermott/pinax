# SPDX-License-Identifier: Apache-2.0
"""`pinax db copy` (P7): a whole catalog into another, empty database, row for row, with ids that new rows do not
collide with; on PostgreSQL runs, through SQLite and back into PostgreSQL."""

import sys
import uuid

import pytest
from conftest import POSTGRES, _postgres
from sqlalchemy import select


def _rows(engine) -> dict[str, list[tuple]]:
    from pinax.db import Base

    with engine.connect() as conn:
        return {t.name: [tuple(r) for r in conn.execute(select(t).order_by(*t.primary_key.columns))]
                for t in Base.metadata.sorted_tables}


@pytest.fixture
def postgres_schema():
    """An empty schema in the test PostgreSQL database, as a URL (only on PostgreSQL runs)."""
    from sqlalchemy.engine import make_url

    if not POSTGRES:
        yield None
        return
    schema = f"copy_{uuid.uuid4().hex[:12]}"
    _postgres(f'CREATE SCHEMA "{schema}"')
    yield make_url(POSTGRES).update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(False)
    _postgres(f'DROP SCHEMA "{schema}" CASCADE')


def _add_lab(engine, slug: str) -> int:
    from sqlalchemy.orm import Session

    from pinax.models import Lab

    with Session(engine) as s:
        lab = Lab(slug=slug, name=slug.title())
        s.add(lab)
        s.commit()
        return lab.id


def test_a_catalog_copied_row_for_row(catalog, tmp_path, postgres_schema):
    from pinax import dbcopy
    from pinax.config import Settings
    from pinax.db import get_engine, make_engine

    source = get_engine()
    want = _rows(source)
    assert want["snapshot"] and want["folder_stat"] and want["audit_event"] and want["tag"]
    first = make_engine(f"sqlite:///{tmp_path / 'copy.db'}")
    copied = dbcopy.copy(source, first, progress=lambda _m: None)
    assert copied == {name: len(rows) for name, rows in want.items()}
    assert _rows(first) == want
    targets = [first]
    if postgres_schema:  # and from SQLite into PostgreSQL, as a site moving its catalog does
        second = make_engine(Settings(database_url=postgres_schema).db_url)
        dbcopy.copy(first, second, progress=lambda _m: None)
        assert _rows(second) == want
        targets.append(second)
    for engine in targets:  # new rows take ids after the copied ones
        assert _add_lab(engine, "gamma") == max(r[0] for r in want["lab"]) + 1
        engine.dispose()


def test_only_into_an_empty_database(catalog, tmp_path):
    from pinax import dbcopy
    from pinax.db import get_engine, make_engine

    with pytest.raises(dbcopy.CopyError, match="same database"):
        dbcopy.copy(get_engine(), get_engine())
    target = make_engine(f"sqlite:///{tmp_path / 'copy.db'}")
    dbcopy.copy(get_engine(), target, progress=lambda _m: None)
    with pytest.raises(dbcopy.CopyError, match="not empty: .*snapshot has 1 row"):
        dbcopy.copy(get_engine(), target, progress=lambda _m: None)
    target.dispose()


def test_pinax_db_copy(catalog, tmp_path, monkeypatch, capsys):
    from pinax import cli

    monkeypatch.setattr(sys, "argv", ["pinax", "db", "copy", f"sqlite:///{tmp_path / 'copy.db'}"])
    with pytest.raises(SystemExit) as done:
        cli.main()
    out = capsys.readouterr().out
    assert done.value.code == 0 and "every count matches" in out and "  snapshot: 1 row\n" in out
