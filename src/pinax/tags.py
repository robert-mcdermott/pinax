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
from decimal import Decimal

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


# ---------- Tagging folders (T2) ----------

def _owner(session: Session, fs, path: str):
    from .plans import owner_of, top_folder

    return top_folder(fs, path), owner_of(session, fs, path)


def can_apply(ident, tag: Tag, lab: Lab | None) -> bool:
    """Whether `ident` (None: the command line) may put `tag` on a folder `lab` owns."""
    if not tag.active:
        return False
    if ident is None:
        return True
    if tag.lab_id is not None and (lab is None or tag.lab_id != lab.id):
        return False  # a lab's own tags are for its folders
    if ident.is_admin:
        return True
    return tag.applied_by == "owners" and lab is not None and lab.id in ident.lab_roles


def _visible(ident, fs, path: str, top: str | None) -> bool:
    from .explore import canonical

    return top is not None and canonical(path, fs.root) and (ident is None or ident.can_view_folder(fs.id, top))


def apply(session: Session, ident, fs, path: str, tag: Tag, value: str | None, *, cold_from: int) -> FolderTag:
    """Tag a folder (and so everything below it). A tag with one value replaces the value the folder had."""
    from . import marks

    top, lab = _owner(session, fs, path)
    if not _visible(ident, fs, path, top):
        raise TagError("That folder is not in the latest scan, or it is not shared with you", 404)
    if not can_apply(ident, tag, lab):
        raise TagError(f"{tag.label} {'is retired' if not tag.active else 'is not one you can put on this folder'}", 403)
    value = valid_value(tag, value)
    try:
        mark = marks.mark_folder(session, fs, path, cold_from=cold_from)
    except marks.MarkError as e:
        raise TagError(str(e), e.status) from e
    had = session.scalars(select(FolderTag).where(FolderTag.tag_id == tag.id, FolderTag.mark_id == mark.id)).all()
    if any(h.value == value for h in had):
        raise TagError("The folder has that tag already", 409)
    if not tag.many:
        for h in had:
            session.delete(h)
    user = ident.user if ident else None
    ft = FolderTag(tag_id=tag.id, mark_id=mark.id, value=value, added_by=user.id if user else None)
    ft.tag, ft.mark = tag, mark
    session.add(ft)
    shown = f"{tag.label}: {value}" if value else tag.label
    _audit(session, user, "folder.tagged", f"{path} tagged {shown}", tag, path=path, value=value)
    session.flush()
    return ft


def untag(session: Session, ident, ft: FolderTag) -> None:
    from .models import Filesystem

    fs = session.get(Filesystem, ft.mark.filesystem_id)
    _, lab = _owner(session, fs, ft.mark.path)
    if not can_apply(ident, ft.tag, lab) and not (ident and ident.is_admin):
        raise TagError("That tag is not one you can take off", 403)
    _audit(session, ident.user if ident else None, "folder.untagged", f"{ft.mark.path}: {ft.tag.label} taken off",
           ft.tag, path=ft.mark.path, value=ft.value)
    session.delete(ft)
    session.flush()


def ancestors(path: str, root: str) -> list[str]:
    """`path` and each folder above it, up to (not including) the filesystem root."""
    root = root.rstrip("/")
    out, p = [], path
    while p.startswith(root + "/"):
        out.append(p)
        p = p.rsplit("/", 1)[0]
    return out


def _ft_json(ft: FolderTag, ident, lab: Lab | None, here: str, session: Session) -> dict:
    by = session.get(User, ft.added_by) if ft.added_by else None
    return {"id": ft.id, "tag": tag_json(ft.tag), "value": ft.value, "from": None if ft.mark.path == here else ft.mark.path,
            "added_by": by.display_name if by else None, "added_at": ft.added_at.isoformat() + "Z",
            "can_remove": ft.mark.path == here and (can_apply(ident, ft.tag, lab) or bool(ident and ident.is_admin))}


def effective(session: Session, fs_id: int, path: str, root: str) -> list[FolderTag]:
    """The tags a folder has: its own, and those of the folders above it."""
    from .models import MarkedFolder

    up = ancestors(path, root)
    if not up:
        return []
    return session.scalars(select(FolderTag).join(MarkedFolder, MarkedFolder.id == FolderTag.mark_id).where(
        MarkedFolder.filesystem_id == fs_id, MarkedFolder.path.in_(up))).all()


def here(session: Session, ident, fs, path: str) -> dict:
    """For the age map's panel: a folder's own tags and those it inherits, and the tags the person can add."""
    top, lab = _owner(session, fs, path)
    if not _visible(ident, fs, path, top):
        return {"taggable": False}
    have = sorted(effective(session, fs.id, path, fs.root), key=lambda ft: (-len(ft.mark.path), ft.tag.label))
    usable = [t for t in standard_tags(session, active_only=True) if can_apply(ident, t, lab)]
    if lab is not None:
        usable += [t for t in lab_tags(session, lab, active_only=True) if can_apply(ident, t, lab)]
    member = lab is not None and (ident.is_admin or lab.id in ident.lab_roles)
    return {"taggable": True, "lab": {"slug": lab.slug, "name": lab.name} if lab else None,
            "tags": [_ft_json(ft, ident, lab, path, session) for ft in have],
            "available": [tag_json(t) for t in usable], "can_make": member}


def make_custom(session: Session, ident, lab: Lab, label: str, kind: str = "flag") -> Tag:
    """A lab's own tag, made by its PI or a delegate (or an administrator): a flag or text."""
    if not (ident.is_admin or lab.id in ident.lab_roles):
        raise TagError("Only the lab's PI and delegates make its tags", 403)
    if kind not in ("flag", "text"):
        raise TagError("A lab's own tag is a flag or text")
    label = label.strip()[:100]
    key = _key(label)
    if session.scalar(select(Tag.id).where(Tag.lab_id == lab.id, Tag.key == key)):
        raise TagError(f"The {lab.name} has a tag {label!r} already", 409)
    tag = Tag(key=key, label=label, lab_id=lab.id, value_type=kind, many=kind == "text", applied_by="owners")
    tag.lab = lab
    session.add(tag)
    _audit(session, ident.user, "tag.added", f"{lab.name}: its own tag {label!r} made", tag)
    session.flush()
    return tag


def merge(session: Session, actor: User | None, custom: Tag, into: Tag, value: str | None = None) -> int:
    """Fold a lab's own tag into a standard one: each folder with it gets the standard tag (with `value`, or the
    custom tag's own value, or for a flag its name), and the lab's tag is retired. Returns how many folders."""
    if custom.lab_id is None or into.lab_id is not None:
        raise TagError("A lab's own tag is merged into a standard tag")
    moved = 0
    for ft in session.scalars(select(FolderTag).where(FolderTag.tag_id == custom.id)).all():
        v = valid_value(into, value if value is not None else (ft.value or custom.label))
        exists = session.scalar(select(FolderTag).where(FolderTag.tag_id == into.id, FolderTag.mark_id == ft.mark_id))
        if exists is not None and not into.many:
            exists.value = v
        elif exists is None or exists.value != v:
            session.add(FolderTag(tag_id=into.id, mark_id=ft.mark_id, value=v, added_by=ft.added_by, added_at=ft.added_at))
        session.delete(ft)
        moved += 1
    custom.active = False
    _audit(session, actor, "tag.merged", f"{custom.lab.name}'s tag {custom.label!r} merged into {into.label!r}", custom,
           into=into.key, folders=moved)
    session.flush()
    return moved


def tagged_below(session: Session, ident, fs, path: str) -> dict[str, list[str]]:
    """For the age map's tiles: each tagged folder at or below `path` the person may see, with its tags' labels."""
    from .models import MarkedFolder

    q = (select(MarkedFolder.path, Tag.label, FolderTag.value).join(FolderTag, FolderTag.mark_id == MarkedFolder.id)
         .join(Tag, Tag.id == FolderTag.tag_id).where(MarkedFolder.filesystem_id == fs.id))
    q = q.where((MarkedFolder.path == path) | MarkedFolder.path.startswith(path.rstrip("/") + "/", autoescape=True))
    out: dict[str, list[str]] = {}
    for p, label, value in session.execute(q):
        top = p[len(fs.root.rstrip("/")) + 1:].split("/", 1)[0]
        if ident.can_view_folder(fs.id, top):
            out.setdefault(p, []).append(f"{label}: {value}" if value else label)
    return out


def import_rows(session: Session, rows: list[dict], *, apply_them: bool, cold_from: int) -> tuple[list[str], list[str]]:
    """Tags from a CSV (filesystem, path, tag, value; a lab's own tag as lab:key), as the command line: what each
    row does, and what is wrong. Nothing is changed unless `apply_them` and nothing is wrong."""
    from .models import Filesystem

    done, problems = [], []
    for n, row in enumerate(rows, start=2):
        fs = session.scalar(select(Filesystem).where(Filesystem.slug == (row.get("filesystem") or "").strip()))
        key = (row.get("tag") or "").strip()
        lab_slug, _, key = key.rpartition(":")
        q = select(Tag).where(Tag.key == key)
        q = q.where(Tag.lab_id == select(Lab.id).where(Lab.slug == lab_slug).scalar_subquery()) if lab_slug else q.where(Tag.lab_id.is_(None))
        tag = session.scalar(q)
        path = (row.get("path") or "").strip().rstrip("/")
        if fs is None or tag is None:
            problems.append(f"line {n}: no {'filesystem' if fs is None else 'tag'} {row.get('filesystem') if fs is None else row.get('tag')!r}")
            continue
        if not path.startswith("/"):
            path = f"{fs.root.rstrip('/')}/{path}"
        try:
            with session.begin_nested():
                apply(session, None, fs, path, tag, row.get("value"), cold_from=cold_from)
                done.append(f"line {n}: {path}: {tag.label}{': ' + row['value'] if row.get('value') else ''}")
                if not apply_them:
                    raise _Undo
        except _Undo:
            pass
        except TagError as e:
            problems.append(f"line {n}: {path}: {e}")
    return done, problems


class _Undo(Exception):
    """Rolls back one row of a dry run."""


# ---------- Finding data by tag (T3) ----------

def filters(session: Session, ident) -> list[dict]:
    """The tags a person can look for, each with the values folders they may see have."""
    from .models import Filesystem, MarkedFolder

    roots = {f.id: f.root for f in session.scalars(select(Filesystem))}
    used: dict[int, set[str]] = {}
    rows = session.execute(select(FolderTag.tag_id, FolderTag.value, MarkedFolder.filesystem_id, MarkedFolder.path)
                           .join(MarkedFolder, MarkedFolder.id == FolderTag.mark_id))
    for tag_id, value, fs_id, path in rows:
        top = path[len(roots[fs_id].rstrip("/")) + 1:].split("/", 1)[0]
        if ident.can_view_folder(fs_id, top):
            used.setdefault(tag_id, set()).add(value)
    q = select(Tag).where(Tag.id.in_(used) | (Tag.lab_id.is_(None) & Tag.active.is_(True)))
    out = []
    for t in session.scalars(q.order_by(Tag.lab_id.is_not(None), Tag.label)):
        if t.lab_id is not None and not (ident.is_admin or t.lab_id in ident.lab_roles):
            continue
        out.append({**tag_json(t), "values": sorted(used.get(t.id, set()) - {""})})
    return out


def collection(session: Session, ident, wanted: list[tuple[int, str | None]]) -> dict:
    """The folders that have every one of `wanted` (a tag, and a value or any), on their own or from a folder above,
    that the person may see: the outermost of them, each with its latest totals, owner, monthly cost, and tags; their
    total; and their history and forecast, from the totals recorded with each scan. Reads only the catalog, each
    table once."""
    from collections import defaultdict
    from datetime import datetime, time

    from .auth import folder_owners
    from .charges import money
    from .config import get_settings
    from .db import utcnow
    from .explore import size_forecast
    from .models import Filesystem, FolderStat, MarkedFolder, MarkedFolderStat, Snapshot, SnapshotStatus
    from .plans import monthly, top_folder

    if not wanted:
        return {"folders": [], "total": None, "points": [], "forecast": None}
    fss = {f.id: f for f in session.scalars(select(Filesystem))}
    tag_by = {tg.id: tg for tg in session.scalars(select(Tag))}
    marks = {m.id: m for m in session.scalars(select(MarkedFolder).where(MarkedFolder.id.in_(select(FolderTag.mark_id))))}
    # Every tag on every tagged folder, by filesystem and path, so ancestors are at hand.
    have: dict[tuple[int, str], list[tuple[int, str]]] = defaultdict(list)
    for tag_id, mark_id, value in session.execute(select(FolderTag.tag_id, FolderTag.mark_id, FolderTag.value)):
        m = marks[mark_id]
        have[(m.filesystem_id, m.path)].append((tag_id, value))
    wanted_ids = {t for t, _ in wanted}

    def effective(fs_id: int, path: str) -> list[tuple[int, str]]:
        return [tv for p in ancestors(path, fss[fs_id].root) for tv in have.get((fs_id, p), [])]

    by_path = {(m.filesystem_id, m.path): m for m in marks.values()}
    hits = []
    for key, tvs in have.items():
        if not any(t in wanted_ids for t, _ in tvs):
            continue
        tags_here = effective(*key)
        if all(any(t == w and (v is None or val == v) for t, val in tags_here) for w, v in wanted) and \
                ident.can_view_folder(key[0], top_folder(fss[key[0]], key[1])):
            hits.append(by_path[key])
    # The outermost: a match with no match above it.
    matched = {(h.filesystem_id, h.path) for h in hits}
    hits = [h for h in hits if not any((h.filesystem_id, p) in matched
                                       for p in ancestors(h.path, fss[h.filesystem_id].root)[1:])]
    cols = (MarkedFolderStat.mark_id, MarkedFolderStat.taken_at, MarkedFolderStat.present, MarkedFolderStat.bytes,
            MarkedFolderStat.files, MarkedFolderStat.cold_bytes, MarkedFolderStat.mean_age_days)
    stats = session.execute(select(*cols).where(MarkedFolderStat.mark_id.in_([m.id for m in hits]))
                            .order_by(MarkedFolderStat.taken_at)).all()
    last = {}
    for s in stats:
        last[s.mark_id] = s
    owners = folder_owners(session)
    held: dict[tuple[int, int], int] = defaultdict(int)  # (lab, filesystem) -> its size in the latest scan
    for fs_id in {m.filesystem_id for m in hits}:
        latest_id = session.scalar(select(Snapshot.id).where(Snapshot.filesystem_id == fs_id, Snapshot.status.in_(
            (SnapshotStatus.READY, SnapshotStatus.PRUNED))).order_by(Snapshot.started_at.desc()).limit(1))
        for folder, b in session.execute(select(FolderStat.folder, FolderStat.bytes).where(FolderStat.snapshot_id == latest_id)):
            if (lab := owners.get((fs_id, folder))) is not None:
                held[(lab.id, fs_id)] += b
    currency, day = get_settings().currency, utcnow().date()
    whole: dict[tuple[int, int], tuple[int, object]] = {}  # (lab, filesystem) -> (its size, its monthly charge)
    rows = []
    for m in sorted(hits, key=lambda h: (fss[h.filesystem_id].name, h.path)):
        fs = fss[m.filesystem_id]
        lab = owners.get((fs.id, top_folder(fs, m.path)))
        s = last.get(m.id)
        here = s is not None and s.present
        size = s.bytes if here else 0
        cost = None
        if lab is not None:
            if (lab.id, fs.id) not in whole:
                n = held[(lab.id, fs.id)]
                whole[(lab.id, fs.id)] = (n, monthly(session, fs, lab, n, day))
            n, charge = whole[(lab.id, fs.id)]
            if charge is not None and n:
                cost = charge * size / n  # its share of the lab's charge there
        labels = sorted({f"{tag_by[t].label}: {v}" if v else tag_by[t].label for t, v in effective(fs.id, m.path)})
        rows.append({"fs": fs.slug, "fs_name": fs.name, "path": m.path, "name": m.path.rsplit("/", 1)[-1],
                     "lab": {"slug": lab.slug, "name": lab.name} if lab else None, "status": m.status,
                     "bytes": size, "files": s.files if here else 0, "cold_bytes": s.cold_bytes if here else 0,
                     "mean_age_days": s.mean_age_days if here else None,
                     "taken_at": s.taken_at.isoformat() + "Z" if s else None,
                     "monthly": str(money(cost, currency)) if cost is not None else None, "tags": labels})
    # History: on each day with a scan of one of them, each folder as of its latest scan by then.
    days = sorted({s.taken_at.date() for s in stats})
    points, latest, i = [], {}, 0
    for d in days:
        end = datetime.combine(d, time.max)
        while i < len(stats) and stats[i].taken_at <= end:  # one pass: each folder's latest scan by then
            latest[stats[i].mark_id] = stats[i]
            i += 1
        at = max(s.taken_at for s in latest.values())
        present = [s for s in latest.values() if s.present]
        points.append({"taken_at": at.isoformat() + "Z", "present": bool(present), "bytes": sum(s.bytes for s in present),
                       "files": sum(s.files for s in present), "cold_bytes": sum(s.cold_bytes for s in present)})
    costs = [Decimal(r["monthly"]) for r in rows if r["monthly"] is not None]
    total = {"folders": len(rows), "bytes": sum(r["bytes"] for r in rows), "files": sum(r["files"] for r in rows),
             "cold_bytes": sum(r["cold_bytes"] for r in rows), "monthly": str(sum(costs, Decimal(0))) if costs else None,
             "currency": currency}
    return {"folders": rows, "total": total, "points": points, "forecast": size_forecast(points) if points else None}


# ---------- Protection from cleanup (T5) ----------

def _protecting(ft: FolderTag, today: date) -> bool:
    if not ft.tag.protects:
        return False
    return ft.tag.value_type != "date" or (bool(ft.value) and date.fromisoformat(ft.value) >= today)


def _why(ft: FolderTag) -> dict:
    return {"tag": ft.tag.label, "value": ft.value, "path": ft.mark.path,
            "text": f"kept until {ft.value}" if ft.tag.value_type == "date" else ft.tag.label.lower()}


def protection(session: Session, fs, path: str, today: date | None = None) -> dict | None:
    """What protects a folder from cleanup, if anything: a protecting tag on it or a folder above it (on legal hold;
    kept until a day not yet past), or on a folder below it, which cleaning it up would take too."""
    from .db import utcnow
    from .models import MarkedFolder

    today = today or utcnow().date()
    for ft in effective(session, fs.id, path, fs.root):
        if _protecting(ft, today):
            return {**_why(ft), "where": "here" if ft.mark.path == path else "above"}
    below = session.scalars(select(FolderTag).join(MarkedFolder, MarkedFolder.id == FolderTag.mark_id).join(
        Tag, Tag.id == FolderTag.tag_id).where(MarkedFolder.filesystem_id == fs.id, Tag.protects.is_(True),
                                               MarkedFolder.path.startswith(path.rstrip("/") + "/", autoescape=True))).all()
    for ft in below:
        if _protecting(ft, today):
            return {**_why(ft), "where": "below"}
    return None


def protection_text(p: dict) -> str:
    where = {"here": "It is", "above": f"It is inside {p['path']}, which is", "below": f"It holds {p['path']}, which is"}
    return f"{where[p['where']]} {p['text']}, so it cannot go in a cleanup plan"
