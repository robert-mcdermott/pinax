# SPDX-License-Identifier: Apache-2.0
"""Administrator endpoints: organization overview, storage health, and scans."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import explore
from ..auth import Identity, require_admin
from ..db import get_session
from ..index import Stats
from ..models import AuditEvent, Filesystem, Heartbeat, Snapshot
from ..serialize import fs_json, iso
from ..snapshots import indexes, latest_snapshot, previous_snapshot, snapshot_json
from ..watch import HEARTBEAT, heartbeat_json

router = APIRouter(prefix="/admin", tags=["admin"])

NAME_FLAGS = [
    (1, "invalid_utf8", "Not valid UTF-8 (a legacy encoding or corruption)"),
    (2, "c0_control", "Contains a control character such as a newline or tab"),
    (4, "c1_control", "Contains a C1 control character"),
    (8, "del", "Contains the DEL character"),
    (16, "bidi_control", "Contains a bidirectional formatting character"),
    (32, "not_nfc", "Not in Unicode normalization form C (common from macOS)"),
    (64, "leading_trailing_space", "Starts or ends with a space"),
    (128, "leading_dash", "Starts with a dash, which tools can mistake for an option"),
    (256, "trailing_dot", "Ends with a dot, which Windows strips"),
    (512, "windows_reserved", "Uses a character or name Windows forbids"),
    (1024, "long_name", "Name longer than 255 bytes"),
    (2048, "long_path", "Full path of 4,096 bytes or more"),
]


@router.get("/overview")
def overview(_: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    """Per filesystem: the latest scan, its totals, and how much of it is mapped to labs."""
    out = []
    for fs in session.scalars(select(Filesystem).where(Filesystem.active.is_(True)).order_by(Filesystem.name)):
        snap = latest_snapshot(session, fs)
        entry = {"filesystem": fs_json(fs), "snapshot": snapshot_json(snap) if snap else None}
        if snap:
            prev = previous_snapshot(session, snap)
            idx = indexes.get(snap)
            root = idx.node(idx.root)
            entry["stats"] = Stats.from_row(root).json(idx.ref_day)
            entry["previous"] = {"id": prev.id, "started_at": iso(prev.started_at), "bytes": prev.bytes,
                                 "files": prev.files} if prev else None
            entry["name_issues"] = sum(r["entries"] for r in idx.name_flag_counts())
        out.append(entry)
    audit = session.scalars(select(AuditEvent).order_by(AuditEvent.ts.desc()).limit(10)).all()
    return {"filesystems": out,
            "activity": [{"ts": iso(a.ts), "summary": a.summary, "action": a.action} for a in audit]}


@router.get("/health")
def health(fs: str, path: str | None = None, huge_entries: int = Query(100_000, ge=1),
           many_files: int = Query(10_000, ge=1), small_bytes: int = Query(65_536, ge=1),
           identity: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    """Scan errors, oversized directories, small-file hotspots, problem names, and unknown owners."""
    place = explore.resolve(session, identity, fs, path)[0]
    idx = place.idx

    def dirs(rows):
        return [{"path": r["path"], "entries": r["dir_entries"], "d_files": r["d_files"], "d_bytes": r["d_bytes"],
                 "files": r["files"], "bytes": r["bytes"], "error": r["error"]} for r in rows]

    errors = idx.errors(place.path)
    huge = idx.subtree_dirs(place.path, where="dir_entries >= ?", params=[huge_entries],
                            order="dir_entries DESC", limit=50)
    small = idx.subtree_dirs(place.path, where="d_files >= ? AND d_bytes < d_files * ?",
                             params=[many_files, small_bytes], order="d_files DESC", limit=50)
    incomplete = idx.subtree_dirs(place.path, where="error IS NOT NULL", order="path", limit=100)
    tops = idx.scope_tops(place.path)
    flags = {r["flag"]: r["entries"] for r in idx.name_flag_counts(None if place.path == idx.root else tops)}
    owners = [o for o in explore.owners(session, identity, fs, place.path)["owners"] if not o["known"]]
    return {
        "fs": fs, "path": place.path, "as_of": iso(place.snap.started_at),
        "errors": errors, "huge_dirs": dirs(huge), "small_file_dirs": dirs(small), "incomplete": dirs(incomplete),
        "name_flags": [{"bit": b, "key": k, "label": label, "entries": flags.get(b, 0)} for b, k, label in NAME_FLAGS],
        "unknown_owners": owners,
        "thresholds": {"huge_entries": huge_entries, "many_files": many_files, "small_bytes": small_bytes},
    }


@router.get("/names")
def names(fs: str, flag: int, path: str | None = None, limit: int = Query(200, ge=1, le=2000),
          identity: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    """Files whose names have a given problem flag."""
    place = explore.resolve(session, identity, fs, path)[0]
    rows = place.idx.files(place.path, where="(name_flags & ?) <> 0", params=[flag], order="path", limit=limit)
    return {"files": [{"path": r["path"], "size": r["size"], "name_flags": r["name_flags"]} for r in rows]}


@router.get("/snapshots")
def snapshots(_: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    rows = session.scalars(select(Snapshot).order_by(Snapshot.started_at.desc())).all()
    return [snapshot_json(s) for s in rows]


@router.get("/ingest")
def ingest_status(_: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    """Whether automatic ingest (`pinax ingest --watch`) is running, and what its last pass did."""
    return {"watch": heartbeat_json(session.get(Heartbeat, HEARTBEAT))}


@router.get("/filesystems")
def filesystems(_: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    return [fs_json(f) for f in session.scalars(select(Filesystem).order_by(Filesystem.name))]
