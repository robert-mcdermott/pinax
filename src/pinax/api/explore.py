# SPDX-License-Identifier: Apache-2.0
"""Explore and report endpoints. Each takes an optional filesystem and path; without them, the
request covers the person's home (all their folders, or every filesystem for administrators)."""

import csv
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import explore
from ..auth import Identity, current_identity
from ..config import get_settings
from ..db import get_session

router = APIRouter(tags=["explore"])


@router.get("/home")
def home(identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    """The person's top-level folders with their latest totals, and their labs."""
    return {"folders": explore.folder_rows(session, identity), "labs": explore.lab_summaries(session, identity)}


@router.get("/tree")
def tree(fs: str | None = None, path: str | None = None, metric: str = "bytes", depth: int = Query(1, ge=1, le=2),
         snapshot: int | None = None, lab: str | None = None,
         identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    """A directory and its largest children (and grandchildren with depth=2), for the treemap."""
    return explore.tree(session, identity, fs, path, metric=metric, depth=depth, snap_id=snapshot, lab_slug=lab)


@router.get("/types")
def types(fs: str | None = None, path: str | None = None, snapshot: int | None = None, lab: str | None = None,
          identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    """Bytes and files by category and file type over a subtree."""
    return explore.types(session, identity, fs, path, snapshot, lab)


@router.get("/owners")
def owners(fs: str | None = None, path: str | None = None, snapshot: int | None = None, lab: str | None = None,
           identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    """Bytes and files by owning account over a subtree."""
    return explore.owners(session, identity, fs, path, snapshot, lab)


@router.get("/cold")
def cold(fs: str | None = None, path: str | None = None, older: int = Query(..., ge=1), min_bytes: int = 0,
         limit: int = Query(200, ge=1, le=2000), snapshot: int | None = None, lab: str | None = None,
         identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    """The largest folders in which nothing has changed for longer than the chosen age."""
    return explore.cold(session, identity, fs, path, older=older, min_bytes=min_bytes, limit=limit,
                        snap_id=snapshot, lab_slug=lab)


@router.get("/files")
def files(fs: str | None = None, path: str | None = None, q: str = "", in_path: bool = False, category: str = "",
          ext: str = "", min_size: int | None = None, max_size: int | None = None, older: int | None = None,
          newer: int | None = None, uid: int | None = None, direct: bool = False, sort: str = "size",
          limit: int = Query(100, ge=1), fmt: str = "json", snapshot: int | None = None, lab: str | None = None,
          identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    """Find files: by name or glob, type, size, age, and owner, largest or oldest first. fmt=csv downloads."""
    s = get_settings()
    flt = explore.FileFilter(q=q.strip(), in_path=in_path, category=category, ext=ext.strip(), min_size=min_size,
                             max_size=max_size, older=older, newer=newer, uid=uid, direct=direct)
    if fmt == "csv":
        res = explore.files(session, identity, fs, path, flt=flt, sort=sort, limit=min(limit, s.export_max_rows),
                            snap_id=snapshot, lab_slug=lab, with_summary=False)
        return _csv(res["files"])
    return explore.files(session, identity, fs, path, flt=flt, sort=sort, limit=min(limit, s.search_max_rows),
                         snap_id=snapshot, lab_slug=lab)


@router.get("/history")
def history(fs: str | None = None, path: str | None = None, snapshot: int | None = None, lab: str | None = None,
            identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    """Size, files, age profile, cold share, and average age over the scans, oldest first: every scan for a home,
    lab, filesystem, or top-level folder; the retained snapshots for a deeper folder."""
    return explore.history(session, identity, fs, path, snapshot, lab)


COLUMNS = ["fs", "path", "size", "alloc", "mtime", "atime", "category", "ext", "owner", "uid", "gid", "nlink", "kind"]


def _csv(rows: list[dict]) -> StreamingResponse:
    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([r.get(c) for c in COLUMNS])
            if buf.tell() > 64 * 1024:
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()
        yield buf.getvalue()

    return StreamingResponse(gen(), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="pinax-files.csv"'})
