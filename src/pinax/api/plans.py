# SPDX-License-Identifier: Apache-2.0
"""Cleanup plans: the folders each lab means to free space from, and how (plans.py). A lab's members and
administrators see and change its plan; Pinax itself changes nothing on the storage."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import marks, plans
from ..auth import Identity, current_identity
from ..db import get_session
from ..models import CleanupItem, Filesystem, Lab
from ..snapshots import cold_bucket

router = APIRouter(prefix="/plans", tags=["plans"])


class ItemBody(BaseModel):
    fs: str | None = None
    path: str | None = None
    action: str
    to: str | None = None  # a move's filesystem
    note: str = ""


def _fs(session: Session, slug: str | None) -> Filesystem:
    fs = session.scalar(select(Filesystem).where(Filesystem.slug == (slug or "")))
    if fs is None:
        raise HTTPException(404, "No such filesystem")
    return fs


def _item(session: Session, ident: Identity, item_id: int) -> CleanupItem:
    item = session.get(CleanupItem, item_id)
    if item is None or not plans.can_change(ident, item):
        raise HTTPException(404, "No such folder in a plan you can see")
    return item


def _apply(session: Session, change):
    try:
        out = change()
    except (plans.PlanError, marks.MarkError) as e:
        session.rollback()
        raise HTTPException(e.status, str(e)) from e
    session.commit()
    return out


@router.get("")
def lab_plans(lab: str | None = None, identity: Identity = Depends(current_identity),
              session: Session = Depends(get_session)):
    """The cleanup plans of the person's labs and departments, or of one (any, for administrators)."""
    q = select(Lab).order_by(Lab.name)
    if not identity.is_admin:
        q = q.where(Lab.id.in_(identity.lab_roles))
    choices = session.scalars(q).all()
    shown = [c for c in choices if c.slug == lab] if lab else [c for c in choices if c.id in identity.lab_roles]
    if lab and not shown:
        raise HTTPException(404, "No such lab or department")
    return {"labs": [plans.lab_plan(session, c) for c in shown],
            "choices": [{"slug": c.slug, "name": c.name} for c in choices]}


@router.get("/suggestions")
def suggestions(lab: str, identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    """Folders of a lab worth adding to its plan, each with what it would free and save."""
    found = session.scalar(select(Lab).where(Lab.slug == lab))
    if found is None or not (identity.is_admin or found.id in identity.lab_roles):
        raise HTTPException(404, "No such lab or department")
    return {"lab": found.slug, "suggestions": plans.suggestions(session, found)}


@router.get("/here")
def here(fs: str, path: str, identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    """For the age map's panel: whether this folder can go in a plan, and its lab's plan so far."""
    return plans.here(session, identity, _fs(session, fs), path)


@router.post("")
def add(body: ItemBody, identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    """Add a folder to the plan of the lab that owns it: to delete, move to another filesystem, or compress."""
    fs = _fs(session, body.fs)
    return _apply(session, lambda: plans.item_json(session, plans.add(
        session, identity, fs, body.path or "", body.action, to=body.to, note=body.note, cold_from=cold_bucket()), []))


@router.put("/{item_id}")
def change(item_id: int, body: ItemBody, identity: Identity = Depends(current_identity),
           session: Session = Depends(get_session)):
    item = _item(session, identity, item_id)
    return _apply(session, lambda: plans.item_json(session, plans.change(
        session, identity, item, body.action, to=body.to, note=body.note), []))


@router.delete("/{item_id}")
def remove(item_id: int, identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    item = _item(session, identity, item_id)
    _apply(session, lambda: plans.remove(session, identity, item))
    return {"removed": item_id}


class MoveBody(BaseModel):
    accept: bool


@router.post("/{item_id}/moved")
def moved(item_id: int, body: MoveBody, identity: Identity = Depends(current_identity),
          session: Session = Depends(get_session)):
    """Confirm that a planned folder moved where its inode is now (the plan follows it), or say it did not."""
    item = _item(session, identity, item_id)
    if body.accept:
        _apply(session, lambda: marks.accept_move(session, identity.user, item.mark, cold_from=cold_bucket()))
    else:
        _apply(session, lambda: marks.dismiss_move(session, identity.user, item.mark))
    return plans.item_json(session, item, [])
