# SPDX-License-Identifier: Apache-2.0
"""Searches as background jobs (R13). A file search of a whole filesystem reads every file in it, which can take longer
than a page may wait (`PINAX_QUERY_TIMEOUT`). Run in the background, it is done by Pinax's own worker threads, at most
`PINAX_WIDE_QUERIES` at once (taking the same turns as whole-filesystem queries in pages), each within
`PINAX_JOB_TIMEOUT`, and writes every match, in order, to a Parquet file under VAR_ROOT/jobs, kept for
`PINAX_JOB_DAYS`. A job searches the scans the person saw when they asked; only they and administrators see it. A job
left queued or running when Pinax stops is marked as stopped when it starts again."""

import dataclasses
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import duckdb
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import limits, metrics
from .auth import Identity
from .config import get_settings
from .db import session_scope, utcnow
from .models import Filesystem, SearchJob, Snapshot

QUEUED, RUNNING, DONE, FAILED, STOPPED = "queued", "running", "done", "failed", "stopped"
_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()
_running: dict[int, limits.Budget] = {}  # a running job's budget, so it can be stopped


class JobError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def job_dir(job_id: int) -> Path:
    return get_settings().var_root / "jobs" / str(job_id)


def _workers() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=max(1, get_settings().wide_queries or 2), thread_name_prefix="search")
        return _pool


def submit(session: Session, ident: Identity, fs_slug, path, flt, sort: str, *, scan=None, lab_slug=None,
           wait: bool = False) -> SearchJob:
    """Check the search as a page would (the person may see the place), then queue it (or, with `wait`, run it
    now, as the demo's seed does)."""
    from .explore import FILE_SORTS, resolve

    if sort not in FILE_SORTS:
        raise JobError("Unknown sort")
    places = resolve(session, ident, fs_slug, path, scan, lab_slug)
    request = {"fs": fs_slug, "path": path, "lab": lab_slug, "sort": sort, "filter": dataclasses.asdict(flt),
               "places": [{"fs": p.fs.slug, "path": p.path, "snapshot": p.snap.id} for p in places]}
    job = SearchJob(user_id=ident.user.id, request=request, status=QUEUED)
    session.add(job)
    session.commit()
    if wait:
        _run(job.id)
        session.refresh(job)
    else:
        _workers().submit(_run, job.id)
    return job


def _run(job_id: int) -> None:
    from .explore import FILE_SORTS, FileFilter
    from .snapshots import indexes

    settings = get_settings()
    with session_scope() as s:
        job = s.get(SearchJob, job_id)
        if job is None or job.status != QUEUED:
            return
        job.status, job.started_at = RUNNING, utcnow()
        request = dict(job.request)
    budget = limits.Budget(settings.job_timeout)
    _running[job_id] = budget
    token = limits._budget.set(budget)
    timer = threading.Timer(settings.job_timeout, budget.stop, [limits.TIMEOUT])
    timer.start()
    out = job_dir(job_id)
    status, message, files, size = DONE, "", 0, 0
    try:
        out.mkdir(parents=True, exist_ok=True)
        flt = FileFilter(**request["filter"])
        with session_scope() as s:
            snaps = [(p, s.get(Snapshot, p["snapshot"])) for p in request["places"]]
            for i, (p, snap) in enumerate(snaps):
                if snap is None or snap.status != "ready":
                    raise JobError("A scan it searches is no longer kept in full")
                idx = indexes.get(snap)
                where, args = flt.sql(idx.ref_epoch)
                n, b = idx.files_to(p["path"], out / f"part-{i:03d}.parquet", where=where, params=args,
                                    order=FILE_SORTS[request["sort"]], direct_only=flt.direct)
                files, size = files + n, size + b
    except limits.Stopped as e:
        status = STOPPED
        message = (f"Stopped after {settings.job_timeout:,} seconds (PINAX_JOB_TIMEOUT)" if e.reason == limits.TIMEOUT
                   else "Stopped" if e.reason == limits.GONE else "Stopped: too many searches of whole filesystems at once")
    except (JobError, duckdb.Error, OSError) as e:
        status, message = FAILED, str(e)
    finally:
        timer.cancel()
        limits._budget.reset(token)
        _running.pop(job_id, None)
    with session_scope() as s:
        job = s.get(SearchJob, job_id)
        if job is None:  # deleted while it ran
            shutil.rmtree(out, ignore_errors=True)
            return
        job.status, job.message, job.finished_at = status, message, utcnow()
        job.files, job.bytes = (files, size) if status == DONE else (None, None)
    metrics.SEARCHES.labels(status).inc()
    if status != DONE:
        shutil.rmtree(out, ignore_errors=True)


def can_see(ident: Identity, job: SearchJob) -> bool:
    return ident.is_admin or job.user_id == ident.user.id


def stop_or_delete(session: Session, job: SearchJob) -> None:
    """Stop a job that is running (its results are not kept), or delete one and its results."""
    if job.id in _running:
        _running[job.id].stop(limits.GONE)
    session.delete(job)
    session.flush()
    shutil.rmtree(job_dir(job.id), ignore_errors=True)


def recover() -> None:
    """At start-up: jobs left queued or running were stopped with Pinax; results older than PINAX_JOB_DAYS go."""
    settings = get_settings()
    cutoff = utcnow() - timedelta(days=settings.job_days)
    with session_scope() as s:
        for job in s.scalars(select(SearchJob).where(SearchJob.status.in_((QUEUED, RUNNING)))):
            job.status, job.message, job.finished_at = STOPPED, "Pinax stopped while it ran", utcnow()
        for job in s.scalars(select(SearchJob).where(SearchJob.created_at < cutoff)):
            shutil.rmtree(job_dir(job.id), ignore_errors=True)
            s.delete(job)


def results(job: SearchJob, *, offset: int = 0, limit: int = 500) -> list[dict]:
    """A page of a job's matches, in the order asked for (each place's in order, merged)."""
    from .explore import FILE_SORTS

    parts = sorted(job_dir(job.id).glob("part-*.parquet"))
    if job.status != DONE or not parts:
        return []
    order = FILE_SORTS[job.request["sort"]]
    source = "read_parquet([" + ", ".join("'" + str(p).replace("'", "''") + "'" for p in parts) + "])"
    with duckdb.connect() as con:
        cur = con.execute(f"SELECT * FROM {source} ORDER BY {order} LIMIT ? OFFSET ?", [limit, offset])
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def all_parts(job: SearchJob) -> list[Path]:
    return sorted(job_dir(job.id).glob("part-*.parquet"))


def job_json(session: Session, job: SearchJob) -> dict:
    fs_names = {f.slug: f.name for f in session.scalars(select(Filesystem))}
    r = job.request
    return {"id": job.id, "status": job.status, "message": job.message, "files": job.files, "bytes": job.bytes,
            "fs": r.get("fs"), "fs_name": fs_names.get(r.get("fs")), "path": r.get("path"), "sort": r.get("sort"),
            "filter": r.get("filter"), "by": job.user.display_name,
            "created_at": job.created_at.isoformat() + "Z",
            "started_at": job.started_at.isoformat() + "Z" if job.started_at else None,
            "finished_at": job.finished_at.isoformat() + "Z" if job.finished_at else None,
            "expires_at": (job.created_at + timedelta(days=get_settings().job_days)).isoformat() + "Z"}
