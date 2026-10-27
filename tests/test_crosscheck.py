# SPDX-License-Identifier: Apache-2.0
"""Every directory's totals against `fs-inventory rollup`, an independent implementation (requirement Q1).

The scan here is built to sit on the edges: files exactly on every age bucket edge and one second either
side, a file newer than the scan, a scan start that rounding trips on, a symlink, an empty directory, an
unreadable one, a mount point not entered, a revisited directory, and a deep path.
"""

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from scanfix import fs_inventory, write_scan

from pinax.categories import bucket_edges
from pinax.crosscheck import check, rollup_edges
from pinax.ingest.build import build_index

ROOT = "/research/edge"
# A scan start at which fractional-day arithmetic (Pinax 0.1.0) put files exactly on every edge in the
# younger bucket.
STARTED = datetime(2026, 10, 1, 13, 37, 41, 124003, tzinfo=UTC)
SECOND = 1 / 86400
EDGES = bucket_edges()


def _files() -> list[dict]:
    files = []
    for i, edge in enumerate(EDGES):
        for name, days in (("before", edge - SECOND), ("on", edge), ("after", edge + SECOND)):
            files.append({"path": f"lab/edges/e{i}_{name}.dat", "size": 100 * (i + 1), "age_days": days,
                          "atime_days": days})
    files += [
        {"path": "lab/edges/future.dat", "size": 7, "age_days": -2, "atime_days": -2},
        {"path": "lab/edges/now.dat", "size": 9, "age_days": 0},
        {"path": "lab/run/reads.fastq.gz", "size": 5000, "age_days": 400, "atime_days": 3},
        {"path": "lab/run/link", "size": 20, "age_days": 30, "kind": "symlink"},
        {"path": "lab/a/b/c/d/e/f/g/deep.txt", "size": 1, "age_days": 5000},
        {"path": "other/table.csv", "size": 300, "age_days": 1000},
        {"path": "root_file.txt", "size": 2, "age_days": 1},
    ]
    return files


@pytest.fixture
def edge_scan(tmp_path) -> Path:
    out = tmp_path / "scan"
    write_scan(out, ROOT, _files(), started=STARTED, errors={"lab/locked": "open: EACCES"},
               dirs_only=["lab/empty", "lab/locked", "lab/mnt", "lab/loop"], mounts={"lab/mnt"}, revisits={"lab/loop"})
    return out


@pytest.fixture
def edge_index(edge_scan, tmp_path) -> Path:
    out = tmp_path / "index"
    build_index(edge_scan, out, memory_limit="1GB", threads=2, progress=lambda m: None)
    return out


def _dir(index: Path, rel: str) -> dict:
    with duckdb.connect(str(index / "index.duckdb"), read_only=True) as con:
        cur = con.execute("SELECT * EXCLUDE (self_mtime, mtime_min, mtime_max, atime_max, ctime_max, d_mtime_min, "
                          "d_mtime_max, d_atime_max) FROM dirs WHERE path = ?", [f"{ROOT}/{rel}".rstrip("/")])
        return dict(zip([d[0] for d in cur.description], cur.fetchone(), strict=True))


def test_rollup_edges_are_pinax_buckets():
    assert rollup_edges() == "90d,1y,2y,3y,5y,10y"


def test_bucket_edges_are_exact(edge_index):
    """A file exactly on an edge is in the older bucket; one second younger is in the younger one."""
    d = _dir(edge_index, "lab/edges")
    expect = [0] * (len(EDGES) + 1)
    expect[0] += 2  # newer than the scan, and age 0
    for i in range(len(EDGES)):
        expect[i] += 1
        expect[i + 1] += 2
    assert [d[f"mf{i}"] for i in range(len(expect))] == expect
    assert d["mb1"] == 100 * 2 + 200  # on and after the 90-day edge, before the 1-year edge
    assert [d[f"ab{i}"] for i in range(len(expect))] == [d[f"mb{i}"] for i in range(len(expect))]


def test_incomplete_directories(edge_index):
    """Unreadable and unentered directories make totals lower bounds; a revisit doesn't."""
    assert _dir(edge_index, "lab/locked")["incomplete"] == 1
    assert _dir(edge_index, "lab/mnt")["incomplete"] == 1
    assert _dir(edge_index, "lab/loop")["incomplete"] == 0
    assert _dir(edge_index, "lab")["incomplete"] == 2
    assert _dir(edge_index, "")["incomplete"] == 2


needs_binary = pytest.mark.skipif(fs_inventory() is None,
                                  reason="fs-inventory is not installed (scripts/install-fs-inventory.sh)")


@needs_binary
def test_index_matches_rollup(edge_scan, edge_index, tmp_path):
    report = check(edge_scan, edge_index, tmp_path / "work", binary=fs_inventory())
    assert report.ok, report.text()
    assert report.dirs == 16 and len(report.measures) == 31
    assert not (edge_scan / "rollup").exists() and "rollup" not in (edge_scan / "manifest.json").read_text()


@needs_binary
def test_check_reports_differences(edge_scan, edge_index, tmp_path):
    with duckdb.connect(str(edge_index / "index.duckdb")) as con:
        con.execute("UPDATE dirs SET bytes = bytes + 1, mf3 = mf3 + 1 WHERE path = ?", [f"{ROOT}/lab/run"])
        con.execute("DELETE FROM dirs WHERE path = ?", [f"{ROOT}/lab/empty"])
    report = check(edge_scan, edge_index, tmp_path / "work", binary=fs_inventory())
    assert not report.ok
    assert report.only_rollup == [f"{ROOT}/lab/empty"] and report.only_pinax_count == 0
    differ = {m.measure: m for m in report.mismatches}
    assert set(differ) == {"bytes", "mtime_files[2-3y]"}
    assert differ["bytes"].examples == [{"path": f"{ROOT}/lab/run", "pinax": 5021, "rollup": 5020}]
    assert "MISMATCH" in report.text() and f"{ROOT}/lab/run: Pinax 5,021, rollup 5,020" in report.text()


@needs_binary
def test_check_command(env, edge_scan, monkeypatch, capsys):
    from pinax import cli

    monkeypatch.setattr(sys, "argv", ["pinax", "check", str(edge_scan)])
    with pytest.raises(SystemExit) as exit_:
        cli.main()
    assert exit_.value.code == 0, capsys.readouterr()
    assert capsys.readouterr().out.startswith("OK: 16 directories, 31 measures each")
    assert not list(env.var_root.glob("check-*"))


@needs_binary
def test_synthetic_scan_matches_rollup(tmp_path):
    """The generator behind the 1- and 10-million-file checks, at a small size."""
    out = tmp_path / "synth"
    script = Path(__file__).parents[1] / "bench" / "synth_scan.py"
    subprocess.run([sys.executable, str(script), "--files", "6000", "--tops", "3", "-o", str(out)], check=True,
                   capture_output=True)
    build_index(out, tmp_path / "index", memory_limit="1GB", threads=2, progress=lambda m: None)
    report = check(out, tmp_path / "index", tmp_path / "work", binary=fs_inventory())
    assert report.ok, report.text()
    assert report.dirs == 1 + 3 + 3 * 20 + 3 * 200 + 3 * 1000
