# SPDX-License-Identifier: Apache-2.0
"""Read-only queries over one snapshot index (see ingest/build.py for its layout).

Every query is scoped by path ranges that the index's sort order turns into contiguous reads:
a directory's children are `depth = d + 1 AND path in [prefix, upper)` in `dirs`, and a subtree's
files are `path in [prefix, upper)` in the top-level folder's own Parquet files. All values from
callers are bound as parameters; the only interpolated SQL fragments are built here from fixed lists.
"""

import hashlib
import json
import threading
from collections.abc import Iterable
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from . import limits
from .categories import AGE_BUCKETS, Category, from_dimensions
from .ingest.build import BASE_ADDITIVE, EXTREMES, NB, category_columns, file_folders

# A folder's extra hard links and their bytes (index version 7) are read on their own (`links`), so the queries
# below stay the same for indexes without them. Each category's file count and size (cf{i}, cb{i}) come back named
# by the category (cf:genomics, cb:genomics), since indexes may be built with different categories
# (`SnapshotIndex.categories`): totals from several add up by category, and the API names them so.
QUERY_ADDITIVE = list(BASE_ADDITIVE)
CATEGORY_PREFIXES = ("cf:", "cb:")

DAY = 86400.0
# Comparing two scans' files, groups of top-level folders are compared together up to this many files, and a larger
# group alone: few queries for a small filesystem, and memory bounded by a group's files for a large one.
COMPARE_FILES = 10_000_000

# Columns selected for every directory row. Times come back as epoch seconds (DOUBLE) so no
# timezone conversion happens in the database driver.


def dir_columns(categories: dict[str, str], alias: str = "") -> str:
    """Every directory column, each category's named by the category (`categories`: column -> name)."""
    a = f"{alias}." if alias else ""
    return (
        f"{a}dir_id, {a}parent_id, {a}depth, {a}top_id, {a}path, {a}name, {a}error, {a}is_mount_point, "
        f"{a}dir_entries, {a}d_files, {a}d_bytes, epoch({a}d_mtime_min) AS d_mtime_min, "
        f"epoch({a}d_mtime_max) AS d_mtime_max, epoch({a}d_atime_max) AS d_atime_max, {a}dirs, {a}incomplete, "
        + ", ".join([f"{a}{c}" for c in QUERY_ADDITIVE] + [f'{a}{c} AS "{n}"' for c, n in categories.items()]) + ", "
        + ", ".join(f"epoch({a}{c}) AS {c}" for c in EXTREMES)
    )


HEAD_KEYS = ["dir_id", "parent_id", "depth", "top_id", "path", "name", "error", "is_mount_point", "dir_entries",
             "d_files", "d_bytes", "d_mtime_min", "d_mtime_max", "d_atime_max"]
SUM_KEYS = ["dirs", "incomplete"] + QUERY_ADDITIVE  # and each category's, named

# A file's columns; its category comes back as the category's key, from the index's own list (`file_columns`).
FILE_COLUMNS = ("path, name, ext, {category} AS category, kind, size, alloc_bytes, uid, gid, epoch(mtime) AS mtime, "
                "epoch(atime) AS atime, nlink, name_flags")
FILE_KEYS = ["path", "name", "ext", "category", "kind", "size", "alloc_bytes", "uid", "gid", "mtime", "atime", "nlink",
             "name_flags"]

# Sizing metrics the treemap can use. Each maps to a SQL expression over `dirs` columns.
METRICS: dict[str, str] = {"bytes": "bytes", "alloc": "alloc", "files": "files"}  # and cat:<key> (`SnapshotIndex.metric`)
METRICS.update({f"older:{i}": "(" + " + ".join(f"mb{j}" for j in range(i, NB)) + ")" for i in range(1, NB)})


# The change between two scans: what each side carries (as o_* and n_*), how tiles can be sized, and the sums
# over a set of directories (n: how many; new and removed: how many are in one scan only).
CHANGE_KEYS = ["files", "dirs", "bytes", "alloc", "m_wsum"]
CHANGE_COLS = ", ".join(f"o_{c}, n_{c}" for c in CHANGE_KEYS)
CHANGE_METRICS = {"max": "greatest(coalesce(n_bytes, 0), coalesce(o_bytes, 0))", "now": "coalesce(n_bytes, 0)",
                  "then": "coalesce(o_bytes, 0)"}
PAIR_SUMS = ", ".join(["count(*) AS n", "count(*) FILTER (WHERE in_new AND NOT in_old) AS new",
                       "count(*) FILTER (WHERE in_old AND NOT in_new) AS removed"]
                      + [f"sum({s}_{c}) AS {s}_{c}" for c in CHANGE_KEYS for s in ("o", "n")])


def no_pairs() -> dict:
    return {"n": 0, "new": 0, "removed": 0, **{f"{s}_{c}": None for c in CHANGE_KEYS for s in ("o", "n")}}


def add_pairs(*parts: dict) -> dict:
    out = no_pairs()
    for p in parts:
        for k in out:
            if p.get(k) is not None:
                out[k] = (out[k] or 0) + p[k]
    return out


def child_range(path: str) -> tuple[str, str]:
    """[lower, upper) bounds of every path strictly below `path`, in byte order."""
    prefix = path if path.endswith("/") else path + "/"
    return prefix, prefix[:-1] + "0"  # "0" sorts right after "/"


def escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def pattern_to_like(q: str) -> str:
    """`*.bam` and `sample_??` are globs; anything else matches as a substring."""
    if "*" in q or "?" in q:
        return escape_like(q).replace("*", "%").replace("?", "_")
    return f"%{escape_like(q)}%"


# ---------- Stats: one directory's (or a sum of directories') totals ----------

@dataclass
class Stats:
    """Additive totals plus extremes, in index units (bytes, counts, epoch-day sums, epoch seconds)."""

    values: dict[str, Any]

    @classmethod
    def from_row(cls, row: dict) -> "Stats":
        return cls({**{k: row.get(k) for k in SUM_KEYS + list(EXTREMES)},
                    **{k: v for k, v in row.items() if k[:3] in CATEGORY_PREFIXES}})

    @staticmethod
    def _categories(values: dict) -> list[str]:
        return [k for k in values if k[:3] in CATEGORY_PREFIXES]

    @classmethod
    def zero(cls) -> "Stats":
        return cls({**{k: 0 for k in SUM_KEYS}, **{k: None for k in EXTREMES}})

    def add(self, other: "Stats", count_as_dir: bool = False) -> "Stats":
        v = dict(self.values)
        for k in SUM_KEYS + self._categories(other.values):  # a category only `self` has stays as it is
            v[k] = (v.get(k) or 0) + (other.values.get(k) or 0)
        if count_as_dir:
            v["dirs"] += 1
        for k, fn in EXTREMES.items():
            a, b = v.get(k), other.values.get(k)
            v[k] = b if a is None else a if b is None else (min(a, b) if fn == "min" else max(a, b))
        return Stats(v)

    def sub(self, other: "Stats") -> "Stats":
        """Additive difference (extremes are left as self's; callers fill them when known)."""
        v = dict(self.values)
        for k in SUM_KEYS + self._categories(other.values):
            v[k] = max(0, (v.get(k) or 0) - (other.values.get(k) or 0))
        return Stats(v)

    def json(self, ref_day: float) -> dict:
        v = self.values
        b, f = int(v.get("bytes") or 0), int(v.get("files") or 0)
        mean = None
        if b > 0 and v.get("m_wsum"):
            mean = ref_day - v["m_wsum"] / b
        elif v.get("m_n"):
            mean = ref_day - v["m_fsum"] / v["m_n"]
        amean = ref_day - v["a_wsum"] / b if b > 0 and v.get("a_wsum") else None
        return {
            "files": f, "dirs": int(v.get("dirs") or 0), "bytes": b, "alloc": int(v.get("alloc") or 0),
            "incomplete": int(v.get("incomplete") or 0),
            "mean_age_days": round(mean, 2) if mean is not None else None,
            "mean_atime_age_days": round(amean, 2) if amean is not None else None,
            "oldest": iso_from_epoch(v.get("mtime_min")), "newest": iso_from_epoch(v.get("mtime_max")),
            "last_access": iso_from_epoch(v.get("atime_max")), "last_change": iso_from_epoch(v.get("ctime_max")),
            "age_bytes": [int(v.get(f"mb{i}") or 0) for i in range(NB)],
            "age_files": [int(v.get(f"mf{i}") or 0) for i in range(NB)],
            "atime_bytes": [int(v.get(f"ab{i}") or 0) for i in range(NB)],
            "cat_bytes": {k[3:]: int(n) for k, n in v.items() if k.startswith("cb:") and n},
            "cat_files": {k[3:]: int(n) for k, n in v.items() if k.startswith("cf:") and n},
        }


def iso_from_epoch(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


def sum_stats(items: Iterable[Stats]) -> Stats:
    total = Stats.zero()
    for s in items:
        total = total.add(s)
    return total


# ---------- The index ----------

class SnapshotIndex:
    """One snapshot's index, opened read-only. Thread-safe: each query uses its own cursor."""

    def __init__(self, index_dir: Path, *, memory_limit: str = "4GB", threads: int = 0):
        self.index_dir = Path(index_dir)
        config = {"memory_limit": memory_limit, "access_mode": "READ_ONLY"}
        if threads:
            config["threads"] = threads
        self._con = duckdb.connect(str(self.index_dir / "index.duckdb"), read_only=True, config=config)
        self._lock = threading.Lock()
        meta = {k: json.loads(v) for k, v in self._con.execute("SELECT key, value FROM meta").fetchall()}
        self.meta = meta
        self.root: str = meta["root"]
        self.prefix = self.root if self.root.endswith("/") else self.root + "/"
        self.ref_day: float = meta["ref_day"]
        self.ref_epoch = self.ref_day * DAY
        # The categories it was built with, and its directory columns, each category's named by the category.
        self.categories: tuple[Category, ...] = from_dimensions(meta.get("dimensions"))
        self.category_keys = [c.key for c in self.categories]
        self.categories_id: str | None = (meta.get("dimensions") or {}).get("categories_id")
        cols = category_columns(len(self.categories))
        self._cat_names = {c: f"{c[:2]}:{self.category_keys[int(c[2:])]}" for c in cols}
        self.dir_sql = dir_columns(self._cat_names)
        self.row_keys = HEAD_KEYS + SUM_KEYS + list(self._cat_names.values()) + list(EXTREMES)
        self.sum_cols = SUM_KEYS + cols  # for sum(...), in SQL
        self.sum_keys = SUM_KEYS + list(self._cat_names.values())  # what each sum is called
        keys = ", ".join("'" + k.replace("'", "''") + "'" for k in self.category_keys)
        self.file_sql = FILE_COLUMNS.format(category=f"[{keys}][cat + 1]")
        tops = self._con.execute("SELECT top_id, name, path FROM tops ORDER BY top_id").fetchall()
        self.top_ids = {name: tid for tid, name, _ in tops}
        self.top_names = {tid: name for tid, name, _ in tops}
        self.parts = file_folders(self._con, "file_parts")
        self.top_files = dict(self._con.execute("SELECT top_id, files FROM file_parts WHERE files > 0").fetchall())
        # Indexes before version 3 have no precomputed cold folders; they are found at query time instead.
        tables = {r[0] for r in self._con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
        self.has_cold_units = "cold_units" in tables
        # The change since the filesystem's previous scan (index version 4, when there was one): what it was.
        self.has_changes = "changes" in tables
        self.previous: dict | None = meta.get("previous") if self.has_changes else None
        # And the files new and removed since then (index version 5).
        self.has_file_changes = "file_changes" in tables
        # Each folder's extra hard links (index version 7), counted once in its totals.
        self.has_links = bool(self._con.execute(
            "SELECT count(*) FROM duckdb_columns() WHERE table_name = 'dirs' AND column_name = 'hl_files'").fetchone()[0])
        # Workflow working folders (index version 6), suggested for cleanup plans.
        self.has_work_dirs = "work_dirs" in tables
        # Each directory's device and inode (index version 6), to follow a marked folder that moved (marks.py).
        self.has_inodes = bool(self._con.execute(
            "SELECT count(*) FROM duckdb_columns() WHERE table_name = 'dirs' AND column_name = 'ino'").fetchone()[0])

    def close(self) -> None:
        self._con.close()

    def set_memory_limit(self, limit: str) -> None:
        """What this index's queries use at once and the blocks it keeps cached; lowering it lets cached blocks go."""
        with self._lock:
            self._con.execute(f"SET memory_limit = '{limit}'")

    def _cursor(self) -> duckdb.DuckDBPyConnection:
        with self._lock:
            return self._con.cursor()

    # Every query runs on its own cursor, within the time limit of the request it serves (limits.py).
    def _rows(self, sql: str, params: list | None = None, keys: list[str] | None = None) -> list[dict]:
        cur = self._cursor()
        try:
            with limits.guard(cur):
                res = cur.execute(sql, params or [])
                names = keys or [d[0] for d in res.description]
                return [dict(zip(names, r)) for r in res.fetchall()]
        finally:
            cur.close()

    def _one(self, sql: str, params: list | None = None) -> tuple | None:
        cur = self._cursor()
        try:
            with limits.guard(cur):
                return cur.execute(sql, params or []).fetchone()
        finally:
            cur.close()

    # ---- Paths ----
    def depth_of(self, path: str) -> int:
        if path == self.root:
            return 0
        return path[len(self.prefix):].count("/") + 1

    def contains(self, path: str) -> bool:
        return path == self.root or path.startswith(self.prefix)

    def top_of(self, path: str) -> str | None:
        """Name of the top-level folder holding `path` (None for the root itself)."""
        if path == self.root:
            return None
        return path[len(self.prefix):].split("/", 1)[0]

    def top_path(self, name: str) -> str:
        return self.prefix + name

    def file_sources(self, top_ids: Iterable[int]) -> str | None:
        folders = sorted({self.parts[t] for t in top_ids if t in self.parts})
        globs = [str(self.index_dir / "files" / f / "*.parquet") for f in folders]
        if not globs:
            return None
        return "read_parquet([" + ", ".join("'" + g.replace("'", "''") + "'" for g in globs) + "])"

    def _turn(self, path: str):
        """File-level queries of a whole filesystem take turns (`limits.wide`); narrower ones never wait."""
        return limits.wide() if path == self.root else nullcontext()

    def scope_tops(self, path: str) -> list[int]:
        top = self.top_of(path)
        if top is None:
            return [0, *self.top_names.keys()]
        return [self.top_ids[top]] if top in self.top_ids else []

    # ---- Directories ----
    def category_number(self, key: str) -> int | None:
        """A category's number in this index's `cat` column; None if it was built without it."""
        return self.category_keys.index(key) if key in self.category_keys else None

    def metric(self, metric: str) -> str:
        """A treemap metric as SQL over `dirs`: a category this index was built without measures nothing."""
        if metric.startswith("cat:"):
            i = self.category_number(metric[4:])
            return "CAST(0 AS BIGINT)" if i is None else f"cb{i}"  # not `0`, which ORDER BY reads as a column's place
        return METRICS[metric]

    def node(self, path: str) -> dict | None:
        rows = self._rows(f"SELECT {self.dir_sql} FROM dirs WHERE depth = ? AND path = ?",
                          [self.depth_of(path), path], self.row_keys)
        return rows[0] if rows else None

    def links(self, path: str) -> dict | None:
        """A folder's extra hard links (each a file whose size is counted at its first link), and the bytes they
        would have counted again; None for an index without them."""
        if not self.has_links:
            return None
        row = self._one("SELECT hl_files, hl_bytes FROM dirs WHERE depth = ? AND path = ?", [self.depth_of(path), path])
        return {"files": int(row[0]), "bytes": int(row[1])} if row else None

    def nodes(self, paths: list[str]) -> dict[str, dict]:
        """The directories at `paths` that are in this scan, each with its device and inode (None before index
        version 6)."""
        ident = "dev, ino" if self.has_inodes else "NULL AS dev, NULL AS ino"
        out = {}
        for i in range(0, len(paths), 1000):
            chunk = paths[i:i + 1000]
            marks = ", ".join("?" * len(chunk))
            for row in self._rows(f"SELECT {self.dir_sql}, {ident} FROM dirs WHERE path IN ({marks})", chunk,
                                  [*self.row_keys, "dev", "ino"]):
                out[row["path"]] = row
        return out

    def work_dirs(self, path: str, *, limit: int = 20) -> list[dict]:
        """Workflow working folders and package caches at or below `path`, the largest first, each with its totals
        and kind; one inside another is left out."""
        if not self.has_work_dirs:
            return []
        lo, hi = child_range(path)
        rows = self._rows(f"SELECT {dir_columns(self._cat_names, 'd')}, w.kind FROM work_dirs w JOIN dirs d "
                          "ON d.depth = w.depth AND d.path = w.path WHERE w.path = ? OR (w.path >= ? AND w.path < ?) "
                          "ORDER BY d.path", [path, lo, hi], [*self.row_keys, "kind"])
        top = [r for i, r in enumerate(rows) if not any(r["path"].startswith(o["path"] + "/") for o in rows[:i])]
        return sorted(top, key=lambda r: -r["bytes"])[:limit]

    def compressible(self, path: str, exts: list[str], *, limit: int = 20) -> list[dict]:
        """The folders at or below `path` holding the most files of these types directly, with their bytes by type."""
        lo, hi = child_range(path)
        marks = ", ".join("?" * len(exts))
        rows = self._raw(f"SELECT path, ext, sum(bytes)::BIGINT FROM dir_ext WHERE (path = ? OR (path >= ? AND path < ?)) "
                         f"AND ext IN ({marks}) GROUP BY path, ext", [path, lo, hi, *exts])
        by: dict[str, dict[str, int]] = {}
        for p, ext, b in rows:
            by.setdefault(p, {})[ext] = int(b)
        return [{"path": p, "types": t} for p, t in sorted(by.items(), key=lambda kv: -sum(kv[1].values()))[:limit]]

    def paths_of(self, inodes: list[tuple[int, int]]) -> dict[tuple[int, int], str]:
        """Where the directories with these (device, inode) are in this scan. A full pass over the directories,
        for the few marked folders no longer at their paths."""
        if not inodes or not self.has_inodes:
            return {}
        wanted = sorted({ino for _, ino in inodes})
        marks = ", ".join("?" * len(wanted))
        rows = self._raw(f"SELECT dev, ino, path FROM dirs WHERE ino IN ({marks})", wanted)
        keys = set(inodes)
        return {(dev, ino): path for dev, ino, path in rows if (dev, ino) in keys}

    def children(self, path: str, *, metric: str = "bytes", limit: int = 60, grand_limit: int = 0) -> dict:
        """The children of `path`, largest first by `metric`, the rest folded into one "other" entry.
        With grand_limit, each listed child also gets its own largest children."""
        expr = self.metric(metric)
        depth = self.depth_of(path)
        lo, hi = child_range(path)
        rows = self._rows(
            f"SELECT {self.dir_sql} FROM dirs WHERE depth = ? AND path >= ? AND path < ? "
            f"ORDER BY {expr} DESC, path LIMIT ?", [depth + 1, lo, hi, limit], self.row_keys)
        ids = [r["dir_id"] for r in rows]
        all_sum, rest = self._child_sums(depth + 1, lo, hi, ids)
        out = {"rows": rows, "all": all_sum, "rest": rest}
        if grand_limit and rows:
            out["grand"] = self._grandchildren(path, depth, expr, grand_limit, ids)
        return out

    def measure(self, path: str, metric: str = "bytes") -> float:
        """How much `path` holds by `metric` (0 if it is not in this scan)."""
        row = self._one(f"SELECT {self.metric(metric)} FROM dirs WHERE depth = ? AND path = ?", [self.depth_of(path), path])
        return float(row[0] or 0) if row else 0.0

    def partition(self, path: str, *, metric: str = "bytes", levels: int = 6, least: float = 1) -> list[dict]:
        """Every folder up to `levels` levels below `path` that holds at least `least` by `metric`, level by level and
        the largest first, for the icicle and the sunburst. A folder holds at least as much as any folder inside it,
        so each one's parent is listed too and the rows form a tree, with at most total / `least` folders on a level."""
        expr = self.metric(metric)
        depth = self.depth_of(path)
        lo, hi = child_range(path)
        return self._rows(
            f"SELECT {self.dir_sql} FROM dirs WHERE depth > ? AND depth <= ? AND path >= ? AND path < ? AND {expr} >= ? "
            f"ORDER BY depth, {expr} DESC, path", [depth, depth + levels, lo, hi, max(least, 1)], self.row_keys)

    def _child_sums(self, depth: int, lo: str, hi: str, listed: list[int]) -> tuple[dict, dict]:
        """Totals over all the children, and over those not listed. One aggregate pass split by whether a child
        is listed, rather than ranking every child: a folder can have hundreds of thousands of subfolders."""
        keys = ["n"] + self.sum_keys + list(EXTREMES)
        agg = ", ".join([f"sum({k})" for k in self.sum_cols] + [f"{fn}(epoch({k}))" for k, fn in EXTREMES.items()])
        res = {bool(r[0]): dict(zip(keys, r[1:])) for r in self._raw(
            f"SELECT list_contains(?::BIGINT[], dir_id), count(*), {agg} FROM dirs "
            "WHERE depth = ? AND path >= ? AND path < ? GROUP BY 1", [listed, depth, lo, hi])}
        empty = {"n": 0, **{k: None for k in self.sum_keys + list(EXTREMES)}}
        parts = [res[k] for k in (True, False) if k in res]
        if not parts:
            return dict(empty), dict(empty)
        total = Stats.zero()
        for x in parts:
            total = total.add(Stats.from_row(x))
        return {"n": sum(x["n"] for x in parts), **total.values}, res.get(False, dict(empty))

    def _raw(self, sql: str, params: list) -> list[tuple]:
        cur = self._cursor()
        try:
            with limits.guard(cur):
                return cur.execute(sql, params).fetchall()
        finally:
            cur.close()

    def _grandchildren(self, path: str, depth: int, expr: str, limit: int, parents: list[int]) -> dict[int, dict]:
        """The largest children of each listed child (`parents`); the others are not drawn."""
        lo, hi = child_range(path)
        rows = self._rows(f"""
            WITH g AS (SELECT {self.dir_sql}, {expr} AS m,
                              row_number() OVER (PARTITION BY parent_id ORDER BY {expr} DESC, path) AS rn
                       FROM dirs WHERE depth = ? AND path >= ? AND path < ? AND list_contains(?::BIGINT[], parent_id))
            SELECT * EXCLUDE (m) FROM g WHERE rn <= ? ORDER BY parent_id, rn
        """, [depth + 2, lo, hi, parents, limit])
        # Group by (parent, beyond-the-limit) rather than using FILTER aggregates: one pass, and far faster.
        agg = ", ".join([f"sum({k})" for k in self.sum_cols] + [f"{fn}(epoch({k}))" for k, fn in EXTREMES.items()])
        sums = self._raw(f"""
            WITH g AS (SELECT *, (row_number() OVER (PARTITION BY parent_id ORDER BY {expr} DESC, path) > ?) AS rest
                       FROM dirs WHERE depth = ? AND path >= ? AND path < ? AND list_contains(?::BIGINT[], parent_id))
            SELECT parent_id, rest, count(*), {agg} FROM g GROUP BY parent_id, rest
        """, [limit, depth + 2, lo, hi, parents])
        keys = self.sum_keys + list(EXTREMES)
        out: dict[int, dict] = {}
        for r in rows:
            out.setdefault(r["parent_id"], {"rows": [], "all": None, "rest": None})["rows"].append(r)
        parts: dict[int, dict[bool, dict]] = {}
        for r in sums:
            parts.setdefault(r[0], {})[bool(r[1])] = {"n": r[2], **dict(zip(keys, r[3:]))}
        for parent, p in parts.items():
            entry = out.setdefault(parent, {"rows": [], "all": None, "rest": None})
            top, rest = p.get(False), p.get(True)
            entry["rest"] = rest or {"n": 0}
            both = [x for x in (top, rest) if x]
            total = Stats.zero()
            for x in both:
                total = total.add(Stats.from_row(x))
            entry["all"] = {"n": sum(x["n"] for x in both), **total.values}
        return out

    def subtree_dirs(self, path: str, *, where: str = "", params: list | None = None, order: str = "bytes DESC",
                     limit: int = 100) -> list[dict]:
        """Directories anywhere below `path` (not `path` itself), filtered by a fixed SQL fragment."""
        lo, hi = child_range(path)
        cond = f"path >= ? AND path < ?{' AND ' + where if where else ''}"
        return self._rows(f"SELECT {self.dir_sql} FROM dirs WHERE {cond} ORDER BY {order} LIMIT ?",
                          [lo, hi, *(params or []), limit], self.row_keys)

    # ---- Changes between scans ----
    def attach(self, other: Path) -> str:
        """Attach another snapshot's index read-only, once, and return its alias for queries."""
        alias = "p_" + hashlib.sha1(str(Path(other).resolve()).encode()).hexdigest()[:12]
        with self._lock:
            self._con.execute(f"ATTACH IF NOT EXISTS '{str(Path(other) / 'index.duckdb').replace(chr(39), chr(39) * 2)}' "
                              f"AS {alias} (READ_ONLY)")
        return alias

    def _pairs(self, against: str | None, cond: str) -> str:
        """Directories matching `cond` in this scan and in the one compared with, joined by path: `in_new`, `in_old`,
        and each scan's CHANGE_KEYS as n_* and o_*. Without `against`, from the `changes` table (the previous scan);
        with it, from that attached index's directories, joined here. `cond` takes its parameters once (from
        `changes`) or twice (this scan's, then the other's)."""
        if against is None:
            return f"SELECT path, name, depth, in_new, in_old, {CHANGE_COLS} FROM changes WHERE {cond}"
        cols = ", ".join(f"o.{c} AS o_{c}, n.{c} AS n_{c}" for c in CHANGE_KEYS)
        return f"""SELECT coalesce(n.path, o.path) AS path, coalesce(n.name, o.name) AS name,
                          coalesce(n.depth, o.depth) AS depth, n.path IS NOT NULL AS in_new, o.path IS NOT NULL AS in_old, {cols}
                   FROM (SELECT * FROM dirs WHERE {cond}) n FULL JOIN (SELECT * FROM {against}.dirs WHERE {cond}) o
                   ON o.path = n.path"""

    def change_node(self, path: str, *, against: str | None = None) -> dict | None:
        """A directory in both scans (or the one it is in), as `_pairs` gives it; None if in neither."""
        rows = self._rows(self._pairs(against, "depth = ? AND path = ?"),
                          [self.depth_of(path), path] * (1 if against is None else 2))
        return rows[0] if rows else None

    def change_children(self, path: str, *, against: str | None = None, metric: str = "max", limit: int = 60,
                        grand_limit: int = 0) -> dict:
        """A directory and its children in both scans (this one and the one compared with), largest first by
        `metric` (CHANGE_METRICS), the rest folded; with grand_limit, each listed child's largest children too.
        A directory in only one scan is new or removed; it is listed with nothing for the other."""
        twice = 1 if against is None else 2
        depth = self.depth_of(path)
        lo, hi = child_range(path)
        expr = CHANGE_METRICS[metric]
        node = self.change_node(path, against=against)
        if node is None:
            return {"node": None, "rows": [], "all": no_pairs(), "rest": no_pairs(), "grand": {}}
        kids = self._pairs(against, "depth = ? AND path >= ? AND path < ?")
        rows = self._rows(f"SELECT * FROM ({kids}) WHERE {expr} > 0 ORDER BY {expr} DESC, path LIMIT ?",
                          [depth + 1, lo, hi] * twice + [limit])
        listed = [r["path"] for r in rows]
        sums = {bool(r["listed"]): r for r in self._rows(
            f"SELECT list_contains(?::VARCHAR[], path) AS listed, {PAIR_SUMS} FROM ({kids}) GROUP BY 1",
            [listed] + [depth + 1, lo, hi] * twice)}
        out = {"node": node, "rows": rows, "all": add_pairs(*sums.values()), "rest": sums.get(False, no_pairs()),
               "grand": {}}
        if grand_limit and rows:
            parent = "substr(path, 1, length(path) - length(name) - 1)"
            g = f"""SELECT *, {parent} AS parent, row_number() OVER (PARTITION BY {parent} ORDER BY {expr} DESC, path) AS rn
                    FROM ({kids}) WHERE list_contains(?::VARCHAR[], {parent})"""
            params = [depth + 2, lo, hi] * twice + [listed]
            for r in self._rows(f"SELECT * EXCLUDE (rn) FROM ({g}) WHERE rn <= ? AND {expr} > 0 ORDER BY parent, rn",
                                params + [grand_limit]):
                out["grand"].setdefault(r["parent"], {"rows": [], "all": no_pairs(), "rest": no_pairs()})["rows"].append(r)
            for r in self._rows(f"SELECT parent, rn > ? AS beyond, {PAIR_SUMS} FROM ({g}) GROUP BY parent, beyond",
                                [grand_limit] + params):
                e = out["grand"].setdefault(r["parent"], {"rows": [], "all": no_pairs(), "rest": no_pairs()})
                e["all"] = add_pairs(e["all"], r)
                if r["beyond"]:
                    e["rest"] = r
        return out

    def change_units(self, path: str, *, against: str | None = None, limit: int = 20) -> dict:
        """What changed below `path` between the two scans, folder by folder:
        - grew and shrank: the folders in both scans that grew (or shrank) most, at any depth, leaving out a folder
          when one of its subfolders holds nine tenths or more of its change, so a chain of folders is listed once,
          where the change is (folders in one scan only are the new and removed ones);
        - new and removed: folders in one scan only whose parent is in both, the outermost of what appeared or went;
        - counts: of every new and removed folder, and the bytes in the outermost ones."""
        twice = 1 if against is None else 2
        lo, hi = child_range(path)
        pairs = self._pairs(against, "path >= ? AND path < ?")
        params = [lo, hi] * twice
        base = f"""
            WITH d AS (SELECT *, coalesce(n_bytes, 0) - coalesce(o_bytes, 0) AS delta,
                              substr(path, 1, length(path) - length(name) - 1) AS parent FROM ({pairs})),
            c AS (SELECT parent, max(delta) AS up, min(delta) AS down FROM d GROUP BY parent),
            x AS (SELECT d.*, c.up, c.down, p.in_new AS parent_new, p.in_old AS parent_old
                  FROM d LEFT JOIN c ON c.parent = d.path LEFT JOIN d p ON p.path = d.parent)"""
        # A parent outside the subtree is `path` itself, in both scans as far as its subfolders are concerned.
        lists = {
            "grew": "in_new AND in_old AND delta > 0 AND coalesce(up, 0) < 0.9 * delta ORDER BY delta DESC, path",
            "shrank": "in_new AND in_old AND delta < 0 AND coalesce(down, 0) > 0.9 * delta ORDER BY delta, path",
            "new": "in_new AND NOT in_old AND coalesce(parent_old, true) ORDER BY n_bytes DESC NULLS LAST, path",
            "removed": "in_old AND NOT in_new AND coalesce(parent_new, true) ORDER BY o_bytes DESC NULLS LAST, path",
        }
        out = {k: self._rows(f"{base} SELECT * EXCLUDE (up, down, parent_new, parent_old) FROM x WHERE {cond} LIMIT ?",
                             [*params, limit]) for k, cond in lists.items()}
        counts = self._rows(f"""{base} SELECT
                count(*) FILTER (WHERE in_new AND NOT in_old) AS new_dirs,
                count(*) FILTER (WHERE in_old AND NOT in_new) AS removed_dirs,
                count(*) FILTER (WHERE in_new AND NOT in_old AND coalesce(parent_old, true)) AS new_tops,
                count(*) FILTER (WHERE in_old AND NOT in_new AND coalesce(parent_new, true)) AS removed_tops,
                coalesce(sum(n_bytes) FILTER (WHERE in_new AND NOT in_old AND coalesce(parent_old, true)), 0) AS new_bytes,
                coalesce(sum(o_bytes) FILTER (WHERE in_old AND NOT in_new AND coalesce(parent_new, true)), 0) AS removed_bytes
            FROM x""", params)[0]
        out["counts"] = {k: int(v or 0) for k, v in counts.items()}
        return out

    def file_changes(self, path: str, other: "SnapshotIndex", *, kind: str = "new", limit: int = 20) -> dict:
        """Files below `path` in this scan and not in `other` (kind "new"), or the reverse ("removed"), largest
        first, with how many there are and their total size. Files are matched by path. Against the previous scan,
        they were found when this index was built (`file_changes`); against an older one, they are found here."""
        lo, hi = child_range(path)
        if self.has_file_changes and self.previous and other.meta.get("scan_id") == self.previous.get("scan_id"):
            cond, params = "change = ? AND path >= ? AND path < ?", [kind, lo, hi]
            rows = self._rows(f"SELECT {self.file_sql} FROM file_changes WHERE {cond} ORDER BY size DESC, path LIMIT ?",
                              [*params, limit], FILE_KEYS)
            n, b = self._one(f"SELECT count(*), coalesce(sum(size), 0) FROM file_changes WHERE {cond}", params)
            return {"rows": rows, "files": int(n), "bytes": int(b)}
        here, there = (self, other) if kind == "new" else (other, self)
        with self._turn(path):
            return here._missing(path, there, limit)

    def _missing(self, path: str, other: "SnapshotIndex", limit: int) -> dict:
        """Files below `path` here and not in `other`, as `file_changes` gives them, against the top-level folders of
        the same names in `other`: groups of top-level folders at a time, up to COMPARE_FILES files (a larger group
        alone), so memory is bounded by a group's files rather than the filesystem's."""
        lo, hi = child_range(path)
        groups: dict[str, list[int]] = {}
        for t in self.scope_tops(path):
            if t in self.parts:
                groups.setdefault(self.parts[t], []).append(t)
        batches: list[list[int]] = []
        size = 0
        for _, tops in sorted(groups.items()):
            n = sum(self.top_files.get(t, 0) for t in tops)
            if not batches or size + n > COMPARE_FILES:
                batches.append([])
                size = 0
            batches[-1] += tops
            size += n
        rows, n, b = [], 0, 0
        for tops in batches:
            src = f"(SELECT * FROM {self.file_sources(tops)} WHERE path >= ? AND path < ?)"
            params = [lo, hi]
            theirs = [0 if t == 0 else other.top_ids.get(self.top_names[t]) for t in tops]
            theirs = [t for t in theirs if t is not None and t in other.parts]
            if theirs:
                src += (f" ANTI JOIN (SELECT path FROM {other.file_sources(theirs)} WHERE path >= ? AND path < ? "
                        f"AND top_id IN ({', '.join(str(int(t)) for t in theirs)})) USING (path)")
                params += [lo, hi]
            got = self._rows(f"SELECT {self.file_sql}, count(*) OVER () AS _n, sum(size) OVER () AS _b FROM {src} "
                             "ORDER BY size DESC, path LIMIT ?", [*params, limit], [*FILE_KEYS, "_n", "_b"])
            if got:
                n += got[0]["_n"]
                b += got[0]["_b"] or 0
            rows += got
        rows = sorted(rows, key=lambda r: (-(r["size"] or 0), r["path"]))[:limit]
        for r in rows:
            del r["_n"], r["_b"]
        return {"rows": rows, "files": int(n), "bytes": int(b)}

    def cold_dirs(self, path: str, older: int, *, min_bytes: int = 0, limit: int = 200) -> dict:
        """The largest directories at or below `path` in which every file is in age bucket `older` or older, and
        whose parent holds a newer file (or is above `path`): each is a self-contained cold unit."""
        if not self.has_cold_units:
            edge_days = [b.max_days for b in AGE_BUCKETS if b.max_days][older - 1]
            return self._cold_scan(path, self.ref_epoch - edge_days * DAY, min_bytes=min_bytes, limit=limit)
        node = self.node(path)
        if node is None:
            return {"rows": [], "count": 0, "bytes": 0, "files": 0}
        if node["files"] > 0 and node["mtime_max"] is not None and not sum(node[f"mf{i}"] for i in range(older)):
            # The folder itself is cold: it is the one unit.
            rows = [node] if node["bytes"] >= min_bytes else []
            return {"rows": rows, "count": 1, "bytes": int(node["bytes"]), "files": int(node["files"])}
        # Not cold, so neither is anything above it, and the units below it are the filesystem's units there.
        lo, hi = child_range(path)
        units = "SELECT dir_id, depth, path, files, bytes FROM cold_units WHERE bucket = ? AND path >= ? AND path < ?"
        params = [older, lo, hi]
        total = self._one(f"SELECT count(*), coalesce(sum(bytes), 0), coalesce(sum(files), 0) FROM ({units})", params)
        rows = self._rows(
            f"SELECT {dir_columns(self._cat_names, 'd')} FROM (SELECT * FROM ({units}) WHERE bytes >= ? "
            "ORDER BY bytes DESC LIMIT ?) u JOIN dirs d ON d.depth = u.depth AND d.path = u.path ORDER BY u.bytes DESC, d.path",
            [*params, min_bytes, limit], self.row_keys)
        return {"rows": rows, "count": int(total[0]), "bytes": int(total[1]), "files": int(total[2])}

    def _cold_scan(self, path: str, cutoff_epoch: float, *, min_bytes: int = 0, limit: int = 200) -> dict:
        """`cold_dirs` for indexes without `cold_units`: joins every directory below `path` with its parent."""
        lo, hi = child_range(path)
        depth = self.depth_of(path)
        base = """
            WITH s AS (SELECT dir_id, parent_id, depth, path, files, bytes, mtime_max FROM dirs
                       WHERE (path >= ? AND path < ?) OR (depth = ? AND path = ?)),
            c AS (SELECT s.* FROM s LEFT JOIN s p ON p.dir_id = s.parent_id
                  WHERE s.files > 0 AND epoch(s.mtime_max) < ? AND
                        (p.dir_id IS NULL OR epoch(p.mtime_max) >= ? OR p.mtime_max IS NULL))
        """
        params = [lo, hi, depth, path, cutoff_epoch, cutoff_epoch]
        total = self._one(base + "SELECT count(*), coalesce(sum(bytes), 0), coalesce(sum(files), 0) FROM c", params)
        rows = self._rows(
            base + f"SELECT {dir_columns(self._cat_names, 'd')} FROM c JOIN dirs d USING (dir_id) "
            "WHERE c.bytes >= ? ORDER BY c.bytes DESC LIMIT ?",
            [*params, min_bytes, limit], self.row_keys)
        return {"rows": rows, "count": int(total[0]), "bytes": int(total[1]), "files": int(total[2])}

    # ---- Types and owners ----
    def types(self, path: str) -> list[dict]:
        depth = self.depth_of(path)
        cols = ("ext, cat, sum(files)::BIGINT AS files, sum(bytes)::BIGINT AS bytes, sum(alloc)::BIGINT AS alloc, "
                "sum(m_wsum) AS m_wsum, epoch(min(mtime_min)) AS oldest, epoch(max(mtime_max)) AS newest, "
                "epoch(max(atime_max)) AS last_access")
        if depth == 0:
            return self._rows(f"SELECT {cols} FROM top_ext GROUP BY ext, cat ORDER BY bytes DESC")
        if depth == 1:
            tid = self.top_ids.get(self.top_of(path))
            return self._rows(f"SELECT {cols} FROM top_ext WHERE top_id = ? GROUP BY ext, cat ORDER BY bytes DESC",
                              [tid])
        lo, hi = child_range(path)
        return self._rows(f"SELECT {cols} FROM dir_ext WHERE path = ? OR (path >= ? AND path < ?) "
                          "GROUP BY ext, cat ORDER BY bytes DESC", [path, lo, hi])

    def owners(self, path: str) -> list[dict]:
        depth = self.depth_of(path)
        cols = ("o.uid, u.name, sum(o.files)::BIGINT AS files, sum(o.bytes)::BIGINT AS bytes, "
                "sum(o.alloc)::BIGINT AS alloc, sum(o.m_wsum) AS m_wsum, epoch(max(o.mtime_max)) AS newest")
        if depth == 0:
            return self._rows(f"SELECT {cols} FROM top_owner o LEFT JOIN users u USING (uid) "
                              "GROUP BY o.uid, u.name ORDER BY bytes DESC")
        if depth == 1:
            tid = self.top_ids.get(self.top_of(path))
            return self._rows(f"SELECT {cols} FROM top_owner o LEFT JOIN users u USING (uid) WHERE o.top_id = ? "
                              "GROUP BY o.uid, u.name ORDER BY bytes DESC", [tid])
        lo, hi = child_range(path)
        return self._rows(f"SELECT {cols} FROM dir_owner o LEFT JOIN users u USING (uid) "
                          "WHERE o.path = ? OR (o.path >= ? AND o.path < ?) GROUP BY o.uid, u.name ORDER BY bytes DESC",
                          [path, lo, hi])

    def user_names(self) -> dict[int, str | None]:
        return {uid: name for uid, name in self._raw("SELECT uid, name FROM users", [])}

    # ---- Files ----
    def files(self, path: str, *, where: str = "", params: list | None = None, order: str = "size DESC",
              limit: int = 100, direct_only: bool = False) -> list[dict]:
        src = self.file_sources(self.scope_tops(path))
        if src is None:
            return []
        lo, hi = child_range(path)
        cond = ["path >= ?", "path < ?"]
        args: list = [lo, hi]
        if direct_only:
            cond.append("depth = ?")
            args.append(self.depth_of(path) + 1)
        if where:
            cond.append(where)
            args.extend(params or [])
        with self._turn(path):
            return self._rows(f"SELECT {self.file_sql} FROM {src} WHERE {' AND '.join(cond)} ORDER BY {order} LIMIT ?",
                              [*args, limit], FILE_KEYS)

    def files_to(self, path: str, out: Path, *, where: str = "", params: list | None = None, order: str = "size DESC",
                 direct_only: bool = False) -> tuple[int, int]:
        """Every file `files` would find, written to the Parquet file `out` in order: for a search in the background
        (jobs.py), which has no row limit. Returns how many files, and their bytes."""
        src = self.file_sources(self.scope_tops(path))
        if src is None:
            return 0, 0
        lo, hi = child_range(path)
        cond, args = ["path >= ?", "path < ?"], [lo, hi]
        if direct_only:
            cond.append("depth = ?")
            args.append(self.depth_of(path) + 1)
        if where:
            cond.append(where)
            args.extend(params or [])
        quoted = "'" + str(out).replace("'", "''") + "'"
        with self._turn(path):
            self._raw(f"COPY (SELECT {self.file_sql} FROM {src} WHERE {' AND '.join(cond)} ORDER BY {order}) "
                      f"TO {quoted} (FORMAT parquet, COMPRESSION zstd)", args)
            n, b = self._raw(f"SELECT count(*), coalesce(sum(size), 0) FROM read_parquet({quoted})", [])[0]
        return int(n), int(b)

    def files_summary(self, path: str, *, where: str = "", params: list | None = None) -> dict:
        src = self.file_sources(self.scope_tops(path))
        if src is None:
            return {"files": 0, "bytes": 0}
        lo, hi = child_range(path)
        cond = "path >= ? AND path < ?" + (f" AND {where}" if where else "")
        with self._turn(path):
            n, b = self._one(f"SELECT count(*), coalesce(sum(size), 0) FROM {src} WHERE {cond}",
                             [lo, hi, *(params or [])])
        return {"files": int(n), "bytes": int(b)}

    # ---- Storage health ----
    def errors(self, path: str, limit: int = 200) -> dict:
        lo, hi = child_range(path)
        cond = "(path = ? OR (path >= ? AND path < ?))"
        by = self._rows(f"SELECT op, errno_name, count(*) AS n FROM errors WHERE {cond} GROUP BY ALL ORDER BY n DESC",
                        [path, lo, hi])
        rows = self._rows(f"SELECT path, op, errno_name, message FROM errors WHERE {cond} ORDER BY path LIMIT ?",
                          [path, lo, hi, limit])
        return {"by_kind": by, "rows": rows}

    def name_flag_counts(self, top_ids: Iterable[int] | None = None) -> list[dict]:
        if top_ids is None:
            return self._rows("SELECT flag, sum(entries)::BIGINT AS entries FROM name_flags GROUP BY flag ORDER BY flag")
        ids = list(top_ids)
        if not ids:
            return []
        marks = ", ".join("?" for _ in ids)
        return self._rows(f"SELECT flag, sum(entries)::BIGINT AS entries FROM name_flags WHERE top_id IN ({marks}) "
                          "GROUP BY flag ORDER BY flag", ids)


def dimensions() -> dict:
    """The age buckets, as the interface shows them; the categories are the catalog's (categories.current)."""
    return {"age_buckets": [{"label": b.label, "short": b.short, "max_days": b.max_days} for b in AGE_BUCKETS]}
