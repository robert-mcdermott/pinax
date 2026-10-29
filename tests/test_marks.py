# SPDX-License-Identifier: Apache-2.0
"""Marked folders (S10): followed from scan to scan by path, and by inode when renamed or moved; a reused inode is
only offered as a move; an older scan found late changes nothing; history outlasts the snapshots' indexes."""

from datetime import UTC, datetime, timedelta

import pytest
from conftest import FILES, ROOT
from scanfix import write_scan

from pinax.marks import GONE, HERE, MOVED

START = datetime(2026, 9, 1, tzinfo=UTC)
INODES = {"alpha_lab/projects/2015_old": 501, "beta_lab/imaging": 502}


def _later() -> list[dict]:
    """A month on: 2015_old renamed 2015_archive; beta_lab/imaging deleted, and a new folder given its inode;
    2025_new loses its legacy SAM; the archive is unchanged."""
    out = []
    for f in FILES:
        p = f["path"]
        if p.startswith(("beta_lab/imaging/", "alpha_lab/projects/2025_new/legacy/")):
            continue
        out.append(dict(f, path=p.replace("2015_old/", "2015_archive/")))
    return out + [{"path": "beta_lab/new_imaging/plate9.tif", "size": 77, "age_days": 1}]


@pytest.fixture
def fast(env, tmp_path):
    from pinax.db import session_scope
    from pinax.models import Filesystem
    from pinax.snapshots import ingest_scan

    with session_scope() as s:
        s.add(Filesystem(slug="fast", name="Fast", root=ROOT))
    write_scan(tmp_path / "s0", ROOT, FILES, started=START, inodes=INODES)
    with session_scope() as s:
        ingest_scan(s, tmp_path / "s0", progress=lambda m: None)
    return tmp_path


def _ingest(tmp_path, name: str, files: list[dict], days: int, inodes: dict):
    from pinax.db import session_scope
    from pinax.snapshots import ingest_scan

    write_scan(tmp_path / name, ROOT, files, started=START + timedelta(days=days), inodes=inodes)
    with session_scope() as s:
        return ingest_scan(s, tmp_path / name, progress=lambda m: None).id


def _mark(*rels: str) -> list[int]:
    from sqlalchemy import select

    from pinax import marks
    from pinax.db import session_scope
    from pinax.models import Filesystem
    from pinax.snapshots import cold_bucket

    with session_scope() as s:
        fs = s.scalar(select(Filesystem))
        return [marks.mark_folder(s, fs, f"{ROOT}/{r}", cold_from=cold_bucket()).id for r in rels]


def _marks() -> dict[str, dict]:
    from sqlalchemy import select

    from pinax import marks
    from pinax.db import session_scope
    from pinax.models import MarkedFolder

    with session_scope() as s:
        return {m.path.removeprefix(ROOT + "/"): {"id": m.id, "status": m.status, "moved_to": m.moved_to, "ino": m.ino,
                                                  "history": marks.history(s, m)}
                for m in s.scalars(select(MarkedFolder))}


def test_the_index_keeps_each_directorys_inode(fast):
    from pinax.index import SnapshotIndex
    from pinax.ingest.build import INDEX_VERSION

    assert INDEX_VERSION >= 6  # directories keep their inodes from version 6
    idx = SnapshotIndex(_index_dir())
    try:
        assert idx.has_inodes
        rows = idx.nodes([f"{ROOT}/alpha_lab/projects/2015_old", f"{ROOT}/beta_lab/imaging", f"{ROOT}/nowhere"])
        assert {p.removeprefix(ROOT + "/"): (r["dev"], r["ino"]) for p, r in rows.items()} == {
            "alpha_lab/projects/2015_old": (1, 501), "beta_lab/imaging": (1, 502)}
        assert idx.paths_of([(1, 502), (1, 99999), (2, 501)]) == {(1, 502): f"{ROOT}/beta_lab/imaging"}
    finally:
        idx.close()


def _index_dir():
    from pathlib import Path

    from sqlalchemy import select

    from pinax.db import session_scope
    from pinax.models import Snapshot

    with session_scope() as s:
        return Path(s.scalar(select(Snapshot.index_dir).order_by(Snapshot.started_at.desc()).limit(1)))


def test_a_mark_records_the_folder_as_it_is(fast):
    from sqlalchemy import select

    from pinax import marks
    from pinax.db import session_scope
    from pinax.models import Filesystem
    from pinax.snapshots import cold_bucket

    first = _mark("alpha_lab/projects/2015_old")
    assert _mark("alpha_lab/projects/2015_old") == first  # one mark per folder
    m = _marks()["alpha_lab/projects/2015_old"]
    assert m["status"] == HERE and m["ino"] == 501
    (point,) = m["history"]
    assert point["present"] and point["bytes"] == 8050 and point["files"] == 3 and point["dirs"] == 1
    assert point["cold_bytes"] == 8050  # all of it over ten years old
    with session_scope() as s, pytest.raises(marks.MarkError, match="not in the latest scan"):
        marks.mark_folder(s, s.scalar(select(Filesystem)), f"{ROOT}/alpha_lab/nowhere", cold_from=cold_bucket())


def test_renamed_moved_gone_and_a_reused_inode(fast):
    _mark("alpha_lab/projects/2015_old", "beta_lab/imaging", "beta_lab/archive", "alpha_lab/projects/2025_new")
    _ingest(fast, "s1", _later(), 30, {"alpha_lab/projects/2015_archive": 501, "beta_lab/new_imaging": 502})
    m = _marks()
    renamed = m["alpha_lab/projects/2015_old"]
    assert renamed["status"] == MOVED and renamed["moved_to"] == f"{ROOT}/alpha_lab/projects/2015_archive"
    assert [p["present"] for p in renamed["history"]] == [True, False]
    # The deleted folder's inode went to a new folder: offered as a move, not applied.
    assert m["beta_lab/imaging"]["status"] == MOVED and m["beta_lab/imaging"]["moved_to"].endswith("new_imaging")
    assert m["beta_lab/archive"]["status"] == HERE and [p["bytes"] for p in m["beta_lab/archive"]["history"]] == [8000, 8000]
    shrunk = m["alpha_lab/projects/2025_new"]
    assert shrunk["status"] == HERE and [p["bytes"] for p in shrunk["history"]] == [11010, 2010]


def test_a_person_confirms_or_dismisses_a_move(fast):
    from sqlalchemy import select

    from pinax import marks
    from pinax.db import session_scope
    from pinax.models import AuditEvent, MarkedFolder
    from pinax.snapshots import cold_bucket

    _mark("alpha_lab/projects/2015_old", "beta_lab/imaging")
    _ingest(fast, "s1", _later(), 30, {"alpha_lab/projects/2015_archive": 501, "beta_lab/new_imaging": 502})
    with session_scope() as s:
        by = {m.path.removeprefix(ROOT + "/"): m for m in s.scalars(select(MarkedFolder))}
        marks.accept_move(s, None, by["alpha_lab/projects/2015_old"], cold_from=cold_bucket())
        marks.dismiss_move(s, None, by["beta_lab/imaging"])
        with pytest.raises(marks.MarkError, match="has not moved"):
            marks.dismiss_move(s, None, by["beta_lab/imaging"])
    m = _marks()
    followed = m["alpha_lab/projects/2015_archive"]
    assert followed["status"] == HERE and [(p["present"], p["bytes"]) for p in followed["history"]] == [(True, 8050), (True, 8050)]
    assert m["beta_lab/imaging"]["status"] == GONE and m["beta_lab/imaging"]["moved_to"] is None
    # Followed by path from then on.
    _ingest(fast, "s2", _later(), 60, {"alpha_lab/projects/2015_archive": 501})
    assert _marks()["alpha_lab/projects/2015_archive"]["status"] == HERE
    with session_scope() as s:
        assert [e.action for e in s.scalars(select(AuditEvent).where(AuditEvent.action.like("mark.%")))] == [
            "mark.moved", "mark.not_moved"]


def test_an_older_scan_found_late_changes_nothing_but_history(fast):
    _mark("beta_lab/imaging")
    _ingest(fast, "s1", _later(), 30, {"beta_lab/new_imaging": 502})
    _ingest(fast, "early", FILES, -30, INODES)  # dated before the first: it had the folder
    m = _marks()["beta_lab/imaging"]
    assert m["status"] == MOVED
    assert [p["present"] for p in m["history"]] == [True, True, False]  # oldest first


def test_history_outlasts_the_indexes(fast, monkeypatch):
    from pinax import config

    monkeypatch.setenv("PINAX_KEEP_SNAPSHOTS", "1")
    config.get_settings.cache_clear()
    _mark("beta_lab/archive")
    _ingest(fast, "s1", _later(), 30, {})
    _ingest(fast, "s2", _later(), 60, {})
    assert [p["bytes"] for p in _marks()["beta_lab/archive"]["history"]] == [8000, 8000, 8000]


def test_an_index_without_inodes_finds_folders_by_path_only(fast, monkeypatch):
    from pinax.index import SnapshotIndex

    _mark("alpha_lab/projects/2015_old")
    monkeypatch.setattr(SnapshotIndex, "has_inodes", False, raising=False)
    original = SnapshotIndex.__init__

    def without(self, *a, **k):
        original(self, *a, **k)
        self.has_inodes = False

    monkeypatch.setattr(SnapshotIndex, "__init__", without)
    _ingest(fast, "s1", _later(), 30, {"alpha_lab/projects/2015_archive": 501})
    assert _marks()["alpha_lab/projects/2015_old"]["status"] == GONE
