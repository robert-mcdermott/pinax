# SPDX-License-Identifier: Apache-2.0
"""Folder tags: the standard tags, for administrators to keep (tags.py)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import tags
from ..auth import Identity, require_admin
from ..db import get_session
from ..models import FolderTag, Tag

router = APIRouter(tags=["tags"])
admin = APIRouter(prefix="/admin/tags")


class TagBody(BaseModel):
    label: str | None = None
    type: str = "flag"
    choices: list[str] | None = None
    many: bool | None = None
    applied_by: str | None = None
    protects: bool | None = None
    description: str | None = None


def _tag(session: Session, tag_id: int) -> Tag:
    tag = session.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(404, "No such tag")
    return tag


def _apply(session: Session, change):
    try:
        out = change()
    except tags.TagError as e:
        session.rollback()
        raise HTTPException(e.status, str(e)) from e
    session.commit()
    return out


def _uses(session: Session) -> dict[int, int]:
    return dict(session.execute(select(FolderTag.tag_id, func.count()).group_by(FolderTag.tag_id)).all())


@admin.get("")
def tag_list(_: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    """The standard tags, retired ones too, and every lab's own, each with how many folders have it."""
    uses = _uses(session)
    labs = session.scalars(select(Tag).where(Tag.lab_id.is_not(None)).order_by(Tag.label)).all()
    return {"standard": [tags.tag_json(t, uses.get(t.id, 0)) for t in tags.standard_tags(session)],
            "custom": [tags.tag_json(t, uses.get(t.id, 0)) for t in labs],
            "types": list(tags.TYPES), "applied_by": list(tags.APPLIED_BY)}


@admin.post("")
def add(body: TagBody, identity: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    return _apply(session, lambda: tags.tag_json(tags.add_standard(
        session, identity.user, body.label or "", body.type, choices=body.choices, many=bool(body.many),
        applied_by=body.applied_by or "owners", protects=bool(body.protects), description=body.description or "")))


@admin.put("/{tag_id}")
def change(tag_id: int, body: TagBody, identity: Identity = Depends(require_admin),
           session: Session = Depends(get_session)):
    tag = _tag(session, tag_id)
    return _apply(session, lambda: tags.tag_json(tags.change_standard(
        session, identity.user, tag, label=body.label, description=body.description, choices=body.choices,
        many=body.many, applied_by=body.applied_by, protects=body.protects)))


class ActiveBody(BaseModel):
    active: bool


@admin.post("/{tag_id}/active")
def set_active(tag_id: int, body: ActiveBody, identity: Identity = Depends(require_admin),
               session: Session = Depends(get_session)):
    """Retire a tag (folders keep it; it can no longer be applied), or bring it back."""
    tag = _tag(session, tag_id)
    return _apply(session, lambda: tags.tag_json(tags.set_active(session, identity.user, tag, body.active)))


@admin.delete("/{tag_id}")
def delete(tag_id: int, identity: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    """Delete a tag no folder has."""
    tag = _tag(session, tag_id)
    _apply(session, lambda: tags.delete_unused(session, identity.user, tag))
    return {"deleted": tag_id}


router.include_router(admin)
