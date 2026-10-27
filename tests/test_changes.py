# SPDX-License-Identifier: Apache-2.0
"""The change between two scans (S9): every directory's totals in each scan, new and removed folders, checked
against brute force over the two file lists and, with the binary, against the two scans' fs-inventory rollups."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from conftest import FILES, ROOT
from scanfix import fs_inventory, write_scan

from pinax.categories import bucket_for_days
from pinax.crosscheck import check_changes
from pinax.ingest.build import CHANGE_MEASURES, build_index

OLD_START = datetime(2026, 9, 1, tzinfo=UTC)
NEW_START = OLD_START + timedelta(days=30)


def _new_files() -> list[dict]:
    """A month later: 2015_old deleted, a file added to 2025_new, a plate removed, a new top-level folder, and
    everything kept unchanged (so a month older)."""
    kept = [dict(f, age_days=f["age_days"] + 30) for f in FILES
            if not f["path"].startswith("alpha_lab/projects/2015_old/") and f["path"] != "beta_lab/imaging/plate1.tif"]
    return kept + [
        {"path": "alpha_lab/projects/2025_new/S3_R1.fastq.gz", "size": 4000, "age_days": 2},
        {"path": "gamma_lab/run1/reads.bam", "size": 7000, "age_days": 10},
    ]


@pytest.fixture
def scans(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    write_scan(old, ROOT, FILES, started=OLD_START, errors={"beta_lab/locked": "open: EACCES"})
    write_scan(new, ROOT, _new_files(), started=NEW_START, errors={"beta_lab/locked": "open: EACCES"})
    build_index(old, tmp_path / "old-index", progress=lambda m: None)
    r = build_index(new, tmp_path / "new-index", previous=tmp_path / "old-index", progress=lambda m: None)
    return old, new, tmp_path / "new-index", r


def _brute(files: list[dict]) -> dict[str, dict]:
    dirs = {""} | {"/".join(f["path"].split("/")[:i]) for f in files for i in range(1, f["path"].count("/") + 1)}
    out = {}
    for d in dirs:
        below = [f for f in files if not d or f["path"].startswith(d + "/")]
        row = {"files": len(below), "bytes": sum(f["size"] for f in below), "alloc": sum(f["size"] for f in below),
               "dirs": sum(1 for x in dirs if x != d and (not d or x.startswith(d + "/")))}
        for i in range(7):
            row[f"mf{i}"] = sum(1 for f in below if bucket_for_days(f["age_days"]) == i)
            row[f"mb{i}"] = sum(f["size"] for f in below if bucket_for_days(f["age_days"]) == i)
        out[f"{ROOT}/{d}" if d else ROOT] = row
    return out


def test_every_directory_matches_brute_force(scans):
    _, _, index, result = scans
    old, new = _brute(FILES), _brute(_new_files())
    with duckdb.connect(str(index / "index.duckdb"), read_only=True) as con:
        cur = con.execute("SELECT * FROM changes")
        names = [d[0] for d in cur.description]
        rows = {r["path"]: r for r in (dict(zip(names, row, strict=True)) for row in cur.fetchall())}
    assert set(rows) == set(old) | set(new)
    for path, r in rows.items():
        assert (r["in_old"], r["in_new"]) == (path in old, path in new), path
        for side, brute in (("o", old), ("n", new)):
            want = brute.get(path)
            got = {c: r[f"{side}_{c}"] for c in CHANGE_MEASURES if c != "m_wsum"}
            assert got == (want or dict.fromkeys(got)), (path, side)
    gone = {p for p, r in rows.items() if not r["in_new"]}
    assert gone == {f"{ROOT}/alpha_lab/projects/2015_old", f"{ROOT}/alpha_lab/projects/2015_old/reads"}
    born = {p for p, r in rows.items() if not r["in_old"]}
    assert born == {f"{ROOT}/gamma_lab", f"{ROOT}/gamma_lab/run1"}
    assert result.compared["new_dirs"] == 2 and result.compared["removed_dirs"] == 2


def test_an_index_says_what_it_was_compared_with(scans, tmp_path):
    from pinax.index import SnapshotIndex

    idx = SnapshotIndex(scans[2])
    try:
        assert idx.has_changes and idx.previous["started_at"].startswith("2026-09-01")
    finally:
        idx.close()
    first = SnapshotIndex(tmp_path / "old-index")
    try:
        assert not first.has_changes and first.previous is None
    finally:
        first.close()


def test_ingest_compares_with_the_previous_snapshot(env, tmp_path):
    from pinax.db import session_scope
    from pinax.models import Filesystem
    from pinax.snapshots import ingest_scan, rebuild_snapshot

    write_scan(tmp_path / "s1", ROOT, FILES, started=OLD_START)
    write_scan(tmp_path / "s2", ROOT, _new_files(), started=NEW_START)
    write_scan(tmp_path / "s0", ROOT, FILES, started=OLD_START - timedelta(days=30))
    with session_scope() as s:
        s.add(Filesystem(slug="fast", name="Fast", root=ROOT))
    with session_scope() as s:
        first = ingest_scan(s, tmp_path / "s1", progress=lambda m: None)
        second = ingest_scan(s, tmp_path / "s2", progress=lambda m: None)
        earlier = ingest_scan(s, tmp_path / "s0", progress=lambda m: None)  # found late: nothing before it
        assert first.info["compared_with"] is None and second.info["compared_with"] == first.id
        assert earlier.info["compared_with"] is None
        rebuild_snapshot(s, second, progress=lambda m: None)
        assert second.info["compared_with"] == first.id


needs_binary = pytest.mark.skipif(fs_inventory() is None,
                                  reason="fs-inventory is not installed (scripts/install-fs-inventory.sh)")


@needs_binary
def test_changes_equal_the_difference_of_two_rollups(scans, tmp_path):
    old, new, index, _ = scans
    report = check_changes(index, new, old, tmp_path / "work", binary=fs_inventory())
    assert report.ok, report.text()
    assert report.dirs == len(set(_brute(FILES)) | set(_brute(_new_files())))


@needs_binary
def test_the_change_check_catches_a_wrong_difference(scans, tmp_path):
    old, new, index, _ = scans
    with duckdb.connect(str(index / "index.duckdb")) as con:
        con.execute("UPDATE changes SET n_bytes = n_bytes + 1, in_old = false WHERE path = ?",
                    [f"{ROOT}/alpha_lab/projects/2025_new"])
    report = check_changes(index, new, old, tmp_path / "work", binary=fs_inventory())
    differ = {m.measure for m in report.mismatches}
    assert not report.ok and {"bytes (this scan)", "in the previous scan"} <= differ, report.text()


@needs_binary
def test_check_command_checks_the_changes_too(env, tmp_path, monkeypatch, capsys):
    import sys

    from pinax import cli
    from pinax.db import session_scope
    from pinax.models import Filesystem
    from pinax.snapshots import ingest_scan

    write_scan(tmp_path / "s1", ROOT, FILES, started=OLD_START)
    write_scan(tmp_path / "s2", ROOT, _new_files(), started=NEW_START)
    with session_scope() as s:
        s.add(Filesystem(slug="fast", name="Fast", root=ROOT))
    with session_scope() as s:
        ingest_scan(s, tmp_path / "s1", progress=lambda m: None)
        second = ingest_scan(s, tmp_path / "s2", progress=lambda m: None).id
    monkeypatch.setattr(sys, "argv", ["pinax", "check", str(second)])
    with pytest.raises(SystemExit) as done:
        cli.main()
    out = capsys.readouterr().out
    assert done.value.code == 0, out
    assert "Changes since the previous scan:\nOK:" in out
    Path(tmp_path / "s1" / "manifest.json").unlink()  # the previous scan is gone: the changes cannot be checked
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == 0 and "were not checked" in capsys.readouterr().out
