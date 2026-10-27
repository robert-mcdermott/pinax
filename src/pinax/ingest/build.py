# SPDX-License-Identifier: Apache-2.0
"""Build a snapshot index from an fs-inventory scan.

The index is what makes billion-file scans interactive. Everything the interface draws at the
directory level is precomputed once per scan, so no page ever reads per-file rows to draw a treemap:

    <index>/index.duckdb       dirs        one row per directory: its subtree totals (files, bytes,
                                           allocated bytes, age histograms, bytes and files by
                                           category, oldest/newest times, byte-weighted mean age),
                                           ordered by (depth, path) so a directory's children are one
                                           contiguous range
                               dir_ext     per directory and file type: totals over the directory's
                                           *own* files, ordered by path, so any subtree's breakdown
                                           is one contiguous range scan
                               dir_owner   the same per owner (uid)
                               top_ext,    the same, already summed per top-level folder
                               top_owner
                               cold_units  per age threshold, the largest folders in which nothing is
                                           younger, ordered by (threshold, path)
                               changes     every directory of this scan or the previous one, joined by
                                           path, with both scans' totals; ordered by (depth, path)
                               tops, users, groups, errors, name_flags, meta
    <index>/files/gNNNNNN/*.parquet        every non-directory entry, in at most 64 groups of
                                           top-level folders, each sorted by path (so any folder,
                                           a lab's included, is a contiguous range of row groups)

Subtree totals are computed bottom-up one depth level at a time: each level reads only its own
rows and its children's totals, so the whole tree costs one pass however deep it is. Files are sorted and
summarized one group of top-level folders at a time, so memory is bounded by the largest group, not the
file system, and DuckDB spills to disk beyond its memory limit.
"""

import json
import logging
import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from ..categories import AGE_BUCKETS, CATEGORIES, COMPRESSION_SUFFIXES, OTHER, bucket_edges, describe, extension_map

log = logging.getLogger("pinax.build")

INDEX_VERSION = 4
# Files are staged and sorted in at most this many groups of top-level folders. A partitioned write keeps a
# file open and buffered for each partition, so one per top-level folder does not scale to thousands of them.
FILE_GROUPS = 64
NB = len(AGE_BUCKETS)
NC = len(CATEGORIES)
US_PER_DAY = 86_400_000_000
EPOCH = datetime(1970, 1, 1)

REQUIRED_COLUMNS = {
    "path", "name", "extension", "kind", "depth", "dir_id", "parent_id", "uid", "gid", "size", "alloc_bytes",
    "mtime", "atime", "ctime", "nlink", "name_flags", "dir_entries", "error", "is_mount_point",
}

# Additive subtree measures (summed up the tree) and extremes (min/max up the tree).
ADDITIVE = (
    ["files", "bytes", "alloc", "m_wsum", "m_fsum", "m_n", "a_wsum", "a_n"]
    + [f"mf{i}" for i in range(NB)] + [f"mb{i}" for i in range(NB)] + [f"ab{i}" for i in range(NB)]
    + [f"cf{i}" for i in range(NC)] + [f"cb{i}" for i in range(NC)]
)
EXTREMES = {"mtime_min": "min", "mtime_max": "max", "atime_max": "max", "ctime_max": "max"}
FLOAT_COLUMNS = {"m_wsum", "m_fsum", "a_wsum"}


class BuildError(Exception):
    pass


@dataclass
class BuildResult:
    index_dir: Path
    manifest: dict
    root: str
    started_at: datetime
    seconds: float
    totals: dict
    steps: dict[str, float] = field(default_factory=dict)
    compared: dict | None = None  # the previous scan this one was compared with


def read_manifest(scan_dir: Path) -> dict:
    path = scan_dir / "manifest.json"
    if not path.exists():
        raise BuildError(f"{scan_dir} has no manifest.json; is it an fs-inventory scan directory?")
    return json.loads(path.read_text())


def check_manifest(manifest: dict, *, allow_incomplete: bool = False) -> None:
    if manifest.get("tool") != "fs-inventory":
        raise BuildError("manifest.json was not written by fs-inventory")
    if manifest.get("format") != "parquet":
        raise BuildError(f"scan format is {manifest.get('format')!r}; Pinax reads Parquet scans")
    if int(manifest.get("schema_version", 0)) < 2:
        raise BuildError("scan schema version is older than 2; rescan with fs-inventory 0.5 or later")
    status = manifest.get("status")
    allowed = {"complete", "incomplete"} if allow_incomplete else {"complete"}
    if status not in allowed:
        raise BuildError(f"scan status is {status!r}, not complete (resume or finish the scan first)")
    roots = manifest.get("roots") or []
    if len(roots) != 1:
        raise BuildError(f"scan has {len(roots)} roots; Pinax indexes one root per filesystem")
    missing = REQUIRED_COLUMNS - set(manifest.get("columns", []))
    if missing:
        raise BuildError(f"scan is missing columns Pinax needs: {', '.join(sorted(missing))}")


def scan_parts(scan_dir: Path, manifest: dict, table: str) -> list[Path]:
    """The part files of one of a scan's tables. The manifest lists them, and only those are the scan: a scan
    folder can also hold leftovers of an interrupted run. Scans whose manifest lists none are read whole."""
    listed = (manifest.get("parts") or {}).get(table)
    if listed is None:
        return sorted((scan_dir / table).glob("*.parquet"))
    parts = [scan_dir / p["file"] for p in listed]
    missing = [str(p) for p in parts if not p.exists()]
    if missing:
        raise BuildError(f"the manifest lists {table} parts that are missing: {', '.join(missing[:3])}")
    return parts


def parquet_source(parts: list[Path]) -> str:
    return "read_parquet([" + ", ".join(_sql_str(str(p)) for p in parts) + "])"


def _is_ns(con: duckdb.DuckDBPyConnection, query: str) -> bool:
    return con.execute(f"DESCRIBE {query}").fetchone()[1].endswith("_NS")


def _to_us(col: str) -> str:
    """Scans made with `--time-unit ns` have nanosecond times. The index keeps microseconds, truncated toward
    zero as fs-inventory rollup does (a plain cast rounds to the nearest)."""
    return f"(TIMESTAMPTZ '1970-01-01 00:00:00+00' + to_microseconds(epoch_ns({col}) // 1000))"


_UNITS = {"": 1, "B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12, "KIB": 2**10, "MIB": 2**20, "GIB": 2**30,
          "TIB": 2**40}


def parse_size(value: str) -> int:
    """`8GB`, `512MiB`, `1.5 TB` -> bytes."""
    m = re.fullmatch(r"\s*([0-9.]+)\s*([A-Za-z]*)\s*", value)
    if not m or m.group(2).upper() not in _UNITS:
        raise BuildError(f"not a memory size: {value!r}")
    return int(float(m.group(1)) * _UNITS[m.group(2).upper()])


# Memory per thread reading a scan, as a multiple of the largest row group's uncompressed size (measured: a
# copy of 146 MB row groups grew by about 0.5 GB per thread).
SCAN_THREAD_FACTOR = 6


def _scan_threads(con: duckdb.DuckDBPyConnection, parts: list[Path], memory_limit: str) -> int:
    """How many threads can read the scan within three quarters of the memory limit."""
    files = ", ".join(_sql_str(str(p)) for p in parts)
    cols = ", ".join(_sql_str(c) for c in sorted(REQUIRED_COLUMNS))
    largest = con.execute(f"""
        SELECT max(u) FROM (SELECT sum(total_uncompressed_size) AS u FROM parquet_metadata([{files}])
                            WHERE path_in_schema IN ({cols}) GROUP BY file_name, row_group_id)
    """).fetchone()[0] or 1
    return max(1, int(parse_size(memory_limit) * 0.75 // (SCAN_THREAD_FACTOR * largest)))


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC).replace(tzinfo=None)


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _bucket_case(age_us: str) -> str:
    """Age bucket for an age in whole microseconds. Exact integer arithmetic, so a file exactly on an edge
    is in the older bucket, as in `fs-inventory rollup`; times after the scan started are in the first."""
    edges = [round(e * US_PER_DAY) for e in bucket_edges()]
    whens = " ".join(f"WHEN {age_us} < {e} THEN {i}" for i, e in enumerate(edges))
    return f"CASE WHEN {age_us} IS NULL THEN NULL {whens} ELSE {len(edges)} END"


def _direct_aggregates() -> str:
    """Per-directory totals over a directory's own (non-directory) entries."""
    cols = [
        "count(*)::BIGINT AS files",
        "coalesce(sum(size), 0)::BIGINT AS bytes",
        "coalesce(sum(alloc_bytes), 0)::BIGINT AS alloc",
        "coalesce(sum(size * mday), 0)::DOUBLE AS m_wsum",
        "coalesce(sum(mday), 0)::DOUBLE AS m_fsum",
        "count(mday)::BIGINT AS m_n",
        "coalesce(sum(size * aday), 0)::DOUBLE AS a_wsum",
        "count(aday)::BIGINT AS a_n",
        "min(mtime) AS mtime_min", "max(mtime) AS mtime_max", "max(atime) AS atime_max", "max(ctime) AS ctime_max",
    ]
    cols += [f"count(*) FILTER (WHERE mbk = {i})::BIGINT AS mf{i}" for i in range(NB)]
    cols += [f"coalesce(sum(size) FILTER (WHERE mbk = {i}), 0)::BIGINT AS mb{i}" for i in range(NB)]
    cols += [f"coalesce(sum(size) FILTER (WHERE abk = {i}), 0)::BIGINT AS ab{i}" for i in range(NB)]
    cols += [f"count(*) FILTER (WHERE cat = {i})::BIGINT AS cf{i}" for i in range(NC)]
    cols += [f"coalesce(sum(size) FILTER (WHERE cat = {i}), 0)::BIGINT AS cb{i}" for i in range(NC)]
    return ",\n  ".join(cols)


def _cold_units() -> str:
    """For each age threshold (the start of buckets 1 to NB - 1), the largest self-contained cold folders: every
    file below them is at least that old, and their parent holds a newer one. A cold report for any folder reads
    these, a range of paths, instead of joining every directory with its parent."""
    def young(b: int, a: str) -> str:
        return " + ".join(f"{a}.mf{i}" for i in range(b))

    def cold(b: int, a: str) -> str:
        return f"({a}.files > 0 AND {a}.mtime_max IS NOT NULL AND {young(b, a)} = 0)"

    return " UNION ALL ".join(f"""
        SELECT {b}::UTINYINT AS bucket, d.dir_id, d.depth, d.top_id, d.path, d.files, d.bytes
        FROM dirs d LEFT JOIN dirs p ON p.dir_id = d.parent_id
        WHERE {cold(b, 'd')} AND (p.dir_id IS NULL OR NOT {cold(b, 'p')})""" for b in range(1, NB))


# What `changes` keeps of each scan, per directory (subtree totals, as in `dirs`).
CHANGE_MEASURES = (["files", "dirs", "bytes", "alloc", "m_wsum"] + [f"mf{i}" for i in range(NB)]
                   + [f"mb{i}" for i in range(NB)])


def _compare(con: duckdb.DuckDBPyConnection, previous: Path, root: str, step: Callable) -> dict | None:
    """Join this scan's directories with the previous scan's by path, keeping both scans' totals (`o_` for the
    previous, `n_` for this one): a directory in only one of them is new or removed. Ages are each scan's own,
    measured from its start. Returns what the comparison was with, for `meta`."""
    con.execute(f"ATTACH {_sql_str(str(previous / 'index.duckdb'))} AS prev (READ_ONLY)")
    try:
        meta = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM prev.meta").fetchall()}
        if meta.get("root") != root:
            log.warning("not comparing with %s: its root is %s, not %s", previous, meta.get("root"), root)
            return None
        cols = ", ".join(f"o.{c} AS o_{c}, n.{c} AS n_{c}" for c in CHANGE_MEASURES)
        step("changes", f"""
            CREATE TABLE changes AS
            SELECT coalesce(n.path, o.path) AS path, coalesce(n.depth, o.depth) AS depth,
                   coalesce(n.name, o.name) AS name, n.dir_id IS NOT NULL AS in_new, o.dir_id IS NOT NULL AS in_old,
                   {cols}
            FROM dirs n FULL JOIN prev.dirs o ON o.path = n.path
            ORDER BY depth, path
        """)
        new, removed = con.execute("SELECT count(*) FILTER (WHERE NOT in_old), count(*) FILTER (WHERE NOT in_new) "
                                   "FROM changes").fetchone()
        return {"scan_id": meta.get("scan_id"), "started_at": meta.get("started_at"), "ref_day": meta.get("ref_day"),
                "index_version": meta.get("index_version"), "new_dirs": int(new), "removed_dirs": int(removed)}
    finally:
        con.execute("DETACH prev")


def build_index(
    scan_dir: Path,
    index_dir: Path,
    *,
    memory_limit: str = "8GB",
    threads: int = 0,
    allow_incomplete: bool = False,
    previous: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> BuildResult:
    """Build `index_dir` from the fs-inventory scan in `scan_dir`. Writes to a temporary sibling
    folder and renames it into place only when complete. With `previous`, the index of the same
    filesystem's previous scan, it also records how every directory changed since then."""
    say = progress or (lambda m: log.info(m))
    t0 = time.monotonic()
    steps: dict[str, float] = {}

    scan_dir = scan_dir.resolve()
    manifest = read_manifest(scan_dir)
    check_manifest(manifest, allow_incomplete=allow_incomplete)
    entry_parts = scan_parts(scan_dir, manifest, "entries")
    if not entry_parts:
        raise BuildError(f"{scan_dir} has no entries")
    root = manifest["roots"][0]["path"]
    started_at = _parse_ts(manifest["started_at"])
    ref_us = (started_at - EPOCH) // timedelta(microseconds=1)
    ref_day = ref_us / US_PER_DAY
    prefix = root if root.endswith("/") else root + "/"

    work = index_dir.with_name(index_dir.name + ".tmp")
    if work.exists():
        shutil.rmtree(work)
    (work / "files").mkdir(parents=True)
    (work / "tmp").mkdir()
    stage = work / "stage"

    con = duckdb.connect(str(work / "index.duckdb"))
    con.execute(f"SET memory_limit = {_sql_str(memory_limit)}")
    con.execute(f"SET temp_directory = {_sql_str(str(work / 'tmp'))}")
    # Intermediate tables live in a scratch database that is deleted at the end, so the index file
    # holds only what queries read.
    con.execute(f"ATTACH {_sql_str(str(work / 'scratch.duckdb'))} AS s")
    all_threads = int(threads) or os.cpu_count() or 1
    scan_threads = min(all_threads, _scan_threads(con, entry_parts, memory_limit))
    current = {"threads": 0}

    def use_threads(n: int) -> None:
        if n != current["threads"]:
            con.execute(f"SET threads = {n}")
            current["threads"] = n

    def step(name: str, sql: str, params: list | None = None, *, clean: Path | None = None) -> None:
        s = time.monotonic()
        while True:
            try:
                con.execute(sql, params or [])
                break
            except duckdb.OutOfMemoryException:
                # The estimate behind scan_threads was too low for this scan: halve the threads and try again.
                # A failed statement leaves nothing behind but the files of a COPY, which `clean` names.
                if current["threads"] <= 1:
                    raise
                log.warning("%s ran out of memory with %d threads; retrying with %d", name, current["threads"],
                            current["threads"] // 2)
                use_threads(current["threads"] // 2)
                if clean is not None:
                    shutil.rmtree(clean, ignore_errors=True)
        steps[name] = steps.get(name, 0.0) + time.monotonic() - s

    con.execute(f"CREATE TEMP VIEW entries AS SELECT * FROM {parquet_source(entry_parts)}")
    # Reading the scan, each thread decodes whole row groups of about a million entries, so memory, not
    # cores, sets how many threads read it. Pinax's own staged files have small row groups and use them all.
    use_threads(scan_threads)
    say(f"Reading the scan with {scan_threads} of {all_threads} threads")

    # ---- Top-level folders and the extension map ----
    say("Finding top-level folders")
    step("tops", """
        CREATE TABLE tops AS
        SELECT row_number() OVER (ORDER BY path)::INTEGER AS top_id, name, path, dir_id
        FROM entries WHERE kind = 'dir' AND depth = 1
    """)
    con.execute("CREATE TEMP TABLE ext_cat (ext VARCHAR PRIMARY KEY, cat UTINYINT)")
    con.executemany("INSERT INTO ext_cat VALUES (?, ?)", list(extension_map().items()))

    # ---- Stage every non-directory entry with its top-level folder, type, category, and age buckets ----
    say("Classifying files")
    comp = ", ".join(_sql_str(s) for s in COMPRESSION_SUFFIXES)
    rel = f"substr(path, {len(prefix) + 1})"

    ns_times = _is_ns(con, "SELECT mtime FROM entries")

    def us(col: str, alias: str | None = None, ns: bool = ns_times) -> str:
        return f"{_to_us(col) if ns else col} AS {alias or col.split('.')[-1]}"

    groups = min(FILE_GROUPS, con.execute("SELECT count(*) FROM tops").fetchone()[0] + 1)
    step("stage", f"""
        COPY (
          WITH e AS (
            SELECT path, parent_id, name, depth, kind, size, alloc_bytes, uid, gid, {us('mtime')}, {us('atime')},
                   {us('ctime')}, nlink, name_flags, extension,
                   CASE WHEN extension IN ({comp})
                        THEN coalesce(regexp_extract(lower(name), '\\.([^./]{{1,16}})\\.[^./]+$', 1), '')
                        ELSE '' END AS inner_ext,
                   CASE WHEN depth >= 2 THEN split_part({rel}, '/', 1) END AS top_name
            FROM entries WHERE kind <> 'dir'
          )
          SELECT coalesce(t.top_id, 0)::INTEGER AS top_id, (coalesce(t.top_id, 0) % {groups})::INTEGER AS grp,
                 e.path, e.parent_id, e.name, e.depth::INTEGER AS depth,
                 CASE WHEN e.inner_ext <> '' THEN e.inner_ext || '.' || e.extension ELSE e.extension END AS ext,
                 coalesce(c.cat, {OTHER})::UTINYINT AS cat,
                 e.kind, e.size, e.alloc_bytes, e.uid, e.gid, e.mtime, e.atime, e.ctime, e.nlink, e.name_flags
          FROM e
          LEFT JOIN tops t ON t.name = e.top_name
          LEFT JOIN ext_cat c ON c.ext = CASE WHEN e.inner_ext <> '' THEN e.inner_ext ELSE e.extension END
        ) TO {_sql_str(str(stage))} (FORMAT parquet, PARTITION_BY (grp), COMPRESSION zstd)
    """, clean=stage)

    # ---- Directories ----
    say("Reading directories")
    step("dirbase", f"""
        CREATE TABLE s.dirbase AS
        SELECT e.dir_id, e.parent_id, e.depth::INTEGER AS depth,
               CASE WHEN e.depth = 0 THEN 0 ELSE coalesce(t.top_id, 0) END::INTEGER AS top_id,
               e.path, e.name, e.uid, e.gid, {us('e.mtime', 'self_mtime')}, e.error, e.is_mount_point, e.dir_entries,
               -- Not read completely: an error, or never listed (a mount point not entered, --max-depth). A
               -- revisit (--detect-cycles) isn't missing anything: its contents are counted where first found.
               (NOT coalesce(starts_with(e.error, 'revisit'), false)
                AND (e.error IS NOT NULL OR e.dir_entries IS NULL))::INTEGER AS incomplete_self
        FROM entries e
        LEFT JOIN tops t ON e.depth >= 1 AND t.name = split_part(substr(e.path, {len(prefix) + 1}), '/', 1)
        WHERE e.kind = 'dir'
        ORDER BY e.depth
    """)
    root_rows = con.execute("SELECT count(*) FROM s.dirbase WHERE depth = 0").fetchone()[0]
    if root_rows != 1:
        raise BuildError(f"expected one root directory row, found {root_rows}")

    use_threads(all_threads)

    # ---- Per group of top-level folders: sort files by path, and aggregate by directory, type, and owner ----
    say("Sorting files and summarizing directories")
    mday = "epoch(mtime) / 86400.0"
    aday = "epoch(atime) / 86400.0"
    empty_src = ("SELECT NULL::BIGINT AS parent_id, NULL::BIGINT AS size, NULL::BIGINT AS alloc_bytes, "
                 "NULL::DOUBLE AS mday, NULL::DOUBLE AS aday, NULL::TIMESTAMPTZ AS mtime, NULL::TIMESTAMPTZ AS atime, "
                 "NULL::TIMESTAMPTZ AS ctime, NULL::INTEGER AS mbk, NULL::INTEGER AS abk, NULL::INTEGER AS cat WHERE false")
    con.execute(f"CREATE TABLE s.direct AS SELECT parent_id AS dir_id, {_direct_aggregates()} "
                f"FROM ({empty_src}) GROUP BY parent_id")
    con.execute("""
        CREATE TABLE s.ext_raw (dir_id BIGINT, ext VARCHAR, cat UTINYINT, files BIGINT, bytes BIGINT, alloc BIGINT,
            m_wsum DOUBLE, mtime_min TIMESTAMPTZ, mtime_max TIMESTAMPTZ, atime_max TIMESTAMPTZ)
    """)
    con.execute("""
        CREATE TABLE s.owner_raw (dir_id BIGINT, uid BIGINT, files BIGINT, bytes BIGINT, alloc BIGINT,
            m_wsum DOUBLE, mtime_max TIMESTAMPTZ)
    """)
    con.execute("CREATE TABLE name_flags (top_id INTEGER, flag INTEGER, entries BIGINT)")
    con.execute("CREATE TABLE file_parts (top_id INTEGER, grp INTEGER, files BIGINT, bytes BIGINT)")

    partitions = sorted(
        (int(p.name.split("=", 1)[1]), p) for p in stage.iterdir() if p.is_dir() and p.name.startswith("grp=")
    )
    for n, (grp, part) in enumerate(partitions, 1):
        src = f"read_parquet({_sql_str(str(part / '*.parquet'))}, hive_partitioning = false)"
        out = work / "files" / f"g{grp:06d}"
        step("sort", f"""
            COPY (SELECT * FROM {src} ORDER BY path)
            TO {_sql_str(str(out))} (FORMAT parquet, COMPRESSION zstd, FILE_SIZE_BYTES '512MB',
                                     FILENAME_PATTERN 'part-{{i}}')
        """, clean=out)
        sorted_src = f"read_parquet({_sql_str(str(out / '*.parquet'))})"
        with_ages = f"""
            SELECT *, {mday} AS mday, {aday} AS aday,
                   {_bucket_case(f'({ref_us} - epoch_us(mtime))')} AS mbk,
                   {_bucket_case(f'({ref_us} - epoch_us(atime))')} AS abk
            FROM {sorted_src}
        """
        step("direct", f"INSERT INTO s.direct SELECT parent_id AS dir_id, {_direct_aggregates()} "
                       f"FROM ({with_ages}) GROUP BY parent_id")
        step("by_type", f"""
            INSERT INTO s.ext_raw
            SELECT parent_id, ext, cat, count(*), coalesce(sum(size), 0), coalesce(sum(alloc_bytes), 0),
                   coalesce(sum(size * {mday}), 0), min(mtime), max(mtime), max(atime)
            FROM {sorted_src} GROUP BY parent_id, ext, cat
        """)
        step("by_owner", f"""
            INSERT INTO s.owner_raw
            SELECT parent_id, uid, count(*), coalesce(sum(size), 0), coalesce(sum(alloc_bytes), 0),
                   coalesce(sum(size * {mday}), 0), max(mtime)
            FROM {sorted_src} GROUP BY parent_id, uid
        """)
        step("names", f"""
            INSERT INTO name_flags
            SELECT top_id, flag, count(*) FROM (
                SELECT top_id, unnest([1,2,4,8,16,32,64,128,256,512,1024,2048]) AS flag, name_flags FROM {sorted_src}
                WHERE name_flags <> 0
            ) WHERE (name_flags & flag) <> 0 GROUP BY top_id, flag
        """)
        step("parts", f"INSERT INTO file_parts SELECT top_id, {grp}, count(*), coalesce(sum(size), 0) "
                      f"FROM {sorted_src} GROUP BY top_id")
        if n % 50 == 0 or n == len(partitions):
            say(f"  {n} of {len(partitions)} groups of top-level folders")
    shutil.rmtree(stage, ignore_errors=True)
    # Directory names with flags count too.
    use_threads(scan_threads)
    step("names", """
        INSERT INTO name_flags
        SELECT top_id, flag, count(*) FROM (
            SELECT d.top_id, unnest([1,2,4,8,16,32,64,128,256,512,1024,2048]) AS flag, e.name_flags
            FROM entries e JOIN s.dirbase d USING (dir_id) WHERE e.kind = 'dir' AND e.name_flags <> 0
        ) WHERE (name_flags & flag) <> 0 GROUP BY ALL
    """)
    use_threads(all_threads)

    # ---- Subtree totals, bottom-up one depth level at a time ----
    say("Rolling up subtree totals")
    step("join_direct", f"""
        CREATE TABLE s.base AS
        SELECT b.dir_id, b.parent_id, b.depth, b.incomplete_self,
               {", ".join(f"coalesce(x.{c}, 0) AS {c}" for c in ADDITIVE)},
               {", ".join(f"x.{c}" for c in EXTREMES)}
        FROM s.dirbase b LEFT JOIN s.direct x USING (dir_id)
        ORDER BY b.depth
    """)
    con.execute(f"""
        CREATE TABLE s.sub AS SELECT dir_id, parent_id, depth, 0::BIGINT AS dirs, 0::BIGINT AS incomplete,
            {", ".join(ADDITIVE)}, {", ".join(EXTREMES)} FROM s.base WHERE false
    """)
    max_depth = con.execute("SELECT max(depth) FROM s.dirbase").fetchone()[0]
    child_cols = ", ".join(
        ["sum(dirs + 1) AS dirs", "sum(incomplete) AS incomplete"]
        + [f"sum({c}) AS {c}" for c in ADDITIVE]
        + [f"{fn}({c}) AS {c}" for c, fn in EXTREMES.items()]
    )
    merged = ", ".join(
        ["coalesce(c.dirs, 0)", "b.incomplete_self + coalesce(c.incomplete, 0)"]
        + [f"b.{c} + coalesce(c.{c}, 0)" for c in ADDITIVE]
        + [f"{'least' if fn == 'min' else 'greatest'}(b.{c}, c.{c})" for c, fn in EXTREMES.items()]
    )
    for d in range(max_depth, -1, -1):
        step("rollup", f"""
            INSERT INTO s.sub
            SELECT b.dir_id, b.parent_id, b.depth, {merged}
            FROM s.base b
            LEFT JOIN (SELECT parent_id, {child_cols} FROM s.sub WHERE depth = {d + 1} GROUP BY parent_id) c
                   ON c.parent_id = b.dir_id
            WHERE b.depth = {d}
        """)

    say("Writing the directory table")
    step("dirs", f"""
        CREATE TABLE dirs AS
        SELECT b.dir_id, b.parent_id, b.depth, b.top_id, b.path, b.name, b.uid, b.gid, b.self_mtime, b.error,
               b.is_mount_point, b.dir_entries,
               coalesce(x.files, 0) AS d_files, coalesce(x.bytes, 0) AS d_bytes,
               x.mtime_min AS d_mtime_min, x.mtime_max AS d_mtime_max, x.atime_max AS d_atime_max,
               u.dirs, u.incomplete, {", ".join(f"u.{c}" for c in ADDITIVE)}, {", ".join(f"u.{c}" for c in EXTREMES)}
        FROM s.dirbase b JOIN s.sub u USING (dir_id) LEFT JOIN s.direct x USING (dir_id)
        ORDER BY b.depth, b.path
    """)

    say("Finding cold folders")
    step("cold", f"CREATE TABLE cold_units AS SELECT * FROM ({_cold_units()}) ORDER BY bucket, path")

    compared = None
    if previous is not None:
        say("Comparing with the previous scan")
        compared = _compare(con, Path(previous), root, step)

    say("Writing type and owner tables")
    step("dir_ext", """
        CREATE TABLE dir_ext AS
        SELECT d.path, d.depth, d.top_id, r.ext, r.cat, r.files, r.bytes, r.alloc, r.m_wsum, r.mtime_min, r.mtime_max,
               r.atime_max
        FROM s.ext_raw r JOIN s.dirbase d USING (dir_id) ORDER BY d.path
    """)
    step("dir_owner", """
        CREATE TABLE dir_owner AS
        SELECT d.path, d.depth, d.top_id, r.uid, r.files, r.bytes, r.alloc, r.m_wsum, r.mtime_max
        FROM s.owner_raw r JOIN s.dirbase d USING (dir_id) ORDER BY d.path
    """)
    step("top_ext", """
        CREATE TABLE top_ext AS
        SELECT top_id, ext, cat, sum(files)::BIGINT AS files, sum(bytes)::BIGINT AS bytes, sum(alloc)::BIGINT AS alloc,
               sum(m_wsum) AS m_wsum, min(mtime_min) AS mtime_min, max(mtime_max) AS mtime_max,
               max(atime_max) AS atime_max
        FROM dir_ext GROUP BY top_id, ext, cat ORDER BY top_id
    """)
    step("top_owner", """
        CREATE TABLE top_owner AS
        SELECT top_id, uid, sum(files)::BIGINT AS files, sum(bytes)::BIGINT AS bytes, sum(alloc)::BIGINT AS alloc,
               sum(m_wsum) AS m_wsum, max(mtime_max) AS mtime_max
        FROM dir_owner GROUP BY top_id, uid ORDER BY top_id
    """)

    # ---- Owner names, errors ----
    for table, key in (("users", "uid"), ("groups", "gid")):
        parts = scan_parts(scan_dir, manifest, table)
        if parts:
            con.execute(f"CREATE TABLE {table} AS SELECT {key}, name FROM {parquet_source(parts)}")
        else:
            con.execute(f"CREATE TABLE {table} ({key} BIGINT, name VARCHAR)")
    error_parts = scan_parts(scan_dir, manifest, "errors")
    if error_parts:
        step("errors", f"""
            CREATE TABLE errors AS
            SELECT r.path, r.op, r.errno, r.errno_name, r.message,
                   {us('r.time', ns=_is_ns(con, f"SELECT time FROM {parquet_source(error_parts)}"))},
                   CASE WHEN r.path = {_sql_str(root)} THEN 0 ELSE coalesce(t.top_id, 0) END AS top_id
            FROM {parquet_source(error_parts)} r
            LEFT JOIN tops t ON t.name = split_part(substr(r.path, {len(prefix) + 1}), '/', 1)
        """)
    else:
        con.execute("CREATE TABLE errors (path VARCHAR, op VARCHAR, errno INTEGER, errno_name VARCHAR, "
                    "message VARCHAR, time TIMESTAMPTZ, top_id INTEGER)")

    # ---- Totals and metadata ----
    totals_row = con.execute(
        "SELECT files, dirs, bytes, alloc, incomplete FROM dirs WHERE depth = 0"
    ).fetchone()
    errors = con.execute("SELECT count(*) FROM errors").fetchone()[0]
    totals = {
        "files": int(totals_row[0]), "dirs": int(totals_row[1]) + 1, "bytes": int(totals_row[2]),
        "alloc_bytes": int(totals_row[3]), "incomplete_dirs": int(totals_row[4]), "errors": int(errors),
        "entries": int(manifest.get("totals", {}).get("entries", 0)),
        "tops": con.execute("SELECT count(*) FROM tops").fetchone()[0],
    }
    meta = {
        "index_version": INDEX_VERSION,
        "scan_id": manifest.get("scan_id"),
        "scan_dir": str(scan_dir),
        "root": root,
        "started_at": started_at.isoformat() + "Z",
        "ref_day": ref_day,
        "ref_us": ref_us,
        "tool_version": manifest.get("tool_version"),
        "schema_version": manifest.get("schema_version"),
        "built_at": datetime.now(UTC).isoformat(),
        "dimensions": describe(),
        "build": {"threads": all_threads, "scan_threads": scan_threads, "memory_limit": memory_limit},
        "totals": totals,
        "previous": compared,
    }
    con.execute("CREATE TABLE meta (key VARCHAR PRIMARY KEY, value JSON)")
    con.executemany("INSERT INTO meta VALUES (?, ?)", [(k, json.dumps(v)) for k, v in meta.items()])
    con.execute("DETACH s")
    (work / "scratch.duckdb").unlink(missing_ok=True)
    con.execute("CHECKPOINT")
    con.close()

    shutil.rmtree(work / "tmp", ignore_errors=True)
    if index_dir.exists():
        shutil.rmtree(index_dir)
    work.rename(index_dir)
    seconds = time.monotonic() - t0
    say(f"Index built in {seconds:.1f}s")
    return BuildResult(index_dir=index_dir, manifest=manifest, root=root, started_at=started_at, seconds=seconds,
                       totals=totals, steps={k: round(v, 3) for k, v in steps.items()}, compared=compared)
