# SPDX-License-Identifier: Apache-2.0
"""Saved views (E9): a page a person keeps, under a name: a report of the app with its place and settings (the part
of its address after `#`). Kept to themselves, or shared with one of their labs, whose members see it among their
views. A view is only an address: opening it still shows each person only what they may see."""

import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .auth import Identity
from .models import AuditEvent, Lab, SavedView

ADDRESS = re.compile(r"^/[a-z]+(/[A-Za-z0-9_.-]+)*(\?[^\s<>\"'`]*)?$")


class ViewError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _address(value: str) -> str:
    value = value.strip().removeprefix("#")
    if len(value) > 2000 or not ADDRESS.match(value):
        raise ViewError("A view is a page of Pinax, such as /explore?fs=fast")
    return value


def _lab(session: Session, ident: Identity, slug: str | None) -> Lab | None:
    if not slug:
        return None
    lab = session.scalar(select(Lab).where(Lab.slug == slug))
    if lab is None or not (ident.is_admin or lab.id in ident.lab_roles):
        raise ViewError("A view is shared with one of your own labs", 404)
    return lab


def _audit(session: Session, ident: Identity, action: str, summary: str, view: SavedView) -> None:
    session.add(AuditEvent(actor_id=ident.user.id, action=action, summary=summary,
                           detail={"view": view.name, "address": view.address, "lab": view.lab.slug if view.lab else None}))


def save(session: Session, ident: Identity, name: str, address: str, lab: str | None = None) -> SavedView:
    name = name.strip()[:200]
    if not name:
        raise ViewError("A view needs a name")
    view = SavedView(user_id=ident.user.id, name=name, address=_address(address), lab=_lab(session, ident, lab))
    session.add(view)
    _audit(session, ident, "view.saved", f"View {name!r} saved" + (f", shared with the {view.lab.name}" if view.lab else ""), view)
    session.flush()
    return view


def _own(ident: Identity, view: SavedView) -> None:
    if view.user_id != ident.user.id and not ident.is_admin:
        raise ViewError("Only whoever saved a view changes it", 403)


def change(session: Session, ident: Identity, view: SavedView, *, name: str | None = None, lab: str | None = None,
           unshare: bool = False) -> SavedView:
    _own(ident, view)
    if name is not None and name.strip():
        view.name = name.strip()[:200]
    if unshare:
        view.lab = None
    elif lab is not None:
        view.lab = _lab(session, ident, lab)
    _audit(session, ident, "view.changed", f"View {view.name!r} changed", view)
    session.flush()
    return view


def delete(session: Session, ident: Identity, view: SavedView) -> None:
    _own(ident, view)
    _audit(session, ident, "view.deleted", f"View {view.name!r} deleted", view)
    session.delete(view)
    session.flush()


def visible(session: Session, ident: Identity) -> list[SavedView]:
    """A person's own views, and those shared with their labs."""
    q = select(SavedView).where(or_(SavedView.user_id == ident.user.id, SavedView.lab_id.in_(list(ident.lab_roles))))
    return session.scalars(q.order_by(SavedView.name)).all()


def view_json(view: SavedView, ident: Identity) -> dict:
    return {"id": view.id, "name": view.name, "address": f"#{view.address}", "mine": view.user_id == ident.user.id,
            "by": view.user.display_name, "lab": {"slug": view.lab.slug, "name": view.lab.name} if view.lab else None,
            "created_at": view.created_at.isoformat() + "Z"}
