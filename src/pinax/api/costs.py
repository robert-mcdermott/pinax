# SPDX-License-Identifier: Apache-2.0
"""Costs, for administrators: each filesystem's prices and free tiers, from a day on, every change audited."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import costs
from ..auth import Identity, require_admin
from ..config import get_settings
from ..db import get_session, utcnow
from ..models import Filesystem, FreeTier, Lab, Price

router = APIRouter(prefix="/admin/costs", tags=["costs"])


class PriceBody(BaseModel):
    starts: str
    price: str
    unit: str = "TB"


class FreeTierBody(BaseModel):
    starts: str
    amount: str | None = None  # None ends a lab's own tier
    unit: str = "TB"
    kind: str | None = None  # every lab or every department
    lab: str | None = None  # or one lab or department, by slug


def _fs(session: Session, slug: str) -> Filesystem:
    fs = session.scalar(select(Filesystem).where(Filesystem.slug == slug))
    if fs is None:
        raise HTTPException(404, "No such filesystem")
    return fs


def _schedule(session: Session, fs: Filesystem) -> dict:
    return costs.schedule_json(session, fs, utcnow().date())


def _apply(session: Session, fs: Filesystem, change) -> dict:
    try:
        change()
    except costs.CostError as e:
        session.rollback()
        raise HTTPException(e.status, str(e)) from e
    session.commit()
    return _schedule(session, fs)


@router.get("")
def overview(_: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    """Every filesystem's prices and free tiers, the labs and departments exceptions can be for, and the currency."""
    filesystems = session.scalars(select(Filesystem).order_by(Filesystem.name)).all()
    labs = session.scalars(select(Lab).order_by(Lab.name)).all()
    return {"currency": get_settings().currency, "today": utcnow().date().isoformat(), "units": list(costs.UNITS),
            "filesystems": [_schedule(session, fs) for fs in filesystems],
            "labs": [{"slug": lab.slug, "name": lab.name, "kind": lab.kind, "active": lab.active} for lab in labs]}


@router.post("/{fs}/prices")
def add_price(fs: str, body: PriceBody, identity: Identity = Depends(require_admin),
              session: Session = Depends(get_session)):
    """A price from a day on: a past day changes the charges since then; a future one is scheduled."""
    f = _fs(session, fs)
    return _apply(session, f, lambda: costs.add_price(session, identity.user, f, body.starts, body.price, body.unit))


@router.delete("/{fs}/prices/{price_id}")
def remove_price(fs: str, price_id: int, identity: Identity = Depends(require_admin),
                 session: Session = Depends(get_session)):
    f = _fs(session, fs)
    price = session.get(Price, price_id)
    if price is None or price.filesystem_id != f.id:
        raise HTTPException(404, "No such price")
    return _apply(session, f, lambda: costs.remove_price(session, identity.user, price))


@router.post("/{fs}/free-tiers")
def set_free_tier(fs: str, body: FreeTierBody, identity: Identity = Depends(require_admin),
                  session: Session = Depends(get_session)):
    """From a day on, a free tier for every lab or every department, or for one of them (or the end of its own)."""
    f = _fs(session, fs)
    lab = None
    if body.lab:
        lab = session.scalar(select(Lab).where(Lab.slug == body.lab))
        if lab is None:
            raise HTTPException(404, "No such lab or department")
    return _apply(session, f, lambda: costs.set_free_tier(session, identity.user, f, body.starts, body.amount,
                                                          body.unit, kind=body.kind, lab=lab))


@router.delete("/{fs}/free-tiers/{tier_id}")
def remove_free_tier(fs: str, tier_id: int, identity: Identity = Depends(require_admin),
                     session: Session = Depends(get_session)):
    f = _fs(session, fs)
    tier = session.get(FreeTier, tier_id)
    if tier is None or tier.filesystem_id != f.id:
        raise HTTPException(404, "No such free tier")
    return _apply(session, f, lambda: costs.remove_free_tier(session, identity.user, tier))
