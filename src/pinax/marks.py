# SPDX-License-Identifier: Apache-2.0
"""Marked folders (S10): folders someone marked, for a cleanup plan (wave 7) or a tag (wave 8), followed from scan
to scan.

A mark is kept by filesystem and path, as the index has them, with the directory's device and inode (index
version 6). With each new scan of its filesystem, ingest looks for every marked folder:

- at its path: `here`, and its totals are recorded for that scan;
- else at another path with the same inode, since a rename or a move within the filesystem keeps it: `moved`,
  offered to a person as a move rather than applied, because a deleted directory's inode can be given to a new one;
- else `gone`.

A marked folder's totals are kept for every scan from the one it was marked in (and for the snapshots still kept
in full before it), so its history outlasts the snapshots' indexes, as folder history does for top-level folders.
Nothing here reads or changes the storage: only scans.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .index import SnapshotIndex, Stats
from .models import AuditEvent, Filesystem, MarkedFolder, MarkedFolderStat, Snapshot, SnapshotStatus, User

HERE, MOVED, GONE = "here", "moved", "gone"


class MarkError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _totals(row: dict, ref_day: float, cold_from: int) -> dict:
    j = Stats.from_row(row).json(ref_day)
    return {"bytes": j["bytes"], "alloc_bytes": j["alloc"], "files": j["files"], "dirs": j["dirs"],
            "cold_bytes": sum(j["age_bytes"][cold_from:]), "mean_age_days": j["mean_age_days"],
            "age_bytes": j["age_bytes"]}


def _record(session: Session, mark: MarkedFolder, snap: Snapshot, totals: dict | None) -> None:
    """A marked folder's totals in a scan (None: not at its path then), replacing any recorded before."""
    row = session.scalar(select(MarkedFolderStat).where(MarkedFolderStat.mark_id == mark.id,
                                                        MarkedFolderStat.snapshot_id == snap.id))
    if row is None:
        row = MarkedFolderStat(mark_id=mark.id, snapshot_id=snap.id)
        session.add(row)
    row.taken_at = snap.started_at
    row.present = totals is not None
    for k, v in (totals or {"bytes": 0, "alloc_bytes": 0, "files": 0, "dirs": 0, "cold_bytes": 0,
                            "mean_age_days": None, "age_bytes": []}).items():
        setattr(row, k, v)


def follow(session: Session, snap: Snapshot, idx: SnapshotIndex, *, latest: bool, cold_from: int) -> None:
    """At ingest: find every marked folder of the scan's filesystem in it, and record its totals. Only the
    filesystem's latest scan changes where a mark is (an older scan found late only adds to its history)."""
    marks = session.scalars(select(MarkedFolder).where(MarkedFolder.filesystem_id == snap.filesystem_id)).all()
    if not marks:
        return
    found = idx.nodes([m.path for m in marks])
    missing = [m for m in marks if m.path not in found]
    elsewhere = idx.paths_of([(m.dev, m.ino) for m in missing if m.ino is not None and m.dev is not None])
    for m in marks:
        row = found.get(m.path)
        _record(session, m, snap, _totals(row, idx.ref_day, cold_from) if row else None)
        if not latest:
            continue
        if row is not None:
            m.status, m.moved_to, m.seen_snapshot_id = HERE, None, snap.id
            if row["ino"] is not None:
                m.dev, m.ino = row["dev"], row["ino"]
        else:
            to = elsewhere.get((m.dev, m.ino))
            m.status, m.moved_to = (MOVED, to) if to else (GONE, None)
    session.flush()


def _retained(session: Session, fs: Filesystem) -> list[Snapshot]:
    return session.scalars(select(Snapshot).where(Snapshot.filesystem_id == fs.id,
                                                  Snapshot.status == SnapshotStatus.READY)
                           .order_by(Snapshot.started_at)).all()


def mark_folder(session: Session, fs: Filesystem, path: str, *, cold_from: int) -> MarkedFolder:
    """The mark on a folder of `fs`, made if there is none: the folder must be in the latest scan. A new mark's
    history starts with the snapshots kept in full."""
    from .snapshots import indexes

    mark = session.scalar(select(MarkedFolder).where(MarkedFolder.filesystem_id == fs.id, MarkedFolder.path == path))
    if mark is not None:
        return mark
    kept = _retained(session, fs)
    if not kept:
        raise MarkError("That filesystem has not been scanned yet", 404)
    latest = kept[-1]
    row = indexes.get(latest).nodes([path]).get(path)
    if row is None:
        raise MarkError("That folder is not in the latest scan", 404)
    mark = MarkedFolder(filesystem_id=fs.id, path=path, dev=row["dev"], ino=row["ino"], status=HERE,
                        seen_snapshot_id=latest.id)
    session.add(mark)
    session.flush()
    for snap in kept:
        idx = indexes.get(snap)
        here = idx.nodes([path]).get(path)
        if here is not None or snap is latest:
            _record(session, mark, snap, _totals(here, idx.ref_day, cold_from) if here else None)
    session.flush()
    return mark


def _audit(session: Session, actor: User | None, action: str, summary: str, mark: MarkedFolder) -> None:
    fs = session.get(Filesystem, mark.filesystem_id)
    session.add(AuditEvent(actor_id=actor.id if actor else None, action=action, summary=summary,
                           detail={"filesystem": fs.slug, "path": mark.path, "moved_to": mark.moved_to}))


def accept_move(session: Session, actor: User | None, mark: MarkedFolder, *, cold_from: int) -> MarkedFolder:
    """A person confirms that a marked folder moved where its inode is now: the mark follows it."""
    from .snapshots import indexes, latest_snapshot

    if mark.status != MOVED or not mark.moved_to:
        raise MarkError("That folder has not moved", 409)
    if session.scalar(select(MarkedFolder.id).where(MarkedFolder.filesystem_id == mark.filesystem_id,
                                                    MarkedFolder.path == mark.moved_to)):
        raise MarkError("The folder it moved to is marked already", 409)
    fs = session.get(Filesystem, mark.filesystem_id)
    _audit(session, actor, "mark.moved", f"{fs.name}: {mark.path} moved to {mark.moved_to}", mark)
    mark.path, mark.moved_to, mark.status = mark.moved_to, None, HERE
    snap = latest_snapshot(session, fs)
    idx = indexes.get(snap)
    row = idx.nodes([mark.path]).get(mark.path)
    if row is not None:
        _record(session, mark, snap, _totals(row, idx.ref_day, cold_from))
        mark.seen_snapshot_id = snap.id
    session.flush()
    return mark


def dismiss_move(session: Session, actor: User | None, mark: MarkedFolder) -> MarkedFolder:
    """A person says the folder at the same inode is not the marked one: the marked folder is gone."""
    if mark.status != MOVED:
        raise MarkError("That folder has not moved", 409)
    fs = session.get(Filesystem, mark.filesystem_id)
    _audit(session, actor, "mark.not_moved", f"{fs.name}: {mark.moved_to} is not {mark.path}", mark)
    mark.status, mark.moved_to = GONE, None
    session.flush()
    return mark


def history(session: Session, mark: MarkedFolder) -> list[dict]:
    """A marked folder's totals in each scan recorded for it, oldest first."""
    rows = session.scalars(select(MarkedFolderStat).where(MarkedFolderStat.mark_id == mark.id)
                           .order_by(MarkedFolderStat.taken_at)).all()
    return [{"snapshot": r.snapshot_id, "taken_at": r.taken_at, "present": r.present, "bytes": r.bytes,
             "alloc_bytes": r.alloc_bytes, "files": r.files, "dirs": r.dirs, "cold_bytes": r.cold_bytes,
             "mean_age_days": r.mean_age_days, "age_bytes": r.age_bytes} for r in rows]
