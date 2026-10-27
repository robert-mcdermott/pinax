# SPDX-License-Identifier: Apache-2.0
"""Automatic ingest: `pinax ingest --watch` loads each new scan under a folder, once.

A pass looks for scan folders (folders holding a `manifest.json`) up to three levels below the watched folder,
oldest scan first, and for each one fs-inventory has finished:

- `complete`: verified with `fs-inventory verify`, then ingested;
- `running` or `interrupted`: left alone, since it may yet be finished or resumed; the next pass looks again;
- `incomplete` or `failed`: recorded as *skipped*, with why, unless `--allow-incomplete` accepts incomplete ones.

Every scan that ends up recorded (ingested, skipped, or failed, with the error) shows on *Scans* and is never
tried again automatically; `pinax ingest SCAN_DIR` builds a failed or skipped one by hand. Scans are matched to
filesystems by their root. Folders are polled on a timer rather than watched for changes, because network
filesystems do not deliver change notifications reliably. Any number of watchers and manual ingests can run at
once: they take the same ingest lock, and a scan's ID can be recorded only once.
"""

import logging
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from .config import Settings, get_settings
from .db import session_scope, utcnow
from .ingest.build import BuildError, read_manifest
from .models import Filesystem, Heartbeat, Snapshot, SnapshotStatus
from .snapshots import IngestError, find_filesystem_for, ingest_scan, record_scan

log = logging.getLogger("pinax.watch")

HEARTBEAT = "ingest-watch"
WAITING = {"running", "interrupted"}  # fs-inventory may still finish or resume these
MAX_DEPTH = 3


@dataclass
class PassResult:
    """What one pass did; scan folders are listed by path."""

    found: int = 0
    ingested: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{len(v)} {k}" for k, v in asdict(self).items() if k != "found" and v]
        return f"{self.found} scans found" + (": " + ", ".join(parts) if parts else ", nothing new")


def find_scans(folder: Path, max_depth: int = MAX_DEPTH) -> list[Path]:
    """Folders holding a manifest.json at or below `folder`, not looking inside a scan folder or hidden ones."""
    out: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            return
        if any(e.name == "manifest.json" and e.is_file() for e in entries):
            out.append(d)
            return
        if depth < max_depth:
            for e in entries:
                if e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                    walk(Path(e.path), depth + 1)

    walk(Path(folder), 0)
    return out


def _verify(scan_dir: Path, settings: Settings) -> str | None:
    """fs-inventory's own check of a finished scan; the problem it reports, or None."""
    binary = settings.fs_inventory_path()
    if not (shutil.which(binary) or Path(binary).exists()):
        log.warning("fs-inventory is not installed, so %s is ingested without `fs-inventory verify`", scan_dir)
        return None
    proc = subprocess.run([binary, "verify", str(scan_dir)], capture_output=True, text=True, check=False)
    if proc.returncode == 0:
        return None
    detail = " ".join((proc.stderr or proc.stdout).split())
    return f"fs-inventory verify failed: {detail[:500]}"


def _recorded(scan_id: str) -> bool:
    with session_scope() as s:
        return s.scalar(select(Snapshot.id).where(Snapshot.scan_id == scan_id)) is not None


def watch_pass(folder: Path, *, fs_slug: str | None = None, allow_incomplete: bool = False, verify: bool = True,
               settings: Settings | None = None, progress: Callable[[str], None] | None = None,
               on_build: Callable[[Path], None] | None = None) -> PassResult:
    """Look at every scan under `folder` once, and ingest or record the new ones."""
    settings = settings or get_settings()
    say = progress or log.info
    result = PassResult()
    scans = []
    for scan_dir in find_scans(folder):
        try:
            manifest = read_manifest(scan_dir)
        except (BuildError, OSError, ValueError) as e:
            log.warning("cannot read the manifest of %s: %s", scan_dir, e)
            result.waiting.append(str(scan_dir))
            continue
        scans.append((manifest.get("started_at") or "", scan_dir, manifest))
    for _, scan_dir, manifest in sorted(scans, key=lambda t: (t[0], str(t[1]))):
        result.found += 1
        scan_id, status = manifest.get("scan_id"), manifest.get("status")
        if not scan_id or _recorded(scan_id):
            continue
        if status in WAITING:
            result.waiting.append(str(scan_dir))
            continue
        root = (manifest.get("roots") or [{}])[0].get("path")
        with session_scope() as s:
            fs = find_filesystem_for(s, root, fs_slug)
            if fs is None:
                log.warning("no filesystem is registered for the root of %s (%s); add one with "
                            "`pinax filesystem add`", scan_dir, root)
                result.unmatched.append(str(scan_dir))
                continue
            reason = None
            if status == "incomplete" and not allow_incomplete:
                stalled = len(manifest.get("stalled") or [])
                reason = (f"the scan is incomplete ({stalled} directories given up on); "
                          "accept it with `pinax ingest --allow-incomplete`")
            elif status not in ("complete", "incomplete"):
                reason = f"the scan's status is {status!r}"
            elif verify and status == "complete":
                problem = _verify(scan_dir, settings)
                if problem:
                    if record_scan(s, scan_dir, manifest, fs, SnapshotStatus.FAILED, problem, settings):
                        result.failed.append(str(scan_dir))
                    continue
            if reason:
                if record_scan(s, scan_dir, manifest, fs, SnapshotStatus.SKIPPED, reason, settings):
                    say(f"Skipped {scan_dir}: {reason}")
                    result.skipped.append(str(scan_dir))
                continue
            fs_id = fs.id
        if on_build:
            on_build(scan_dir)
        say(f"Ingesting {scan_dir}")
        try:
            with session_scope() as s:
                snap = ingest_scan(s, scan_dir, fs_slug=fs_slug, allow_incomplete=allow_incomplete, only_new=True,
                                   settings=settings, progress=say)
                say(f"Snapshot {snap.id}: {snap.files:,} files from {scan_dir}")
                result.ingested.append(str(scan_dir))
        except IngestError as e:
            if "already" in str(e):  # another watcher or a manual ingest got there first
                continue
            # Refused before the build started, so nothing recorded it yet.
            with session_scope() as s:
                fs = s.get(Filesystem, fs_id)
                record_scan(s, scan_dir, manifest, fs, SnapshotStatus.FAILED, str(e), settings)
            log.error("could not ingest %s: %s", scan_dir, e)
            result.failed.append(str(scan_dir))
        except Exception as e:  # noqa: BLE001 - a watcher outlives any one scan; ingest_scan recorded the error
            log.error("ingest of %s failed: %s", scan_dir, e)
            result.failed.append(str(scan_dir))
    return result


def beat(detail: dict) -> None:
    with session_scope() as s:
        hb = s.get(Heartbeat, HEARTBEAT) or Heartbeat(name=HEARTBEAT)
        hb.updated_at = utcnow()
        hb.detail = detail
        s.add(hb)


def heartbeat_json(hb: Heartbeat | None) -> dict | None:
    """The watcher's state for the interface; `stale` when it has missed three passes and is not building."""
    if hb is None:
        return None
    d = hb.detail or {}
    interval = d.get("interval") or 300
    stale = d.get("state") == "stopped" or (
        d.get("state") != "building" and utcnow() - hb.updated_at > timedelta(seconds=3 * interval + 60))
    return {"updated_at": hb.updated_at.isoformat() + "Z", "stale": stale, **d}


def watch(folder: Path, *, interval: int = 300, once: bool = False, fs_slug: str | None = None,
          allow_incomplete: bool = False, verify: bool = True, settings: Settings | None = None) -> PassResult:
    """Run passes every `interval` seconds (or one, with `once`), keeping the heartbeat up to date."""
    folder = Path(folder).resolve()
    base = {"folder": str(folder), "interval": interval, "pid": os.getpid(), "host": os.uname().nodename}
    last: dict = {}
    try:
        while True:
            beat({**base, "state": "checking", "last": last})
            result = watch_pass(folder, fs_slug=fs_slug, allow_incomplete=allow_incomplete, verify=verify,
                                settings=settings,
                                on_build=lambda d, last=last: beat({**base, "state": "building", "scan": str(d),
                                                                    "last": last}))
            log.info("%s: %s", folder, result.summary())
            last = {"at": utcnow().isoformat() + "Z", **{k: (v if k == "found" else len(v))
                                                        for k, v in asdict(result).items()}}
            if result.failed:
                last["failed_scans"] = result.failed[-5:]
            beat({**base, "state": "idle", "last": last})
            if once:
                return result
            time.sleep(interval)
    finally:
        if not once:
            beat({**base, "state": "stopped", "last": last})

