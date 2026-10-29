# SPDX-License-Identifier: Apache-2.0
"""`pinax db copy`: the whole catalog copied into another database, such as a SQLite catalog into PostgreSQL when a
site moves to it (or back).

Both catalogs are brought to, or must be at, the same revision; the target must be empty. Every table is copied in
the order its foreign keys need, in batches, in one transaction, so a copy that fails leaves the target as empty as
it was. PostgreSQL's sequences are then set past the copied ids, so new rows do not collide with them, and every
table's count is compared. Indexes and scans are files on disk, not in the catalog, and stay where they are.
"""

from collections.abc import Callable

from sqlalchemy import Engine, func, select, text

from . import migrate, models  # noqa: F401 - register tables
from .db import Base

BATCH = 5000


class CopyError(Exception):
    pass


def counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {t.name: conn.scalar(select(func.count()).select_from(t)) for t in Base.metadata.sorted_tables}


def copy(source: Engine, target: Engine, progress: Callable[[str], None] = print) -> dict[str, int]:
    """Copy every row of `source` into the empty catalog `target`; returns each table's count."""
    if source.url == target.url:
        raise CopyError("The source and the target are the same database")
    with source.connect() as conn:
        found, rev = migrate.state(conn)
    if found != "current":
        raise CopyError(f"The source catalog ({migrate.describe(source)}) is not up to date (revision {rev or 'none'}): "
                        "run `pinax db upgrade` on it first")
    applied = migrate.upgrade(target)  # creates the tables of an empty target, or checks one already made
    if applied:
        progress(f"Created the catalog's tables in {migrate.describe(target)}")
    have = {name: n for name, n in counts(target).items() if n}
    if have:
        raise CopyError(f"The target ({migrate.describe(target)}) is not empty: "
                        + ", ".join(f"{name} has {_rows(n)}" for name, n in have.items()))
    copied = {}
    with source.connect() as src, target.begin() as dst:
        for table in Base.metadata.sorted_tables:  # parents before the rows that refer to them
            order = list(table.primary_key.columns)
            rows = src.execution_options(yield_per=BATCH).execute(select(table).order_by(*order)).mappings()
            n = 0
            for batch in rows.partitions():
                dst.execute(table.insert(), [dict(r) for r in batch])
                n += len(batch)
            copied[table.name] = n
            if n:
                progress(f"  {table.name}: {_rows(n)}")
        if dst.dialect.name == "postgresql":
            _advance_sequences(dst)
    if counts(target) != copied or counts(source) != copied:
        raise CopyError("The counts of the source and the target differ after the copy: was Pinax still running?")
    return copied


def _rows(n: int) -> str:
    return f"{n:,} row{'' if n == 1 else 's'}"


def _advance_sequences(conn) -> None:
    """Set each table's id sequence past the ids copied into it."""
    prep = conn.dialect.identifier_preparer
    for table in Base.metadata.sorted_tables:
        pk = list(table.primary_key.columns)
        if len(pk) != 1 or pk[0].autoincrement is False or pk[0].type.python_type is not int:
            continue
        col, name = pk[0].name, prep.quote(table.name)
        conn.execute(text(f"SELECT setval(pg_get_serial_sequence(:table, :col), "
                          f"COALESCE((SELECT MAX({prep.quote(col)}) FROM {name}), 0) + 1, false)"),
                     {"table": name, "col": col})
