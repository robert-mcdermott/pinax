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
from conftest import FILES, ROOT
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


# ---------- History charts: every point against brute force over the scans' files ----------

SECURE = "/research/secure"
T1 = datetime(2026, 7, 1, 8, tzinfo=UTC)
HOUR = timedelta(hours=1)


def _older(files: list[dict], days: int) -> list[dict]:
    return [dict(f, age_days=f["age_days"] + days) for f in files]


SEC_1 = [{"path": "alpha_lab/linked/records.sas7bdat", "size": 4000, "age_days": 200},
         {"path": "alpha_lab/linked/registry.sas7bdat", "size": 100, "age_days": 800}]
FAST_2 = _older(FILES, 30) + [{"path": "gamma_lab/run1/reads.bam", "size": 7000, "age_days": 10}]
FAST_3 = [f for f in _older(FAST_2, 30) if not f["path"].startswith("beta_lab/imaging/")] + [
    {"path": "alpha_lab/projects/2025_new/S3_R1.fastq.gz", "size": 2500, "age_days": 3}]
# Fast is scanned monthly; Secure two hours after Fast, but not in the second month.
SCANS = [("fast", ROOT, T1, FILES), ("secure", SECURE, T1 + 2 * HOUR, SEC_1),
         ("fast", ROOT, T1 + timedelta(days=30), FAST_2), ("fast", ROOT, T1 + timedelta(days=60), FAST_3),
         ("secure", SECURE, T1 + timedelta(days=60) + 2 * HOUR, _older(SEC_1, 60) + [
             {"path": "alpha_lab/linked/new.csv", "size": 50, "age_days": 1}])]


@pytest.fixture
def months(env, tmp_path, monkeypatch):
    """Two filesystems over three months, keeping two snapshots: the Alpha Lab owns a folder on each, the Gamma
    Lab's folder appears in the second month, and a folder is deleted in the third."""
    from fastapi.testclient import TestClient

    from pinax import config
    from pinax.db import session_scope
    from pinax.main import app
    from pinax.models import Filesystem, Lab, LabFolder, LabMembership, User
    from pinax.snapshots import ingest_scan

    monkeypatch.setenv("PINAX_KEEP_SNAPSHOTS", "2")
    config.get_settings.cache_clear()
    with session_scope() as s:
        fss = {slug: Filesystem(slug=slug, name=slug.title(), root=root) for slug, root in (("fast", ROOT), ("secure", SECURE))}
        s.add_all(fss.values())
        s.flush()
        alpha, gamma = Lab(slug="alpha", name="Alpha Lab"), Lab(slug="gamma", name="Gamma Lab")
        s.add_all([alpha, gamma])
        s.flush()
        s.add_all([LabFolder(lab_id=alpha.id, filesystem_id=fss["fast"].id, path="alpha_lab"),
                   LabFolder(lab_id=alpha.id, filesystem_id=fss["secure"].id, path="alpha_lab"),
                   LabFolder(lab_id=gamma.id, filesystem_id=fss["fast"].id, path="gamma_lab")])
        ada = User(username="ada", display_name="Ada", email="ada@example.org")
        s.add_all([ada, User(username="ivy", display_name="Ivy", email="ivy@example.org", is_admin=True)])
        s.flush()
        s.add(LabMembership(user_id=ada.id, lab_id=alpha.id, role="pi"))
    for i, (slug, root, started, files) in enumerate(SCANS):
        write_scan(tmp_path / f"s{i}", root, files, started=started)
        with session_scope() as s:
            ingest_scan(s, tmp_path / f"s{i}", fs_slug=slug, progress=lambda m: None)
    with TestClient(app) as c:
        yield c


def _expect(parts: list[tuple[list[dict], str, float]]) -> dict:
    """Brute force for one point: the files under each prefix ("" for all), with ages `shift` days later."""
    from pinax.categories import bucket_for_days
    from pinax.snapshots import cold_bucket

    below = [(f, shift) for files, prefix, shift in parts for f in files if not prefix or f["path"].startswith(prefix + "/")]
    size = sum(f["size"] for f, _ in below)
    age = [sum(f["size"] for f, _ in below if bucket_for_days(f["age_days"]) == i) for i in range(7)]
    return {"present": bool(below), "bytes": size, "files": len(below), "age_bytes": age,
            "cold_bytes": sum(age[cold_bucket():]),
            "mean_age_days": sum(f["size"] * (f["age_days"] + s) for f, s in below) / size if size else None}


def _check(points: list[dict], expected: list[dict]) -> None:
    assert len(points) == len(expected)
    for got, want in zip(points, expected, strict=True):
        for k, v in want.items():
            assert got[k] == (pytest.approx(v, abs=0.02) if k == "mean_age_days" and v is not None else v), (k, got)


def _history(client, user: str, **params) -> dict:
    client.cookies.set("pinax_user", user)
    r = client.get("/api/history", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_top_level_folder_has_every_scan(months):
    h = _history(months, "ada", fs="fast", path=f"{ROOT}/alpha_lab")
    assert h["kind"] == "folder" and h["lab"]["slug"] == "alpha" and not h["merged"]
    fast = [files for slug, _, _, files in SCANS if slug == "fast"]
    _check(h["points"], [_expect([(files, "alpha_lab", 0)]) for files in fast])  # the first scan's index is pruned
    gamma = _history(months, "ivy", fs="fast", path=f"{ROOT}/gamma_lab")["points"]
    assert [p["present"] for p in gamma] == [False, True, True] and gamma[0]["bytes"] == 0


def test_a_filesystem_has_every_scan(months):
    h = _history(months, "ivy", fs="fast")
    _check(h["points"], [_expect([(files, "", 0)]) for slug, _, _, files in SCANS if slug == "fast"])
    assert h["points"][1]["taken_at"].startswith("2026-07-31")
    # Snapshots ingested before their filesystem totals were recorded: summed from folder history instead.
    from sqlalchemy import select

    from pinax.db import session_scope
    from pinax.models import Snapshot

    with session_scope() as s:
        for snap in s.scalars(select(Snapshot)):
            assert "totals" in snap.info
            snap.info = {k: v for k, v in snap.info.items() if k != "totals"}
    again = _history(months, "ivy", fs="fast")["points"]
    _check(again, [{k: v for k, v in p.items() if k not in ("taken_at", "snapshots")} for p in h["points"]])


def test_a_deeper_folder_has_the_retained_snapshots(months):
    h = _history(months, "ada", fs="fast", path=f"{ROOT}/alpha_lab/projects")
    assert h["kind"] == "subfolder" and h["older_scans"] == 1 and h["keep_snapshots"] == 2
    _check(h["points"], [_expect([(f, "alpha_lab/projects", 0)]) for f in (FAST_2, FAST_3)])
    # Deleted in the last scan, so found through the snapshot before it.
    months.cookies.set("pinax_user", "ivy")
    assert months.get("/api/history", params={"fs": "fast", "path": f"{ROOT}/beta_lab/imaging"}).status_code == 404
    before = _history(months, "ivy", fs="fast")["points"][1]["snapshots"][0]
    gone = _history(months, "ivy", fs="fast", path=f"{ROOT}/beta_lab/imaging", snapshot=before)["points"]
    assert [p["present"] for p in gone] == [True, False] and gone[-1]["mean_age_days"] is None


def test_a_lab_on_two_filesystems_is_summed_by_day(months):
    """A point per day with a scan; each filesystem as of its latest scan by then, its average age carried
    forward to the point's time."""
    h = _history(months, "ada", lab="alpha")
    assert h["kind"] == "lab" and h["merged"] and h["name"] == "Alpha Lab"
    f1, s1, f2, f3, s2 = (files for _, _, _, files in SCANS)
    two_hours, month = 2 / 24, 30.0
    _check(h["points"], [
        _expect([(f1, "alpha_lab", two_hours), (s1, "alpha_lab", 0)]),
        _expect([(f2, "alpha_lab", 0), (s1, "alpha_lab", month - two_hours)]),
        _expect([(f3, "alpha_lab", two_hours), (s2, "alpha_lab", 0)]),
    ])
    assert [len(p["snapshots"]) for p in h["points"]] == [2, 2, 2]
    assert _history(months, "ada")["points"] == h["points"]  # Ada's home is the Alpha Lab's folders


def test_history_follows_access(months):
    months.cookies.set("pinax_user", "ada")
    assert months.get("/api/history", params={"fs": "fast"}).status_code == 404
    assert months.get("/api/history", params={"fs": "fast", "path": f"{ROOT}/gamma_lab"}).status_code == 404
    assert months.get("/api/history", params={"lab": "gamma"}).status_code == 404
    everything = _history(months, "ivy")
    assert everything["name"] == "All data" and len(everything["points"]) == 3
    _check(everything["points"][-1:], [_expect([(SCANS[3][3], "", 2 / 24), (SCANS[4][3], "", 0)])])
