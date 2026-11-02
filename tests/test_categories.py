# SPDX-License-Identifier: Apache-2.0
"""File categories a site defines (wave 14, R14): kept in the catalog, applied when an index is built (extensions,
and patterns on file names, checked first), recorded in the index, and read by every report from each index's own,
so an index built with other categories still answers, and `pinax rebuild --outdated` builds it again."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from conftest import FILES, ROOT, login
from scanfix import write_scan
from sqlalchemy import select

from pinax.categories import DEFAULTS, Category, CategoryError, check, fingerprint, pattern_problem
from pinax.index import SnapshotIndex, Stats
from pinax.ingest.build import BuildError, build_index

# Raw sequencing, by name: `raw_2014.sam` would be sequencing data by its extension, and `run1.bam` too.
RAW = Category("raw", "Raw runs", "Instrument output not yet processed", ("bai",), (r"^raw_", r"\.bam$"))
LATE = Category("late", "Late", "Never reached: the earlier pattern wins", (), (r"^raw_2014",))


def _with(*added: Category) -> tuple[Category, ...]:
    """The built-in categories with `added` before *other*."""
    return (*DEFAULTS[:-1], *added, DEFAULTS[-1])


def _by_name(idx: SnapshotIndex) -> dict[str, str]:
    return {r["name"]: r["category"] for r in idx.files(idx.root, limit=100)}


def _cli(monkeypatch, capsys, *args: str) -> tuple[int, str]:
    from pinax import cli

    monkeypatch.setattr("sys.argv", ["pinax", *args])
    with pytest.raises(SystemExit) as done:
        cli.main()
    out = capsys.readouterr()
    return done.value.code, out.out + out.err


def test_the_catalog_starts_with_the_built_in_categories(client):
    from pinax.categories import current
    from pinax.db import session_scope
    from pinax.models import Snapshot

    with session_scope() as s:
        assert [(c.key, c.extensions) for c in current(s)] == [(c.key, c.extensions) for c in DEFAULTS]
        assert s.scalar(select(Snapshot)).info["categories_id"] == fingerprint(DEFAULTS)
    config = login(client, "ada").get("/api/config").json()
    assert [c["key"] for c in config["categories"]] == [c.key for c in DEFAULTS]
    assert {"cat:genomics", "cat:other"} <= set(config["metrics"])


def test_patterns_come_before_extensions_and_the_first_match_wins(tmp_path, scan):
    build_index(scan, tmp_path / "index", categories=_with(RAW, LATE), progress=lambda m: None)
    idx = SnapshotIndex(tmp_path / "index")
    try:
        got = _by_name(idx)
        assert got["raw_2014.sam"] == "raw"  # its name, before genomics' extension, and before LATE's pattern
        assert got["run1.bam"] == "raw"
        assert got["run1.bam.bai"] == "genomics"  # an extension two categories list is the first's
        assert got["S1_R1.fastq.gz"] == "genomics" and got["README"] == "other"
        assert idx.category_keys == [c.key for c in _with(RAW, LATE)]
        assert idx.categories_id == fingerprint(_with(RAW, LATE))
        stats = Stats.from_row(idx.node(idx.root)).json(idx.ref_day)
        assert stats["cat_bytes"]["raw"] == 9000 + 5000 and "late" not in stats["cat_bytes"]
        assert sum(stats["cat_bytes"].values()) == stats["bytes"]
    finally:
        idx.close()


def test_a_category_the_site_adds(client, monkeypatch, capsys):
    """Indexes built before the change answer with their own categories, a search for the new one finds nothing in
    them, and `pinax rebuild --outdated` builds them with the catalog's."""
    from pinax.categories import current
    from pinax.db import session_scope
    from pinax.models import FileCategory, FolderStat, Snapshot
    from pinax.snapshots import indexes

    with session_scope() as s:
        s.scalar(select(FileCategory).where(FileCategory.key == "other")).position = 12
        s.add(FileCategory(key="raw", label=RAW.label, description=RAW.description, position=11,
                           extensions=list(RAW.extensions), patterns=list(RAW.patterns)))
    c = login(client, "ivy")
    types = c.get("/api/types?fs=fast").json()["categories"]
    assert [t["key"] for t in types][-2:] == ["raw", "other"] and types[-2]["bytes"] == 0
    assert c.get("/api/files?fs=fast&category=raw").json()["files"] == []
    assert c.get("/api/files?fs=fast&category=nonesuch").status_code == 422
    assert c.post("/api/files/jobs?fs=fast&category=nonesuch").status_code == 422  # a search in the background too
    assert c.get("/api/tree?fs=fast&metric=cat:raw").status_code == 200
    assert "cat:raw" in c.get("/api/config").json()["metrics"]

    indexes.clear()  # the command line is a process of its own, which opens the indexes itself
    code, out = _cli(monkeypatch, capsys, "rebuild", "--outdated")
    assert code == 0 and "rebuilt" in out
    assert "is current" in _cli(monkeypatch, capsys, "rebuild", "--outdated")[1]

    types = {t["key"]: t for t in c.get("/api/types?fs=fast").json()["categories"]}
    assert types["raw"]["bytes"] == 9000 + 5000 and types["genomics"]["bytes"] == 50 + 3000 + 2000
    found = {f["name"]: f["category"] for f in c.get("/api/files?fs=fast&category=raw").json()["files"]}
    assert found == {"raw_2014.sam": "raw", "run1.bam": "raw"}
    tree = c.get("/api/tree?fs=fast&metric=cat:raw").json()
    assert tree["node"]["stats"]["cat_bytes"]["raw"] == 14000
    with session_scope() as s:
        snap = s.scalar(select(Snapshot))
        assert snap.info["categories_id"] == fingerprint(current(s))
        alpha = s.scalar(select(FolderStat).where(FolderStat.snapshot_id == snap.id, FolderStat.folder == "alpha_lab"))
        assert alpha.cat_bytes["raw"] == 14000


def test_removed_files_keep_their_categories(tmp_path):
    """Comparing with a previous index built with other categories, its removed files keep theirs."""
    start = datetime(2026, 9, 1, tzinfo=UTC)
    kept = [f for f in FILES if f["path"] != "beta_lab/imaging/plate1.tif" and not f["path"].endswith(".bam")]
    write_scan(tmp_path / "old", ROOT, FILES, started=start)
    write_scan(tmp_path / "new", ROOT, kept, started=start + timedelta(days=30))
    build_index(tmp_path / "old", tmp_path / "old-index", progress=lambda m: None)
    first = Category("first", "First", "Every number after it moves up one", ("csv",))
    build_index(tmp_path / "new", tmp_path / "new-index", previous=tmp_path / "old-index",
                categories=(first, *DEFAULTS), progress=lambda m: None)
    old, new = SnapshotIndex(tmp_path / "old-index"), SnapshotIndex(tmp_path / "new-index")
    try:
        removed = {r["name"]: r["category"] for r in new.file_changes(new.root, old, kind="removed")["rows"]}
        assert removed == {"plate1.tif": "imaging", "run1.bam": "genomics"}
    finally:
        old.close()
        new.close()


def test_an_index_from_before_reads_as_the_defaults(tmp_path, scan):
    """Indexes built before categories were the catalog's recorded only their keys and labels."""
    from pinax.categories import describe

    build_index(scan, tmp_path / "index", progress=lambda m: None)
    before = {k: v for k, v in describe(DEFAULTS).items() if k != "categories_id"}
    before["categories"] = [{k: c[k] for k in ("key", "label", "description")} for c in before["categories"]]
    with duckdb.connect(str(tmp_path / "index" / "index.duckdb")) as con:
        con.execute("UPDATE meta SET value = ? WHERE key = 'dimensions'", [json.dumps(before)])
    idx = SnapshotIndex(tmp_path / "index")
    try:
        assert idx.categories == DEFAULTS and idx.categories_id is None
        assert _by_name(idx)["raw_2014.sam"] == "genomics"
    finally:
        idx.close()


def test_categories_are_checked(tmp_path, scan):
    check(DEFAULTS)
    other = DEFAULTS[-1]
    many = tuple(Category(f"c{i}", f"C{i}", "", ()) for i in range(24))
    patterned = Category("p", "P", "", (), tuple(f"^x{i}" for i in range(26)))
    for cats, says in (((other, DEFAULTS[0]), "last category must be *other*"),
                       ((*many, other), "At most 24 categories"),
                       ((patterned, other), "At most 25 patterns"),
                       ((DEFAULTS[0], DEFAULTS[0], other), "key of its own"),
                       ((Category("p", "P", "", (), ("raw_(",)), other), "not a regular expression")):
        with pytest.raises(CategoryError, match=says.replace("*", r"\*")):
            check(cats)
    assert pattern_problem(r"^Undetermined_.*\.fastq") is None and pattern_problem("(?i)^readme") is None
    assert "regular expression" in pattern_problem(r"(a)\1")  # RE2: no backreferences, so no exponential time
    with pytest.raises(BuildError, match="file categories cannot be used"):
        build_index(scan, tmp_path / "index", categories=(other, DEFAULTS[0]), progress=lambda m: None)
    assert not Path(tmp_path / "index").exists()
