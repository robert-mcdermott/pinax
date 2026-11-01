# SPDX-License-Identifier: Apache-2.0
"""Folder tags (tags.py): the standard tags, for administrators to keep; and tagging folders, for a lab's members
and administrators."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import tags
from ..auth import Identity, current_identity, require_admin, require_sees_all
from ..db import get_session
from ..models import Filesystem, FolderTag, Lab, Tag
from ..snapshots import cold_bucket

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
def tag_list(_: Identity = Depends(require_sees_all), session: Session = Depends(get_session)):
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


class MergeBody(BaseModel):
    into: int
    value: str | None = None


@admin.post("/{tag_id}/merge")
def merge(tag_id: int, body: MergeBody, identity: Identity = Depends(require_admin),
          session: Session = Depends(get_session)):
    """Fold a lab's own tag into a standard one; the lab's tag is retired."""
    custom, into = _tag(session, tag_id), _tag(session, body.into)
    return {"folders": _apply(session, lambda: tags.merge(session, identity.user, custom, into, body.value))}


# ---------- Tagging folders ----------

def _fs(session: Session, slug: str | None) -> Filesystem:
    fs = session.scalar(select(Filesystem).where(Filesystem.slug == (slug or "")))
    if fs is None:
        raise HTTPException(404, "No such filesystem")
    return fs


@router.get("/tags/here")
def here(fs: str, path: str, identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    """A folder's own tags and those it inherits, and the tags the person can put on it."""
    return tags.here(session, identity, _fs(session, fs), path)


@router.get("/tags/below")
def below(fs: str, path: str = "", identity: Identity = Depends(current_identity),
          session: Session = Depends(get_session)):
    """The tagged folders at or below a folder (the filesystem's root by default), for marking the age map's tiles."""
    fsys = _fs(session, fs)
    return {"tagged": tags.tagged_below(session, identity, fsys, path or fsys.root)}


@router.get("/tags/filters")
def filters(identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    """The tags a person can look for, with the values on folders they may see."""
    return {"tags": tags.filters(session, identity)}


@router.get("/tags/collection")
def collection(want: list[str] = Query(default=[]), identity: Identity = Depends(current_identity),
               session: Session = Depends(get_session)):
    """The folders that have every wanted tag (`want=ID` for any value, `want=ID:value` for one), on their own or
    from a folder above: the outermost, with their totals, history, and forecast."""
    parsed = []
    for w in want:
        tag_id, _, value = w.partition(":")
        if not tag_id.isdigit():
            raise HTTPException(422, f"A wanted tag is ID or ID:value, not {w!r}")
        parsed.append((int(tag_id), value or None))
    return tags.collection(session, identity, parsed)


class FolderTagBody(BaseModel):
    fs: str
    path: str
    tag: int
    value: str | None = None


@router.post("/tags/folders")
def tag_folder(body: FolderTagBody, identity: Identity = Depends(current_identity),
               session: Session = Depends(get_session)):
    fs, tag = _fs(session, body.fs), _tag(session, body.tag)
    _apply(session, lambda: tags.apply(session, identity, fs, body.path, tag, body.value, cold_from=cold_bucket()))
    return tags.here(session, identity, fs, body.path)


@router.delete("/tags/folders/{ft_id}")
def untag_folder(ft_id: int, identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    ft = session.get(FolderTag, ft_id)
    if ft is None or not identity.can_view_folder(ft.mark.filesystem_id, _top(session, ft)):
        raise HTTPException(404, "No such tag on a folder you can see")
    fs, path = session.get(Filesystem, ft.mark.filesystem_id), ft.mark.path
    _apply(session, lambda: tags.untag(session, identity, ft))
    return tags.here(session, identity, fs, path)


def _top(session: Session, ft: FolderTag) -> str | None:
    from ..plans import top_folder

    return top_folder(session.get(Filesystem, ft.mark.filesystem_id), ft.mark.path)


class CustomBody(BaseModel):
    lab: str
    label: str
    type: str = "flag"


@router.post("/tags/custom")
def make_custom(body: CustomBody, identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    """A lab's own tag, made by its PI or a delegate."""
    lab = session.scalar(select(Lab).where(Lab.slug == body.lab))
    if lab is None or not (identity.is_admin or lab.id in identity.lab_roles):
        raise HTTPException(404, "No such lab or department")
    return _apply(session, lambda: tags.tag_json(tags.make_custom(session, identity, lab, body.label, body.type)))


router.include_router(admin)
