# SPDX-License-Identifier: Apache-2.0
"""Saved views: pages people keep and share with their labs (views.py)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import views
from ..auth import Identity, current_identity
from ..db import get_session
from ..models import SavedView

router = APIRouter(prefix="/views", tags=["views"])


class ViewBody(BaseModel):
    name: str | None = None
    address: str | None = None
    lab: str | None = None
    unshare: bool = False


def _apply(session: Session, change):
    try:
        out = change()
    except views.ViewError as e:
        session.rollback()
        raise HTTPException(e.status, str(e)) from e
    session.commit()
    return out


def _view(session: Session, ident: Identity, view_id: int) -> SavedView:
    view = session.get(SavedView, view_id)
    if view is None or view not in views.visible(session, ident) and not ident.is_admin:
        raise HTTPException(404, "No such view")
    return view


@router.get("")
def my_views(identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    """A person's own views, those shared with their labs, and the labs they can share with."""
    from sqlalchemy import select

    from ..models import Lab

    labs = session.scalars(select(Lab).where(Lab.id.in_(list(identity.lab_roles))).order_by(Lab.name)).all()
    return {"views": [views.view_json(v, identity) for v in views.visible(session, identity)],
            "labs": [{"slug": lab.slug, "name": lab.name} for lab in labs]}


@router.post("")
def save(body: ViewBody, identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    return _apply(session, lambda: views.view_json(views.save(session, identity, body.name or "", body.address or "",
                                                              body.lab), identity))


@router.put("/{view_id}")
def change(view_id: int, body: ViewBody, identity: Identity = Depends(current_identity),
           session: Session = Depends(get_session)):
    view = _view(session, identity, view_id)
    return _apply(session, lambda: views.view_json(views.change(session, identity, view, name=body.name, lab=body.lab,
                                                                unshare=body.unshare), identity))


@router.delete("/{view_id}")
def delete(view_id: int, identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    view = _view(session, identity, view_id)
    _apply(session, lambda: views.delete(session, identity, view))
    return {"deleted": view_id}
