# SPDX-License-Identifier: Apache-2.0
"""Snapshots: running scans, ingesting them into indexes, retention, and the cache of open indexes."""

import json
import logging
import shutil
import subprocess
import threading
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .categories import AGE_BUCKETS, CATEGORY_KEYS, bucket_edges
from .config import Settings, get_settings
from .db import utcnow
from .index import SnapshotIndex, Stats
from .ingest.build import BuildError, build_index, check_manifest, read_manifest
from .models import AuditEvent, Filesystem, FolderStat, Snapshot, SnapshotStatus

log = logging.getLogger("pinax.snapshots")


class IngestError(Exception):
    pass


def cold_bucket(settings: Settings | None = None) -> int:
    """First age bucket that counts as cold: the one starting at (about) COLD_DAYS."""
    days = (settings or get_settings()).cold_days
    for i, edge in enumerate(bucket_edges(), start=1):
        if edge >= days * 0.99:
            return i
    return len(AGE_BUCKETS) - 1


# ---------- Open indexes ----------

class IndexCache:
    """Keeps recently used snapshot indexes open (each is a read-only DuckDB connection)."""

    def __init__(self, size: int = 8):
        self.size = size
        self._items: OrderedDict[int, SnapshotIndex] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, snap: Snapshot) -> SnapshotIndex:
        with self._lock:
            idx = self._items.get(snap.id)
            if idx is not None:
                self._items.move_to_end(snap.id)
                return idx
            s = get_settings()
            idx = SnapshotIndex(Path(snap.index_dir), memory_limit=s.query_memory_limit, threads=s.query_threads)
            self._items[snap.id] = idx
            while len(self._items) > self.size:
                _, old = self._items.popitem(last=False)
                old.close()
            return idx

    def drop(self, snapshot_id: int) -> None:
        with self._lock:
            idx = self._items.pop(snapshot_id, None)
            if idx:
                idx.close()

    def clear(self) -> None:
        with self._lock:
            for idx in self._items.values():
                idx.close()
            self._items.clear()


indexes = IndexCache()


def latest_snapshot(session: Session, fs: Filesystem) -> Snapshot | None:
    return session.scalar(
        select(Snapshot)
        .where(Snapshot.filesystem_id == fs.id, Snapshot.status == SnapshotStatus.READY)
        .order_by(Snapshot.started_at.desc())
        .limit(1)
    )


def previous_snapshot(session: Session, snap: Snapshot) -> Snapshot | None:
    """The scan before `snap` (ready or pruned: its folder history survives pruning)."""
    return session.scalar(
        select(Snapshot)
        .where(Snapshot.filesystem_id == snap.filesystem_id, Snapshot.started_at < snap.started_at,
               Snapshot.status.in_([SnapshotStatus.READY, SnapshotStatus.PRUNED]))
        .order_by(Snapshot.started_at.desc())
        .limit(1)
    )


# ---------- Scanning ----------

def run_scan(fs: Filesystem, settings: Settings, *, extra_args: list[str] | None = None,
             progress: Callable[[str], None] = print) -> Path:
    """Scan `fs.root` with fs-inventory into a new folder under SCAN_ROOT, then verify it."""
    binary = settings.fs_inventory_path()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = settings.scan_root / fs.slug / stamp
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [binary, "scan", fs.root, "-o", str(out), "--resolve-ids", "--threads", str(settings.scan_threads),
           "--progress", "plain", *settings.scan_extra_args, *(extra_args or [])]
    progress("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, check=False)
    # Exit 1 means complete with some errors (recorded in the scan); anything else needs attention.
    if proc.returncode not in (0, 1):
        raise IngestError(f"fs-inventory scan exited with code {proc.returncode}; see its output above")
    verify = subprocess.run([binary, "verify", str(out)], capture_output=True, text=True, check=False)
    if verify.returncode != 0:
        raise IngestError(f"fs-inventory verify failed: {verify.stdout.strip()} {verify.stderr.strip()}")
    progress(f"Scan written to {out}")
    return out


# ---------- Ingest ----------

def find_filesystem_for(session: Session, root: str, slug: str | None) -> Filesystem | None:
    if slug:
        return session.scalar(select(Filesystem).where(Filesystem.slug == slug))
    return session.scalar(select(Filesystem).where(Filesystem.root == root))


def ingest_scan(session: Session, scan_dir: Path, *, fs_slug: str | None = None, allow_incomplete: bool = False,
                settings: Settings | None = None, progress: Callable[[str], None] = print) -> Snapshot:
    """Build an index from a finished scan and register it as the filesystem's newest snapshot."""
    settings = settings or get_settings()
    scan_dir = Path(scan_dir).resolve()
    manifest = read_manifest(scan_dir)
    try:
        check_manifest(manifest, allow_incomplete=allow_incomplete)
    except BuildError as e:
        raise IngestError(str(e)) from e
    root = manifest["roots"][0]["path"]
    fs = find_filesystem_for(session, root, fs_slug)
    if fs is None:
        raise IngestError(f"no filesystem is registered for {root}; add one with `pinax filesystem add`")
    if fs.root != root:
        raise IngestError(f"scan root {root} does not match filesystem {fs.slug} ({fs.root})")
    existing = session.scalar(select(Snapshot).where(Snapshot.scan_id == manifest["scan_id"]))
    if existing is not None and existing.status == SnapshotStatus.READY:
        raise IngestError(f"scan {manifest['scan_id']} is already snapshot {existing.id}")

    started = datetime.fromisoformat(manifest["started_at"]).astimezone(UTC).replace(tzinfo=None)
    finished = manifest.get("finished_at")
    snap = existing or Snapshot(filesystem_id=fs.id, scan_id=manifest["scan_id"], started_at=started)
    snap.scan_dir = str(scan_dir)
    snap.status = SnapshotStatus.BUILDING
    snap.message = ""
    snap.finished_at = (datetime.fromisoformat(finished).astimezone(UTC).replace(tzinfo=None)
                        if finished else None)
    snap.scan_seconds = manifest.get("duration_seconds")
    snap.scan_status = manifest.get("status", "")
    snap.tool_version = manifest.get("tool_version", "")
    snap.host = manifest.get("host", "")
    session.add(snap)
    session.commit()

    index_dir = settings.snapshot_root / fs.slug / f"{started:%Y%m%dT%H%M%SZ}-{manifest['scan_id'][:8]}"
    try:
        result = build_index(scan_dir, index_dir, memory_limit=settings.build_memory_limit,
                             threads=settings.build_threads, allow_incomplete=allow_incomplete, progress=progress)
    except Exception as e:  # record the failure, then re-raise for the caller
        snap.status = SnapshotStatus.FAILED
        snap.message = str(e)
        session.commit()
        raise

    t = result.totals
    snap.index_dir = str(index_dir)
    snap.ingested_at = utcnow()
    snap.build_seconds = round(result.seconds, 2)
    snap.entries, snap.files, snap.dirs = t["entries"], t["files"], t["dirs"]
    snap.bytes, snap.alloc_bytes, snap.errors = t["bytes"], t["alloc_bytes"], t["errors"]
    snap.index_bytes = sum(p.stat().st_size for p in index_dir.rglob("*") if p.is_file())
    snap.info = {"steps": result.steps, "incomplete_dirs": t["incomplete_dirs"], "tops": t["tops"],
                 "categories": list(CATEGORY_KEYS), "age_buckets": [b.short for b in AGE_BUCKETS],
                 "errors_by_kind": manifest.get("errors_by_kind", [])}
    session.flush()
    _record_folder_stats(session, snap, index_dir, settings)
    snap.status = SnapshotStatus.READY
    session.add(AuditEvent(action="snapshot.ingested",
                           summary=f"Ingested scan of {fs.name} taken {started:%Y-%m-%d %H:%M} UTC",
                           detail={"snapshot_id": snap.id, "scan_dir": str(scan_dir), "files": snap.files,
                                   "bytes": snap.bytes, "build_seconds": snap.build_seconds}))
    session.commit()
    prune(session, fs, settings)
    return snap


def _record_folder_stats(session: Session, snap: Snapshot, index_dir: Path, settings: Settings) -> None:
    idx = SnapshotIndex(index_dir, memory_limit=settings.query_memory_limit)
    try:
        session.query(FolderStat).filter(FolderStat.snapshot_id == snap.id).delete()
        cold_from = cold_bucket(settings)
        root = idx.node(idx.root)
        kids = idx.children(idx.root, limit=1_000_000)
        rows = [(r["name"], Stats.from_row(r)) for r in kids["rows"]]
        # Files directly in the root belong to no top-level folder; record them under "".
        direct = Stats.from_row(root).sub(Stats.from_row(kids["all"]) if kids["all"]["n"] else Stats.zero())
        direct.values["dirs"] = 0
        if direct.values.get("files"):
            rows.append(("", direct))
        for name, st in rows:
            j = st.json(idx.ref_day)
            session.add(FolderStat(
                snapshot_id=snap.id, filesystem_id=snap.filesystem_id, folder=name, taken_at=snap.started_at,
                bytes=j["bytes"], alloc_bytes=j["alloc"], files=j["files"], dirs=j["dirs"],
                cold_bytes=sum(j["age_bytes"][cold_from:]), mean_age_days=j["mean_age_days"],
                newest_mtime=_dt(j["newest"]), oldest_mtime=_dt(j["oldest"]), incomplete=j["incomplete"],
                age_bytes=j["age_bytes"], cat_bytes=dict(zip(CATEGORY_KEYS, j["cat_bytes"])),
            ))
    finally:
        idx.close()


def _dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso).astimezone(UTC).replace(tzinfo=None)


def prune(session: Session, fs: Filesystem, settings: Settings) -> list[int]:
    """Delete the indexes of all but the newest KEEP_SNAPSHOTS ready snapshots. Folder history stays."""
    ready = session.scalars(
        select(Snapshot).where(Snapshot.filesystem_id == fs.id, Snapshot.status == SnapshotStatus.READY)
        .order_by(Snapshot.started_at.desc())
    ).all()
    pruned = []
    for snap in ready[max(1, settings.keep_snapshots):]:
        indexes.drop(snap.id)
        if snap.index_dir and Path(snap.index_dir).exists():
            shutil.rmtree(snap.index_dir, ignore_errors=True)
        snap.status = SnapshotStatus.PRUNED
        snap.message = "Index removed by retention; folder history kept"
        pruned.append(snap.id)
    if pruned:
        session.add(AuditEvent(action="snapshot.pruned", summary=f"Pruned {len(pruned)} old snapshot index(es) of {fs.name}",
                               detail={"snapshot_ids": pruned}))
        session.commit()
    return pruned


def snapshot_json(snap: Snapshot) -> dict:
    return {
        "id": snap.id, "filesystem": snap.filesystem.slug if snap.filesystem else None, "scan_id": snap.scan_id,
        "status": snap.status, "message": snap.message, "started_at": _iso(snap.started_at),
        "finished_at": _iso(snap.finished_at), "scan_seconds": snap.scan_seconds, "scan_status": snap.scan_status,
        "tool_version": snap.tool_version, "host": snap.host, "ingested_at": _iso(snap.ingested_at),
        "build_seconds": snap.build_seconds, "entries": snap.entries, "files": snap.files, "dirs": snap.dirs,
        "bytes": snap.bytes, "alloc_bytes": snap.alloc_bytes, "errors": snap.errors, "index_bytes": snap.index_bytes,
        "scan_dir": snap.scan_dir, "info": snap.info or {},
    }


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def describe_scan(scan_dir: Path) -> str:
    m = read_manifest(scan_dir)
    return json.dumps({k: m.get(k) for k in ("scan_id", "status", "roots", "started_at", "tool_version")}, indent=2)
