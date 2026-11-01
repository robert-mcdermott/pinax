# SPDX-License-Identifier: Apache-2.0
"""The audit log (O6): every audited change and sign-in (models.AuditEvent), found for administrators and auditors.

- **Filters**, any of them together: a person (changes they made, and changes to them), an action (`plan.added`) or a
  kind of action (`plan`), a lab (changes to it, its members, plan, and folders, and folders given to it or taken
  from it), a folder (changes to it and to the folders in it, and to who owns the top-level folder it is in), and the
  days from and to.
- **Newest first**, a page at a time, with how many there are in all; or every one, as CSV.
- **Shown** with each filesystem's display root in place of its root, as the rest of Pinax shows paths. The CSV keeps
  what was recorded, with the detail as JSON.
"""

import csv
import io
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .models import AuditEvent, Filesystem, Lab, LabFolder, User
from .serialize import iso, join_root

# What each kind of action is about, for choosing them.
KINDS = {
    "auth": "Signing in", "person": "People", "lab": "Labs and departments", "folder": "Folders",
    "plan": "Cleanup plans", "tag": "Tags", "mark": "Marked folders", "cost": "Prices and free tiers",
    "capacity": "Capacity", "token": "API tokens", "view": "Saved views", "snapshot": "Scans", "import": "Imports",
    "access": "Access reviews", "demo": "The demo",
}
CSV_FIELDS = ["time", "username", "name", "token", "action", "summary", "address", "source", "by", "detail"]


class AuditError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass
class Filters:
    person: str | None = None
    action: str | None = None
    lab: str | None = None
    fs: str | None = None
    path: str | None = None
    since: date | None = None
    until: date | None = None


def _text(column, key: str):
    return column[key].as_string()


def query(session: Session, f: Filters) -> Select:
    """The events that match, newest first."""
    q = select(AuditEvent)
    if f.person:
        user = session.scalar(select(User).where(User.username == f.person))
        if user is None:
            raise AuditError(f"No person {f.person!r}", 404)
        q = q.where(or_(AuditEvent.actor_id == user.id, _text(AuditEvent.detail, "username") == user.username))
    if f.action:
        q = q.where(AuditEvent.action == f.action if "." in f.action else AuditEvent.action.startswith(f.action + "."))
    if f.lab:
        q = q.where(_of_lab(session, f.lab))
    if f.fs or f.path:
        q = q.where(_in_folder(session, f.fs, f.path))
    if f.since:
        q = q.where(AuditEvent.ts >= datetime.combine(f.since, time()))
    if f.until:
        q = q.where(AuditEvent.ts < datetime.combine(f.until + timedelta(days=1), time()))
    return q.order_by(AuditEvent.ts.desc(), AuditEvent.id.desc())


def _of_lab(session: Session, slug: str):
    """Changes to a lab: those that name it (its details, members, plan, own tags, and free tier), folders given to it
    or taken from it, and changes to the folders it owns now."""
    lab = session.scalar(select(Lab).where(Lab.slug == slug).options(selectinload(Lab.folders).selectinload(LabFolder.filesystem)))
    if lab is None:
        raise AuditError(f"No lab or department {slug!r}", 404)
    mapped = and_(AuditEvent.action == "folder.mapped",
                  or_(_text(AuditEvent.detail, "before") == slug, _text(AuditEvent.detail, "after") == slug))
    recorded = _text(AuditEvent.detail, "path")
    folders = [join_root(f.filesystem.root, f.path) for f in lab.folders]
    return or_(_text(AuditEvent.detail, "lab") == slug, mapped,
               *(or_(recorded == p, recorded.startswith(p + "/", autoescape=True)) for p in folders))


def _in_folder(session: Session, slug: str | None, path: str | None):
    """Changes to a folder, to the folders in it, and to who owns the top-level folder it is in (a whole filesystem,
    without a path). Folders are recorded by their path (plans, tags, marks) or, for who owns them, by filesystem and
    top-level folder."""
    fs = session.scalar(select(Filesystem).where(Filesystem.slug == (slug or "")))
    if fs is None:
        raise AuditError("Choose the folder's filesystem", 404 if slug else 422)
    root = fs.root.rstrip("/")
    path = (path or root).rstrip("/") or "/"
    if path != root and not path.startswith(root + "/"):
        raise AuditError(f"{path} is not on {fs.name}")
    recorded = _text(AuditEvent.detail, "path")
    below = or_(recorded == path, recorded.startswith(path + "/", autoescape=True))
    owner = and_(_text(AuditEvent.detail, "fs") == fs.slug, AuditEvent.action == "folder.mapped")
    if path != root:
        owner = and_(owner, _text(AuditEvent.detail, "folder") == path[len(root) + 1:].split("/", 1)[0])
    return or_(below, owner)


def actions(session: Session) -> list[dict]:
    """Every action recorded, with how many times, by kind."""
    rows = session.execute(select(AuditEvent.action, func.count()).group_by(AuditEvent.action)).all()
    return sorted(({"action": a, "kind": a.split(".", 1)[0], "count": n} for a, n in rows),
                  key=lambda r: (KINDS.get(r["kind"], r["kind"]), r["action"]))


def _shown(text: str, roots: list[tuple[str, str]]) -> str:
    for root, display in roots:
        text = text.replace(root, display)
    return text


def _roots(session: Session) -> list[tuple[str, str]]:
    """Each filesystem's root and how it is shown, longest first (a root inside another's is replaced first)."""
    out = [(fs.root.rstrip("/"), (fs.display_root or fs.root).rstrip("/")) for fs in session.scalars(select(Filesystem))]
    return sorted([r for r in out if r[0] and r[0] != r[1]], key=lambda r: -len(r[0]))


def _address(e: AuditEvent) -> str:
    """Where it came from: recorded with each change made in the app; sign-ins before that kept it in the detail."""
    return e.address or ((e.detail or {}).get("address", "") if e.action.startswith("auth.") else "")


def event_json(e: AuditEvent, roots: list[tuple[str, str]]) -> dict:
    detail = e.detail or {}
    return {"id": e.id, "ts": iso(e.ts), "action": e.action, "kind": e.action.split(".", 1)[0],
            "summary": _shown(e.summary, roots), "address": _address(e), "token": detail.get("token"),
            "who": {"username": e.actor.username, "display_name": e.actor.display_name} if e.actor else None,
            "source": detail.get("source", "app" if e.actor else "pinax"), "by": detail.get("by"),
            "detail": {k: _shown(v, roots) if isinstance(v, str) else v for k, v in detail.items()}}


def page(session: Session, f: Filters, offset: int = 0, limit: int = 50) -> dict:
    q = query(session, f)
    total = session.scalar(select(func.count()).select_from(q.order_by(None).subquery())) or 0
    rows = session.scalars(q.options(selectinload(AuditEvent.actor)).offset(offset).limit(limit)).all()
    roots = _roots(session)
    return {"events": [event_json(e, roots) for e in rows], "total": total, "offset": offset, "limit": limit}


def csv_rows(session: Session, f: Filters) -> Iterator[str]:
    """Every matching event as CSV, newest first, as recorded, a thousand at a time."""
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(CSV_FIELDS)
    q = query(session, f).options(selectinload(AuditEvent.actor)).execution_options(yield_per=1000)
    for n, e in enumerate(session.scalars(q), 1):
        detail = e.detail or {}
        out.writerow([iso(e.ts), e.actor.username if e.actor else "", e.actor.display_name if e.actor else "",
                      detail.get("token", ""), e.action, e.summary, _address(e),
                      detail.get("source", "app" if e.actor else "pinax"), detail.get("by", ""),
                      json.dumps(detail, sort_keys=True, default=str)])
        if n % 1000 == 0:
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()
    yield buf.getvalue()
