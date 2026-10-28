# SPDX-License-Identifier: Apache-2.0
"""Costs: each filesystem's prices and free tiers, for administrators, from a day on, every change audited; and
what each lab or department is charged, for its members (and administrators)."""

from fractions import Fraction

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import charges, costs
from ..auth import Identity, current_identity, require_admin
from ..config import get_settings
from ..db import get_session, utcnow
from ..models import Filesystem, FreeTier, Lab, Price

router = APIRouter(tags=["costs"])
admin = APIRouter(prefix="/admin/costs")


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


@admin.get("")
def overview(_: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    """Every filesystem's prices and free tiers, the labs and departments exceptions can be for, and the currency."""
    filesystems = session.scalars(select(Filesystem).order_by(Filesystem.name)).all()
    labs = session.scalars(select(Lab).order_by(Lab.name)).all()
    return {"currency": get_settings().currency, "today": utcnow().date().isoformat(), "units": list(costs.UNITS),
            "filesystems": [_schedule(session, fs) for fs in filesystems],
            "labs": [{"slug": lab.slug, "name": lab.name, "kind": lab.kind, "active": lab.active} for lab in labs]}


@admin.post("/{fs}/prices")
def add_price(fs: str, body: PriceBody, identity: Identity = Depends(require_admin),
              session: Session = Depends(get_session)):
    """A price from a day on: a past day changes the charges since then; a future one is scheduled."""
    f = _fs(session, fs)
    return _apply(session, f, lambda: costs.add_price(session, identity.user, f, body.starts, body.price, body.unit))


@admin.delete("/{fs}/prices/{price_id}")
def remove_price(fs: str, price_id: int, identity: Identity = Depends(require_admin),
                 session: Session = Depends(get_session)):
    f = _fs(session, fs)
    price = session.get(Price, price_id)
    if price is None or price.filesystem_id != f.id:
        raise HTTPException(404, "No such price")
    return _apply(session, f, lambda: costs.remove_price(session, identity.user, price))


@admin.post("/{fs}/free-tiers")
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


@admin.delete("/{fs}/free-tiers/{tier_id}")
def remove_free_tier(fs: str, tier_id: int, identity: Identity = Depends(require_admin),
                     session: Session = Depends(get_session)):
    f = _fs(session, fs)
    tier = session.get(FreeTier, tier_id)
    if tier is None or tier.filesystem_id != f.id:
        raise HTTPException(404, "No such free tier")
    return _apply(session, f, lambda: costs.remove_free_tier(session, identity.user, tier))


@admin.get("/charges")
def monthly_charges(month: str | None = None, _: Identity = Depends(require_admin),
                    session: Session = Depends(get_session)):
    """Every lab's and department's charges for a month (YYYY-MM; this month by default, estimated to its end), by
    filesystem, with the storage no one owns, and the months there are scans for."""
    month = month or utcnow().strftime("%Y-%m")
    try:
        out = charges.monthly_charges(session, month)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {**out, "months": charges.months(session)}


@router.get("/charges")
def lab_charges(month: str | None = None, lab: str | None = None, history: bool = True,
                identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    """What the person's labs and departments are charged for a month (YYYY-MM; this month by default, estimated
    to its end), by filesystem, period, and folder, with each lab's past months. Administrators may ask for any
    lab; anyone else, for their own."""
    month = month or utcnow().strftime("%Y-%m")
    if identity.is_admin:
        choices = session.scalars(select(Lab).order_by(Lab.name)).all()
    else:
        choices = session.scalars(select(Lab).where(Lab.id.in_(identity.lab_roles)).order_by(Lab.name)).all()
    if lab:
        shown = [c for c in choices if c.slug == lab]
        if not shown:
            raise HTTPException(404, "No such lab or department")
    else:
        shown = [c for c in choices if c.id in identity.lab_roles] or choices[:1]
    ids = {c.id for c in shown}
    try:
        out = charges.monthly_charges(session, month, labs_only=ids)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    fs_ids = {f.slug: f.id for f in session.scalars(select(Filesystem))}
    past = charges.lab_history(session, ids) if history else {}
    found = {r["lab"]["slug"]: r for r in out["labs"]}
    labs = []
    for c in shown:
        row = found.get(c.slug) or {"lab": {"slug": c.slug, "name": c.name, "kind": c.kind, "active": c.active},
                                    "total": str(charges.money(Fraction(0), out["currency"])),
                                    "filesystems": []}
        for line in row["filesystems"]:
            for f in line["folders"]:
                f["open"] = identity.can_view_folder(fs_ids[line["fs"]], f["folder"])  # owned then, maybe not now
        labs.append({**row, "role": identity.lab_roles.get(c.id), "history": past.get(c.slug, [])})
    return {**{k: v for k, v in out.items() if k not in ("labs", "unbilled", "by_filesystem")}, "labs": labs,
            "months": charges.months(session),
            "priced": session.scalar(select(Price.id).limit(1)) is not None,
            "choices": [{"slug": c.slug, "name": c.name, "kind": c.kind} for c in choices]}


router.include_router(admin)
