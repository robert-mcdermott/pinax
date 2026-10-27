# SPDX-License-Identifier: Apache-2.0
"""A history to show: the demo's six months of scans, and `synth_scan.py --evolve`, which derives a changed scan
from another; the changes between evolved scans are checked against brute force and, with the binary, against
fs-inventory rollup."""

import json
import random
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from conftest import ROOT
from scanfix import fs_inventory, write_scan

from pinax import seed
from pinax.crosscheck import check, check_changes
from pinax.ingest.build import build_index

SYNTH = Path(__file__).parents[1] / "bench" / "synth_scan.py"
DAY = 86400


def test_the_demo_months_tell_their_stories():
    """Without writing anything: the files of each month are dated within it, nothing is dated after its scan,
    no file is written twice, and the cleanups, the move, and the cold threshold find what they expect."""
    rng = random.Random(1)
    now = datetime(2026, 10, 27, tzinfo=UTC).timestamp()
    taken = [now - (seed.SCANS - n) * seed.MONTH for n in range(1, seed.SCANS + 1)]
    base = seed.build_trees(rng, taken[0], 0.2)
    months = {n: seed.month_files(rng, taken[n - 1], 0.2, n) for n in range(2, seed.SCANS + 1)}
    first = [f for t in base.values() for f in t.files]
    assert first and all(f.mtime <= taken[0] and f.atime <= taken[0] for f in first)
    paths = [(fs, f.path) for fs, t in base.items() for f in t.files]
    joined = {n: slug for slug, n in seed.JOINS.items()}
    for n, trees in months.items():
        for fs, t in trees.items():
            assert all(f.mtime <= taken[n - 1] and f.atime <= taken[n - 1] for f in t.files), (n, fs)
            # Only a lab that joins brings older files: everything else was written during the month.
            assert all(f.mtime > taken[n - 2] for f in t.files if not f.path.startswith(f"{joined.get(n)}/")), (n, fs)
            paths += [(fs, f.path) for f in t.files]
    assert len(paths) == len(set(paths))
    assert not any(p.startswith(f"{slug}/") for slug in seed.JOINS for _, p in paths[:len(first)])
    assert any(p.startswith("quintero_v/") for t in months[seed.JOINS["quintero_v"]].values() for p in
               (f.path for f in t.files))

    def under(fs: str, rel: str) -> list:
        return [f for f in base[fs].files if f.path.startswith(rel + "/")]

    for fs, rel in [r for rs in seed.REMOVED.values() for r in rs]:
        assert under(fs, rel), rel
    for n, moves in seed.MOVED.items():
        for fs, src, _dst in moves:
            assert under(fs, src), src
            assert not any(f.path.startswith(src + "/") for m in range(n, seed.SCANS + 1) for f in months[m][fs].files)
    for fs, src, _dst in [c for cs in seed.COMPRESSED.values() for c in cs]:
        assert under(fs, src) and all(f.path.endswith(".fastq") for f in under(fs, src))
    # Not yet cold at the first scan, all past two years at the last.
    tmt = under("fast", "kowalski_d/raw/2024_tmt_plex")
    assert tmt and max((taken[0] - f.mtime) / DAY for f in tmt) < 730 < min((taken[-1] - f.mtime) / DAY for f in tmt)


def test_a_demo_scan_is_dated_by_the_seed(tmp_path):
    """The seed dates each scan a month after the last; the times the seed cannot set, here a directory and a
    file changed after that date, are brought back to it, and ages are measured from it."""
    taken = datetime(2026, 7, 1, tzinfo=UTC)
    out = tmp_path / "scan"
    files = [{"path": "chen_l/a.csv", "size": 10, "age_days": 40}, {"path": "chen_l/b.csv", "size": 20, "age_days": 3}]
    write_scan(out, ROOT, files, started=taken + timedelta(days=10))
    seed._make_demo_scan(out, "fast", ROOT, {"fast/chen_l": [(20001, "lchen")]}, {"chen_l": 30002}, taken.timestamp())
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["started_at"] == "2026-07-01T00:00:00Z" and (out / "DEMO.txt").exists()
    with duckdb.connect() as con:
        con.execute("SET TimeZone = 'UTC'")
        rows = con.execute(f"SELECT name, epoch(mtime), uid FROM read_parquet('{out}/entries/*.parquet')").fetchall()
    got = {name: mtime for name, mtime, _ in rows}
    assert {uid for name, _, uid in rows if name.endswith(".csv")} == {20001}
    assert got["a.csv"] == (taken - timedelta(days=30)).timestamp()  # before the date: unchanged
    assert got["b.csv"] == got["chen_l"] == taken.timestamp()  # after it: set to it
    build_index(out, tmp_path / "index", progress=lambda m: None)
    with duckdb.connect(str(tmp_path / "index" / "index.duckdb"), read_only=True) as con:
        buckets = con.execute(f"SELECT mf0, mf1 FROM dirs WHERE path = '{ROOT}/chen_l'").fetchone()
    assert buckets == (2, 0)  # both under 90 days old on 1 July


def _synth(out: Path, *args: str) -> dict:
    subprocess.run([sys.executable, str(SYNTH), *args, "-o", str(out)], check=True, capture_output=True)
    return json.loads((out / "manifest.json").read_text())


@pytest.fixture
def evolved(tmp_path):
    """A small synthetic scan (2 labs, 2,443 folders, 4,000 files) and the same 30 days later, with high rates so
    every kind of change happens."""
    old, new = tmp_path / "old", tmp_path / "new"
    _synth(old, "--files", "4000", "--tops", "2")
    _synth(new, "--evolve", str(old), "--days", "30", "--remove", "0.1", "--grow", "0.2", "--touch", "0.05")
    build_index(old, tmp_path / "old-index", progress=lambda m: None)
    build_index(new, tmp_path / "new-index", previous=tmp_path / "old-index", progress=lambda m: None)
    return old, new, tmp_path / "new-index"


def _entries(scan: Path) -> str:
    return f"read_parquet('{scan}/entries/*.parquet')"


def _brute(con: duckdb.DuckDBPyConnection, scan: Path) -> dict[str, tuple[int, int, int]]:
    """Per directory, its files, bytes, and folders below it, by matching path prefixes."""
    rows = con.execute(f"""
        WITH e AS (SELECT path, kind, size FROM {_entries(scan)}), d AS (SELECT path FROM e WHERE kind = 'dir')
        SELECT d.path, count(x.path) FILTER (WHERE x.kind = 'file'), coalesce(sum(x.size) FILTER (WHERE x.kind = 'file'), 0),
               count(x.path) FILTER (WHERE x.kind = 'dir')
        FROM d LEFT JOIN e x ON starts_with(x.path, d.path || '/') GROUP BY d.path""").fetchall()
    return {p: (f, b, n) for p, f, b, n in rows}


def test_evolved_scan_changes_match_brute_force(evolved):
    old, new, index = evolved
    m_old, m_new = (json.loads((s / "manifest.json").read_text()) for s in (old, new))
    summary = m_new["evolved"]
    assert m_new["evolved_from"] == m_old["scan_id"] and m_new["generation"] == 1
    assert (datetime.fromisoformat(m_new["started_at"]) - datetime.fromisoformat(m_old["started_at"])).days == 30
    assert summary["removed_projects"] and summary["added_files"] and summary["touched_files"]
    assert summary["new_tops"] == ["/synth/research/new_lab_g1_1"]
    with duckdb.connect() as con:
        before, after = _brute(con, old), _brute(con, new)
        with duckdb.connect(str(index / "index.duckdb"), read_only=True) as idx:
            rows = idx.execute("SELECT path, in_old, in_new, o_files, o_bytes, o_dirs, n_files, n_bytes, n_dirs "
                               "FROM changes").fetchall()
        assert {r[0] for r in rows} == set(before) | set(after)
        for path, in_old, in_new, *totals in rows:
            assert (in_old, in_new) == (path in before, path in after), path
            assert tuple(totals[:3]) == (before.get(path) or (None,) * 3), path
            assert tuple(totals[3:]) == (after.get(path) or (None,) * 3), path
        # What it said it did: projects gone, a new lab, new files named apart, the rest only a month older.
        gone = {p for p in before if p not in after}
        assert sum(1 for p in gone if p.count("/") == 4) == summary["removed_projects"]
        assert {p for p in after if p not in before and p.count("/") == 3} == set(summary["new_tops"])
        added = con.execute(f"SELECT count(*) FROM {_entries(new)} WHERE name LIKE 'g1\\_%' ESCAPE '\\'").fetchone()[0]
        assert added == summary["added_files"]
        same, rewritten = con.execute(f"""
            SELECT count(*) FILTER (WHERE o.mtime = n.mtime AND o.size = n.size),
                   count(*) FILTER (WHERE o.mtime <> n.mtime AND n.mtime > TIMESTAMPTZ '{m_old["started_at"]}')
            FROM {_entries(old)} o JOIN {_entries(new)} n USING (path) WHERE o.kind = 'file'""").fetchone()
        assert rewritten == summary["touched_files"] and same > 0.8 * 4000


def test_a_scan_can_be_evolved_again(evolved, tmp_path):
    _, new, index = evolved
    again = _synth(tmp_path / "again", "--evolve", str(new), "--remove", "0.1", "--grow", "0.2")
    assert again["generation"] == 2 and again["evolved"]["new_tops"] == ["/synth/research/new_lab_g2_1"]
    r = build_index(tmp_path / "again", tmp_path / "again-index", previous=index, progress=lambda m: None)
    assert r.compared["new_dirs"] and r.compared["removed_dirs"]


@pytest.mark.skipif(fs_inventory() is None, reason="fs-inventory is not installed (scripts/install-fs-inventory.sh)")
def test_evolved_scans_match_rollups(evolved, tmp_path):
    """Both scans' totals and the change between them equal fs-inventory rollup's, on every directory."""
    old, new, index = evolved
    report = check(new, index, tmp_path / "work", binary=fs_inventory())
    assert report.ok, report.text()
    report = check_changes(index, new, old, tmp_path / "work", binary=fs_inventory())
    assert report.ok, report.text()
