# SPDX-License-Identifier: Apache-2.0
"""Explore and report services: resolve what a person may see, then query snapshot indexes.

A request names a place by filesystem and path. Without one it means the person's *home*: every
filesystem root for an administrator, or every top-level folder their labs own for everyone else,
which may span several filesystems. Reports over home merge the results of each place.
"""

import heapq
from collections import defaultdict
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .auth import Identity, folder_owners
from .categories import AGE_BUCKETS, CATEGORIES, CATEGORY_KEYS
from .config import get_settings
from .index import DAY, METRICS, SnapshotIndex, Stats, iso_from_epoch, pattern_to_like
from .models import Filesystem, FolderStat, Lab, LabFolder, Snapshot, SnapshotStatus
from .serialize import folder_stat_json, iso, lab_brief
from .snapshots import cold_bucket, indexes, latest_snapshot, previous_snapshot

NB = len(AGE_BUCKETS)
TILE_LIMIT = 60  # children per treemap level; the rest fold into one "smaller folders" tile
GRAND_LIMIT = 12  # grandchildren per child when drawing two levels


@dataclass
class Place:
    fs: Filesystem
    snap: Snapshot
    idx: SnapshotIndex
    path: str
    lab: Lab | None = None

    @property
    def key(self) -> str:
        return f"{self.fs.slug}:{self.path}"


def not_found(what: str = "That folder is not in the latest scan, or it is not shared with you") -> HTTPException:
    return HTTPException(404, what)


# ---------- Resolving places ----------

def canonical(path: str, root: str) -> bool:
    """Index paths are exactly as the scanner recorded them: no `.` or `..` components, no empty ones."""
    if path == root:
        return True
    rest = path[len(root.rstrip("/")) + 1:]
    return all(part not in ("", ".", "..") for part in rest.split("/"))


def _filesystems(session: Session) -> list[Filesystem]:
    return list(session.scalars(select(Filesystem).where(Filesystem.active.is_(True)).order_by(Filesystem.name)))


def _snapshot_for(session: Session, fs: Filesystem, snap_id: int | None) -> Snapshot | None:
    if snap_id:
        snap = session.get(Snapshot, snap_id)
        if snap is None or snap.filesystem_id != fs.id or snap.status != SnapshotStatus.READY:
            raise not_found("That snapshot is not available")
        return snap
    return latest_snapshot(session, fs)


def home_places(session: Session, ident: Identity, lab_slug: str | None = None) -> list[Place]:
    places: list[Place] = []
    owners = folder_owners(session) if ident.is_admin else ident.folders
    for fs in _filesystems(session):
        snap = latest_snapshot(session, fs)
        if snap is None:
            continue
        idx = indexes.get(snap)
        if ident.is_admin and not lab_slug:
            places.append(Place(fs, snap, idx, idx.root))
            continue
        for (fs_id, name), lab in sorted(owners.items(), key=lambda kv: kv[0][1]):
            if fs_id != fs.id or name not in idx.top_ids:
                continue
            if lab_slug and lab.slug != lab_slug:
                continue
            places.append(Place(fs, snap, idx, idx.top_path(name), lab))
    return places


def resolve(session: Session, ident: Identity, fs_slug: str | None, path: str | None,
            snap_id: int | None = None, lab_slug: str | None = None) -> list[Place]:
    """The places a request covers, after checking the person may see them."""
    if not fs_slug:
        return home_places(session, ident, lab_slug)
    fs = session.scalar(select(Filesystem).where(Filesystem.slug == fs_slug))
    if fs is None:
        raise not_found("No such filesystem")
    snap = _snapshot_for(session, fs, snap_id)
    if snap is None:
        raise not_found(f"{fs.name} has not been scanned yet")
    idx = indexes.get(snap)
    path = path or idx.root
    if not idx.contains(path) or not canonical(path, idx.root):
        raise not_found()
    top = idx.top_of(path)
    if not ident.can_view_folder(fs.id, top):
        raise not_found()
    owners = folder_owners(session)
    return [Place(fs, snap, idx, path, owners.get((fs.id, top)) if top else None)]


# ---------- Stats helpers ----------

def rebase(stats: Stats, from_ref: float, to_ref: float) -> Stats:
    """Shift time sums so mean ages measured from `from_ref` read the same from `to_ref`."""
    if from_ref == to_ref:
        return stats
    v = dict(stats.values)
    shift = to_ref - from_ref
    v["m_wsum"] = (v.get("m_wsum") or 0) + (v.get("bytes") or 0) * shift
    v["m_fsum"] = (v.get("m_fsum") or 0) + (v.get("m_n") or 0) * shift
    v["a_wsum"] = (v.get("a_wsum") or 0) + (v.get("bytes") or 0) * shift
    return Stats(v)


TILE_KEYS = ("bytes", "alloc", "files", "dirs", "incomplete", "mean_age_days", "newest", "oldest", "last_access",
             "age_bytes", "cat_bytes")


def tile_stats(stats: Stats, ref_day: float) -> dict:
    j = stats.json(ref_day)
    return {k: j[k] for k in TILE_KEYS}


def _direct(row: dict, all_children: dict | None) -> Stats:
    """A directory's own files: its subtree minus all of its subdirectories' subtrees."""
    st = Stats.from_row(row)
    if all_children and all_children.get("n"):
        st = st.sub(Stats.from_row(all_children))
    v = st.values
    v["dirs"] = 0
    v["incomplete"] = 0
    v["mtime_min"], v["mtime_max"], v["atime_max"] = row["d_mtime_min"], row["d_mtime_max"], row["d_atime_max"]
    v["files"], v["bytes"] = row["d_files"], row["d_bytes"]
    return st


def _dir_tile(place_fs: str, row: dict, ref_day: float, lab: Lab | None = None) -> dict:
    return {
        "kind": "dir", "fs": place_fs, "path": row["path"], "name": row["name"],
        "error": row.get("error"), "mount": bool(row.get("is_mount_point")), "lab": lab_brief(lab),
        "stats": tile_stats(Stats.from_row(row), ref_day),
    }


def _tiles_for(fs: str, idx: SnapshotIndex, rows: list[dict], all_sum: dict | None, rest: dict | None,
               parent_row: dict | None, ref_day: float, owners: dict | None = None,
               fs_id: int | None = None) -> list[dict]:
    tiles = []
    for r in rows:
        lab = owners.get((fs_id, r["name"])) if owners is not None and r["depth"] == 1 else None
        tiles.append(_dir_tile(fs, r, ref_day, lab))
    if rest and rest.get("n"):
        tiles.append({"kind": "other", "fs": fs, "path": None, "count": int(rest["n"]),
                      "name": f"{int(rest['n']):,} smaller folder{'s' if rest['n'] != 1 else ''}",
                      "stats": tile_stats(Stats.from_row(rest), ref_day)})
    if parent_row is not None and parent_row.get("d_files"):
        tiles.append({"kind": "files", "fs": fs, "path": parent_row["path"],
                      "name": "Files in this folder", "count": int(parent_row["d_files"]),
                      "stats": tile_stats(_direct(parent_row, all_sum), ref_day)})
    return tiles


def _breadcrumbs(ident: Identity, place: Place) -> list[dict]:
    crumbs = [{"label": "All data" if ident.is_admin else "My data", "fs": None, "path": None}]
    idx = place.idx
    if ident.is_admin:
        crumbs.append({"label": place.fs.name, "fs": place.fs.slug, "path": idx.root})
    if place.path == idx.root:
        return crumbs
    rel = place.path[len(idx.prefix):].split("/")
    for i in range(len(rel)):
        crumbs.append({"label": rel[i], "fs": place.fs.slug, "path": idx.prefix + "/".join(rel[: i + 1])})
    return crumbs


def _snap_brief(snap: Snapshot) -> dict:
    return {"id": snap.id, "started_at": iso(snap.started_at), "filesystem": snap.filesystem.slug}


# ---------- Treemap ----------

def tree(session: Session, ident: Identity, fs_slug: str | None, path: str | None, *, metric: str = "bytes",
         depth: int = 1, snap_id: int | None = None, lab_slug: str | None = None) -> dict:
    if metric not in METRICS:
        raise HTTPException(422, f"Unknown size metric {metric!r}")
    places = resolve(session, ident, fs_slug, path, snap_id, lab_slug)
    if fs_slug:
        return _tree_dir(session, ident, places[0], metric, depth)
    return _tree_home(session, ident, places, metric, depth, lab_slug)


def _tree_dir(session: Session, ident: Identity, place: Place, metric: str, depth: int) -> dict:
    idx = place.idx
    row = idx.node(place.path)
    if row is None:
        raise not_found()
    ch = idx.children(place.path, metric=metric, limit=TILE_LIMIT, grand_limit=GRAND_LIMIT if depth > 1 else 0)
    owners = folder_owners(session) if place.path == idx.root else None
    if owners is not None and not ident.is_admin:
        owners = None
    tiles = _tiles_for(place.fs.slug, idx, ch["rows"], ch["all"], ch["rest"], row, idx.ref_day, owners, place.fs.id)
    grand = ch.get("grand") or {}
    by_path = {r["path"]: r for r in ch["rows"]}
    for t in tiles:
        if t["kind"] != "dir":
            continue
        r = by_path[t["path"]]
        g = grand.get(r["dir_id"])
        if g:
            t["children"] = _tiles_for(place.fs.slug, idx, g["rows"], g["all"], g["rest"], r, idx.ref_day)
        elif depth > 1 and r["d_files"] and r["dirs"]:
            t["children"] = _tiles_for(place.fs.slug, idx, [], None, None, r, idx.ref_day)
    stats = Stats.from_row(row)
    node = {
        "kind": "root" if place.path == idx.root else "folder" if idx.depth_of(place.path) == 1 else "dir",
        "fs": place.fs.slug, "fs_name": place.fs.name, "path": place.path,
        "name": place.fs.name if place.path == idx.root else row["name"],
        "lab": lab_brief(place.lab), "error": row["error"], "dir_entries": row["dir_entries"],
        "stats": stats.json(idx.ref_day),
        "direct": tile_stats(_direct(row, ch["all"]), idx.ref_day),
        "subdirs": int(ch["all"]["n"] or 0),
    }
    return {"node": node, "tiles": tiles, "breadcrumbs": _breadcrumbs(ident, place),
            "snapshots": [_snap_brief(place.snap)], "as_of": iso(place.snap.started_at), "metric": metric,
            "cold_bucket": cold_bucket()}


def _tree_home(session: Session, ident: Identity, places: list[Place], metric: str, depth: int,
               lab_slug: str | None) -> dict:
    ref = max((p.idx.ref_day for p in places), default=0.0)
    tiles, totals = [], []
    for p in places:
        row = p.idx.node(p.path)
        if row is None:
            continue
        st = rebase(Stats.from_row(row), p.idx.ref_day, ref)
        totals.append(st)
        is_root = p.path == p.idx.root
        tile = {"kind": "fs" if is_root else "dir", "fs": p.fs.slug, "path": p.path,
                "name": p.fs.name if is_root else row["name"], "lab": lab_brief(p.lab),
                "fs_name": p.fs.name, "error": row["error"], "stats": tile_stats(st, ref)}
        if depth > 1:
            ch = p.idx.children(p.path, metric=metric, limit=GRAND_LIMIT)
            owners = folder_owners(session) if is_root else None
            tile["children"] = _tiles_for(p.fs.slug, p.idx, ch["rows"], ch["all"], ch["rest"], row, p.idx.ref_day,
                                          owners, p.fs.id)
        tiles.append(tile)
    total = Stats.zero()
    for st in totals:
        total = total.add(st, count_as_dir=True)
    lab = session.scalar(select(Lab).where(Lab.slug == lab_slug)) if lab_slug else None
    node = {"kind": "home", "fs": None, "path": None, "lab": lab_brief(lab),
            "name": lab.name if lab else ("All data" if ident.is_admin else "My data"),
            "stats": total.json(ref), "direct": None, "subdirs": len(tiles)}
    as_of = max((p.snap.started_at for p in places), default=None)
    return {"node": node, "tiles": tiles, "breadcrumbs": [{"label": node["name"], "fs": None, "path": None}],
            "snapshots": [_snap_brief(p.snap) for p in {p.snap.id: p for p in places}.values()],
            "as_of": iso(as_of), "metric": metric, "cold_bucket": cold_bucket()}


# ---------- Reports ----------

def types(session: Session, ident: Identity, fs_slug, path, snap_id=None, lab_slug=None) -> dict:
    places = resolve(session, ident, fs_slug, path, snap_id, lab_slug)
    merged: dict[tuple, dict] = {}
    for p in places:
        for r in p.idx.types(p.path):
            key = (r["ext"], r["cat"])
            m = merged.setdefault(key, {"ext": r["ext"], "cat": r["cat"], "files": 0, "bytes": 0, "alloc": 0,
                                        "age_wsum": 0.0, "oldest": None, "newest": None, "last_access": None})
            m["files"] += r["files"]
            m["bytes"] += r["bytes"]
            m["alloc"] += r["alloc"]
            m["age_wsum"] += r["bytes"] * p.idx.ref_day - (r["m_wsum"] or 0)
            for k, fn in (("oldest", min), ("newest", max), ("last_access", max)):
                if r[k] is not None:
                    m[k] = r[k] if m[k] is None else fn(m[k], r[k])
    rows = []
    for m in sorted(merged.values(), key=lambda m: -m["bytes"]):
        rows.append({
            "ext": m["ext"], "category": CATEGORY_KEYS[m["cat"]], "files": m["files"], "bytes": m["bytes"],
            "alloc": m["alloc"], "mean_age_days": round(m["age_wsum"] / m["bytes"], 1) if m["bytes"] else None,
            "oldest": _iso_epoch(m["oldest"]), "newest": _iso_epoch(m["newest"]),
            "last_access": _iso_epoch(m["last_access"]),
        })
    cats = []
    for i, c in enumerate(CATEGORIES):
        sel = [r for r in rows if r["category"] == c.key]
        b = sum(r["bytes"] for r in sel)
        cats.append({"key": c.key, "label": c.label, "files": sum(r["files"] for r in sel), "bytes": b,
                     "types": len(sel),
                     "mean_age_days": round(sum((r["mean_age_days"] or 0) * r["bytes"] for r in sel) / b, 1) if b else None})
    return {"categories": cats, "types": rows, **_scope_meta(places)}


def owners(session: Session, ident: Identity, fs_slug, path, snap_id=None, lab_slug=None) -> dict:
    places = resolve(session, ident, fs_slug, path, snap_id, lab_slug)
    merged: dict[tuple, dict] = {}
    for p in places:
        for r in p.idx.owners(p.path):
            m = merged.setdefault((r["uid"], r["name"]), {"uid": r["uid"], "name": r["name"], "files": 0, "bytes": 0,
                                                         "alloc": 0, "age_wsum": 0.0, "newest": None})
            m["files"] += r["files"]
            m["bytes"] += r["bytes"]
            m["alloc"] += r["alloc"]
            m["age_wsum"] += r["bytes"] * p.idx.ref_day - (r["m_wsum"] or 0)
            if r["newest"] is not None:
                m["newest"] = r["newest"] if m["newest"] is None else max(m["newest"], r["newest"])
    rows = [{"uid": m["uid"], "name": m["name"], "known": m["name"] is not None, "files": m["files"],
             "bytes": m["bytes"], "alloc": m["alloc"],
             "mean_age_days": round(m["age_wsum"] / m["bytes"], 1) if m["bytes"] else None,
             "newest": _iso_epoch(m["newest"])}
            for m in sorted(merged.values(), key=lambda m: -m["bytes"])]
    return {"owners": rows, **_scope_meta(places)}


FILE_SORTS = {"size": "size DESC", "oldest": "mtime ASC NULLS LAST", "newest": "mtime DESC NULLS LAST",
              "accessed": "atime ASC NULLS LAST", "path": "path"}


def _merge_key(sort: str):
    return {
        "size": lambda r: -(r["size"] or 0), "oldest": lambda r: r["mtime"] or float("inf"),
        "newest": lambda r: -(r["mtime"] or 0), "accessed": lambda r: r["atime"] or float("inf"),
        "path": lambda r: r["path"],
    }[sort]


def _file_json(fs: str, r: dict) -> dict:
    return {"fs": fs, "path": r["path"], "name": r["name"], "ext": r["ext"], "category": CATEGORY_KEYS[r["cat"]],
            "kind": r["kind"], "size": r["size"], "alloc": r["alloc_bytes"], "uid": r["uid"], "gid": r["gid"],
            "mtime": _iso_epoch(r["mtime"]), "atime": _iso_epoch(r["atime"]), "nlink": r["nlink"],
            "name_flags": r["name_flags"]}


@dataclass
class FileFilter:
    q: str = ""
    in_path: bool = False
    category: str = ""
    ext: str = ""
    min_size: int | None = None
    max_size: int | None = None
    older: int | None = None  # age bucket: modified before the start of this bucket
    newer: int | None = None  # modified within this many buckets (bucket index, exclusive upper)
    uid: int | None = None
    direct: bool = False

    def sql(self, ref_epoch: float) -> tuple[str, list]:
        cond, args = [], []
        if self.q:
            cond.append(f"{'path' if self.in_path else 'name'} ILIKE ? ESCAPE '\\'")
            args.append(pattern_to_like(self.q))
        if self.category:
            if self.category not in CATEGORY_KEYS:
                raise HTTPException(422, "Unknown category")
            cond.append("cat = ?")
            args.append(CATEGORY_KEYS.index(self.category))
        if self.ext:
            cond.append("ext = ?")
            args.append(self.ext.lower().lstrip("."))
        if self.min_size is not None:
            cond.append("size >= ?")
            args.append(self.min_size)
        if self.max_size is not None:
            cond.append("size <= ?")
            args.append(self.max_size)
        edges = [0.0] + [b.max_days for b in AGE_BUCKETS if b.max_days]
        if self.older is not None and 0 < self.older < len(edges):
            cond.append("epoch(mtime) < ?")
            args.append(ref_epoch - edges[self.older] * DAY)
        if self.newer is not None and 0 < self.newer < len(edges):
            cond.append("epoch(mtime) >= ?")
            args.append(ref_epoch - edges[self.newer] * DAY)
        if self.uid is not None:
            cond.append("uid = ?")
            args.append(self.uid)
        return " AND ".join(cond), args


def files(session: Session, ident: Identity, fs_slug, path, *, flt: FileFilter, sort: str = "size",
          limit: int = 100, snap_id=None, lab_slug=None, with_summary: bool = True) -> dict:
    if sort not in FILE_SORTS:
        raise HTTPException(422, "Unknown sort")
    places = resolve(session, ident, fs_slug, path, snap_id, lab_slug)
    limit = max(1, min(limit, get_settings().export_max_rows))
    streams, total_files, total_bytes = [], 0, 0
    for p in places:
        where, args = flt.sql(p.idx.ref_epoch)
        rows = p.idx.files(p.path, where=where, params=args, order=FILE_SORTS[sort], limit=limit,
                           direct_only=flt.direct)
        streams.append([_file_json(p.fs.slug, r) | {"_r": r} for r in rows])
        if with_summary:
            w = (where + " AND " if where else "") + ("depth = ?" if flt.direct else "true")
            s = p.idx.files_summary(p.path, where=w, params=[*args, *([p.idx.depth_of(p.path) + 1] if flt.direct else [])])
            total_files += s["files"]
            total_bytes += s["bytes"]
    key = _merge_key(sort)
    merged = list(heapq.merge(*streams, key=lambda x: key(x["_r"])))[:limit]
    for m in merged:
        m.pop("_r", None)
    names: dict[int, str | None] = {}
    for p in places:
        names.update(p.idx.user_names())
    for m in merged:
        m["owner"] = names.get(m["uid"])
    out = {"files": merged, **_scope_meta(places)}
    if with_summary:
        out["total"] = {"files": total_files, "bytes": total_bytes, "shown": len(merged)}
    return out


def cold(session: Session, ident: Identity, fs_slug, path, *, older: int, min_bytes: int = 0, limit: int = 200,
         snap_id=None, lab_slug=None) -> dict:
    if not 1 <= older < NB:
        raise HTTPException(422, "Choose an age threshold")
    places = resolve(session, ident, fs_slug, path, snap_id, lab_slug)
    rows, count, cbytes, cfiles = [], 0, 0, 0
    scope_bytes, cold_file_bytes = 0, 0
    owners = folder_owners(session)
    for p in places:
        res = p.idx.cold_dirs(p.path, older, min_bytes=min_bytes, limit=limit)
        count += res["count"]
        cbytes += res["bytes"]
        cfiles += res["files"]
        node = p.idx.node(p.path)
        if node:
            j = Stats.from_row(node).json(p.idx.ref_day)
            scope_bytes += j["bytes"]
            cold_file_bytes += sum(j["age_bytes"][older:])
        for r in res["rows"]:
            t = _dir_tile(p.fs.slug, r, p.idx.ref_day, owners.get((p.fs.id, p.idx.top_of(r["path"]))))
            t["depth"] = r["depth"]
            rows.append(t)
    rows.sort(key=lambda t: -t["stats"]["bytes"])
    return {"dirs": rows[:limit], "count": count, "bytes": cbytes, "files": cfiles, "scope_bytes": scope_bytes,
            "cold_file_bytes": cold_file_bytes, "older": older, "threshold": AGE_BUCKETS[older].label,
            **_scope_meta(places)}


def _scope_meta(places: list[Place]) -> dict:
    return {"as_of": iso(max((p.snap.started_at for p in places), default=None)),
            "places": [{"fs": p.fs.slug, "path": p.path} for p in places]}


def _iso_epoch(e: float | None) -> str | None:
    return iso_from_epoch(e)


# ---------- Home and folders ----------

def _latest_two(session: Session, fs: Filesystem) -> tuple[Snapshot | None, Snapshot | None]:
    snap = latest_snapshot(session, fs)
    return snap, previous_snapshot(session, snap) if snap else None


def folder_rows(session: Session, ident: Identity) -> list[dict]:
    """Every top-level folder the person may see, with its latest totals and change since the previous scan."""
    owners = folder_owners(session)
    out = []
    for fs in _filesystems(session):
        snap, prev = _latest_two(session, fs)
        if snap is None:
            continue
        stats = {s.folder: s for s in session.scalars(select(FolderStat).where(FolderStat.snapshot_id == snap.id))}
        before = ({s.folder: s for s in session.scalars(select(FolderStat).where(FolderStat.snapshot_id == prev.id))}
                  if prev else {})
        for name, s in sorted(stats.items()):
            if not ident.can_view_folder(fs.id, name or None):
                continue
            if not ident.is_admin and (fs.id, name) not in ident.folders:
                continue
            lab = owners.get((fs.id, name))
            p = before.get(name)
            out.append({
                "fs": fs.slug, "fs_name": fs.name, "folder": name, "path": (fs.root.rstrip("/") + "/" + name) if name else fs.root,
                "lab": lab_brief(lab), "latest": folder_stat_json(s),
                "change": {"bytes": s.bytes - p.bytes, "files": s.files - p.files,
                           "since": iso(p.taken_at)} if p else None,
            })
    return out


def history(session: Session, ident: Identity, fs_slug: str, folder: str) -> dict:
    fs = session.scalar(select(Filesystem).where(Filesystem.slug == fs_slug))
    if fs is None or not ident.can_view_folder(fs.id, folder or None):
        raise not_found()
    rows = session.scalars(select(FolderStat).where(FolderStat.filesystem_id == fs.id, FolderStat.folder == folder)
                           .order_by(FolderStat.taken_at)).all()
    return {"fs": fs.slug, "folder": folder, "points": [folder_stat_json(r) for r in rows]}


def lab_summaries(session: Session, ident: Identity) -> list[dict]:
    """Per lab or department: totals over its folders in the latest scans."""
    rows = folder_rows(session, ident)
    labs = session.scalars(select(Lab).options(selectinload(Lab.folders).selectinload(LabFolder.filesystem),
                                               selectinload(Lab.memberships))).all()
    by_slug: dict[str, dict] = defaultdict(lambda: {"bytes": 0, "files": 0, "cold_bytes": 0, "change": 0, "folders": 0})
    for r in rows:
        if r["lab"]:
            a = by_slug[r["lab"]["slug"]]
            a["bytes"] += r["latest"]["bytes"]
            a["files"] += r["latest"]["files"]
            a["cold_bytes"] += r["latest"]["cold_bytes"]
            a["folders"] += 1
            a["change"] += (r["change"] or {}).get("bytes", 0)
    out = []
    for lab in labs:
        if not ident.is_admin and lab.id not in ident.lab_roles:
            continue
        out.append({**lab_brief(lab), "division": lab.division, "research_area": lab.research_area,
                    "members": len(lab.memberships), **by_slug[lab.slug]})
    return sorted(out, key=lambda r: -r["bytes"])
