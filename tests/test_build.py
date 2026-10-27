# SPDX-License-Identifier: Apache-2.0
"""The index builder: subtree totals, file types, top-level folders, and refusing bad scans."""

import json

import duckdb
import pytest
from conftest import FILES, ROOT

from pinax.categories import CATEGORY_KEYS, bucket_for_days
from pinax.ingest.build import BuildError, build_index


@pytest.fixture
def index(tmp_path, scan):
    out = tmp_path / "index"
    build_index(scan, out, progress=lambda m: None)
    con = duckdb.connect(str(out / "index.duckdb"), read_only=True)
    yield out, con
    con.close()


def expected(prefix: str):
    rel = prefix[len(ROOT) + 1:] if prefix != ROOT else ""
    sel = [f for f in FILES if not rel or f["path"].startswith(rel + "/")]
    return sel


def test_subtree_totals_match_brute_force(index):
    _, con = index
    rows = con.execute("SELECT path, files, bytes, " + ", ".join(f"mb{i}" for i in range(7)) + ", "
                       + ", ".join(f"cb{i}" for i in range(len(CATEGORY_KEYS))) + " FROM dirs").fetchall()
    assert rows
    for path, files, nbytes, *rest in rows:
        sel = expected(path)
        assert files == len(sel), path
        assert nbytes == sum(f["size"] for f in sel), path
        ages = rest[:7]
        for i in range(7):
            assert ages[i] == sum(f["size"] for f in sel if bucket_for_days(f["age_days"]) == i), (path, i)


def test_file_types_and_categories(index):
    out, _ = index
    con = duckdb.connect()
    rows = dict(con.execute(f"SELECT name, ext || ':' || cat FROM read_parquet('{out}/files/*/*.parquet')").fetchall())
    cat = lambda key: str(CATEGORY_KEYS.index(key))
    assert rows["S1_R1.fastq.gz"] == "fastq.gz:" + cat("genomics")
    assert rows["run1.bam.bai"] == "bai:" + cat("genomics")
    assert rows["brain.nii.gz"] == "nii.gz:" + cat("imaging")
    assert rows["backup.tar.gz"] == "tar.gz:" + cat("compressed")
    assert rows["raw_2014.sam"] == "sam:" + cat("genomics")
    assert con.execute(f"SELECT ext, cat FROM read_parquet('{out}/files/*/*.parquet') WHERE name = 'README'").fetchone() \
        == (None, CATEGORY_KEYS.index("other"))


def test_files_are_split_by_top_folder_and_sorted(index):
    out, con = index
    tops = dict(con.execute("SELECT name, top_id FROM tops").fetchall())
    assert set(tops) == {"alpha_lab", "alpha_lab2", "beta_lab", "orphans"}
    alpha = sorted((out / "files" / f"t{tops['alpha_lab']:06d}").glob("*.parquet"))
    paths = [r[0] for r in duckdb.connect().execute(f"SELECT path FROM read_parquet('{alpha[0]}')").fetchall()]
    assert paths == sorted(paths) and all(p.startswith(ROOT + "/alpha_lab/") for p in paths)
    # A file directly in the root belongs to no top-level folder.
    root_files = duckdb.connect().execute(f"SELECT name FROM read_parquet('{out}/files/t000000/*.parquet')").fetchall()
    assert root_files == [("root_file.txt",)]


def test_unreadable_directories_are_counted_up_the_tree(index):
    _, con = index
    inc = dict(con.execute("SELECT path, incomplete FROM dirs").fetchall())
    assert inc[ROOT + "/beta_lab/locked"] == 1
    assert inc[ROOT + "/beta_lab"] == 1 and inc[ROOT] == 1 and inc[ROOT + "/alpha_lab"] == 0
    assert con.execute("SELECT count(*) FROM errors").fetchone()[0] == 1


def test_owner_names_come_from_the_scan(index):
    _, con = index
    assert dict(con.execute("SELECT uid, name FROM users").fetchall())[2001] == "alice"
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    assert json.loads(meta["root"]) == ROOT


def test_refuses_incomplete_and_foreign_scans(tmp_path, scan):
    m = json.loads((scan / "manifest.json").read_text())
    m["status"] = "interrupted"
    (scan / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(BuildError, match="not complete"):
        build_index(scan, tmp_path / "x", progress=lambda _: None)
    m["status"] = "complete"
    m["roots"].append({"path": "/elsewhere"})
    (scan / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(BuildError, match="2 roots"):
        build_index(scan, tmp_path / "x", progress=lambda _: None)
    m["roots"].pop()
    m["format"] = "csv"
    (scan / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(BuildError, match="Parquet"):
        build_index(scan, tmp_path / "x", progress=lambda _: None)


def test_reads_only_the_parts_the_manifest_lists(tmp_path, scan):
    """A scan folder can hold leftovers of an interrupted run; they are not part of the scan."""
    clean = build_index(scan, tmp_path / "clean", progress=lambda m: None).totals
    part = scan / "entries" / "part-w00-000000.parquet"
    (scan / "entries" / "part-w03-000007.parquet").write_bytes(part.read_bytes())
    assert build_index(scan, tmp_path / "leftover", progress=lambda m: None).totals == clean
    part.unlink()
    with pytest.raises(BuildError, match="missing"):
        build_index(scan, tmp_path / "missing", progress=lambda m: None)


def test_nanosecond_times_are_truncated_to_microseconds(tmp_path):
    """`--time-unit ns` scans: times are kept as microseconds, truncated toward zero as fs-inventory rollup
    does, including before 1970."""
    from datetime import UTC, datetime

    from scanfix import write_scan

    started = datetime(2026, 10, 1, tzinfo=UTC)
    files = [{"path": "lab/new.dat", "size": 1, "age_days": 10, "extra_ns": 999},
             {"path": "old/pre1970.dat", "size": 1, "age_days": 21000, "extra_ns": 999}]
    write_scan(tmp_path / "scan", "/research/ns", files, started=started, time_unit="ns")
    build_index(tmp_path / "scan", tmp_path / "index", progress=lambda m: None)
    us = round(started.timestamp() * 1_000_000)
    with duckdb.connect(str(tmp_path / "index" / "index.duckdb"), read_only=True) as con:
        got = dict(con.execute("SELECT name, epoch_us(mtime_max) FROM dirs WHERE depth = 1").fetchall())
    def toward_zero(ns: int) -> int:
        return ns // 1000 if ns >= 0 else -(-ns // 1000)

    # 999 ns after a whole microsecond: the same microsecond after 1970, the next one toward 1970 before it.
    new, old = us - 10 * 86_400_000_000, us - 21000 * 86_400_000_000
    assert got == {"lab": toward_zero(new * 1000 + 999), "old": toward_zero(old * 1000 + 999)}
    assert got == {"lab": new, "old": old + 1}


def test_every_extension_has_one_category():
    from collections import Counter

    from pinax.categories import CATEGORIES, extension_map

    counts = Counter(e for c in CATEGORIES for e in c.extensions)
    assert [e for e, n in counts.items() if n > 1] == []
    keys = [c.key for c in CATEGORIES]
    m = extension_map()
    # Types found on a real disk that 0.1.0 counted as "other".
    assert {e: keys[m[e]] for e in ("heic", "xlsm", "vsdx", "insv", "exe", "asif")} == {
        "heic": "imaging", "xlsm": "tabular", "vsdx": "documents", "insv": "media", "exe": "code",
        "asif": "containers"}
