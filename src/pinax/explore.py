# SPDX-License-Identifier: Apache-2.0
"""Explore and report services: resolve what a person may see, then query snapshot indexes.

A request names a place by filesystem and path. Without one it means the person's *home*: every
filesystem root for an administrator, or every top-level folder their labs own for everyone else,
which may span several filesystems. Reports over home merge the results of each place.
"""

import heapq
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import BigInteger, cast, func, select
from sqlalchemy.orm import Session, selectinload

from .auth import Identity, folder_owners
from .categories import AGE_BUCKETS, CATEGORIES, CATEGORY_KEYS
from .config import get_settings
from .index import CHANGE_METRICS, DAY, METRICS, SnapshotIndex, Stats, add_pairs, iso_from_epoch, pattern_to_like
from .models import Filesystem, FolderStat, Lab, LabFolder, Snapshot, SnapshotStatus
from .serialize import folder_stat_json, iso, lab_brief
from .snapshots import cold_bucket, indexes, latest_snapshot, previous_snapshot

NB = len(AGE_BUCKETS)
SCANNED = (SnapshotStatus.READY, SnapshotStatus.PRUNED)  # snapshots with folder history
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


# Which scan a request is about: the latest (None), a snapshot by ID, or a day, meaning each filesystem's newest
# scan taken by the end of that day.
Scan = int | date | None


def _snapshot_for(session: Session, fs: Filesystem, scan: Scan) -> Snapshot | None:
    if isinstance(scan, date):
        snap = session.scalar(select(Snapshot).where(Snapshot.filesystem_id == fs.id, Snapshot.status.in_(SCANNED),
                                                     Snapshot.started_at < datetime.combine(scan, time.min) + timedelta(days=1))
                              .order_by(Snapshot.started_at.desc()).limit(1))
        if snap is not None and snap.status == SnapshotStatus.PRUNED:
            raise HTTPException(410, f"The scan of {fs.name} on {snap.started_at:%Y-%m-%d} is no longer kept in full: "
                                     "only its folder totals remain, in History")
        if snap is None:
            raise not_found(f"{fs.name} had not been scanned by {scan:%Y-%m-%d}")
        return snap
    if scan:
        snap = session.get(Snapshot, scan)
        if snap is None or snap.filesystem_id != fs.id or snap.status != SnapshotStatus.READY:
            raise not_found("That snapshot is not available")
        return snap
    return latest_snapshot(session, fs)


def home_places(session: Session, ident: Identity, lab_slug: str | None = None, scan: Scan = None) -> list[Place]:
    """A person's home: each filesystem root (administrators), or their labs' folders, as of `scan` (a day; a
    filesystem with no full scan by then is left out)."""
    places: list[Place] = []
    owners = folder_owners(session) if ident.is_admin else ident.folders
    for fs in _filesystems(session):
        try:
            snap = _snapshot_for(session, fs, scan if isinstance(scan, date) else None)
        except HTTPException:
            continue
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
            scan: Scan = None, lab_slug: str | None = None) -> list[Place]:
    """The places a request covers, after checking the person may see them."""
    if not fs_slug:
        return home_places(session, ident, lab_slug, scan)
    fs = session.scalar(select(Filesystem).where(Filesystem.slug == fs_slug))
    if fs is None:
        raise not_found("No such filesystem")
    snap = _snapshot_for(session, fs, scan)
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
         depth: int = 1, scan: Scan = None, lab_slug: str | None = None) -> dict:
    if metric not in METRICS:
        raise HTTPException(422, f"Unknown size metric {metric!r}")
    places = resolve(session, ident, fs_slug, path, scan, lab_slug)
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


# ---------- The change map ----------

def _earlier(session: Session, snap: Snapshot) -> list[Snapshot]:
    """Snapshots of the same filesystem taken before `snap` whose index is kept, newest first."""
    rows = session.scalars(select(Snapshot).where(Snapshot.filesystem_id == snap.filesystem_id,
                                                  Snapshot.status == SnapshotStatus.READY,
                                                  Snapshot.started_at < snap.started_at)
                           .order_by(Snapshot.started_at.desc()))
    return [r for r in rows if r.index_dir and (Path(r.index_dir) / "index.duckdb").exists()]


def _comparison(session: Session, place: Place, since: date | None) -> tuple[Snapshot, str | None] | None:
    """The scan a place is compared with, and how: None when its index's own `changes` hold the comparison (the
    previous scan), else the alias of the attached index. By default the previous scan; with `since`, the newest
    one taken on or before that day, or the oldest kept if none was."""
    earlier = _earlier(session, place.snap)
    if not earlier:
        return None
    other = earlier[0] if since is None else next((s for s in earlier if s.started_at.date() <= since), earlier[-1])
    prev = place.idx.previous
    if prev and prev.get("scan_id") == other.scan_id:
        return other, None
    return other, place.idx.attach(Path(other.index_dir))


def _ref_day(snap: Snapshot) -> float:
    return (snap.started_at - datetime(1970, 1, 1)).total_seconds() / DAY


def _side(r: dict, s: str, ref_day: float) -> dict | None:
    """One scan's totals from a row of both (s is "n" for this scan, "o" for the earlier one)."""
    b, f = r.get(f"{s}_bytes"), r.get(f"{s}_files")
    if b is None and f is None:
        return None
    b, w = int(b or 0), r.get(f"{s}_m_wsum")
    return {"bytes": b, "alloc": int(r.get(f"{s}_alloc") or 0), "files": int(f or 0), "dirs": int(r.get(f"{s}_dirs") or 0),
            "mean_age_days": round(ref_day - w / b, 2) if b and w is not None else None}


def _minus(a: dict, b: dict) -> dict:
    """Totals in `a` and not in `b`, per scan (a folder's own files: the folder minus all its subfolders)."""
    out = dict(a)
    for k, v in b.items():
        if k[:2] in ("o_", "n_") and a.get(k) is not None:
            out[k] = a[k] - (v or 0)
    return out


def _change_tiles(fs: Filesystem, res: dict, refs: tuple[float, float], owners: dict | None) -> list[dict]:
    def tile(r: dict, kind: str = "dir", **extra) -> dict:
        return {"kind": kind, "fs": fs.slug, "path": r.get("path"), "name": r.get("name"), "now": _side(r, "n", refs[0]),
                "then": _side(r, "o", refs[1]), **extra}

    tiles = []
    for r in res["rows"]:
        lab = owners.get((fs.id, r["name"])) if owners is not None and r["depth"] == 1 else None
        tiles.append(tile(r, in_new=r["in_new"], in_old=r["in_old"], lab=lab_brief(lab)))
    rest = res["rest"]
    if rest.get("n"):
        n = int(rest["n"])
        tiles.append(tile({**rest, "path": None, "name": f"{n:,} smaller folder{'s' if n != 1 else ''}"}, "other",
                          count=n, new=int(rest["new"]), removed=int(rest["removed"])))
    node = res["node"]
    if node is not None:
        own = _minus(node, res["all"])
        if (own.get("n_files") or 0) + (own.get("o_files") or 0) > 0:
            tiles.append(tile({**own, "name": "Files in this folder"}, "files",
                              count=max(int(own.get("n_files") or 0), int(own.get("o_files") or 0))))
    return tiles


def change_tree(session: Session, ident: Identity, fs_slug: str | None, path: str | None, *, since: date | None = None,
                metric: str = "max", depth: int = 1, scan: Scan = None, lab_slug: str | None = None) -> dict:
    """The change map: a place and its children (and grandchildren with depth=2) in this scan and an earlier one,
    with folders in only one of them, sized by `metric` (the larger of the two, now, or then)."""
    if metric not in CHANGE_METRICS:
        raise HTTPException(422, f"Unknown size {metric!r}")
    places = resolve(session, ident, fs_slug, path, scan, lab_slug)
    owners = folder_owners(session) if ident.is_admin else None
    compared, tiles, total = {}, [], {}
    choices = sorted({s.started_at.date() for p in places for s in _earlier(session, p.snap)}, reverse=True)
    for p in places:
        if p.fs.id not in compared:
            compared[p.fs.id] = _comparison(session, p, since)
        if compared[p.fs.id] is None:
            continue
        other, against = compared[p.fs.id]
        refs = (p.idx.ref_day, _ref_day(other))
        res = p.idx.change_children(p.path, against=against, metric=metric, limit=TILE_LIMIT if fs_slug else GRAND_LIMIT,
                                    grand_limit=GRAND_LIMIT if fs_slug and depth > 1 else 0)
        if res["node"] is None:
            if fs_slug:
                raise not_found("That folder is in neither scan, or it is not shared with you")
            continue
        at_root = p.path == p.idx.root
        if fs_slug:
            tiles = _change_tiles(p.fs, res, refs, owners if at_root else None)
            for t in tiles:
                g = res["grand"].get(t["path"]) if t["kind"] == "dir" else None
                if g:
                    t["children"] = _change_tiles(p.fs, {**g, "node": None}, refs, None)
            total = res["node"]
            kids = res["all"]
        else:
            n = res["node"]
            t = {"kind": "fs" if at_root else "dir", "fs": p.fs.slug, "path": p.path,
                 "name": p.fs.name if at_root else n["name"], "lab": lab_brief(p.lab), "fs_name": p.fs.name,
                 "now": _side(n, "n", refs[0]), "then": _side(n, "o", refs[1]), "in_new": n["in_new"], "in_old": n["in_old"]}
            if depth > 1:
                t["children"] = _change_tiles(p.fs, res, refs, owners if at_root else None)
            tiles.append(t)
            total = add_pairs(total, n) if total else add_pairs(n)
            kids = None
    if not fs_slug:
        lab = session.scalar(select(Lab).where(Lab.slug == lab_slug)) if lab_slug else None
        name = lab.name if lab else ("All data" if ident.is_admin else "My data")
        node = {"kind": "home", "fs": None, "path": None, "name": name, "lab": lab_brief(lab)}
        crumbs = [{"label": name, "fs": None, "path": None}]
    else:
        p = places[0]
        node = {"kind": "root" if p.path == p.idx.root else "folder" if p.idx.depth_of(p.path) == 1 else "dir",
                "fs": p.fs.slug, "fs_name": p.fs.name, "path": p.path, "lab": lab_brief(p.lab),
                "name": p.fs.name if p.path == p.idx.root else (total or {}).get("name") or p.path.rsplit("/", 1)[-1]}
        if total:
            node.update(in_new=total["in_new"], in_old=total["in_old"],
                        subfolders={k: int(kids.get(k) or 0) for k in ("n", "new", "removed")})
        crumbs = _breadcrumbs(ident, p)
    first = next(iter(compared.values()), None)
    refs = (places[0].idx.ref_day, _ref_day(first[0])) if first else (0, 0)
    node.update(now=_side(total, "n", refs[0]) if total else None, then=_side(total, "o", refs[1]) if total else None)
    return {"node": node, "tiles": tiles, "breadcrumbs": crumbs, "metric": metric, "since": since.isoformat() if since else None,
            "as_of": iso(max((p.snap.started_at for p in places), default=None)),
            "compared": [{"fs": session.get(Filesystem, fs_id).slug, "snapshot_id": c[0].id, "started_at": iso(c[0].started_at),
                          "previous": c[1] is None} for fs_id, c in compared.items() if c],
            "choices": [d.isoformat() for d in choices]}


# ---------- What changed ----------

CHANGE_LISTS = ("grew", "shrank", "new_folders", "removed_folders", "new_files", "removed_files")


def _unit_json(fs: Filesystem, r: dict, refs: tuple[float, float]) -> dict:
    return {"fs": fs.slug, "path": r["path"], "name": r["name"], "depth": r["depth"], "in_new": r["in_new"],
            "in_old": r["in_old"], "now": _side(r, "n", refs[0]), "then": _side(r, "o", refs[1]), "delta": int(r["delta"])}


def what_changed(session: Session, ident: Identity, fs_slug: str | None, path: str | None, *, since: date | None = None,
                 limit: int = 20, scan: Scan = None, lab_slug: str | None = None) -> dict:
    """What changed in a place between this scan and an earlier one (as for the change map): the folders that
    grew and shrank most, the new and removed folders, and the largest new and removed files."""
    places = resolve(session, ident, fs_slug, path, scan, lab_slug)
    choices = sorted({s.started_at.date() for p in places for s in _earlier(session, p.snap)}, reverse=True)
    compared: dict[int, tuple[Snapshot, str | None] | None] = {}
    lists: dict[str, list] = {k: [] for k in CHANGE_LISTS}
    files = {k: {"files": 0, "bytes": 0} for k in ("new_files", "removed_files")}
    counts: dict[str, int] = defaultdict(int)
    total: dict = {}
    for p in places:
        if p.fs.id not in compared:
            compared[p.fs.id] = _comparison(session, p, since)
        if compared[p.fs.id] is None:
            continue
        other, against = compared[p.fs.id]
        node = p.idx.change_node(p.path, against=against)
        if node is None:
            if fs_slug:
                raise not_found("That folder is in neither scan, or it is not shared with you")
            continue
        refs = (p.idx.ref_day, _ref_day(other))
        total = add_pairs(*([total] if total else []), node) | {k: node[k] for k in ("in_new", "in_old")}
        units = p.idx.change_units(p.path, against=against, limit=limit)
        for key, name in (("grew", "grew"), ("shrank", "shrank"), ("new", "new_folders"), ("removed", "removed_folders")):
            lists[name] += [_unit_json(p.fs, r, refs) for r in units[key]]
        for k, v in units["counts"].items():
            counts[k] += v
        theirs = indexes.get(other)
        names = {**theirs.user_names(), **p.idx.user_names()}
        for kind in ("new", "removed"):
            res = p.idx.file_changes(p.path, theirs, kind=kind, limit=limit)
            lists[f"{kind}_files"] += [_file_json(p.fs.slug, r) | {"owner": names.get(r["uid"])} for r in res["rows"]]
            files[f"{kind}_files"]["files"] += res["files"]
            files[f"{kind}_files"]["bytes"] += res["bytes"]
    order = {"grew": lambda r: -r["delta"], "shrank": lambda r: r["delta"],
             "new_folders": lambda r: -(r["now"] or {}).get("bytes", 0),
             "removed_folders": lambda r: -(r["then"] or {}).get("bytes", 0),
             "new_files": lambda r: -r["size"], "removed_files": lambda r: -r["size"]}
    for k, v in lists.items():
        lists[k] = sorted(v, key=order[k])[:limit]
    first = next((c for c in compared.values() if c), None)
    refs = (places[0].idx.ref_day, _ref_day(first[0])) if first else (0, 0)
    if fs_slug:
        p = places[0]
        node = {"fs": p.fs.slug, "path": p.path, "lab": lab_brief(p.lab),
                "name": p.fs.name if p.path == p.idx.root else p.path.rsplit("/", 1)[-1]}
    else:
        lab = session.scalar(select(Lab).where(Lab.slug == lab_slug)) if lab_slug else None
        node = {"fs": None, "path": None, "lab": lab_brief(lab), "name": lab.name if lab else ("All data" if ident.is_admin else "My data")}
    node.update(now=_side(total, "n", refs[0]) if total else None, then=_side(total, "o", refs[1]) if total else None,
                in_new=total.get("in_new", True), in_old=total.get("in_old", True))
    return {"node": node, **lists, "files": files, "counts": dict(counts), "limit": limit,
            "since": since.isoformat() if since else None, "as_of": iso(max((p.snap.started_at for p in places), default=None)),
            "compared": [{"fs": session.get(Filesystem, fs_id).slug, "snapshot_id": c[0].id, "started_at": iso(c[0].started_at),
                          "previous": c[1] is None} for fs_id, c in compared.items() if c],
            "choices": [d.isoformat() for d in choices]}


# ---------- Reports ----------

def types(session: Session, ident: Identity, fs_slug, path, scan=None, lab_slug=None) -> dict:
    places = resolve(session, ident, fs_slug, path, scan, lab_slug)
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


def owners(session: Session, ident: Identity, fs_slug, path, scan=None, lab_slug=None) -> dict:
    places = resolve(session, ident, fs_slug, path, scan, lab_slug)
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
          limit: int = 100, scan=None, lab_slug=None, with_summary: bool = True) -> dict:
    if sort not in FILE_SORTS:
        raise HTTPException(422, "Unknown sort")
    places = resolve(session, ident, fs_slug, path, scan, lab_slug)
    limit = max(1, min(limit, get_settings().export_max_rows))
    streams = []
    for p in places:
        where, args = flt.sql(p.idx.ref_epoch)
        rows = p.idx.files(p.path, where=where, params=args, order=FILE_SORTS[sort], limit=limit,
                           direct_only=flt.direct)
        streams.append([_file_json(p.fs.slug, r) | {"_r": r} for r in rows])
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
        out["total"] = _count(places, flt) | {"shown": len(merged)}
    return out


def _count(places: list[Place], flt: FileFilter) -> dict:
    files = size = 0
    for p in places:
        where, args = flt.sql(p.idx.ref_epoch)
        if flt.direct:
            where, args = (where + " AND " if where else "") + "depth = ?", [*args, p.idx.depth_of(p.path) + 1]
        s = p.idx.files_summary(p.path, where=where, params=args)
        files += s["files"]
        size += s["bytes"]
    return {"files": files, "bytes": size}


def files_count(session: Session, ident: Identity, fs_slug, path, *, flt: FileFilter, scan=None, lab_slug=None) -> dict:
    """How many files match `flt`, and their total size: a second pass over what `files` searched."""
    places = resolve(session, ident, fs_slug, path, scan, lab_slug)
    return _count(places, flt) | _scope_meta(places)


def cold(session: Session, ident: Identity, fs_slug, path, *, older: int, min_bytes: int = 0, limit: int = 200,
         scan=None, lab_slug=None) -> dict:
    if not 1 <= older < NB:
        raise HTTPException(422, "Choose an age threshold")
    places = resolve(session, ident, fs_slug, path, scan, lab_slug)
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


# ---------- Scans to choose from ----------

def scan_days(session: Session, ident: Identity, fs_slug: str | None, lab_slug: str | None = None) -> dict:
    """The days a place was scanned, newest first, for the snapshot picker: `ready` when some scan of that day is
    kept in full (every report), else only its folder totals remain (History). For a place on several filesystems,
    a day is any day one of them was scanned; each counts as of its newest scan by then."""
    if fs_slug:
        fs = session.scalar(select(Filesystem).where(Filesystem.slug == fs_slug))
        if fs is None or not (ident.is_admin or any(f == fs.id for f, _ in ident.folders)):
            raise not_found("No such filesystem")
        fs_ids = {fs.id}
    elif lab_slug:
        lab = session.scalar(select(Lab).where(Lab.slug == lab_slug))
        fs_ids = {f.filesystem_id for f in (lab.folders if lab else []) if ident.can_view_folder(f.filesystem_id, f.path)}
    elif ident.is_admin:
        fs_ids = {fs.id for fs in _filesystems(session)}
    else:
        fs_ids = {f for f, _ in ident.folders}
    days: dict[date, bool] = {}
    for snap in session.scalars(select(Snapshot).where(Snapshot.filesystem_id.in_(fs_ids), Snapshot.status.in_(SCANNED))):
        d = snap.started_at.date()
        days[d] = days.get(d, False) or snap.status == SnapshotStatus.READY
    order = sorted(days, reverse=True)
    return {"days": [{"day": d.isoformat(), "ready": days[d], "latest": i == 0} for i, d in enumerate(order)]}


# ---------- History ----------

def _point(taken_at: datetime, snaps: list[int], parts: list[tuple[dict, float]], cold_from: int) -> dict:
    """One point of a history: the sum of `parts`, each (totals, days from its scan to `taken_at`), with mean
    ages carried forward to `taken_at`."""
    age = [0] * NB
    total = {"bytes": 0, "alloc_bytes": 0, "files": 0}
    wsum = 0.0
    for t, shift in parts:
        for k in total:
            total[k] += t[k]
        age = [a + b for a, b in zip(age, t["age_bytes"], strict=True)]
        if t["mean_age_days"] is not None:
            wsum += t["bytes"] * (t["mean_age_days"] + shift)
    present = bool(parts)
    return {"taken_at": iso(taken_at), "snapshots": snaps, "present": present, **total,
            "cold_bytes": sum(age[cold_from:]), "age_bytes": age,
            "mean_age_days": round(wsum / total["bytes"], 2) if total["bytes"] else None}


def _scans(session: Session, members: dict[int, set[str] | None]) -> dict[int, list[tuple[Snapshot, dict | None]]]:
    """Per filesystem, its scans oldest first, each with the totals of the member folders (None: the whole
    filesystem); a scan without them (the folder did not exist yet, or no longer) has None. A whole filesystem's
    totals are recorded with each scan; folders, and scans ingested before Pinax recorded them, are summed from
    folder history by the catalog."""
    sums = [func.sum(FolderStat.bytes), func.sum(FolderStat.alloc_bytes), func.sum(FolderStat.files),
            func.sum(FolderStat.bytes * FolderStat.mean_age_days),
            *[func.sum(cast(FolderStat.age_bytes[i].as_string(), BigInteger)) for i in range(NB)]]
    out = {}
    for fs_id, names in members.items():
        snaps = session.scalars(select(Snapshot).where(Snapshot.filesystem_id == fs_id, Snapshot.status.in_(SCANNED))
                                .order_by(Snapshot.started_at)).all()
        totals = {snap.id: snap.info["totals"] for snap in snaps if names is None and "totals" in (snap.info or {})}
        q = select(FolderStat.snapshot_id, *sums).where(FolderStat.filesystem_id == fs_id).group_by(FolderStat.snapshot_id)
        if names is not None:
            q = q.where(FolderStat.folder.in_(sorted(names)))
        else:
            q = q.where(FolderStat.snapshot_id.in_([snap.id for snap in snaps if snap.id not in totals]))
        for sid, b, alloc, files, wsum, *age in (session.execute(q) if names is not None or len(totals) < len(snaps) else []):
            b = int(b or 0)
            totals[sid] = {"bytes": b, "alloc_bytes": int(alloc or 0), "files": int(files or 0),
                           "age_bytes": [int(a or 0) for a in age], "mean_age_days": (wsum or 0) / b if b else None}
        out[fs_id] = [(snap, totals.get(snap.id)) for snap in snaps]
    return out


def _series(session: Session, members: dict[int, set[str] | None], cold_from: int) -> list[dict]:
    """Points over every scan. With one filesystem, a point per scan. With several, whose scans are taken
    independently, a point per day with a scan, each filesystem as of its latest scan by the end of that day."""
    scans = _scans(session, members)
    if len(scans) == 1:
        return [_point(snap.started_at, [snap.id], [(t, 0.0)] if t else [], cold_from) for snap, t in
                next(iter(scans.values()))]
    days = sorted({snap.started_at.date() for series in scans.values() for snap, _ in series})
    points = []
    for day in days:
        latest = [next((x for x in reversed(series) if x[0].started_at.date() <= day), None) for series in scans.values()]
        latest = [x for x in latest if x]
        at = max(snap.started_at for snap, _ in latest)
        parts = [(t, (at - snap.started_at).total_seconds() / DAY) for snap, t in latest if t]
        points.append(_point(at, [snap.id for snap, _ in latest], parts, cold_from))
    return points


def _retained(session: Session, place: Place, cold_from: int) -> tuple[list[dict], int]:
    """A folder below the top level, in each snapshot whose index is kept; and how many older scans are not."""
    snaps = session.scalars(select(Snapshot).where(Snapshot.filesystem_id == place.fs.id,
                                                   Snapshot.status.in_(SCANNED)).order_by(Snapshot.started_at)).all()
    points, older = [], 0
    for snap in snaps:
        if snap.status != SnapshotStatus.READY:
            older += 1
            continue
        idx = indexes.get(snap)
        row = idx.node(place.path)
        if row is None:
            points.append(_point(snap.started_at, [snap.id], [], cold_from))
            continue
        j = Stats.from_row(row).json(idx.ref_day)
        t = {"bytes": j["bytes"], "alloc_bytes": j["alloc"], "files": j["files"], "age_bytes": j["age_bytes"],
             "mean_age_days": j["mean_age_days"]}
        points.append(_point(snap.started_at, [snap.id], [(t, 0.0)], cold_from))
    return points, older


def history(session: Session, ident: Identity, fs_slug: str | None, path: str | None, scan: Scan = None,
            lab_slug: str | None = None) -> dict:
    """A place's size, files, age profile, cold share, and average age over its scans, oldest first: every scan
    for the person's home, a lab or department, a filesystem, or a top-level folder (from folder history); the
    retained snapshots for a deeper folder."""
    cold_from = cold_bucket()
    lab = session.scalar(select(Lab).where(Lab.slug == lab_slug)) if lab_slug and not fs_slug else None
    older = 0
    if fs_slug:
        try:
            place = resolve(session, ident, fs_slug, path, scan)[0]
        except HTTPException as e:  # a pruned scan: its folder totals are what history shows
            if e.status_code != 410:
                raise
            place = resolve(session, ident, fs_slug, path)[0]
        idx, lab = place.idx, place.lab
        node = idx.node(place.path)
        if node is None:
            raise not_found()
        depth = idx.depth_of(place.path)
        if depth == 0:
            kind, name, members = "filesystem", place.fs.name, {place.fs.id: None}
        elif depth == 1:
            kind, name, members = "folder", node["name"], {place.fs.id: {idx.top_of(place.path)}}
        else:
            kind, name, members = "subfolder", node["name"], None
    elif lab_slug:
        if lab is None:
            raise not_found("No such lab or department")
        members = defaultdict(set)
        for f in session.scalars(select(LabFolder).where(LabFolder.lab_id == lab.id)):
            if ident.can_view_folder(f.filesystem_id, f.path):
                members[f.filesystem_id].add(f.path)
        if not members:
            raise not_found("That lab or department is not shared with you")
        kind, name = "lab", lab.name
    elif ident.is_admin:
        kind, name, members = "home", "All data", {fs.id: None for fs in _filesystems(session)}
    else:
        members = defaultdict(set)
        for fs_id, folder in ident.folders:
            members[fs_id].add(folder)
        kind, name = "home", "My data"
    if members is None:
        points, older = _retained(session, place, cold_from)
    else:
        points = _series(session, dict(members), cold_from) if members else []
    return {"kind": kind, "name": name, "lab": lab_brief(lab), "points": points, "older_scans": older,
            "keep_snapshots": get_settings().keep_snapshots, "merged": members is not None and len(members) > 1,
            "cold_bucket": cold_from, "as_of": points[-1]["taken_at"] if points else None}


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
