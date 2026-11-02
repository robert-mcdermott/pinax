# SPDX-License-Identifier: Apache-2.0
"""The audit log (audit.py), for administrators and auditors: a page of events at a time, or every one as CSV."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Identity, require_sees_all
from ..db import get_session, release, session_scope, utcnow
from ..models import Lab, User

router = APIRouter(prefix="/admin", tags=["audit"])


@router.get("/audit")
def audit_log(person: str | None = None, action: str | None = None, lab: str | None = None, fs: str | None = None,
              path: str | None = None, since: date | None = None, until: date | None = None,
              offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500), fmt: str = "json",
              _: Identity = Depends(require_sees_all), session: Session = Depends(get_session)):
    """Audited changes and sign-ins, newest first: by or to a person, of an action (`plan.added`) or a kind
    (`plan`), to a lab, to a folder or those in it (`fs` and `path`), and from and to a day. fmt=csv downloads every
    one that matches. Without `fmt`, a page of them, how many in all, every action recorded, and the people and labs
    to choose from."""
    filters = audit.Filters(person=person or None, action=action or None, lab=lab or None, fs=fs or None,
                            path=path or None, since=since, until=until)
    try:
        audit.query(session, filters)  # a filter that names nothing answers now, not halfway through a download
        if fmt == "csv":
            release(session)  # the download reads with a session of its own, while it lasts

            def rows():
                with session_scope() as s:
                    yield from audit.csv_rows(s, filters)

            name = f"pinax-audit-{utcnow():%Y-%m-%d}.csv"
            return StreamingResponse(rows(), media_type="text/csv",
                                     headers={"Content-Disposition": f'attachment; filename="{name}"'})
        out = audit.page(session, filters, offset, limit)
    except audit.AuditError as e:
        raise HTTPException(e.status, str(e)) from e
    people = session.execute(select(User.username, User.display_name).order_by(User.display_name)).all()
    labs = session.execute(select(Lab.slug, Lab.name).order_by(Lab.name)).all()
    return {**out, "actions": audit.actions(session), "kinds": audit.KINDS,
            "people": [{"username": u, "display_name": n} for u, n in people],
            "labs": [{"slug": s, "name": n} for s, n in labs]}
