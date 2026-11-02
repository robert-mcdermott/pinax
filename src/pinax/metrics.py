# SPDX-License-Identifier: Apache-2.0
"""Prometheus metrics (O4, O5): `/metrics`, for a site's monitoring to trend and alert on.

Off until PINAX_METRICS_TOKEN is set, and then only with it (`Authorization: Bearer ...`, Prometheus's
`authorization` setting). The figures name filesystems, never labs, people, or paths.

- **From the catalog**, worked out when Prometheus asks (at most once every CACHE_SECONDS): for each filesystem, when
  its newest scan was taken, ingested, and failed, how long the scan and its ingest took, its totals, how much is
  cold, its capacity, how much of it is used, how fast that grows, and when it fills; the watcher's heartbeat;
  snapshots by state, indexes and their size, background searches, people, labs, and sessions; Pinax's own disk.
  These count whichever process did the work: the watcher, a timer, or the command line.
- **From this web app**, since it started: requests by method, route, and status, and how long they took; index
  queries and their time; queries stopped and why; whole-filesystem query turns in use and waited for; searches in
  the background finished; and audit events by action, sign-ins among them. Prometheus's `rate()` makes rates of
  them.
"""

import shutil
import threading
import time
from datetime import UTC, datetime

from prometheus_client import CONTENT_TYPE_LATEST as CONTENT_TYPE  # noqa: F401 - for the endpoint
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    GCCollector,
    Histogram,
    PlatformCollector,
    ProcessCollector,
    disable_created_metrics,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily, InfoMetricFamily
from prometheus_client.registry import Collector

CACHE_SECONDS = 15
disable_created_metrics()  # no `_created` series beside each counter
REGISTRY = CollectorRegistry()
ProcessCollector(registry=REGISTRY)
PlatformCollector(registry=REGISTRY)
GCCollector(registry=REGISTRY)

BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 180, 600)
REQUESTS = Counter("pinax_http_requests", "Requests answered, by method, route (its template), and status",
                   ["method", "route", "status"], registry=REGISTRY)
DURATION = Histogram("pinax_http_request_duration_seconds", "How long requests took, by method and route",
                     ["method", "route"], buckets=BUCKETS, registry=REGISTRY)
IN_PROGRESS = Gauge("pinax_http_requests_in_progress", "Requests being answered now", registry=REGISTRY)
QUERIES = Counter("pinax_index_queries", "Index queries run for requests", registry=REGISTRY)
QUERY_SECONDS = Counter("pinax_index_query_seconds", "Time index queries took, for requests", registry=REGISTRY)
STOPPED = Counter("pinax_queries_stopped", "Requests whose queries were stopped: timeout (PINAX_QUERY_TIMEOUT), busy "
                  "(no whole-filesystem turn in time), or gone (the browser left)", ["reason"], registry=REGISTRY)
CATALOG_BUSY = Counter("pinax_catalog_busy", "Requests turned away as busy: no catalog connection was free in time "
                       "(PINAX_DATABASE_POOL_SIZE)", registry=REGISTRY)
SEARCHES = Counter("pinax_search_jobs_finished", "Searches in the background that finished, by how: done, failed, "
                   "or stopped", ["status"], registry=REGISTRY)
AUDIT = Counter("pinax_audit_events", "Audit events this process recorded, by action (auth.signed_in, "
                "auth.sign_in_failed, and auth.locked are sign-ins)", ["action"], registry=REGISTRY)


def request_done(method: str, route: str, status: int, seconds: float) -> None:
    REQUESTS.labels(method, route, str(status)).inc()
    DURATION.labels(method, route).observe(seconds)


def _ts(value: datetime | None) -> float | None:
    return value.replace(tzinfo=UTC).timestamp() if value else None


class CatalogCollector(Collector):
    """The figures that come from the catalog and the disk, kept for CACHE_SECONDS so several scrapers cost one."""

    def __init__(self):
        self._lock = threading.Lock()
        self._at = 0.0
        self._families: list = []

    def collect(self):
        with self._lock:
            if time.monotonic() - self._at >= CACHE_SECONDS:
                self._families = list(_from_catalog())
                self._at = time.monotonic()
            return list(self._families)


def _gauge(name: str, doc: str, labels=()) -> GaugeMetricFamily:
    return GaugeMetricFamily(name, doc, labels=list(labels))


def _from_catalog():
    # Imported here: logs.py, which db.py imports, imports this module.
    from sqlalchemy.exc import SQLAlchemyError

    from . import __version__, limits
    from .config import get_settings
    from .db import get_engine, session_scope
    from .snapshots import indexes

    settings = get_settings()
    engine = get_engine()
    yield InfoMetricFamily("pinax", "Pinax's version and its catalog's database",
                           value={"version": __version__, "database": engine.dialect.name})
    disk = shutil.disk_usage(settings.var_root)
    yield GaugeMetricFamily("pinax_var_size_bytes", "Size of the disk holding PINAX_VAR_ROOT (indexes, the SQLite "
                            "catalog, search results)", value=disk.total)
    yield GaugeMetricFamily("pinax_var_free_bytes", "Free space on the disk holding PINAX_VAR_ROOT", value=disk.free)
    yield GaugeMetricFamily("pinax_indexes_open", "Snapshot indexes this web app has open", value=len(indexes._items))
    running, waiting = limits.wide_state()
    turns = _gauge("pinax_wide_queries", "Whole-filesystem file queries running, and waiting for one of the "
                   "PINAX_WIDE_QUERIES turns", ["state"])
    turns.add_metric(["running"], running)
    turns.add_metric(["waiting"], waiting)
    yield turns
    up = _gauge("pinax_catalog_up", "1 if the catalog could be read")
    try:
        with session_scope() as s:
            families = _catalog_families(s)
    except SQLAlchemyError:
        up.add_metric([], 0)
        yield up
        return
    up.add_metric([], 1)
    yield up
    yield from families


def _catalog_families(s) -> list:
    from sqlalchemy import func, select

    from . import capacity
    from .db import utcnow
    from .models import Filesystem as FS
    from .models import FolderStat, Heartbeat, Lab, LoginSession, SearchJob, Snapshot, SnapshotStatus, User
    from .watch import HEARTBEAT, heartbeat_json

    fss = s.scalars(select(FS).where(FS.active).order_by(FS.slug)).all()
    slug = {f.id: f.slug for f in fss}
    by_fs = ["filesystem"]
    info = InfoMetricFamily("pinax_filesystem", "Each filesystem Pinax reports on", labels=by_fs)
    for f in fss:
        info.add_metric([f.slug], {"name": f.name})
    out = [info]

    counts = _gauge("pinax_snapshots", "Snapshots by filesystem and state (ready: indexed and kept; pruned: only its "
                    "folder history kept; failed or skipped: not ingested)", ["filesystem", "status"])
    for fs_id, status, n in s.execute(select(Snapshot.filesystem_id, Snapshot.status, func.count())
                                      .group_by(Snapshot.filesystem_id, Snapshot.status)):
        if fs_id in slug:
            counts.add_metric([slug[fs_id], status], n)
    out.append(counts)

    ready = s.scalars(select(Snapshot).where(Snapshot.status == SnapshotStatus.READY)
                      .order_by(Snapshot.started_at.desc())).all()
    latest = {}
    for snap in ready:
        latest.setdefault(snap.filesystem_id, snap)
    failed = dict(s.execute(select(Snapshot.filesystem_id, func.max(Snapshot.started_at))
                            .where(Snapshot.status.in_((SnapshotStatus.FAILED, SnapshotStatus.SKIPPED)))
                            .group_by(Snapshot.filesystem_id)).all())
    cold = dict(s.execute(select(FolderStat.snapshot_id, func.sum(FolderStat.cold_bytes))
                          .where(FolderStat.snapshot_id.in_([x.id for x in latest.values()]))
                          .group_by(FolderStat.snapshot_id)).all()) if latest else {}
    index_bytes = {}
    for snap in ready:
        index_bytes[snap.filesystem_id] = index_bytes.get(snap.filesystem_id, 0) + (snap.index_bytes or 0)

    per_fs = {
        "pinax_scan_last_success_timestamp_seconds": "When the newest scan that was ingested was taken",
        "pinax_ingest_last_success_timestamp_seconds": "When the newest scan was ingested",
        "pinax_scan_last_failure_timestamp_seconds": "When the newest scan that failed or was skipped was taken "
                                                     "(newer than the last success: the newest scan was not ingested)",
        "pinax_scan_duration_seconds": "How long the newest ingested scan took fs-inventory",
        "pinax_ingest_duration_seconds": "How long the newest ingest took Pinax",
        "pinax_filesystem_bytes": "Logical size of the newest ingested scan",
        "pinax_filesystem_allocated_bytes": "Space on disk of the newest ingested scan",
        "pinax_filesystem_files": "Files in the newest ingested scan",
        "pinax_filesystem_directories": "Folders in the newest ingested scan",
        "pinax_filesystem_cold_bytes": "Logical size, in the newest ingested scan, of data unchanged for PINAX_COLD_DAYS",
        "pinax_scan_errors": "Entries fs-inventory could not read in the newest ingested scan",
        "pinax_index_bytes": "Size of the indexes kept for the filesystem",
        "pinax_filesystem_capacity_bytes": "The filesystem's capacity now (as set, or as the storage reported)",
        "pinax_filesystem_used_bytes": "How much of the capacity is used, by the measure Pinax uses for it",
        "pinax_filesystem_growth_bytes_per_month": "How fast use grows, at the trend of the last six months of scans",
        "pinax_filesystem_fill_timestamp_seconds": "When the filesystem fills at its trend, if within three years",
        "pinax_filesystem_fill_earliest_timestamp_seconds": "When it could fill at the fast end of its trend's range, "
                                                            "if within three years",
    }
    fam = {name: _gauge(name, doc, by_fs) for name, doc in per_fs.items()}

    def put(name: str, fs_slug: str, value) -> None:
        if value is not None:
            fam[name].add_metric([fs_slug], float(value))

    for f in fss:
        snap = latest.get(f.id)
        put("pinax_scan_last_failure_timestamp_seconds", f.slug, _ts(failed.get(f.id)))
        put("pinax_index_bytes", f.slug, index_bytes.get(f.id, 0))
        if snap is not None:
            put("pinax_scan_last_success_timestamp_seconds", f.slug, _ts(snap.started_at))
            put("pinax_ingest_last_success_timestamp_seconds", f.slug, _ts(snap.ingested_at))
            put("pinax_scan_duration_seconds", f.slug, snap.scan_seconds)
            put("pinax_ingest_duration_seconds", f.slug, snap.build_seconds)
            put("pinax_filesystem_bytes", f.slug, snap.bytes)
            put("pinax_filesystem_allocated_bytes", f.slug, snap.alloc_bytes)
            put("pinax_filesystem_files", f.slug, snap.files)
            put("pinax_filesystem_directories", f.slug, snap.dirs)
            put("pinax_filesystem_cold_bytes", f.slug, cold.get(snap.id))
            put("pinax_scan_errors", f.slug, snap.errors)
        cap = capacity.summary(s, f)
        put("pinax_filesystem_capacity_bytes", f.slug, cap["capacity"]["bytes"])
        put("pinax_filesystem_used_bytes", f.slug, cap["used"])
        if cap["forecast"] and cap["forecast"].get("ok"):
            put("pinax_filesystem_growth_bytes_per_month", f.slug, cap["forecast"].get("per_month"))
        for key, name in (("at", "pinax_filesystem_fill_timestamp_seconds"),
                          ("earliest", "pinax_filesystem_fill_earliest_timestamp_seconds")):
            if cap["fills"] and cap["fills"].get(key):
                put(name, f.slug, datetime.fromisoformat(cap["fills"][key].removesuffix("Z")).replace(tzinfo=UTC).timestamp())
    out += fam.values()

    hb = heartbeat_json(s.get(Heartbeat, HEARTBEAT))
    watcher = _gauge("pinax_watcher_up", "1 if automatic ingest (`pinax ingest --watch`) is running and responding; "
                     "absent if it has never run")
    beat = _gauge("pinax_watcher_last_heartbeat_timestamp_seconds", "When automatic ingest last reported")
    if hb is not None:
        watcher.add_metric([], 0 if hb["stale"] else 1)
        beat.add_metric([], datetime.fromisoformat(hb["updated_at"].removesuffix("Z")).replace(tzinfo=UTC).timestamp())
    out += [watcher, beat]

    jobs = _gauge("pinax_search_jobs", "Searches in the background kept, by state", ["status"])
    for status, n in s.execute(select(SearchJob.status, func.count()).group_by(SearchJob.status)):
        jobs.add_metric([status], n)
    people = _gauge("pinax_users", "People in the catalog, active or not", ["state"])
    for active, n in s.execute(select(User.active, func.count()).group_by(User.active)):
        people.add_metric(["active" if active else "inactive"], n)
    labs = _gauge("pinax_labs", "Labs and departments in the catalog, active or not", ["state"])
    for active, n in s.execute(select(Lab.active, func.count()).group_by(Lab.active)):
        labs.add_metric(["active" if active else "inactive"], n)
    sessions = _gauge("pinax_sessions_active", "Local sign-in sessions not yet expired")
    sessions.add_metric([], s.scalar(select(func.count()).select_from(LoginSession)
                                     .where(LoginSession.expires_at > utcnow())))
    return [*out, jobs, people, labs, sessions]


REGISTRY.register(CatalogCollector())


def render() -> bytes:
    return generate_latest(REGISTRY)
