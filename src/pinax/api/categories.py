# SPDX-License-Identifier: Apache-2.0
"""File categories (categories.py): read by administrators and auditors, changed by administrators, with what a
change would do before it is saved. Changes apply to indexes built after them; the page says which snapshots are
outdated and how long rebuilding them takes, and `pinax rebuild --outdated` does it, never the web app."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import categories
from ..auth import Identity, require_admin, require_sees_all
from ..db import get_session
from ..index import Stats
from ..snapshots import indexes, latest_snapshot

router = APIRouter(prefix="/admin/categories", tags=["categories"])


class CategoryBody(BaseModel):
    key: str | None = None  # in a preview: the category it changes (None: a new one)
    label: str | None = None
    description: str | None = None
    extensions: list[str] | None = None
    patterns: list[str] | None = None
    remove: bool = False  # in a preview: removing `key`


class MoveBody(BaseModel):
    to: int  # its new place, from 0


class PatternBody(BaseModel):
    pattern: str


def _proposed(session: Session, key: str | None, body: CategoryBody | None = None, **change):
    try:
        if body is not None:
            change |= {"label": body.label, "description": body.description, "extensions": body.extensions,
                       "patterns": body.patterns, "remove": body.remove}
        return categories.proposed(session, key, **change)
    except categories.CategoryError as e:
        raise HTTPException(e.status, str(e)) from e


def _bytes(session: Session) -> dict[str, int]:
    """Each category's bytes in each filesystem's latest scan, by the categories its index was built with."""
    from sqlalchemy import select

    from ..models import Filesystem

    out: dict[str, int] = {}
    for fs in session.scalars(select(Filesystem).where(Filesystem.active.is_(True))):
        snap = latest_snapshot(session, fs)
        if snap is None:
            continue
        idx = indexes.get(snap)
        for k, v in Stats.from_row(idx.node(idx.root)).json(idx.ref_day)["cat_bytes"].items():
            out[k] = out.get(k, 0) + v
    return out


def _listing(session: Session, identity: Identity) -> dict:
    cats = categories.current(session)
    return {"categories": [categories.category_json(r) for r in categories._rows(session)],
            "fingerprint": categories.fingerprint(cats), "bytes": _bytes(session),
            "limits": {"categories": categories.MAX_CATEGORIES, "patterns": categories.MAX_PATTERNS},
            "rebuild": categories.rebuild_info(session, cats), "can_change": identity.is_admin}


@router.get("")
def category_list(identity: Identity = Depends(require_sees_all), session: Session = Depends(get_session)):
    """The catalog's categories in order, each's bytes in the latest scans, and the snapshots built with others."""
    return _listing(session, identity)


def _change(session: Session, identity: Identity, cats, action: str, key: str) -> dict:
    categories.apply(session, identity.user, cats, action, key)
    session.commit()
    return _listing(session, identity)


@router.post("", status_code=201)
def add(body: CategoryBody, identity: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    before = {c.key for c in categories.current(session)}
    cats = _proposed(session, None, body)
    return _change(session, identity, cats, "category.added", next(c.key for c in cats if c.key not in before))


@router.put("/{key}")
def change(key: str, body: CategoryBody, identity: Identity = Depends(require_admin),
           session: Session = Depends(get_session)):
    return _change(session, identity, _proposed(session, key, body), "category.changed", key)


@router.delete("/{key}")
def remove(key: str, identity: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    return _change(session, identity, _proposed(session, key, remove=True), "category.removed", key)


@router.post("/{key}/move")
def move(key: str, body: MoveBody, identity: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    return _change(session, identity, _proposed(session, key, to=body.to), "category.moved", key)


@router.post("/preview")
def preview(body: CategoryBody, _: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    """What a change would do, before it is saved: how the latest scans' types would move between categories, and
    which snapshots would be outdated."""
    cats = _proposed(session, body.key, body)
    return {"moves": categories.preview(session, cats), "rebuild": categories.rebuild_info(session, cats),
            "fingerprint": categories.fingerprint(cats), "labels": {c.key: c.label for c in cats}}


@router.post("/try")
def try_pattern(body: PatternBody, _: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    """Whether DuckDB takes a pattern, and which names it matches in a sample of each filesystem's latest scan."""
    return categories.try_pattern(session, body.pattern)
