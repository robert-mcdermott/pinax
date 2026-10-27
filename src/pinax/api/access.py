# SPDX-License-Identifier: Apache-2.0
"""People and access: labs and departments, their members, and which top-level folders they own."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..auth import Identity, require_admin
from ..db import get_session
from ..models import AuditEvent, Filesystem, Lab, LabFolder, LabMembership, User
from ..serialize import lab_json, user_json

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


@router.get("/people")
def people(_: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    users = session.scalars(
        select(User).options(selectinload(User.memberships).selectinload(LabMembership.lab).selectinload(Lab.folders))
        .order_by(User.is_admin.desc(), User.display_name)
    ).all()
    return [user_json(u) for u in users]


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
    if body.lab is None:
        if current:
            session.delete(current)
    else:
        lab = session.scalar(select(Lab).where(Lab.slug == body.lab))
        if lab is None:
            raise HTTPException(404, "No such lab or department")
        if current:
            current.lab_id = lab.id
        else:
            session.add(LabFolder(lab_id=lab.id, filesystem_id=fs.id, path=body.folder))
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
