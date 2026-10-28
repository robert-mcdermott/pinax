# SPDX-License-Identifier: Apache-2.0
"""Cleanup plans (C1): the folders a lab means to free space from, and how.

Pinax changes nothing on the storage it reports on. A plan records what a lab means to do with each folder: delete
it, move it to another filesystem, or compress it, with a note. The lab's people do it, or the storage team with
the plan's CSV, and later scans show what happened (marks.py follows each folder, by path and by inode).

A lab's PI and its delegates, and administrators, change its plan; every change is audited. A folder inside one
already planned for the same lab is counted once, as part of the larger.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import marks
from .auth import Identity, folder_owners
from .models import AuditEvent, CleanupItem, Filesystem, Lab, MarkedFolder, MarkedFolderStat, User

ACTIONS = ("delete", "move", "compress")


class PlanError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def top_folder(fs: Filesystem, path: str) -> str | None:
    """The top-level folder `path` is in (None for the root, or a path not under it)."""
    prefix = fs.root.rstrip("/") + "/"
    return path[len(prefix):].split("/", 1)[0] if path.startswith(prefix) and len(path) > len(prefix) else None


def owner_of(session: Session, fs: Filesystem, path: str) -> Lab | None:
    top = top_folder(fs, path)
    return folder_owners(session).get((fs.id, top)) if top else None


def _audit(session: Session, user: User | None, action: str, summary: str, item: CleanupItem, fs: Filesystem) -> None:
    session.add(AuditEvent(actor_id=user.id if user else None, action=action, summary=summary,
                           detail={"lab": item.lab.slug, "filesystem": fs.slug, "path": item.mark.path,
                                   "action": item.action, "note": item.note}))


def _target(session: Session, fs: Filesystem, action: str, to: str | None) -> Filesystem | None:
    if action not in ACTIONS:
        raise PlanError(f"A folder is planned to be deleted, moved, or compressed, not {action!r}")
    if action != "move":
        return None
    target = session.scalar(select(Filesystem).where(Filesystem.slug == (to or ""), Filesystem.active.is_(True)))
    if target is None or target.id == fs.id:
        raise PlanError("A move goes to another filesystem")
    return target


def _what(item: CleanupItem, target: Filesystem | None) -> str:
    return f"move to {target.name}" if item.action == "move" and target else item.action


def add(session: Session, ident: Identity, fs: Filesystem, path: str, action: str, *, to: str | None = None,
        note: str = "", cold_from: int) -> CleanupItem:
    """Add a folder to the plan of the lab that owns it."""
    from .explore import canonical

    top = top_folder(fs, path)
    if top is None or not canonical(path, fs.root) or not ident.can_view_folder(fs.id, top):
        raise PlanError("That folder is not in the latest scan, or it is not shared with you", 404)
    lab = owner_of(session, fs, path)
    if lab is None:
        raise PlanError("No lab or department owns that folder, so it has no plan to go in")
    target = _target(session, fs, action, to)
    try:
        mark = marks.mark_folder(session, fs, path, cold_from=cold_from)
    except marks.MarkError as e:
        raise PlanError(str(e), e.status) from e
    if session.scalar(select(CleanupItem.id).where(CleanupItem.lab_id == lab.id, CleanupItem.mark_id == mark.id)):
        raise PlanError("That folder is in the plan already", 409)
    latest = latest_stat(session, mark)
    item = CleanupItem(lab_id=lab.id, mark_id=mark.id, action=action, target_filesystem_id=target.id if target else None,
                       note=note.strip()[:2000], added_by=ident.user.id,
                       baseline_snapshot_id=latest.snapshot_id if latest else None,
                       baseline_bytes=latest.bytes if latest else 0, baseline_files=latest.files if latest else 0)
    item.lab, item.mark = lab, mark
    session.add(item)
    _audit(session, ident.user, "plan.added", f"{lab.name}: {path} added to the cleanup plan ({_what(item, target)})",
           item, fs)
    session.flush()
    return item


def change(session: Session, ident: Identity, item: CleanupItem, action: str, *, to: str | None = None,
           note: str = "") -> CleanupItem:
    fs = session.get(Filesystem, item.mark.filesystem_id)
    target = _target(session, fs, action, to)
    item.action, item.target_filesystem_id, item.note = action, target.id if target else None, note.strip()[:2000]
    _audit(session, ident.user, "plan.changed", f"{item.lab.name}: {item.mark.path} now planned to {_what(item, target)}",
           item, fs)
    session.flush()
    return item


def remove(session: Session, ident: Identity, item: CleanupItem) -> None:
    fs = session.get(Filesystem, item.mark.filesystem_id)
    _audit(session, ident.user, "plan.removed", f"{item.lab.name}: {item.mark.path} taken out of the cleanup plan",
           item, fs)
    session.delete(item)
    session.flush()


def can_change(ident: Identity, item: CleanupItem) -> bool:
    return ident.is_admin or item.lab_id in ident.lab_roles


def latest_stat(session: Session, mark: MarkedFolder) -> MarkedFolderStat | None:
    return session.scalar(select(MarkedFolderStat).where(MarkedFolderStat.mark_id == mark.id)
                          .order_by(MarkedFolderStat.taken_at.desc()).limit(1))


def _inside(path: str, other: str) -> bool:
    return path.startswith(other.rstrip("/") + "/")


def _outermost(items: list[CleanupItem], fs_id: int, path: str) -> CleanupItem | None:
    """The outermost planned folder `path` is inside: the one it is counted in."""
    around = [i for i in items if i.mark.filesystem_id == fs_id and _inside(path, i.mark.path)]
    return min(around, key=lambda i: len(i.mark.path)) if around else None


def item_json(session: Session, item: CleanupItem, items: list[CleanupItem]) -> dict:
    fs = session.get(Filesystem, item.mark.filesystem_id)
    target = session.get(Filesystem, item.target_filesystem_id) if item.target_filesystem_id else None
    by = session.get(User, item.added_by) if item.added_by else None
    now = latest_stat(session, item.mark)
    outer = _outermost(items, item.mark.filesystem_id, item.mark.path)
    return {
        "id": item.id, "lab": item.lab.slug, "fs": fs.slug, "fs_name": fs.name, "path": item.mark.path,
        "name": item.mark.path.rsplit("/", 1)[-1], "action": item.action,
        "to": {"slug": target.slug, "name": target.name} if target else None, "note": item.note,
        "added_by": by.display_name if by else None, "added_at": item.added_at.isoformat() + "Z",
        "baseline": {"bytes": item.baseline_bytes, "files": item.baseline_files, "snapshot": item.baseline_snapshot_id},
        "now": {"bytes": now.bytes, "files": now.files, "present": now.present} if now else None,
        "mark": {"id": item.mark.id, "status": item.mark.status, "moved_to": item.mark.moved_to},
        "inside": outer.id if outer else None,
    }


def lab_items(session: Session, lab: Lab) -> list[CleanupItem]:
    return session.scalars(select(CleanupItem).where(CleanupItem.lab_id == lab.id).order_by(CleanupItem.added_at)).all()


def lab_plan(session: Session, lab: Lab) -> dict:
    """A lab's plan: each folder, and the total, counting a folder inside another planned one once."""
    items = lab_items(session, lab)
    rows = [item_json(session, i, items) for i in items]
    counted = [r for r in rows if r["inside"] is None]
    return {"lab": {"slug": lab.slug, "name": lab.name, "kind": lab.kind}, "items": rows,
            "total": {"folders": len(counted), "bytes": sum(r["baseline"]["bytes"] for r in counted)}}


def here(session: Session, ident: Identity, fs: Filesystem, path: str) -> dict:
    """For the age map's panel: whether this folder can go in a plan, the item it is (or is inside), and its lab's
    plan so far."""
    lab = owner_of(session, fs, path)
    top = top_folder(fs, path)
    if lab is None or top is None or not ident.can_view_folder(fs.id, top):
        return {"plannable": False, "lab": None}
    items = lab_items(session, lab)
    mine = next((i for i in items if i.mark.filesystem_id == fs.id and i.mark.path == path), None)
    outer = _outermost(items, fs.id, path)
    plan = lab_plan(session, lab)
    targets = session.scalars(select(Filesystem).where(Filesystem.active.is_(True), Filesystem.id != fs.id)
                              .order_by(Filesystem.name)).all()
    return {"plannable": True, "lab": plan["lab"], "total": plan["total"],
            "item": item_json(session, mine, items) if mine else None,
            "inside": item_json(session, outer, items) if outer else None,
            "targets": [{"slug": t.slug, "name": t.name} for t in targets]}
