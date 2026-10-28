# SPDX-License-Identifier: Apache-2.0
"""Capacity, for administrators: how much each filesystem holds, where that comes from, and when it fills at its
trend (capacity.py); setting it from a day on, every change audited."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import capacity, costs
from ..auth import Identity, require_admin
from ..db import get_session, utcnow
from ..models import Capacity, Filesystem

router = APIRouter(tags=["capacity"])
admin = APIRouter(prefix="/admin/capacity")


class CapacityBody(BaseModel):
    starts: str
    amount: str
    unit: str = "TB"


def _fs(session: Session, slug: str) -> Filesystem:
    fs = session.scalar(select(Filesystem).where(Filesystem.slug == slug))
    if fs is None:
        raise HTTPException(404, "No such filesystem")
    return fs


def _apply(session: Session, fs: Filesystem, change) -> dict:
    try:
        change()
    except costs.CostError as e:
        session.rollback()
        raise HTTPException(e.status, str(e)) from e
    session.commit()
    return capacity.summary(session, fs)


@admin.get("")
def overview(_: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    """Every active filesystem's capacity, its schedule, and when it fills."""
    filesystems = session.scalars(select(Filesystem).where(Filesystem.active.is_(True)).order_by(Filesystem.name))
    return {"today": utcnow().date().isoformat(), "units": list(capacity.UNITS),
            "filesystems": [capacity.summary(session, fs) for fs in filesystems]}


@admin.post("/{fs}")
def set_capacity(fs: str, body: CapacityBody, identity: Identity = Depends(require_admin),
                 session: Session = Depends(get_session)):
    """The filesystem's capacity from a day on: a past day says what it held then; a future one is scheduled."""
    f = _fs(session, fs)
    return _apply(session, f, lambda: capacity.set_capacity(session, identity.user, f, body.starts, body.amount,
                                                            body.unit))


@admin.delete("/{fs}/{row_id}")
def remove_capacity(fs: str, row_id: int, identity: Identity = Depends(require_admin),
                    session: Session = Depends(get_session)):
    f = _fs(session, fs)
    row = session.get(Capacity, row_id)
    if row is None or row.filesystem_id != f.id:
        raise HTTPException(404, "No such capacity")
    return _apply(session, f, lambda: capacity.remove_capacity(session, identity.user, row))


router.include_router(admin)
