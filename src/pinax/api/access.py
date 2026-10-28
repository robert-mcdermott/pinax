# SPDX-License-Identifier: Apache-2.0
"""People and access: labs and departments, their members, and which top-level folders they own."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import labs as labs_service
from .. import ownership, signin
from .. import people as people_service
from ..auth import Identity, require_admin
from ..config import get_settings
from ..db import get_session
from ..models import AuditEvent, Filesystem, Lab, LabFolder, LabMembership, User
from ..serialize import iso, lab_json, user_json

router = APIRouter(prefix="/admin", tags=["access"])


def _labs(session: Session) -> list[Lab]:
    return list(session.scalars(
        select(Lab).options(selectinload(Lab.folders).selectinload(LabFolder.filesystem),
                            selectinload(Lab.memberships).selectinload(LabMembership.user))
        .order_by(Lab.name)
    ))


@router.get("/labs")
def labs(_: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    return [lab_json(lab) for lab in _labs(session)]


def _lab(session: Session, slug: str) -> Lab:
    lab = session.scalar(select(Lab).where(Lab.slug == slug)
                         .options(selectinload(Lab.folders).selectinload(LabFolder.filesystem),
                                  selectinload(Lab.memberships).selectinload(LabMembership.user)))
    if lab is None:
        raise HTTPException(404, "No such lab or department")
    return lab


def _lab_json(session: Session, lab: Lab) -> dict:
    """A lab, with when it was added and the latest audited changes to it (by the app or an import)."""
    events = session.scalars(select(AuditEvent).where(AuditEvent.detail["lab"].as_string() == lab.slug)
                             .options(selectinload(AuditEvent.actor)).order_by(AuditEvent.ts.desc()).limit(12))
    return {**lab_json(lab), "created_at": iso(lab.created_at),
            "history": [{"ts": iso(e.ts), "action": e.action, "summary": e.summary,
                         "by": e.actor.display_name if e.actor else (e.detail or {}).get("by")} for e in events]}


class LabBody(BaseModel):
    slug: str | None = None  # when adding; it cannot be changed
    name: str = ""
    kind: str = "lab"
    division: str = ""
    research_area: str = ""


def _lab_change(session: Session, slug: str, change) -> dict:
    try:
        lab = change()
    except labs_service.LabError as e:
        session.rollback()
        raise HTTPException(e.status, str(e)) from e
    session.commit()
    session.expire_all()
    return _lab_json(session, _lab(session, lab.slug if lab else slug))


@router.get("/labs/{slug}")
def lab(slug: str, _: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    return _lab_json(session, _lab(session, slug))


@router.post("/labs", status_code=201)
def add_lab(body: LabBody, identity: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    """Add a lab or department; its members and folders are added on *People and access* and *Folders and labs*."""
    data = body.model_dump(exclude_none=True)
    return _lab_change(session, body.slug or "", lambda: labs_service.save_lab(session, identity.user, None, data))


@router.put("/labs/{slug}")
def change_lab(slug: str, body: LabBody, identity: Identity = Depends(require_admin),
               session: Session = Depends(get_session)):
    """Change a lab's name, kind, division, or research area."""
    data, existing = body.model_dump(exclude_none=True), _lab(session, slug)
    return _lab_change(session, slug, lambda: labs_service.save_lab(session, identity.user, existing, data))


@router.post("/labs/{slug}/deactivate")
def deactivate_lab(slug: str, identity: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    """Its members no longer see its folders; its members and folders are kept, and its folders still charged to it."""
    existing = _lab(session, slug)
    return _lab_change(session, slug, lambda: labs_service.set_active(session, identity.user, existing, False))


@router.post("/labs/{slug}/reactivate")
def reactivate_lab(slug: str, identity: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    existing = _lab(session, slug)
    return _lab_change(session, slug, lambda: labs_service.set_active(session, identity.user, existing, True))


@router.get("/people")
def people(_: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    users = session.scalars(
        select(User).options(selectinload(User.memberships).selectinload(LabMembership.lab).selectinload(Lab.folders))
        .order_by(User.is_admin.desc(), User.display_name)
    ).all()
    return [user_json(u) for u in users]


def _person(session: Session, username: str) -> User:
    user = session.scalar(select(User).where(User.username == username)
                          .options(selectinload(User.memberships).selectinload(LabMembership.lab).selectinload(Lab.folders)))
    if user is None:
        raise HTTPException(404, "No such person")
    return user


def _person_json(session: Session, user: User) -> dict:
    """A person, with when they were added and the latest audited changes to them."""
    events = session.scalars(select(AuditEvent).where(AuditEvent.detail["username"].as_string() == user.username)
                             .options(selectinload(AuditEvent.actor)).order_by(AuditEvent.ts.desc()).limit(12))
    return {**user_json(user), "created_at": iso(user.created_at), "is_self": False,
            "history": [{"ts": iso(e.ts), "action": e.action, "summary": e.summary,
                         "by": e.actor.display_name if e.actor else (e.detail or {}).get("by")} for e in events]}


class PersonBody(BaseModel):
    username: str | None = None
    display_name: str = ""
    email: str = ""
    title: str = ""
    is_admin: bool = False
    memberships: list[dict[str, Any]] | None = None  # [{lab: slug, role}], the full list; absent leaves them


def _save(session: Session, identity: Identity, user: User | None, body: PersonBody) -> dict:
    data = body.model_dump(exclude_none=True)
    try:
        saved = people_service.save_person(session, identity.user, user, data)
    except people_service.PeopleError as e:
        session.rollback()
        raise HTTPException(e.status, str(e)) from e
    session.commit()
    session.expire_all()
    return _person_json(session, _person(session, saved.username)) | {"is_self": saved.id == identity.user.id}


@router.get("/people/{username}")
def person(username: str, identity: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    user = _person(session, username)
    return _person_json(session, user) | {"is_self": user.id == identity.user.id}


@router.post("/people", status_code=201)
def add_person(body: PersonBody, identity: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    """Add a person who can sign in (their username is the name the sign-on sends), with any lab memberships."""
    return _save(session, identity, None, body)


@router.put("/people/{username}")
def change_person(username: str, body: PersonBody, identity: Identity = Depends(require_admin),
                  session: Session = Depends(get_session)):
    """Change a person's name, email, title, administrator role, and, if given, lab memberships."""
    return _save(session, identity, _person(session, username), body)


@router.post("/people/{username}/password")
def set_password(username: str, identity: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    """Local sign-in: give a person a new temporary password, returned this once, which they must change when they
    sign in. Unlocks their account and ends their sessions."""
    if get_settings().auth_mode != "local":
        raise HTTPException(409, "Pinax keeps no passwords unless PINAX_AUTH_MODE is local")
    user = _person(session, username)
    try:
        password = signin.set_temporary_password(session, user, identity.user)
    except signin.SignInError as e:
        session.rollback()
        raise HTTPException(e.status, str(e)) from e
    session.expire_all()
    return {"temporary_password": password,
            "person": _person_json(session, _person(session, username)) | {"is_self": user.id == identity.user.id}}


@router.post("/people/{username}/deactivate")
def deactivate(username: str, identity: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    """The person cannot sign in, and their sessions end; their memberships and role are kept, unused."""
    return _set_active(session, identity, username, False)


@router.post("/people/{username}/reactivate")
def reactivate(username: str, identity: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    return _set_active(session, identity, username, True)


def _set_active(session: Session, identity: Identity, username: str, active: bool) -> dict:
    user = _person(session, username)
    try:
        people_service.set_active(session, identity.user, user, active)
    except people_service.PeopleError as e:
        session.rollback()
        raise HTTPException(e.status, str(e)) from e
    session.commit()
    session.expire_all()
    return _person_json(session, _person(session, username)) | {"is_self": user.id == identity.user.id}


class FolderMapping(BaseModel):
    fs: str
    folder: str
    lab: str | None = None  # lab slug; None removes the mapping


@router.post("/folders")
def map_folder(body: FolderMapping, identity: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    """Assign a top-level folder to a lab or department, move it to another, or unmap it."""
    fs = session.scalar(select(Filesystem).where(Filesystem.slug == body.fs))
    if fs is None:
        raise HTTPException(404, "No such filesystem")
    if not body.folder or "/" in body.folder:
        raise HTTPException(422, "Give a single top-level folder name")
    current = session.scalar(select(LabFolder).where(LabFolder.filesystem_id == fs.id, LabFolder.path == body.folder))
    before = current.lab.slug if current else None
    before_id = current.lab_id if current else None
    if body.lab is None:
        if current:
            session.delete(current)
        after_id = None
    else:
        lab = session.scalar(select(Lab).where(Lab.slug == body.lab))
        if lab is None:
            raise HTTPException(404, "No such lab or department")
        if current:
            current.lab_id = lab.id
        else:
            session.add(LabFolder(lab_id=lab.id, filesystem_id=fs.id, path=body.folder))
        after_id = lab.id
    ownership.record_change(session, fs.id, body.folder, before_id, after_id)
    session.add(AuditEvent(actor_id=identity.user.id, action="folder.mapped",
                           summary=f"{fs.name}: {body.folder} mapped to {body.lab or 'no one'} (was {before or 'unmapped'})",
                           detail={"fs": fs.slug, "folder": body.folder, "before": before, "after": body.lab}))
    session.commit()
    return {"ok": True}


class Membership(BaseModel):
    username: str
    role: str | None = None  # "pi" or "delegate"; None removes the membership


@router.post("/labs/{slug}/members")
def set_member(slug: str, body: Membership, identity: Identity = Depends(require_admin),
               session: Session = Depends(get_session)):
    lab = session.scalar(select(Lab).where(Lab.slug == slug))
    user = session.scalar(select(User).where(User.username == body.username))
    if lab is None or user is None:
        raise HTTPException(404, "No such lab or person")
    if body.role not in (None, "pi", "delegate"):
        raise HTTPException(422, "Role must be pi or delegate")
    m = session.scalar(select(LabMembership).where(LabMembership.lab_id == lab.id, LabMembership.user_id == user.id))
    if body.role is None:
        if m:
            session.delete(m)
    elif m:
        m.role = body.role
    else:
        session.add(LabMembership(lab_id=lab.id, user_id=user.id, role=body.role))
    session.add(AuditEvent(actor_id=identity.user.id, action="lab.member",
                           summary=f"{user.display_name} {'removed from' if body.role is None else 'set as ' + body.role + ' in'} {lab.name}",
                           detail={"lab": slug, "username": body.username, "role": body.role}))
    session.commit()
    return {"ok": True}
