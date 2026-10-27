# SPDX-License-Identifier: Apache-2.0
"""Write a synthetic fs-inventory scan of any size, for testing Pinax at scale without a real filesystem.

The scan has the fs-inventory schema (version 3) and a manifest, and models a research filesystem:
top-level lab folders, projects of different ages, runs and batches, and a realistic mix of file types
and sizes. It is generated entirely inside DuckDB, so ten million entries take well under a minute.

`--wide N` adds the shapes real filesystems have and the model doesn't: `lab_wide/files` holding N files in one
folder and `lab_wide/folders` holding N/10 subfolders; `--deep N` adds a chain of N nested folders under
`lab_deep`, with a file at the bottom.

`--evolve SCAN` writes the scan as it might look `--days` later instead (see `evolve`): some projects deleted,
some folders grown, some files rewritten, and a new top-level folder, for checking and timing comparisons.

    uv run python bench/synth_scan.py --files 10000000 -o /scratch/synth-10m
    uv run python bench/synth_scan.py --evolve /scratch/synth-10m -o /scratch/synth-10m-next --days 30
"""

import argparse
import json
import shutil
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

COLUMNS = [
    "path", "parent_path", "name", "extension", "path_raw", "name_flags", "kind", "depth", "dir_id", "parent_id", "dev",
    "ino", "nlink", "uid", "gid", "mode", "size", "alloc_bytes", "atime", "mtime", "ctime", "btime", "rdev",
    "dir_entries", "dir_files", "dir_subdirs", "dir_bytes", "dir_alloc_bytes", "is_mount_point", "time_flags", "error",
    "symlink_target", "symlink_target_raw",
]
# extension, weight, typical size in bytes
TYPES = [("fastq.gz", 14, 4e9), ("bam", 6, 3e10), ("bai", 6, 8e6), ("vcf.gz", 4, 2e8), ("tif", 12, 3e8),
         ("csv", 10, 2e7), ("txt", 8, 5e4), ("h5ad", 2, 5e9), ("log", 10, 1e6), ("py", 6, 2e4), ("png", 10, 3e5),
         ("raw", 3, 2e9), ("pdf", 4, 3e6), ("tar.gz", 1, 5e10), ("json", 4, 1e5)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--files", type=int, default=10_000_000, help="Number of files (directories are added)")
    ap.add_argument("--tops", type=int, default=200, help="Top-level folders")
    ap.add_argument("--root", default="/synth/research", help="Root path recorded in the scan")
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--part-rows", type=int, default=5_000_000)
    ap.add_argument("--wide", type=int, default=0, help="Files in one folder (and a tenth as many subfolders in another)")
    ap.add_argument("--deep", type=int, default=0, help="Depth of a chain of nested folders")
    ev = ap.add_argument_group("evolving a scan")
    ev.add_argument("--evolve", type=Path, metavar="SCAN", help="Write SCAN as it might look later, instead")
    ev.add_argument("--days", type=float, default=30, help="Days later")
    ev.add_argument("--remove", type=float, default=0.02, help="Share of projects deleted")
    ev.add_argument("--grow", type=float, default=0.05, help="Share of folders with files that gain a fifth more")
    ev.add_argument("--touch", type=float, default=0.01, help="Share of files rewritten")
    ev.add_argument("--new-tops", type=int, default=1, help="New top-level folders")
    args = ap.parse_args()
    if args.evolve:
        evolve(args.evolve, args.output, days=args.days, remove=args.remove, grow=args.grow, touch=args.touch,
               new_tops=args.new_tops)
        return

    t0 = time.monotonic()
    out = args.output
    (out / "entries").mkdir(parents=True, exist_ok=False)
    T, P, S, L = args.tops, 20, 10, 5  # tops, projects per top, runs per project, batches per run
    leaves = T * P * S * L
    per_leaf = max(1, args.files // leaves)
    nfiles = leaves * per_leaf
    started = datetime.now(UTC)
    ref = started.timestamp()
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")
    weights = sum(w for _, w, _ in TYPES)
    cum, case = 0, []
    for ext, w, size in TYPES:
        cum += w
        case.append(f"WHEN u < {cum / weights} THEN {{'ext': '{ext}', 'size': {size}}}")
    pick = "CASE " + " ".join(case) + " ELSE {'ext': 'txt', 'size': 5e4} END"
    root = args.root.rstrip("/")
    b2, b3, b4 = 2 + T, 2 + T + T * P, 2 + T + T * P + T * P * S

    common = """NULL::BLOB AS path_raw, 0 AS name_flags, {kind} AS kind, {depth} AS depth, {dir_id} AS dir_id,
        {parent_id} AS parent_id, 1::UBIGINT AS dev, ({ino})::UBIGINT AS ino, 1::BIGINT AS nlink,
        (20000 + {uid})::BIGINT AS uid, (30000 + {gid})::BIGINT AS gid, {mode} AS mode"""
    dirs = f"""
      WITH lv AS (
        SELECT 0 AS depth, 0 AS t, 0 AS p, 0 AS s, 0 AS l, 1::BIGINT AS dir_id, 0::BIGINT AS parent_id,
               '{root}' AS path, '' AS parent_path, '{root.rsplit('/', 1)[-1]}' AS name,
               {T + bool(args.wide) + bool(args.deep)} AS kids
        UNION ALL SELECT 1, t, 0, 0, 0, 2 + t, 1, '{root}/' || printf('lab_%04d', t), '{root}',
               printf('lab_%04d', t), {P} FROM range({T}) r(t)
        UNION ALL SELECT 2, i // {P}, i % {P}, 0, 0, {b2} + i, 2 + i // {P},
               '{root}/' || printf('lab_%04d/project_%03d', i // {P}, i % {P}), '{root}/' || printf('lab_%04d', i // {P}),
               printf('project_%03d', i % {P}), {S} FROM range({T * P}) r(i)
        UNION ALL SELECT 3, i // ({P * S}), (i // {S}) % {P}, i % {S}, 0, {b3} + i, {b2} + i // {S},
               '{root}/' || printf('lab_%04d/project_%03d/run_%03d', i // {P * S}, (i // {S}) % {P}, i % {S}),
               '{root}/' || printf('lab_%04d/project_%03d', i // {P * S}, (i // {S}) % {P}),
               printf('run_%03d', i % {S}), {L} FROM range({T * P * S}) r(i)
        UNION ALL SELECT 4, i // ({P * S * L}), (i // {S * L}) % {P}, (i // {L}) % {S}, i % {L}, {b4} + i, {b3} + i // {L},
               '{root}/' || printf('lab_%04d/project_%03d/run_%03d/batch_%03d', i // {P * S * L}, (i // {S * L}) % {P}, (i // {L}) % {S}, i % {L}),
               '{root}/' || printf('lab_%04d/project_%03d/run_%03d', i // {P * S * L}, (i // {S * L}) % {P}, (i // {L}) % {S}),
               printf('batch_%03d', i % {L}), {per_leaf} FROM range({leaves}) r(i)
      )
      SELECT path, parent_path, name, NULL::VARCHAR AS extension,
        {common.format(kind="'dir'", depth="depth", dir_id="dir_id", parent_id="parent_id", ino="dir_id", uid="t % 50", gid="t", mode="16877")},
        4096::BIGINT AS size, 4096::BIGINT AS alloc_bytes,
        to_timestamp({ref}) AS atime, to_timestamp({ref} - 86400 * 30) AS mtime, to_timestamp({ref} - 86400 * 30) AS ctime,
        NULL::TIMESTAMPTZ AS btime, NULL::UBIGINT AS rdev, kids::BIGINT AS dir_entries,
        (CASE WHEN depth = 4 THEN kids ELSE 0 END)::BIGINT AS dir_files, (CASE WHEN depth = 4 THEN 0 ELSE kids END)::BIGINT AS dir_subdirs,
        0::BIGINT AS dir_bytes, 0::BIGINT AS dir_alloc_bytes, false AS is_mount_point, 0 AS time_flags, NULL::VARCHAR AS error,
        NULL::VARCHAR AS symlink_target, NULL::BLOB AS symlink_target_raw
      FROM lv
    """
    con.execute(f"COPY ({dirs}) TO '{out / 'entries' / 'part-dirs.parquet'}' (FORMAT parquet, COMPRESSION zstd)")

    # Files: each project has an age (0 to 14 years); its files spread over the year after it started.
    files = f"""
      WITH f AS (
        SELECT i, i // {per_leaf} AS leaf, (hash(i) % 1000000) / 1e6 AS u, (hash(i * 7 + 3) % 1000000) / 1e6 AS v,
               (hash(i * 13 + 5) % 1000000) / 1e6 AS w FROM range({nfiles}) r(i)
      ), g AS (
        SELECT *, leaf // {P * S * L} AS t, (leaf // {S * L}) % {P} AS p, (leaf // {L}) % {S} AS s, leaf % {L} AS l,
               {pick} AS ty FROM f
      ), h AS (
        SELECT *, (hash(t * 1000 + p) % 1400) / 100.0 AS age_years FROM g
      )
      SELECT '{root}/' || printf('lab_%04d/project_%03d/run_%03d/batch_%03d/file_%08d.', t, p, s, l, i) || ty.ext AS path,
        '{root}/' || printf('lab_%04d/project_%03d/run_%03d/batch_%03d', t, p, s, l) AS parent_path,
        printf('file_%08d.', i) || ty.ext AS name, split_part(ty.ext, '.', -1) AS extension,
        {common.format(kind="'file'", depth="5", dir_id="NULL::BIGINT", parent_id=f"{b4} + leaf", ino="1000000000 + i", uid="(t * 3 + p) % 50", gid="t", mode="33188")},
        (ty.size * (0.2 + 1.6 * v))::BIGINT AS size, (ty.size * (0.2 + 1.6 * v))::BIGINT AS alloc_bytes,
        to_timestamp({ref} - 86400 * 365.25 * greatest(0, age_years - w * 0.9) + 86400 * 30 * u) AS atime,
        to_timestamp({ref} - 86400 * 365.25 * greatest(0, age_years - w * 0.9)) AS mtime,
        to_timestamp({ref} - 86400 * 365.25 * greatest(0, age_years - w * 0.9)) AS ctime,
        NULL::TIMESTAMPTZ AS btime, NULL::UBIGINT AS rdev, NULL::BIGINT AS dir_entries, NULL::BIGINT AS dir_files,
        NULL::BIGINT AS dir_subdirs, NULL::BIGINT AS dir_bytes, NULL::BIGINT AS dir_alloc_bytes, false AS is_mount_point,
        0 AS time_flags, NULL::VARCHAR AS error, NULL::VARCHAR AS symlink_target, NULL::BLOB AS symlink_target_raw
      FROM h
    """
    con.execute(f"COPY ({files}) TO '{out / 'entries'}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 1048576, "
                f"FILE_SIZE_BYTES '1GB', FILENAME_PATTERN 'part-files-{{i}}', OVERWRITE_OR_IGNORE)")
    if args.wide or args.deep:
        _shapes(con, out, root, ref, b4 + leaves, args.wide, args.deep)
    parts = []
    total_rows = 0
    for p in sorted((out / "entries").glob("*.parquet")):
        rows = con.execute(f"SELECT count(*) FROM read_parquet('{p}')").fetchone()[0]
        total_rows += rows
        parts.append({"file": f"entries/{p.name}", "rows": rows, "bytes": p.stat().st_size})
    total_bytes, nfiles = con.execute(f"SELECT sum(size), count(*) FROM read_parquet('{out / 'entries' / '*.parquet'}') "
                                      "WHERE kind = 'file'").fetchone()
    ndirs = total_rows - nfiles
    manifest = {
        "tool": "fs-inventory", "tool_version": "1.0.0", "schema_version": 3, "scan_id": str(uuid.uuid4()),
        "status": "complete", "host": "synthetic", "roots": [{"path": root, "path_b64": None}], "format": "parquet",
        "compression": "zstd", "columns": COLUMNS, "started_at": started.isoformat().replace("+00:00", "Z"),
        "finished_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "duration_seconds": time.monotonic() - t0,
        "parts": {"entries": parts},
        "totals": {"entries": total_rows, "by_kind": {"dir": ndirs, "file": nfiles}, "bytes": int(total_bytes),
                   "dirs_read": ndirs, "errors": 0, "vanished": 0, "revisits": 0, "spilled": 0},
        "synthetic": True,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"Wrote {total_rows:,} entries ({nfiles:,} files, {ndirs:,} directories) to {out} "
          f"in {time.monotonic() - t0:.1f}s")


def _shapes(con: duckdb.DuckDBPyConnection, out: Path, root: str, ref: float, first_id: int, wide: int, deep: int) -> None:
    """The wide folders and the deep chain, as entries with the same columns as the rest of the scan."""
    rows = []
    if wide:
        w, m = first_id, wide // 10
        rows += [
            f"SELECT '{root}/lab_wide', '{root}', 'lab_wide', NULL, 'dir', 1, {w}, 1, 2, 4096, 30",
            f"SELECT '{root}/lab_wide/files', '{root}/lab_wide', 'files', NULL, 'dir', 2, {w + 1}, {w}, {wide}, 4096, 30",
            f"SELECT '{root}/lab_wide/folders', '{root}/lab_wide', 'folders', NULL, 'dir', 2, {w + 2}, {w}, {m}, 4096, 30",
            f"""SELECT '{root}/lab_wide/folders/' || printf('f_%07d', i), '{root}/lab_wide/folders', printf('f_%07d', i),
                NULL, 'dir', 3, {w + 3} + i, {w + 2}, 1, 4096, 30 FROM range({m}) r(i)""",
            f"""SELECT '{root}/lab_wide/files/' || printf('w_%08d.dat', i), '{root}/lab_wide/files', printf('w_%08d.dat', i),
                'dat', 'file', 3, NULL, {w + 1}, NULL, 1000 + i % 5000, i % 5000 FROM range({wide}) r(i)""",
            f"""SELECT '{root}/lab_wide/folders/' || printf('f_%07d', i) || '/x.dat', '{root}/lab_wide/folders/' ||
                printf('f_%07d', i), 'x.dat', 'dat', 'file', 4, NULL, {w + 3} + i, NULL, 100, i % 4000 FROM range({m}) r(i)""",
        ]
        first_id = w + 3 + m
    if deep:
        d = first_id
        chain = "array_to_string(list_transform(range(1, k + 1), lambda x: printf('l%04d', x)), '/')"
        rows += [
            f"SELECT '{root}/lab_deep', '{root}', 'lab_deep', NULL, 'dir', 1, {d}, 1, 1, 4096, 30",
            f"""SELECT '{root}/lab_deep/' || {chain}, '{root}/lab_deep' || CASE WHEN k > 1 THEN '/' ||
                array_to_string(list_transform(range(1, k), lambda x: printf('l%04d', x)), '/') ELSE '' END, printf('l%04d', k),
                NULL, 'dir', 1 + k, {d} + k, {d} + k - 1, 1, 4096, 30
                FROM range(1, {deep + 1}) r(k)""",
            f"""SELECT '{root}/lab_deep/' || array_to_string(list_transform(range(1, {deep + 1}), lambda x: printf('l%04d', x)), '/')
                || '/bottom.bam', '{root}/lab_deep/' || array_to_string(list_transform(range(1, {deep + 1}),
                lambda x: printf('l%04d', x)), '/'), 'bottom.bam', 'bam', 'file', {deep + 2}, NULL, {d + deep}, NULL, 5000, 900""",
        ]
    src = " UNION ALL ".join(f"({r})" for r in rows)
    con.execute(f"""
        COPY (
          SELECT path, parent_path, name, extension::VARCHAR AS extension, NULL::BLOB AS path_raw, 0 AS name_flags, kind,
                 depth::INTEGER AS depth, dir_id::BIGINT AS dir_id, parent_id::BIGINT AS parent_id, 1::UBIGINT AS dev,
                 (4000000000 + row_number() OVER ())::UBIGINT AS ino, 1::BIGINT AS nlink, 20000::BIGINT AS uid,
                 30000::BIGINT AS gid, CASE WHEN kind = 'dir' THEN 16877 ELSE 33188 END AS mode, size::BIGINT AS size,
                 size::BIGINT AS alloc_bytes, to_timestamp({ref} - 86400 * age_days) AS atime,
                 to_timestamp({ref} - 86400 * age_days) AS mtime, to_timestamp({ref} - 86400 * age_days) AS ctime,
                 NULL::TIMESTAMPTZ AS btime, NULL::UBIGINT AS rdev, kids::BIGINT AS dir_entries, NULL::BIGINT AS dir_files,
                 NULL::BIGINT AS dir_subdirs, NULL::BIGINT AS dir_bytes, NULL::BIGINT AS dir_alloc_bytes,
                 false AS is_mount_point, 0 AS time_flags, NULL::VARCHAR AS error, NULL::VARCHAR AS symlink_target,
                 NULL::BLOB AS symlink_target_raw
          FROM ({src}) s(path, parent_path, name, extension, kind, depth, dir_id, parent_id, kids, size, age_days)
        ) TO '{out / 'entries' / 'part-shapes.parquet'}' (FORMAT parquet, COMPRESSION zstd)
    """)


def evolve(src: Path, out: Path, *, days: float, remove: float, grow: float, touch: float, new_tops: int) -> dict:
    """Write `out`, the scan `src` as it might look `days` later, and return what changed:
    - `remove` of its projects (folders two levels below the root) deleted, with everything in them;
    - `grow` of its folders holding files gain about a fifth more files, copies of some of theirs;
    - `touch` of its files rewritten, with a new size and modification time;
    - `new_tops` top-level folders added, each a copy of an existing one.
    New and rewritten files are dated within the `days`; everything else is unchanged, and so `days` older.
    Folder entry counts are recounted; other tables (users, groups, errors) are copied. Each generation names
    its new files and folders apart (`g1_`, `g2_`, ...), so a scan can be evolved again and again."""
    t0 = time.monotonic()
    manifest = json.loads((src / "manifest.json").read_text())
    gen = manifest.get("generation", 0) + 1
    root = manifest["roots"][0]["path"].rstrip("/")
    started = datetime.fromisoformat(manifest["started_at"]) + timedelta(days=days)
    ref, span = started.timestamp(), days * 86400
    (out / "entries").mkdir(parents=True, exist_ok=False)
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")
    parts = ", ".join(f"'{src / p['file']}'" for p in manifest["parts"]["entries"])
    con.execute(f"CREATE VIEW e AS SELECT * FROM read_parquet([{parts}])")
    types = dict(con.execute("SELECT column_name, column_type FROM (DESCRIBE e)").fetchall())
    max_id, max_ino = con.execute("SELECT max(dir_id), max(ino) FROM e").fetchone()
    salt = f"'#{gen}'"

    def chance(key: str, share: float, why: str) -> str:
        return f"hash({key} || {salt} || '{why}') % 1000000 < {round(share * 1e6)}"

    def u(path: str) -> str:  # a number in [0, 1) for each entry
        return f"(hash({path} || {salt}) % 1000000) / 1e6"

    def new_time(path: str) -> str:
        return f"to_timestamp({ref} - {span} * {u(path)})"

    nroot = len(root.split("/"))
    con.execute(f"""CREATE TEMP TABLE removed AS SELECT path FROM e
                    WHERE kind = 'dir' AND depth = 2 AND {chance('path', remove, 'remove')}""")
    con.execute(f"""CREATE TEMP TABLE grown AS SELECT DISTINCT parent_id AS dir_id FROM e
                    WHERE kind = 'file' AND {chance('parent_id::VARCHAR', grow, 'grow')}""")
    con.execute(f"""CREATE TEMP TABLE tops AS SELECT path, name, row_number() OVER (ORDER BY hash(path || {salt})) AS j
                    FROM e WHERE kind = 'dir' AND depth = 1 ORDER BY hash(path || {salt}) LIMIT {new_tops}""")
    con.execute(f"""CREATE TEMP TABLE kept AS
        SELECT * REPLACE (
            CASE WHEN touched THEN {new_time('path')} ELSE mtime END AS mtime,
            CASE WHEN touched THEN {new_time('path')} ELSE ctime END AS ctime,
            CASE WHEN touched THEN {new_time('path')} ELSE atime END AS atime,
            CASE WHEN touched THEN (size * (0.5 + {u('path')}))::BIGINT ELSE size END AS size,
            CASE WHEN touched THEN (alloc_bytes * (0.5 + {u('path')}))::BIGINT ELSE alloc_bytes END AS alloc_bytes)
        FROM (SELECT *, kind = 'file' AND {chance('path', touch, 'touch')} AS touched FROM e
              WHERE depth < 2 OR array_to_string(string_split(path, '/')[1:{nroot + 2}], '/') NOT IN
                    (SELECT path FROM removed))""")
    prefix = f"'g{gen}_'"
    added = f"""
        SELECT k.* EXCLUDE (touched, nth, n) REPLACE (
            k.parent_path || '/' || {prefix} || k.name AS path, {prefix} || k.name AS name,
            {max_ino} + row_number() OVER () AS ino, {new_time('k.path')} AS mtime, {new_time('k.path')} AS ctime,
            {new_time('k.path')} AS atime)
        FROM (SELECT k.*, row_number() OVER (PARTITION BY k.parent_id ORDER BY hash(k.path || {salt})) AS nth,
                     count(*) OVER (PARTITION BY k.parent_id) AS n
              FROM kept k JOIN grown g ON g.dir_id = k.parent_id WHERE k.kind = 'file') k
        WHERE k.nth <= greatest(1, k.n // 5)"""
    # A new top-level folder: a copy of an existing one, with its own ids and inodes, written during the days.
    top = f"'new_lab_g{gen}_' || t.j"
    born = f"""
        SELECT k.* EXCLUDE (touched) REPLACE (
            '{root}/' || {top} || substr(k.path, length(t.path) + 1) AS path,
            CASE WHEN k.path = t.path THEN k.parent_path
                 ELSE '{root}/' || {top} || substr(k.parent_path, length(t.path) + 1) END AS parent_path,
            CASE WHEN k.path = t.path THEN {top} ELSE k.name END AS name,
            k.dir_id + t.j * ({max_id} + 1) AS dir_id,
            CASE WHEN k.path = t.path THEN k.parent_id ELSE k.parent_id + t.j * ({max_id} + 1) END AS parent_id,
            k.ino + t.j * ({max_ino} + 1) * 2 AS ino, {new_time('k.path')} AS mtime, {new_time('k.path')} AS ctime,
            {new_time('k.path')} AS atime)
        FROM kept k JOIN tops t ON k.path = t.path OR starts_with(k.path, t.path || '/')"""
    con.execute(f"""CREATE TEMP TABLE result AS
        SELECT * EXCLUDE (touched) FROM kept UNION ALL BY NAME ({added}) UNION ALL BY NAME ({born})""")
    cols = ", ".join(
        f'(CASE WHEN r.kind = \'dir\' AND r.dir_entries IS NOT NULL THEN coalesce(c.{c}, 0) ELSE r."{c}" END)::{types[c]} '
        f'AS "{c}"' if c in ("dir_entries", "dir_files", "dir_subdirs") else f'r."{c}"::{types[c]} AS "{c}"'
        for c in types)
    con.execute(f"""
        COPY (
          SELECT {cols} FROM result r LEFT JOIN (
            SELECT parent_id, count(*) AS dir_entries, count(*) FILTER (WHERE kind = 'file') AS dir_files,
                   count(*) FILTER (WHERE kind = 'dir') AS dir_subdirs FROM result GROUP BY parent_id
          ) c ON c.parent_id = r.dir_id
        ) TO '{out / 'entries'}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 1048576, FILE_SIZE_BYTES '1GB',
                                  FILENAME_PATTERN 'part-evolved-{{i}}')
    """)
    summary = {
        "removed_projects": con.execute("SELECT count(*) FROM removed").fetchone()[0],
        "removed_entries": con.execute("SELECT (SELECT count(*) FROM e) - (SELECT count(*) FROM kept)").fetchone()[0],
        "grown_dirs": con.execute(f"SELECT count(DISTINCT parent_id) FROM ({added})").fetchone()[0],
        "added_files": con.execute(f"SELECT count(*) FROM ({added})").fetchone()[0],
        "touched_files": con.execute("SELECT count(*) FROM kept WHERE touched").fetchone()[0],
        "new_tops": [r[0] for r in con.execute(f"SELECT DISTINCT path FROM ({born}) WHERE depth = 1").fetchall()],
    }
    for table, items in manifest["parts"].items():
        if table != "entries":
            for item in items:
                (out / item["file"]).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src / item["file"], out / item["file"])
    entries = []
    for p in sorted((out / "entries").glob("*.parquet")):
        rows = con.execute(f"SELECT count(*) FROM read_parquet('{p}')").fetchone()[0]
        entries.append({"file": f"entries/{p.name}", "rows": rows, "bytes": p.stat().st_size})
    by_kind = dict(con.execute("SELECT kind, count(*) FROM result GROUP BY kind").fetchall())
    total_bytes = con.execute("SELECT coalesce(sum(size), 0) FROM result WHERE kind = 'file'").fetchone()[0]

    def iso(d: datetime) -> str:
        return d.isoformat().replace("+00:00", "Z")

    seconds = time.monotonic() - t0
    manifest.update({
        "scan_id": str(uuid.uuid4()), "status": "complete", "started_at": iso(started),
        "finished_at": iso(started + timedelta(seconds=seconds)), "duration_seconds": seconds,
        "parts": {**manifest["parts"], "entries": entries},
        "totals": {**manifest.get("totals", {}), "entries": sum(by_kind.values()), "by_kind": by_kind,
                   "bytes": int(total_bytes)},
        "synthetic": True, "evolved_from": manifest["scan_id"], "generation": gen, "evolved": summary,
    })
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"Wrote {sum(by_kind.values()):,} entries to {out}, {days:g} days after {src.name}, in {seconds:.1f}s: "
          f"{summary['removed_projects']:,} projects deleted ({summary['removed_entries']:,} entries), "
          f"{summary['added_files']:,} files added to {summary['grown_dirs']:,} folders, "
          f"{summary['touched_files']:,} files rewritten, new top-level folders {', '.join(summary['new_tops']) or 'none'}")
    return summary


if __name__ == "__main__":
    main()
