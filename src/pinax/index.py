# SPDX-License-Identifier: Apache-2.0
"""Read-only queries over one snapshot index (see ingest/build.py for its layout).

Every query is scoped by path ranges that the index's sort order turns into contiguous reads:
a directory's children are `depth = d + 1 AND path in [prefix, upper)` in `dirs`, and a subtree's
files are `path in [prefix, upper)` in the top-level folder's own Parquet files. All values from
callers are bound as parameters; the only interpolated SQL fragments are built here from fixed lists.
"""

import json
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from .categories import AGE_BUCKETS, CATEGORIES, CATEGORY_KEYS
from .ingest.build import ADDITIVE, EXTREMES, NB, NC

DAY = 86400.0

# Columns selected for every directory row. Times come back as epoch seconds (DOUBLE) so no
# timezone conversion happens in the database driver.


def dir_columns(alias: str = "") -> str:
    a = f"{alias}." if alias else ""
    return (
        f"{a}dir_id, {a}parent_id, {a}depth, {a}top_id, {a}path, {a}name, {a}error, {a}is_mount_point, "
        f"{a}dir_entries, {a}d_files, {a}d_bytes, epoch({a}d_mtime_min) AS d_mtime_min, "
        f"epoch({a}d_mtime_max) AS d_mtime_max, epoch({a}d_atime_max) AS d_atime_max, {a}dirs, {a}incomplete, "
        + ", ".join(f"{a}{c}" for c in ADDITIVE) + ", "
        + ", ".join(f"epoch({a}{c}) AS {c}" for c in EXTREMES)
    )


DIR_COLUMNS = dir_columns()
ROW_KEYS = (
    ["dir_id", "parent_id", "depth", "top_id", "path", "name", "error", "is_mount_point", "dir_entries", "d_files",
     "d_bytes", "d_mtime_min", "d_mtime_max", "d_atime_max", "dirs", "incomplete"] + ADDITIVE + list(EXTREMES)
)
SUM_KEYS = ["dirs", "incomplete"] + ADDITIVE

FILE_COLUMNS = ("path, name, ext, cat, kind, size, alloc_bytes, uid, gid, epoch(mtime) AS mtime, epoch(atime) AS atime, "
                "nlink, name_flags")
FILE_KEYS = ["path", "name", "ext", "cat", "kind", "size", "alloc_bytes", "uid", "gid", "mtime", "atime", "nlink",
             "name_flags"]

# Sizing metrics the treemap can use. Each maps to a SQL expression over `dirs` columns.
METRICS: dict[str, str] = {"bytes": "bytes", "alloc": "alloc", "files": "files"}
METRICS.update({f"cat:{k}": f"cb{i}" for i, k in enumerate(CATEGORY_KEYS)})
METRICS.update({f"older:{i}": "(" + " + ".join(f"mb{j}" for j in range(i, NB)) + ")" for i in range(1, NB)})


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
        return cls({k: row.get(k) for k in SUM_KEYS + list(EXTREMES)})

    @classmethod
    def zero(cls) -> "Stats":
        return cls({**{k: 0 for k in SUM_KEYS}, **{k: None for k in EXTREMES}})

    def add(self, other: "Stats", count_as_dir: bool = False) -> "Stats":
        v = dict(self.values)
        for k in SUM_KEYS:
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
        for k in SUM_KEYS:
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
            "cat_bytes": [int(v.get(f"cb{i}") or 0) for i in range(NC)],
            "cat_files": [int(v.get(f"cf{i}") or 0) for i in range(NC)],
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
        tops = self._con.execute("SELECT top_id, name, path FROM tops ORDER BY top_id").fetchall()
        self.top_ids = {name: tid for tid, name, _ in tops}
        self.top_names = {tid: name for tid, name, _ in tops}
        self.parts = {
            tid for (tid,) in self._con.execute("SELECT top_id FROM file_parts WHERE files > 0").fetchall()
        }

    def close(self) -> None:
        self._con.close()

    def _cursor(self) -> duckdb.DuckDBPyConnection:
        with self._lock:
            return self._con.cursor()

    def _rows(self, sql: str, params: list | None = None, keys: list[str] | None = None) -> list[dict]:
        cur = self._cursor()
        try:
            res = cur.execute(sql, params or [])
            names = keys or [d[0] for d in res.description]
            return [dict(zip(names, r)) for r in res.fetchall()]
        finally:
            cur.close()

    def _one(self, sql: str, params: list | None = None) -> tuple | None:
        cur = self._cursor()
        try:
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
        globs = [str(self.index_dir / "files" / f"t{t:06d}" / "*.parquet") for t in sorted(set(top_ids)) if t in self.parts]
        if not globs:
            return None
        return "read_parquet([" + ", ".join("'" + g.replace("'", "''") + "'" for g in globs) + "])"

    def scope_tops(self, path: str) -> list[int]:
        top = self.top_of(path)
        if top is None:
            return [0, *self.top_names.keys()]
        return [self.top_ids[top]] if top in self.top_ids else []

    # ---- Directories ----
    def node(self, path: str) -> dict | None:
        rows = self._rows(f"SELECT {DIR_COLUMNS} FROM dirs WHERE depth = ? AND path = ?",
                          [self.depth_of(path), path], ROW_KEYS)
        return rows[0] if rows else None

    def children(self, path: str, *, metric: str = "bytes", limit: int = 60, grand_limit: int = 0) -> dict:
        """The children of `path`, largest first by `metric`, the rest folded into one "other" entry.
        With grand_limit, each listed child also gets its own largest children."""
        expr = METRICS[metric]
        depth = self.depth_of(path)
        lo, hi = child_range(path)
        rows = self._rows(
            f"SELECT {DIR_COLUMNS} FROM dirs WHERE depth = ? AND path >= ? AND path < ? "
            f"ORDER BY {expr} DESC, path LIMIT ?", [depth + 1, lo, hi, limit], ROW_KEYS)
        agg = ", ".join([f"sum({k}) AS {k}" for k in SUM_KEYS]
                        + [f"{fn}(epoch({k})) AS {k}" for k, fn in EXTREMES.items()])
        all_sum, rest = self._child_sums(agg, depth + 1, lo, hi, expr, limit)
        out = {"rows": rows, "all": all_sum, "rest": rest}
        if grand_limit and rows:
            out["grand"] = self._grandchildren(path, depth, expr, grand_limit)
        return out

    def _child_sums(self, agg: str, depth: int, lo: str, hi: str, expr: str, limit: int) -> tuple[dict, dict]:
        keys = ["n"] + SUM_KEYS + list(EXTREMES)
        sql = f"""
            WITH c AS (SELECT *, row_number() OVER (ORDER BY {expr} DESC, path) AS rn
                       FROM dirs WHERE depth = ? AND path >= ? AND path < ?)
            SELECT 'all' AS which, count(*) AS n, {agg} FROM c
            UNION ALL
            SELECT 'rest', count(*), {agg} FROM c WHERE rn > ?
        """
        res = {r[0]: dict(zip(keys, r[1:])) for r in self._raw(sql, [depth, lo, hi, limit])}
        return res["all"], res["rest"]

    def _raw(self, sql: str, params: list) -> list[tuple]:
        cur = self._cursor()
        try:
            return cur.execute(sql, params).fetchall()
        finally:
            cur.close()

    def _grandchildren(self, path: str, depth: int, expr: str, limit: int) -> dict[int, dict]:
        lo, hi = child_range(path)
        rows = self._rows(f"""
            WITH g AS (SELECT {DIR_COLUMNS}, {expr} AS m,
                              row_number() OVER (PARTITION BY parent_id ORDER BY {expr} DESC, path) AS rn
                       FROM dirs WHERE depth = ? AND path >= ? AND path < ?)
            SELECT * EXCLUDE (m) FROM g WHERE rn <= ? ORDER BY parent_id, rn
        """, [depth + 2, lo, hi, limit])
        # Group by (parent, beyond-the-limit) rather than using FILTER aggregates: one pass, and far faster.
        agg = ", ".join([f"sum({k})" for k in SUM_KEYS] + [f"{fn}(epoch({k}))" for k, fn in EXTREMES.items()])
        sums = self._raw(f"""
            WITH g AS (SELECT *, (row_number() OVER (PARTITION BY parent_id ORDER BY {expr} DESC, path) > ?) AS rest
                       FROM dirs WHERE depth = ? AND path >= ? AND path < ?)
            SELECT parent_id, rest, count(*), {agg} FROM g GROUP BY parent_id, rest
        """, [limit, depth + 2, lo, hi])
        keys = SUM_KEYS + list(EXTREMES)
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
        return self._rows(f"SELECT {DIR_COLUMNS} FROM dirs WHERE {cond} ORDER BY {order} LIMIT ?",
                          [lo, hi, *(params or []), limit], ROW_KEYS)

    def cold_dirs(self, path: str, cutoff_epoch: float, *, min_bytes: int = 0, limit: int = 200) -> dict:
        """The largest directories below `path` in which no file changed after `cutoff_epoch`, whose
        parent did have a newer change (or is `path` itself): each is a self-contained cold unit."""
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
            base + f"SELECT {dir_columns('d')} FROM c JOIN dirs d USING (dir_id) "
            "WHERE c.bytes >= ? ORDER BY c.bytes DESC LIMIT ?",
            [*params, min_bytes, limit], ROW_KEYS)
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
        return self._rows(f"SELECT {FILE_COLUMNS} FROM {src} WHERE {' AND '.join(cond)} ORDER BY {order} LIMIT ?",
                          [*args, limit], FILE_KEYS)

    def files_summary(self, path: str, *, where: str = "", params: list | None = None) -> dict:
        src = self.file_sources(self.scope_tops(path))
        if src is None:
            return {"files": 0, "bytes": 0}
        lo, hi = child_range(path)
        cond = "path >= ? AND path < ?" + (f" AND {where}" if where else "")
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


def category_index(key: str) -> int:
    return CATEGORY_KEYS.index(key)


def dimensions() -> dict:
    return {
        "categories": [{"key": c.key, "label": c.label, "description": c.description} for c in CATEGORIES],
        "age_buckets": [{"label": b.label, "short": b.short, "max_days": b.max_days} for b in AGE_BUCKETS],
    }
