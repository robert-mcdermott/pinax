# SPDX-License-Identifier: Apache-2.0
"""Cleanup plans (C1): the folders a lab means to free space from, and how.

Pinax changes nothing on the storage it reports on. A plan records what a lab means to do with each folder: delete
it, move it to another filesystem, or compress it, with a note. The lab's people do it, or the storage team with
the plan's CSV, and later scans show what happened (marks.py follows each folder, by path and by inode).

A lab's PI and its delegates, and administrators, change its plan; every change is audited. A folder inside one
already planned for the same lab is counted once, as part of the larger.

Progress (C3) comes from later scans: a folder gone (and not moved) is done; one compressed is done once it has
shrunk by most of its estimate; one smaller than when it was added is partly done; one that moved freed nothing.
What is done frees space, and saves what that space would cost the lab a month now.

What a plan saves (C2) is the fall in the lab's monthly charge, at today's prices and free tiers, from the
storage it frees: a folder deleted frees its size; one moved frees it here and is charged on the filesystem it
goes to; one compressed frees an estimate, from typical ratios for the types in it (`COMPRESSION`). A lab within its
free tier saves nothing by freeing storage, so the saving is worked out on the lab's whole size on each filesystem,
not as size times price.
"""

from collections import defaultdict
from datetime import date
from fractions import Fraction

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import costs, marks
from .auth import Identity, folder_owners
from .charges import money
from .config import get_settings
from .db import utcnow
from .models import (
    AuditEvent,
    CleanupItem,
    Filesystem,
    FolderStat,
    Lab,
    LabFolder,
    MarkedFolder,
    MarkedFolderStat,
    Snapshot,
    SnapshotStatus,
    User,
)

ACTIONS = ("delete", "move", "compress")

# The share of a file's size that compressing it typically frees: gzip or bgzip for uncompressed FASTQ, VCF,
# FASTA, and pileups; SAM to BAM; BAM to CRAM. Estimates from published figures, to be set from a site's own.
COMPRESSION = {"fastq": 0.75, "fq": 0.75, "sam": 0.75, "bam": 0.5, "vcf": 0.85, "gvcf": 0.85, "fasta": 0.7,
               "fa": 0.7, "fna": 0.7, "bedgraph": 0.8, "pileup": 0.85, "mpileup": 0.85}


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
    if action == "compress":
        item.baseline_estimate = freed(session, item)[0]
    session.add(item)
    _audit(session, ident.user, "plan.added", f"{lab.name}: {path} added to the cleanup plan ({_what(item, target)})",
           item, fs)
    session.flush()
    return item


def change(session: Session, ident: Identity, item: CleanupItem, action: str, *, to: str | None = None,
           note: str = "") -> CleanupItem:
    fs = session.get(Filesystem, item.mark.filesystem_id)
    target = _target(session, fs, action, to)
    if action == "compress" and item.action != "compress":
        item.baseline_estimate = freed(session, CleanupItem(mark=item.mark, action="compress"))[0]
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


DONE, PARTLY, MOVED, TO_DO = "done", "partly done", "moved", "to do"


def progress(session: Session, item: CleanupItem) -> dict:
    """What later scans show of a planned folder: done, partly done, moved (nothing freed), or still to do; the
    space freed, and since which scan."""
    stats = session.scalars(select(MarkedFolderStat).where(MarkedFolderStat.mark_id == item.mark_id,
                                                           MarkedFolderStat.taken_at > _added_scan(session, item))
                            .order_by(MarkedFolderStat.taken_at)).all()
    base = item.baseline_bytes
    if item.mark.status == marks.MOVED:
        return {"status": MOVED, "freed": 0, "since": None}
    if item.mark.status == marks.GONE:
        since = next((s.taken_at for s in stats if not s.present), None)
        return {"status": DONE, "freed": base, "since": _iso(since)}
    now = stats[-1].bytes if stats and stats[-1].present else base
    smaller = next((s.taken_at for s in stats if s.present and s.bytes < base), None)
    gain = max(0, base - now)
    if item.action == "compress" and item.baseline_estimate and gain >= 0.9 * item.baseline_estimate:
        return {"status": DONE, "freed": gain, "since": _iso(smaller)}
    if gain > 0:
        return {"status": PARTLY, "freed": gain, "since": _iso(smaller)}
    return {"status": TO_DO, "freed": 0, "since": None}


def _added_scan(session: Session, item: CleanupItem):
    snap = session.get(Snapshot, item.baseline_snapshot_id) if item.baseline_snapshot_id else None
    return snap.started_at if snap else item.added_at


def _iso(t) -> str | None:
    return t.isoformat() + "Z" if t else None


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
        "estimate": item.baseline_estimate, "progress": progress(session, item),
    }


def lab_items(session: Session, lab: Lab) -> list[CleanupItem]:
    return session.scalars(select(CleanupItem).where(CleanupItem.lab_id == lab.id).order_by(CleanupItem.added_at)).all()


# ---------- What a plan saves ----------

def lab_size(session: Session, lab: Lab, fs: Filesystem) -> int:
    """The size of the folders `lab` owns on `fs`, in its latest scan."""
    latest = session.scalar(select(Snapshot.id).where(Snapshot.filesystem_id == fs.id, Snapshot.status.in_(
        (SnapshotStatus.READY, SnapshotStatus.PRUNED))).order_by(Snapshot.started_at.desc()).limit(1))
    if latest is None:
        return 0
    owned = select(LabFolder.path).where(LabFolder.lab_id == lab.id, LabFolder.filesystem_id == fs.id)
    return int(session.scalar(select(func.sum(FolderStat.bytes)).where(
        FolderStat.snapshot_id == latest, FolderStat.folder.in_(owned))) or 0)


def monthly(session: Session, fs: Filesystem, lab: Lab, size: int, day: date) -> Fraction | None:
    """What `lab` pays a month for `size` on `fs` at the price and free tier of `day`; None with no price."""
    price = costs.price_on(session, fs, day)
    if price is None:
        return None
    return max(0, size - costs.free_on(session, fs, lab, day)) * Fraction(price.micros, 10**6 * costs.UNITS[price.unit])


def compressible(types: list[dict]) -> int:
    """What compressing files of these types would typically free (an estimate)."""
    return round(sum(t["bytes"] * COMPRESSION.get(t["ext"], 0) for t in types))


def freed(session: Session, item: CleanupItem) -> tuple[int, bool]:
    """What doing `item` would free now, and whether that is an estimate; nothing if the folder is no longer at
    its path."""
    from .snapshots import indexes, latest_snapshot

    now = latest_stat(session, item.mark)
    if now is None or not now.present:
        return 0, False
    if item.action != "compress":
        return now.bytes, False
    fs = session.get(Filesystem, item.mark.filesystem_id)
    return compressible(indexes.get(latest_snapshot(session, fs)).types(item.mark.path)), True


def savings(session: Session, lab: Lab, items: list[CleanupItem], *, day: date | None = None) -> dict:
    """What the lab's plan would save a month, at the prices and free tiers of `day` (today): for each folder on its
    own, for each filesystem, and in all. Folders inside another planned one are counted with it."""
    day = day or utcnow().date()
    currency = get_settings().currency
    counted = [i for i in items if _outermost(items, i.mark.filesystem_id, i.mark.path) is None]
    fss = {f.id: f for f in session.scalars(select(Filesystem))}
    sizes: dict[int, int] = {}

    def size_on(fs_id: int) -> int:
        if fs_id not in sizes:
            sizes[fs_id] = lab_size(session, lab, fss[fs_id])
        return sizes[fs_id]

    def change(fs_id: int, delta: int) -> Fraction | None:
        """How much the lab's monthly charge on a filesystem changes if its size changes by `delta`."""
        before = monthly(session, fss[fs_id], lab, size_on(fs_id), day)
        if before is None:
            return None
        return monthly(session, fss[fs_id], lab, max(0, size_on(fs_id) + delta), day) - before

    per_item, out_of, into = {}, defaultdict(int), defaultdict(int)
    estimated = False
    for i in counted:
        # A folder done frees nothing more (compressing what compression made is not asked of it).
        b, est = (0, False) if progress(session, i)["status"] == DONE else freed(session, i)
        estimated |= est
        out_of[i.mark.filesystem_id] += b
        if i.action == "move" and i.target_filesystem_id:
            into[i.target_filesystem_id] += b
        here = change(i.mark.filesystem_id, -b)
        there = change(i.target_filesystem_id, b) if i.action == "move" and i.target_filesystem_id else 0
        alone = None if here is None else -here - (there or 0)
        per_item[i.id] = {"freed": b, "estimate": est, "saves": str(money(alone, currency)) if alone is not None else None}
    rows, total = [], Fraction(0)
    for fs_id in sorted(set(out_of) | set(into), key=lambda k: fss[k].name):
        now = monthly(session, fss[fs_id], lab, size_on(fs_id), day)
        after = monthly(session, fss[fs_id], lab, max(0, size_on(fs_id) - out_of[fs_id] + into[fs_id]), day)
        if now is not None:
            total += now - after
        free = costs.free_on(session, fss[fs_id], lab, day)
        rows.append({"fs": fss[fs_id].slug, "fs_name": fss[fs_id].name, "size": size_on(fs_id), "freed": out_of[fs_id],
                     "added": into[fs_id], "free": free, "priced": now is not None,
                     "now": str(money(now, currency)) if now is not None else None,
                     "after": str(money(after, currency)) if after is not None else None,
                     "within_free": now is not None and size_on(fs_id) <= free})
    # What is done already: the space freed, and what it saves a month now: what it would cost the lab if it
    # were back, less what a moved folder costs where it went.
    done, landed, saved = defaultdict(int), defaultdict(int), Fraction(0)
    for i in counted:
        got = progress(session, i)["freed"]
        done[i.mark.filesystem_id] += got
        if got and i.action == "move" and i.target_filesystem_id:
            landed[i.target_filesystem_id] += got
    for fs_id, b in done.items():
        saved += (change(fs_id, b) or 0) if b else 0
    for fs_id, b in landed.items():
        saved += change(fs_id, -b) or 0
    return {"currency": currency, "items": per_item, "filesystems": rows, "estimate": estimated,
            "done": {"freed": sum(done.values()), "monthly": str(money(saved, currency))},
            "freed": sum(out_of.values()), "moved": sum(into.values()),
            "monthly": str(money(total, currency)), "yearly": str(money(total * 12, currency))}


def lab_plan(session: Session, lab: Lab) -> dict:
    """A lab's plan: each folder, the total (counting a folder inside another planned one once), and what it
    would save."""
    items = lab_items(session, lab)
    rows = [item_json(session, i, items) for i in items]
    counted = [r for r in rows if r["inside"] is None]
    saved = savings(session, lab, items)
    for r in rows:
        r["saving"] = saved["items"].get(r["id"])
    return {"lab": {"slug": lab.slug, "name": lab.name, "kind": lab.kind}, "items": rows,
            "total": {"folders": len(counted), "bytes": sum(r["baseline"]["bytes"] for r in counted)},
            "savings": {k: v for k, v in saved.items() if k != "items"}}


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
    return {"plannable": True, "lab": plan["lab"], "total": plan["total"], "savings": plan["savings"],
            "item": item_json(session, mine, items) if mine else None,
            "inside": item_json(session, outer, items) if outer else None,
            "targets": [{"slug": t.slug, "name": t.name} for t in targets]}


# ---------- Suggestions (C4) ----------

WORKFLOW = {"nextflow": "Nextflow work folder", "snakemake": "Snakemake working folder",
            "cromwell": "Cromwell executions", "conda": "Conda package cache", "containers": "Container image cache"}


def suggestions(session: Session, lab: Lab, *, limit: int = 10) -> list[dict]:
    """Folders of the lab worth adding to its plan, each with what it would free and save a month on its own:
    workflow working folders and package caches (made again by running the workflow), the largest cold folders
    (to move to cheaper storage), and the folders with the most uncompressed sequencing data (to compress). Folders
    already planned, or inside one, are left out; a folder suggested twice is suggested for the larger saving, and
    one inside another suggested folder is left to it."""
    from .snapshots import cold_bucket, indexes, latest_snapshot

    currency = get_settings().currency
    day = utcnow().date()
    items = lab_items(session, lab)
    planned = {(i.mark.filesystem_id, i.mark.path) for i in items}
    found: dict[tuple[int, str], dict] = {}
    others = {f.id for f in session.scalars(select(Filesystem).where(Filesystem.active.is_(True)))}
    for lf in session.scalars(select(LabFolder).where(LabFolder.lab_id == lab.id)):
        fs = session.get(Filesystem, lf.filesystem_id)
        snap = latest_snapshot(session, fs) if fs.active else None
        if snap is None:
            continue
        idx = indexes.get(snap)
        top = f"{fs.root.rstrip('/')}/{lf.path}"
        size = lab_size(session, lab, fs)

        def offer(kind: str, row_path: str, what: str, size_: int, frees: int, action: str, *, fs=fs, size=size) -> None:
            key = (fs.id, row_path)
            if key in planned or _outermost(items, fs.id, row_path) or frees <= 0:
                return
            now = monthly(session, fs, lab, size, day)
            saves = None if now is None else now - monthly(session, fs, lab, max(0, size - frees), day)
            if key not in found or found[key]["freed"] < frees:
                found[key] = {"kind": kind, "what": what, "fs": fs.slug, "fs_name": fs.name, "path": row_path,
                              "name": row_path.rsplit("/", 1)[-1], "bytes": size_, "freed": frees, "action": action,
                              "estimate": kind == "compress",
                              "saves": str(money(saves, currency)) if saves is not None else None}

        for r in idx.work_dirs(top, limit=limit):
            offer("workflow", r["path"], WORKFLOW[r["kind"]], r["bytes"], r["bytes"], "delete")
        cold = cold_bucket()
        for r in idx.cold_dirs(top, cold, limit=limit)["rows"]:
            offer("cold", r["path"], "Unchanged for years", r["bytes"], r["bytes"], "move" if len(others) > 1 else "delete")
        rows = [(c["path"], sum(c["types"].values()), compressible([{"ext": e, "bytes": b} for e, b in c["types"].items()]))
                for c in idx.compressible(top, list(COMPRESSION), limit=5 * limit)]
        for p, b, est in sorted(rows, key=lambda r: -r[2])[:limit]:
            offer("compress", p, "Uncompressed sequencing data", b, est, "compress")
    # One inside another (compressing inside a work folder suggested for deletion) is left to the outer one.
    out = [s for s in found.values()
           if not any(o is not s and o["fs"] == s["fs"] and _inside(s["path"], o["path"]) for o in found.values())]
    out.sort(key=lambda s: -s["freed"])
    return [s for k in ("workflow", "compress", "cold") for s in out if s["kind"] == k][: 3 * limit]


# ---------- Every lab's plan (C5) ----------

def overview(session: Session) -> dict:
    """For administrators: every lab's plan with what it saves and what is done, totals by filesystem, and the
    largest folders planned."""
    from decimal import Decimal

    labs = session.scalars(select(Lab).where(Lab.id.in_(select(CleanupItem.lab_id))).order_by(Lab.name)).all()
    plans_ = [lab_plan(session, lab) for lab in labs]
    by_fs: dict[str, dict] = {}
    for p in plans_:
        for f in p["savings"]["filesystems"]:
            row = by_fs.setdefault(f["fs"], {"fs": f["fs"], "fs_name": f["fs_name"], "to_free": 0, "moved_in": 0})
            row["to_free"] += f["freed"]
            row["moved_in"] += f["added"]
    for p in plans_:
        for i in p["items"]:
            if i["inside"] is None and i["progress"]["freed"]:
                by_fs.setdefault(i["fs"], {"fs": i["fs"], "fs_name": i["fs_name"], "to_free": 0, "moved_in": 0})
                by_fs[i["fs"]]["freed"] = by_fs[i["fs"]].get("freed", 0) + i["progress"]["freed"]
    largest = sorted((dict(i, lab_name=p["lab"]["name"]) for p in plans_ for i in p["items"]
                      if i["inside"] is None and i["progress"]["status"] != DONE),
                     key=lambda i: -(i["saving"]["freed"] if i["saving"] else 0))[:20]
    zero = Decimal(0)
    return {"currency": get_settings().currency, "labs": plans_, "largest": largest,
            "filesystems": sorted(({"freed": 0, **r} for r in by_fs.values()), key=lambda r: r["fs_name"]),
            "monthly": str(sum((Decimal(p["savings"]["monthly"]) for p in plans_), zero)),
            "done": {"freed": sum(p["savings"]["done"]["freed"] for p in plans_),
                     "monthly": str(sum((Decimal(p["savings"]["done"]["monthly"]) for p in plans_), zero))}}
