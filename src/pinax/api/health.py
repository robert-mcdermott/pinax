# SPDX-License-Identifier: Apache-2.0
"""A health check for load balancers, systemd, and monitoring. It needs no sign-in, so it reports only states
and counts: never a path, a name, or anything about who owns what."""

import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from .. import __version__
from ..db import session_scope
from ..models import Heartbeat, Snapshot, SnapshotStatus
from ..watch import HEARTBEAT, heartbeat_json

router = APIRouter(tags=["health"])
log = logging.getLogger("pinax.health")


@router.get("/health")
def health():
    """`ok`; `degraded` when a ready snapshot's index is missing or automatic ingest, once started, has stopped or
    stopped responding; 503 `error` when the catalog cannot be read."""
    try:
        with session_scope() as s:
            s.execute(text("SELECT 1"))
            ready = s.scalars(select(Snapshot.index_dir).where(Snapshot.status == SnapshotStatus.READY)).all()
            watch = heartbeat_json(s.get(Heartbeat, HEARTBEAT))
    except SQLAlchemyError:
        log.exception("health check could not read the catalog")
        return JSONResponse({"status": "error", "version": __version__, "checks": {"catalog": "unavailable"}},
                            status_code=503)
    missing = sum(1 for d in ready if not (Path(d) / "index.duckdb").exists())
    if watch is None:
        ingest = "not running"  # fine: scans may be ingested by hand or from a timer
    elif watch["state"] == "stopped" or watch["stale"]:
        ingest = "stopped" if watch["state"] == "stopped" else "not responding"
    else:
        ingest = watch["state"]
    degraded = missing > 0 or ingest in ("stopped", "not responding")
    return {"status": "degraded" if degraded else "ok", "version": __version__,
            "checks": {"catalog": "ok", "snapshots_ready": len(ready), "indexes_missing": missing,
                       "automatic_ingest": ingest}}
