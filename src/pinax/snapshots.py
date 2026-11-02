# SPDX-License-Identifier: Apache-2.0
"""Snapshots: running scans, ingesting them into indexes, retention, and the cache of open indexes."""

import fcntl
import json
import logging
import shutil
import subprocess
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import marks
from .capacity import record_storage, scan_storage
from .categories import AGE_BUCKETS, CATEGORY_KEYS, bucket_edges
from .config import Settings, get_settings, size_bytes
from .db import utcnow
from .index import SnapshotIndex, Stats
from .ingest.build import INDEX_VERSION, BuildError, build_index, check_manifest, read_manifest
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

def _stamp(index_dir: str) -> tuple[int, int] | None:
    try:
        st = (Path(index_dir) / "index.duckdb").stat()
    except OSError:
        return None
    return st.st_ino, st.st_mtime_ns


class IndexCache:
    """Keeps recently used snapshot indexes open (each is a read-only DuckDB connection). An index rebuilt since
    it was opened (`pinax rebuild`, in another process) is a new file, and is opened again.

    PINAX_QUERY_MEMORY_LIMIT is for all of them together: each open index is a DuckDB database with a memory limit
    of its own (what its queries use at once, and the blocks it keeps cached), so each gets an equal share, set
    again as indexes open and close. One index alone has all of it."""

    def __init__(self, size: int = 8):
        self.size = size
        self._items: OrderedDict[int, tuple[SnapshotIndex, tuple | None]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, snap: Snapshot) -> SnapshotIndex:
        with self._lock:
            stamp = _stamp(snap.index_dir)
            item = self._items.get(snap.id)
            if item is not None and item[1] == stamp:
                self._items.move_to_end(snap.id)
                return item[0]
            if item is not None:
                self._items.pop(snap.id)
                item[0].close()
            # Opened as everywhere else (DuckDB refuses a second connection to a file open in this process with other
            # settings), then given its share.
            s = get_settings()
            idx = SnapshotIndex(Path(snap.index_dir), memory_limit=s.query_memory_limit, threads=s.query_threads)
            self._items[snap.id] = (idx, stamp)
            while len(self._items) > self.size:
                _, (old, _) = self._items.popitem(last=False)
                old.close()
            self._share()
            return idx

    def _share(self) -> None:
        share = _memory_share(len(self._items))
        for idx, _ in self._items.values():
            idx.set_memory_limit(share)

    def drop(self, snapshot_id: int) -> None:
        with self._lock:
            item = self._items.pop(snapshot_id, None)
            if item:
                item[0].close()
                self._share()

    def clear(self) -> None:
        with self._lock:
            for idx, _ in self._items.values():
                idx.close()
            self._items.clear()


def _memory_share(open_indexes: int) -> str:
    """Each of `open_indexes` indexes' part of PINAX_QUERY_MEMORY_LIMIT, in bytes, as DuckDB reads a size."""
    return f"{size_bytes(get_settings().query_memory_limit) // max(1, open_indexes)}B"


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
    try:  # what the storage reports for the filesystem, for its capacity (capacity.py)
        storage = record_storage(out, fs.root)
        progress(f"The storage reports {storage['used']:,} bytes used of {storage['total']:,}")
    except OSError as e:
        progress(f"Could not ask the storage its size ({e}); set the capacity on Capacity instead")
    progress(f"Scan written to {out}")
    return out


# ---------- Ingest ----------

def find_filesystem_for(session: Session, root: str, slug: str | None) -> Filesystem | None:
    if slug:
        return session.scalar(select(Filesystem).where(Filesystem.slug == slug))
    return session.scalar(select(Filesystem).where(Filesystem.root == root))


@contextmanager
def ingest_lock(settings: Settings) -> Iterator[None]:
    """One ingest at a time per catalog: `pinax ingest`, `pinax scan`, and any number of watchers take this lock
    (a file lock in VAR_ROOT), so two never build at once or claim the same scan."""
    settings.ensure_dirs()
    with open(settings.var_root / "ingest.lock", "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _scan_fields(snap: Snapshot, manifest: dict, scan_dir: Path) -> None:
    finished = manifest.get("finished_at")
    snap.scan_dir = str(scan_dir)
    snap.finished_at = (datetime.fromisoformat(finished).astimezone(UTC).replace(tzinfo=None)
                        if finished else None)
    snap.scan_seconds = manifest.get("duration_seconds")
    snap.scan_status = manifest.get("status", "")
    snap.tool_version = manifest.get("tool_version", "")
    snap.host = manifest.get("host", "")


def _started(manifest: dict) -> datetime:
    value = manifest.get("started_at")
    return datetime.fromisoformat(value).astimezone(UTC).replace(tzinfo=None) if value else utcnow()


def record_scan(session: Session, scan_dir: Path, manifest: dict, fs: Filesystem, status: str, message: str,
                settings: Settings | None = None) -> Snapshot | None:
    """Record a scan that was found but not loaded (SKIPPED or FAILED, with why), so it shows on *Scans* and is
    not tried again automatically. Returns None if the scan is already recorded."""
    with ingest_lock(settings or get_settings()):
        if session.scalar(select(Snapshot.id).where(Snapshot.scan_id == manifest["scan_id"])) is not None:
            return None
        snap = Snapshot(filesystem_id=fs.id, scan_id=manifest["scan_id"], started_at=_started(manifest),
                        status=status, message=message)
        _scan_fields(snap, manifest, Path(scan_dir).resolve())
        session.add(snap)
        session.add(AuditEvent(action=f"snapshot.{status}", summary=f"Did not ingest a scan of {fs.name}: {message}",
                               detail={"scan_dir": str(scan_dir), "scan_id": manifest["scan_id"]}))
        session.commit()
        return snap


def ingest_scan(session: Session, scan_dir: Path, *, fs_slug: str | None = None, allow_incomplete: bool = False,
                only_new: bool = False, settings: Settings | None = None,
                progress: Callable[[str], None] = print) -> Snapshot:
    """Build an index from a finished scan and register it as the filesystem's newest snapshot. A scan recorded
    earlier as failed, skipped, or building (an ingest that died) is built again, unless `only_new` (automatic
    ingest); a ready one is refused."""
    settings = settings or get_settings()
    with ingest_lock(settings):
        return _ingest(session, Path(scan_dir).resolve(), fs_slug=fs_slug, allow_incomplete=allow_incomplete,
                       only_new=only_new, settings=settings, progress=progress)


def _ingest(session: Session, scan_dir: Path, *, fs_slug: str | None, allow_incomplete: bool, only_new: bool,
            settings: Settings, progress: Callable[[str], None]) -> Snapshot:
    try:
        manifest = read_manifest(scan_dir)
    except BuildError as e:
        raise IngestError(str(e)) from e
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
        raise IngestError(f"scan {manifest['scan_id']} is already snapshot {existing.id} "
                          f"(build its index again with `pinax rebuild {existing.id}`)")
    if existing is not None and only_new:
        raise IngestError(f"scan {manifest['scan_id']} is already recorded as snapshot {existing.id} ({existing.status})")

    started = _started(manifest)
    snap = existing or Snapshot(filesystem_id=fs.id, scan_id=manifest["scan_id"], started_at=started)
    snap.status = SnapshotStatus.BUILDING
    snap.message = ""
    _scan_fields(snap, manifest, scan_dir)
    session.add(snap)
    try:
        session.commit()
    except IntegrityError as e:  # claimed by an ingest that does not take the lock (another catalog host)
        session.rollback()
        raise IngestError(f"scan {manifest['scan_id']} is already being ingested") from e

    index_dir = settings.snapshot_root / fs.slug / f"{started:%Y%m%dT%H%M%SZ}-{manifest['scan_id'][:8]}"
    prev = previous_with_index(session, fs.id, started)
    try:
        result = build_index(scan_dir, index_dir, memory_limit=settings.build_memory_limit,
                             threads=settings.build_threads, allow_incomplete=allow_incomplete,
                             previous=Path(prev.index_dir) if prev else None, progress=progress)
    except Exception as e:  # record the failure, then re-raise for the caller
        snap.status = SnapshotStatus.FAILED
        snap.message = str(e)
        session.commit()
        raise

    _record_build(session, snap, index_dir, result, manifest, settings, prev)
    snap.status = SnapshotStatus.READY
    session.add(AuditEvent(action="snapshot.ingested",
                           summary=f"Ingested scan of {fs.name} taken {started:%Y-%m-%d %H:%M} UTC",
                           detail={"snapshot_id": snap.id, "scan_dir": str(scan_dir), "files": snap.files,
                                   "bytes": snap.bytes, "build_seconds": snap.build_seconds}))
    session.commit()
    prune(session, fs, settings)
    return snap


def previous_with_index(session: Session, filesystem_id: int, before: datetime) -> Snapshot | None:
    """The filesystem's newest ready snapshot taken before `before` whose index is on disk: what a new index is
    compared with."""
    for snap in session.scalars(select(Snapshot).where(Snapshot.filesystem_id == filesystem_id,
                                                       Snapshot.status == SnapshotStatus.READY,
                                                       Snapshot.started_at < before)
                                .order_by(Snapshot.started_at.desc())):
        if snap.index_dir and (Path(snap.index_dir) / "index.duckdb").exists():
            return snap
    return None


def _record_build(session: Session, snap: Snapshot, index_dir: Path, result, manifest: dict, settings: Settings,
                  prev: Snapshot | None) -> None:
    t = result.totals
    snap.index_dir = str(index_dir)
    snap.ingested_at = utcnow()
    snap.build_seconds = round(result.seconds, 2)
    snap.entries, snap.files, snap.dirs = t["entries"], t["files"], t["dirs"]
    snap.bytes, snap.alloc_bytes, snap.errors = t["bytes"], t["alloc_bytes"], t["errors"]
    snap.index_bytes = sum(p.stat().st_size for p in index_dir.rglob("*") if p.is_file())
    snap.info = {"steps": result.steps, "incomplete_dirs": t["incomplete_dirs"], "tops": t["tops"],
                 "categories": list(CATEGORY_KEYS), "age_buckets": [b.short for b in AGE_BUCKETS],
                 "errors_by_kind": manifest.get("errors_by_kind", []), "index_version": INDEX_VERSION,
                 "compared_with": prev.id if prev and result.compared else None}
    if storage := scan_storage(Path(snap.scan_dir)):
        snap.info["storage"] = storage
    session.flush()
    _record_folder_stats(session, snap, index_dir, settings)


def index_version(snap: Snapshot) -> int | None:
    """The format version of a snapshot's index on disk; None if it is missing or unreadable."""
    try:
        idx = SnapshotIndex(Path(snap.index_dir), memory_limit="256MB", threads=1)
    except (OSError, duckdb.Error):
        return None
    try:
        return int(idx.meta.get("index_version", 0))
    finally:
        idx.close()


def rebuild_snapshot(session: Session, snap: Snapshot, *, settings: Settings | None = None,
                     progress: Callable[[str], None] = print) -> Snapshot:
    """Build a ready snapshot's index again from its scan, in place: after an upgrade changes the index format,
    or if the index was lost. The snapshot stays ready on its old index until the new one replaces it, and keeps
    it if the build fails."""
    settings = settings or get_settings()
    with ingest_lock(settings):
        session.refresh(snap)
        if snap.status != SnapshotStatus.READY:
            raise IngestError(f"snapshot {snap.id} is {snap.status}; only ready snapshots are rebuilt "
                              "(ingest its scan with `pinax ingest`)")
        scan_dir = Path(snap.scan_dir)
        try:
            manifest = read_manifest(scan_dir)
        except BuildError as e:
            raise IngestError(f"snapshot {snap.id}'s scan is gone: {e}") from e
        if manifest.get("scan_id") != snap.scan_id:
            raise IngestError(f"{scan_dir} now holds a different scan than snapshot {snap.id}")
        index_dir = Path(snap.index_dir)
        prev = previous_with_index(session, snap.filesystem_id, snap.started_at)
        try:
            result = build_index(scan_dir, index_dir, memory_limit=settings.build_memory_limit,
                                 threads=settings.build_threads, allow_incomplete=snap.scan_status != "complete",
                                 previous=Path(prev.index_dir) if prev else None, progress=progress)
        except Exception as e:  # record it, keep the old index, then re-raise for the caller
            snap.message = f"Rebuild failed; the previous index is still in use: {e}"
            session.commit()
            raise
        _record_build(session, snap, index_dir, result, manifest, settings, prev)
        snap.message = ""
        session.add(AuditEvent(action="snapshot.rebuilt", summary=f"Rebuilt the index of snapshot {snap.id}",
                               detail={"snapshot_id": snap.id, "index_version": INDEX_VERSION,
                                       "build_seconds": snap.build_seconds}))
        session.commit()
    return snap


def _record_folder_stats(session: Session, snap: Snapshot, index_dir: Path, settings: Settings) -> None:
    idx = SnapshotIndex(index_dir, memory_limit=settings.query_memory_limit)
    try:
        session.query(FolderStat).filter(FolderStat.snapshot_id == snap.id).delete()
        cold_from = cold_bucket(settings)
        root = idx.node(idx.root)
        whole = Stats.from_row(root).json(idx.ref_day)
        # The filesystem's own totals, so its history needs one row per scan rather than one per folder.
        snap.info = {**(snap.info or {}), "totals": {
            "bytes": whole["bytes"], "alloc_bytes": whole["alloc"], "files": whole["files"],
            "age_bytes": whole["age_bytes"], "mean_age_days": whole["mean_age_days"]}}
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
        later = session.scalar(select(Snapshot.id).where(
            Snapshot.filesystem_id == snap.filesystem_id, Snapshot.id != snap.id,
            Snapshot.status.in_((SnapshotStatus.READY, SnapshotStatus.PRUNED)), Snapshot.started_at > snap.started_at))
        marks.follow(session, snap, idx, latest=later is None, cold_from=cold_from)
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
