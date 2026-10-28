# SPDX-License-Identifier: Apache-2.0
"""Labs and departments: the units that own top-level folders, whose members may view them, and that are charged.

*People and access* and `pinax import` follow the same rules (`slug_problem`, `LabKind`). A lab's slug is its key
in links, the API, and import files, so it cannot be changed. Labs are never deleted, so the audit log and past
charges keep every reference: a deactivated lab gives its members no access, and keeps its members and folders,
so reactivating it restores exactly what it had. Its folders stay mapped to it, so they are still charged to it
until they are moved or unmapped on *Folders and labs*. Every change is audited with the fields before and after.
"""

import getpass
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent, Lab, LabKind, User

NONE = "none"  # removes a membership or unmaps a folder in an import; no lab may use it as a slug
SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
PROFILE = ("name", "kind", "division", "research_area")
LIMIT = 200


class LabError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def slug_problem(slug: str) -> str | None:
    """Why `slug` cannot be a lab's slug, or None."""
    if slug == NONE or not SLUG.fullmatch(slug):
        return (f"{slug!r} is not a usable slug (lowercase letters, digits, - and _, starting with a letter or digit; "
                f"at most 64 characters; not 'none')")
    return None


def _audit(session: Session, actor: User | None, action: str, summary: str, detail: dict) -> None:
    """`actor` None: a change from the command line, recorded with the operating-system user who made it."""
    source = {"source": "app"} if actor is not None else {"source": "cli", "by": getpass.getuser()}
    session.add(AuditEvent(actor_id=actor.id if actor else None, action=action, summary=summary,
                           detail={**detail, **source}))


def _profile(data: dict[str, Any]) -> dict[str, str]:
    out = {k: str(data.get(k) or "").strip() for k in PROFILE}
    out["kind"] = out["kind"] or LabKind.LAB
    if not out["name"]:
        raise LabError("A name is required")
    if out["kind"] not in LabKind.ALL:
        raise LabError(f"A kind is {' or '.join(LabKind.ALL)}, not {out['kind']!r}")
    for k in ("name", "division", "research_area"):
        if len(out[k]) > LIMIT:
            raise LabError(f"{k.replace('_', ' ').capitalize()} is longer than {LIMIT} characters")
    return out


def save_lab(session: Session, actor: User | None, lab: Lab | None, data: dict[str, Any]) -> Lab:
    """Add a lab or department (`lab` None, with a `slug`) or change one's name, kind, division, or research area."""
    profile = _profile(data)
    if lab is None:
        slug = str(data.get("slug") or "").strip()
        if problem := slug_problem(slug):
            raise LabError(problem[0].upper() + problem[1:])
        if session.scalar(select(Lab.id).where(Lab.slug == slug)) is not None:
            raise LabError(f"The slug {slug!r} is already taken", 409)
        lab = Lab(slug=slug, **profile)
        session.add(lab)
        session.flush()
        _audit(session, actor, "lab.created", f"Added the {lab.name} ({lab.slug}), a {lab.kind}",
               {"lab": lab.slug, "fields": {k: {"before": None, "after": v} for k, v in profile.items()}})
        return lab
    if not lab.active:
        raise LabError(f"Reactivate the {lab.name} before changing its details", 409)
    if "slug" in data and str(data["slug"]).strip() != lab.slug:
        raise LabError("A slug cannot be changed: links, the API, and import files use it")
    fields = {k: {"before": getattr(lab, k), "after": v} for k, v in profile.items() if getattr(lab, k) != v}
    for k, v in profile.items():
        setattr(lab, k, v)
    session.flush()
    if fields:
        _audit(session, actor, "lab.updated", f"{lab.name}: {', '.join(k.replace('_', ' ') for k in fields)}",
               {"lab": lab.slug, "fields": fields})
    return lab


def set_active(session: Session, actor: User | None, lab: Lab, active: bool) -> None:
    """Deactivate a lab (its members lose access to its folders; members and folders are kept) or reactivate it."""
    if lab.active == active:
        raise LabError(f"The {lab.name} is already {'active' if active else 'inactive'}", 409)
    lab.active = active
    members = sorted(f"{m.user.display_name} ({m.role})" for m in lab.memberships)
    folders = sorted(f"{f.filesystem.slug}:{f.path}" for f in lab.folders)
    _audit(session, actor, "lab.reactivated" if active else "lab.deactivated",
           f"{'Reactivated' if active else 'Deactivated'} the {lab.name}"
           + ("; its members see its folders again" if active else "; its members no longer see its folders"),
           {"lab": lab.slug, "fields": {"active": {"before": not active, "after": active}},
            "members": members, "folders": folders})
    session.flush()
