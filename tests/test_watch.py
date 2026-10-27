# SPDX-License-Identifier: Apache-2.0
"""Automatic ingest (S4, O4): every new scan under a folder is ingested once, whatever state fs-inventory left it
in, and what could not be ingested shows on *Scans* with why."""

import json
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import FILES, ROOT
from scanfix import fs_inventory, write_scan
from sqlalchemy import select

from pinax.db import session_scope
from pinax.models import Filesystem, Heartbeat, Snapshot, User
from pinax.snapshots import ingest_scan
from pinax.watch import HEARTBEAT, find_scans, watch, watch_pass


@pytest.fixture
def fs(env):
    with session_scope() as s:
        s.add(Filesystem(slug="fast", name="Fast", root=ROOT))
        s.add(User(username="ivy", display_name="Ivy", email="ivy@example.org", is_admin=True))
    return env


def _scan(folder: Path, name: str, *, day: int, status: str = "complete", root: str = ROOT) -> Path:
    out = folder / name
    write_scan(out, root, FILES, started=datetime(2026, 10, day, tzinfo=UTC), status=status)
    return out


def _rows() -> dict[str, tuple[str, str]]:
    with session_scope() as s:
        return {Path(r.scan_dir).name: (r.status, r.message) for r in s.scalars(select(Snapshot))}


def _pass(folder: Path, **kw):
    return watch_pass(folder, verify=False, progress=lambda m: None, **kw)


def test_finds_scan_folders_without_looking_inside_them(tmp_path):
    root = tmp_path / "scans"
    for rel in ("fast/20261001", "fast/20261002", ".hidden/x", "a/b/c/too_deep"):
        _scan(root, rel, day=1)
    (root / "fast" / "20261002" / "nested").mkdir()
    _scan(root / "fast" / "20261002" / "nested", "inner", day=1)
    assert [p.relative_to(root).as_posix() for p in find_scans(root)] == ["fast/20261001", "fast/20261002"]


def test_new_complete_scans_are_ingested_once_oldest_first(fs, tmp_path):
    folder = tmp_path / "scans"
    _scan(folder, "b_newer", day=5)
    _scan(folder, "a_older", day=2)
    first = _pass(folder)
    assert first.found == 2 and [Path(p).name for p in first.ingested] == ["a_older", "b_newer"]
    second = _pass(folder)
    assert second.found == 2 and not (second.ingested or second.skipped or second.failed)
    assert _rows() == {"a_older": ("ready", ""), "b_newer": ("ready", "")}


def test_unfinished_scans_wait_until_finished(fs, tmp_path):
    folder = tmp_path / "scans"
    running = _scan(folder, "running", day=2, status="running")
    _scan(folder, "interrupted", day=3, status="interrupted")
    res = _pass(folder)
    assert len(res.waiting) == 2 and not res.ingested and _rows() == {}
    manifest = json.loads((running / "manifest.json").read_text())
    (running / "manifest.json").write_text(json.dumps({**manifest, "status": "complete"}))
    assert [Path(p).name for p in _pass(folder).ingested] == ["running"]


def test_incomplete_and_failed_scans_are_skipped_once(fs, tmp_path):
    folder = tmp_path / "scans"
    incomplete = _scan(folder, "incomplete", day=2, status="incomplete")
    _scan(folder, "failed", day=3, status="failed")
    res = _pass(folder)
    assert sorted(Path(p).name for p in res.skipped) == ["failed", "incomplete"]
    rows = _rows()
    assert rows["incomplete"][0] == "skipped" and "--allow-incomplete" in rows["incomplete"][1]
    assert rows["failed"] == ("skipped", "the scan's status is 'failed'")
    assert not _pass(folder).skipped
    # Accepted by hand, a skipped scan is built.
    with session_scope() as s:
        snap = ingest_scan(s, incomplete, allow_incomplete=True, progress=lambda m: None)
        assert snap.status == "ready"


def test_incomplete_scans_can_be_accepted_by_the_watcher(fs, tmp_path):
    folder = tmp_path / "scans"
    _scan(folder, "incomplete", day=2, status="incomplete")
    assert len(_pass(folder, allow_incomplete=True).ingested) == 1


def test_build_failures_are_recorded_and_not_retried(fs, tmp_path):
    from fastapi.testclient import TestClient

    from pinax.main import app

    folder = tmp_path / "scans"
    broken = _scan(folder, "broken", day=2)
    (broken / "entries" / "part-w00-000000.parquet").unlink()
    res = _pass(folder)
    assert [Path(p).name for p in res.failed] == ["broken"]
    status, message = _rows()["broken"]
    assert status == "failed" and "missing" in message
    assert not _pass(folder).failed and _rows()["broken"] == (status, message)
    with TestClient(app) as c:
        c.cookies.set("pinax_user", "ivy")
        shown = [s for s in c.get("/api/admin/snapshots").json() if s["status"] == "failed"]
        assert len(shown) == 1 and "missing" in shown[0]["message"]


def test_refused_scans_are_recorded_too(fs, tmp_path):
    """A scan the ingest refuses before building (here, one claimed for a filesystem with another root)."""
    with session_scope() as s:
        s.add(Filesystem(slug="other", name="Other", root="/research/other"))
    folder = tmp_path / "scans"
    _scan(folder, "elsewhere", day=2, root="/research/other")
    res = _pass(folder, fs_slug="fast")
    assert [Path(p).name for p in res.failed] == ["elsewhere"]
    assert "does not match" in _rows()["elsewhere"][1]


def test_scans_of_unregistered_filesystems_are_left_alone(fs, tmp_path):
    folder = tmp_path / "scans"
    _scan(folder, "unknown", day=2, root="/research/unknown")
    res = _pass(folder)
    assert len(res.unmatched) == 1 and _rows() == {}


def test_two_watchers_at_once_ingest_each_scan_once(fs, tmp_path):
    folder = tmp_path / "scans"
    for i in range(3):
        _scan(folder, f"scan{i}", day=2 + i)
    results, errors = [], []

    def run():
        try:
            results.append(_pass(folder))
        except Exception as e:  # noqa: BLE001 - reported below
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    ingested = [p for r in results for p in r.ingested]
    assert sorted(Path(p).name for p in ingested) == ["scan0", "scan1", "scan2"]
    assert not any(r.failed for r in results)
    assert set(_rows().values()) == {("ready", "")}


def test_heartbeat_shows_on_scans(fs, tmp_path):
    from fastapi.testclient import TestClient

    from pinax.main import app

    folder = tmp_path / "scans"
    _scan(folder, "one", day=2)
    watch(folder, once=True, verify=False, interval=60)
    with session_scope() as s:
        hb = s.get(Heartbeat, HEARTBEAT)
        assert hb.detail["state"] == "idle" and hb.detail["last"]["ingested"] == 1
    with TestClient(app) as c:
        c.cookies.set("pinax_user", "ivy")
        w = c.get("/api/admin/ingest").json()["watch"]
        assert w["folder"] == str(folder.resolve()) and w["interval"] == 60 and not w["stale"]


def test_once_from_the_command_line(fs, tmp_path, monkeypatch):
    from pinax import cli

    folder = tmp_path / "scans"
    _scan(folder, "good", day=2)
    monkeypatch.setattr(sys, "argv", ["pinax", "ingest", "--watch", str(folder), "--once", "--no-verify"])
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == 0
    broken = _scan(folder, "broken", day=3)
    (broken / "entries" / "part-w00-000000.parquet").unlink()
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == 1  # a failure in the pass, for cron or a timer to report


@pytest.mark.skipif(fs_inventory() is None, reason="fs-inventory is not installed (scripts/install-fs-inventory.sh)")
def test_scans_that_fail_verify_are_recorded(fs, tmp_path):
    """`fs-inventory verify` checks what Pinax cannot, such as each part carrying the scan's ID; these test scans
    don't, so verify refuses them."""
    folder = tmp_path / "scans"
    _scan(folder, "unverified", day=2)
    res = watch_pass(folder, progress=lambda m: None)
    assert [Path(p).name for p in res.failed] == ["unverified"]
    assert _rows()["unverified"][1].startswith("fs-inventory verify failed")
