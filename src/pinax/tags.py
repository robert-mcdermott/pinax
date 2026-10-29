# SPDX-License-Identifier: Apache-2.0
"""Folder tags (T1, T2): folders described in terms a lab and IT share.

Administrators keep the **standard tags**, which every lab can use: added, changed, and retired (a tag in use is
retired, not deleted, so what was tagged stays tagged). Each has a type, so values compare: a flag (no value), text,
a date, or a choice from a list the site keeps. It says who may apply it (a lab's PI and delegates, or only
administrators), whether a folder may have more than one value, and whether it protects a folder from cleanup plans
(a date tag until its date). A default list is added the first time the catalog is set up, for a site to change.

A lab makes **custom tags** of its own, seen by its members and administrators, and administrators can merge one
into a standard tag. A tag is on a folder (a marked folder, so it follows it across scans) and applies to
everything below it.
"""

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent, FolderTag, Lab, Tag, User

TYPES = ("flag", "text", "date", "choice")
APPLIED_BY = ("owners", "admins")

# The default list: key, label, type, choices, more than one, applied by, protects, description.
DEFAULTS = [
    ("project", "Project", "text", [], True, "owners", False, "Finding a project's data wherever it is"),
    ("grant", "Grant or award", "text", [], True, "owners", False,
     "The grant or award the data is for; storage charges can be split by it"),
    ("stage", "Data stage", "choice", ["raw", "processed", "results", "reference", "scratch"], False, "owners", False,
     "Raw data is often the copy that must be kept; scratch is the first to clean up"),
    ("source", "Source", "choice", ["sequencing", "mass spectrometry", "cryo-EM", "light microscopy",
                                    "medical imaging", "flow cytometry", "simulation", "downloaded"], False, "owners",
     False, "Where the data came from; add the site's own instruments"),
    ("classification", "Data classification", "choice", ["public", "internal", "confidential", "restricted"], False,
     "admins", False, "How sensitive the data is, in the site's own levels"),
    ("irb", "IRB protocol", "text", [], True, "owners", False, "Data from human subjects research"),
    ("repository", "Shared in a repository", "text", [], True, "owners", False,
     "An accession (GEO, SRA, PRIDE, EMPIAR, ...): a public copy exists"),
    ("keep_until", "Keep until", "date", [], False, "owners", True, "Protects the folder from cleanup until the date"),
    ("legal_hold", "Legal hold", "flag", [], False, "admins", True, "Protects the folder from cleanup"),
]


class TagError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def ensure_defaults() -> None:
    """The default standard tags, the first time the catalog is set up (never again, so a site's changes stay)."""
    from .db import session_scope

    with session_scope() as s:
        if s.scalar(select(Tag.id).where(Tag.lab_id.is_(None)).limit(1)) is not None:
            return
        for key, label, kind, choices, many, by, protects, about in DEFAULTS:
            s.add(Tag(key=key, label=label, value_type=kind, choices=choices, many=many, applied_by=by,
                      protects=protects, description=about))


def _audit(session: Session, actor: User | None, action: str, summary: str, tag: Tag, **detail) -> None:
    session.add(AuditEvent(actor_id=actor.id if actor else None, action=action, summary=summary,
                           detail={"tag": tag.key, "lab": tag.lab.slug if tag.lab else None, **detail}))


def _key(label: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:64]
    if not key:
        raise TagError("A tag needs a name with letters or digits")
    return key


def _clean_choices(kind: str, choices: list[str] | None) -> list[str]:
    if kind != "choice":
        return []
    out = []
    for c in choices or []:
        c = c.strip()
        if c and c not in out:
            out.append(c[:100])
    if not out:
        raise TagError("A choice tag needs at least one choice")
    return out


def _check(kind: str, applied_by: str) -> None:
    if kind not in TYPES:
        raise TagError(f"A tag is a flag, text, a date, or a choice, not {kind!r}")
    if applied_by not in APPLIED_BY:
        raise TagError("A tag is applied by owners or by administrators")


def add_standard(session: Session, actor: User | None, label: str, kind: str, *, choices: list[str] | None = None,
                 many: bool = False, applied_by: str = "owners", protects: bool = False, description: str = "") -> Tag:
    _check(kind, applied_by)
    label = label.strip()[:100]
    key = _key(label)
    if session.scalar(select(Tag.id).where(Tag.lab_id.is_(None), Tag.key == key)):
        raise TagError(f"There is a standard tag {label!r} already", 409)
    if protects and kind not in ("flag", "date"):
        raise TagError("Only a flag or a date tag can protect a folder")
    tag = Tag(key=key, label=label, value_type=kind, choices=_clean_choices(kind, choices), many=many,
              applied_by=applied_by, protects=protects, description=description.strip())
    session.add(tag)
    _audit(session, actor, "tag.added", f"Standard tag {label!r} added", tag)
    session.flush()
    return tag


def change_standard(session: Session, actor: User | None, tag: Tag, *, label: str | None = None,
                    description: str | None = None, choices: list[str] | None = None, many: bool | None = None,
                    applied_by: str | None = None, protects: bool | None = None) -> Tag:
    """Change a standard tag. Its type cannot change once it is in use; a choice in use cannot be taken away."""
    if tag.lab_id is not None:
        raise TagError("That is a lab's own tag")
    if applied_by is not None:
        _check(tag.value_type, applied_by)
        tag.applied_by = applied_by
    if choices is not None and tag.value_type == "choice":
        kept = _clean_choices("choice", choices)
        used = {v for (v,) in session.execute(select(FolderTag.value).where(FolderTag.tag_id == tag.id).distinct())}
        if missing := sorted(used - set(kept)):
            raise TagError(f"Folders are tagged {', '.join(missing)}: keep those choices, or retag them first", 409)
        tag.choices = kept
    if label is not None:
        tag.label = label.strip()[:100] or tag.label
    if description is not None:
        tag.description = description.strip()
    if many is not None:
        tag.many = many
    if protects is not None:
        if protects and tag.value_type not in ("flag", "date"):
            raise TagError("Only a flag or a date tag can protect a folder")
        tag.protects = protects
    _audit(session, actor, "tag.changed", f"Standard tag {tag.label!r} changed", tag)
    session.flush()
    return tag


def set_active(session: Session, actor: User | None, tag: Tag, active: bool) -> Tag:
    """Retire a tag (it stays on the folders that have it, and can no longer be applied), or bring it back."""
    tag.active = active
    _audit(session, actor, "tag.reactivated" if active else "tag.retired",
           f"Tag {tag.label!r} {'brought back' if active else 'retired'}", tag)
    session.flush()
    return tag


def delete_unused(session: Session, actor: User | None, tag: Tag) -> None:
    if session.scalar(select(FolderTag.id).where(FolderTag.tag_id == tag.id).limit(1)):
        raise TagError("Folders have that tag: retire it instead", 409)
    _audit(session, actor, "tag.deleted", f"Tag {tag.label!r} deleted", tag)
    session.delete(tag)
    session.flush()


def valid_value(tag: Tag, value: str | None) -> str:
    """A value for `tag`, as kept: '' for a flag, an ISO date, or one of its choices."""
    value = (value or "").strip()
    if tag.value_type == "flag":
        return ""
    if not value:
        raise TagError(f"{tag.label} needs a value")
    if tag.value_type == "date":
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            raise TagError(f"A date is written YYYY-MM-DD, not {value!r}") from None
    if tag.value_type == "choice" and value not in tag.choices:
        raise TagError(f"{tag.label} is one of {', '.join(tag.choices)}")
    return value[:200]


def tag_json(tag: Tag, uses: int | None = None) -> dict:
    return {"id": tag.id, "key": tag.key, "label": tag.label, "description": tag.description,
            "lab": {"slug": tag.lab.slug, "name": tag.lab.name} if tag.lab else None, "type": tag.value_type,
            "choices": tag.choices, "many": tag.many, "applied_by": tag.applied_by, "protects": tag.protects,
            "active": tag.active, **({"uses": uses} if uses is not None else {})}


def standard_tags(session: Session, *, active_only: bool = False) -> list[Tag]:
    q = select(Tag).where(Tag.lab_id.is_(None))
    if active_only:
        q = q.where(Tag.active.is_(True))
    return session.scalars(q.order_by(Tag.id)).all()


def lab_tags(session: Session, lab: Lab, *, active_only: bool = False) -> list[Tag]:
    q = select(Tag).where(Tag.lab_id == lab.id)
    if active_only:
        q = q.where(Tag.active.is_(True))
    return session.scalars(q.order_by(Tag.label)).all()
