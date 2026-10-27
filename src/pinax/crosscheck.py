# SPDX-License-Identifier: Apache-2.0
"""Check a snapshot index against `fs-inventory rollup`, directory by directory.

`fs-inventory rollup` is a separate implementation (Rust, streaming) of the subtree totals Pinax
precomputes. Run with Pinax's age bucket edges and an age depth as deep as the scan, it gives every
directory the measures Pinax stores, so the two can be compared row by row, joined on `dir_id`:

    rollup                                   Pinax `dirs`
    path, files, dirs, bytes, alloc_bytes    path, files, dirs, bytes, alloc
    incomplete_dirs                          incomplete
    oldest_mtime, newest_mtime               mtime_min, mtime_max
    newest_atime, newest_ctime               atime_max, ctime_max
    rollup_ages: mtime files and bytes,      mf0-6, mb0-6,
                 atime bytes, per bucket     ab0-6

Rollup's `future` bucket (times after the scan started) is part of Pinax's first bucket. Any difference
is a bug in one of them until explained.

The scan directory is never written to: rollup runs on a shadow scan folder holding a copy of the
manifest and a link to the scan's entries.
"""

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from .categories import AGE_BUCKETS, bucket_edges
from .ingest.build import INDEX_VERSION

NB = len(AGE_BUCKETS)


class CheckError(Exception):
    pass


def rollup_edges() -> str:
    """Pinax's age bucket edges in fs-inventory's duration syntax (a year is 365.25 days in both)."""
    out = []
    for days in bucket_edges():
        years = days / 365.25
        out.append(f"{years:g}y" if years == int(years) else f"{days:g}d")
    return ",".join(out)


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def scan_depth(scan_dir: Path) -> int:
    """The deepest directory in the scan, read from the scan itself."""
    glob = _sql_str(str(scan_dir / "entries" / "*.parquet"))
    with duckdb.connect() as con:
        depth = con.execute(f"SELECT max(depth) FROM read_parquet({glob}) WHERE kind = 'dir'").fetchone()[0]
    return int(depth or 0)


def run_rollup(scan_dir: Path, work_dir: Path, *, binary: str, age_depth: int) -> Path:
    """Run `fs-inventory rollup` on a shadow of `scan_dir` in `work_dir`; returns the shadow folder,
    which holds `rollup/` and `rollup_ages/`."""
    scan_dir = scan_dir.resolve()
    shadow = work_dir / "rollup-scan"
    if shadow.exists():
        shutil.rmtree(shadow)
    shadow.mkdir(parents=True)
    shutil.copyfile(scan_dir / "manifest.json", shadow / "manifest.json")
    (shadow / "entries").symlink_to(scan_dir / "entries", target_is_directory=True)
    cmd = [binary, "rollup", "--age-buckets", rollup_edges(), "--age-depth", str(age_depth), str(shadow)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as e:
        raise CheckError(f"could not run {binary}: {e}") from e
    if proc.returncode != 0:
        raise CheckError(f"fs-inventory rollup failed (exit {proc.returncode}): {proc.stderr.strip()}")
    return shadow


def _measures() -> list[tuple[str, str, str]]:
    """(name, Pinax expression over `d`, rollup expression over `r` and the age pivot `a`)."""
    labels = [b.short for b in AGE_BUCKETS]
    out = [
        ("path", "d.path", "r.path"),
        ("files", "d.files", "r.files"),
        ("dirs", "d.dirs", "r.dirs"),
        ("bytes", "d.bytes", "r.bytes"),
        ("alloc_bytes", "d.alloc", "r.alloc_bytes"),
        ("incomplete_dirs", "d.incomplete", "r.incomplete_dirs"),
        ("oldest_mtime", "epoch_us(d.mtime_min)", "epoch_us(r.oldest_mtime)"),
        ("newest_mtime", "epoch_us(d.mtime_max)", "epoch_us(r.newest_mtime)"),
        ("newest_atime", "epoch_us(d.atime_max)", "epoch_us(r.newest_atime)"),
        ("newest_ctime", "epoch_us(d.ctime_max)", "epoch_us(r.newest_ctime)"),
    ]
    out += [(f"mtime_files[{labels[i]}]", f"d.mf{i}", f"coalesce(a.mf{i}, 0)") for i in range(NB)]
    out += [(f"mtime_bytes[{labels[i]}]", f"d.mb{i}", f"coalesce(a.mb{i}, 0)") for i in range(NB)]
    out += [(f"atime_bytes[{labels[i]}]", f"d.ab{i}", f"coalesce(a.ab{i}, 0)") for i in range(NB)]
    return out


def _age_pivot() -> str:
    """rollup_ages as one row per directory. Rollup's bucket 0 is `future` and bucket b + 1 is Pinax's b."""
    def buckets(i: int) -> str:
        return "bucket IN (0, 1)" if i == 0 else f"bucket = {i + 1}"

    cols = [f"sum(files) FILTER (WHERE time = 'mtime' AND {buckets(i)})::BIGINT AS mf{i}" for i in range(NB)]
    cols += [f"sum(bytes) FILTER (WHERE time = 'mtime' AND {buckets(i)})::BIGINT AS mb{i}" for i in range(NB)]
    cols += [f"sum(bytes) FILTER (WHERE time = 'atime' AND {buckets(i)})::BIGINT AS ab{i}" for i in range(NB)]
    return f"SELECT dir_id, {', '.join(cols)} FROM ages GROUP BY dir_id"


def _show(measure: str, value) -> str:
    """Times are compared as microseconds since the epoch; show them as times."""
    if measure.endswith(("_mtime", "_atime", "_ctime")):
        if value is None:
            return "none"
        return (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=value)).isoformat()
    return f"{value:,}" if isinstance(value, int) else repr(value)


@dataclass
class Mismatch:
    measure: str
    dirs: int
    examples: list[dict] = field(default_factory=list)  # path, pinax, rollup


@dataclass
class CheckReport:
    dirs: int  # directories in both
    only_pinax: list[str]
    only_rollup: list[str]
    only_pinax_count: int
    only_rollup_count: int
    measures: list[str]
    mismatches: list[Mismatch]
    age_depth: int
    edges: str
    index_version: int
    rollup_seconds: float = 0.0
    compare_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.mismatches and not self.only_pinax_count and not self.only_rollup_count

    def json(self) -> dict:
        return {
            "ok": self.ok, "dirs": self.dirs, "measures": self.measures, "age_buckets": self.edges,
            "age_depth": self.age_depth, "index_version": self.index_version,
            "only_pinax": {"count": self.only_pinax_count, "examples": self.only_pinax},
            "only_rollup": {"count": self.only_rollup_count, "examples": self.only_rollup},
            "mismatches": [{"measure": m.measure, "dirs": m.dirs, "examples": m.examples} for m in self.mismatches],
            "rollup_seconds": round(self.rollup_seconds, 2), "compare_seconds": round(self.compare_seconds, 2),
        }

    def text(self) -> str:
        lines = []
        if self.ok:
            lines.append(f"OK: {self.dirs:,} directories, {len(self.measures)} measures each, all equal "
                         f"to fs-inventory rollup (age buckets {self.edges}, age depth {self.age_depth})")
        else:
            lines.append(f"MISMATCH: {self.dirs:,} directories compared on {len(self.measures)} measures "
                         f"(age buckets {self.edges}, age depth {self.age_depth})")
            for name, count, examples in (("only in Pinax", self.only_pinax_count, self.only_pinax),
                                          ("only in rollup", self.only_rollup_count, self.only_rollup)):
                if count:
                    lines.append(f"  {count:,} directories {name}")
                    lines += [f"    {p}" for p in examples]
            for m in self.mismatches:
                lines.append(f"  {m.measure}: {m.dirs:,} directories differ")
                for e in m.examples:
                    lines.append(f"    {e['path']}: Pinax {_show(m.measure, e['pinax'])}, "
                                 f"rollup {_show(m.measure, e['rollup'])}")
        if self.index_version < INDEX_VERSION:
            lines.append(f"note: the index was built by an older Pinax (index version {self.index_version}, now "
                         f"{INDEX_VERSION}); check the scan directory to build a current one")
        lines.append(f"rollup {self.rollup_seconds:.1f}s, compare {self.compare_seconds:.1f}s")
        return "\n".join(lines)


def compare(index_dir: Path, rollup_scan: Path, *, age_depth: int, examples: int = 10) -> CheckReport:
    """Compare Pinax's `dirs` table in `index_dir` with the rollup tables in `rollup_scan`."""
    t0 = time.monotonic()
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH {_sql_str(str(index_dir / 'index.duckdb'))} AS p (READ_ONLY)")
        for view, table in (("r", "rollup"), ("ages", "rollup_ages")):
            glob = _sql_str(str(rollup_scan / table / "*.parquet"))
            con.execute(f"CREATE TEMP VIEW {view} AS SELECT * FROM read_parquet({glob})")
        measures = _measures()
        cols = ", ".join(f"{p} AS p{i}, {r} AS r{i}" for i, (_, p, r) in enumerate(measures))
        con.execute(f"""
            CREATE TEMP TABLE j AS
            SELECT coalesce(d.dir_id, r.dir_id) AS dir_id, coalesce(d.path, r.path) AS path,
                   d.dir_id IS NOT NULL AS in_p, r.dir_id IS NOT NULL AS in_r, {cols}
            FROM p.dirs d
            FULL JOIN r ON r.dir_id = d.dir_id
            LEFT JOIN ({_age_pivot()}) a ON a.dir_id = r.dir_id
        """)
        counts = ", ".join(f"count(*) FILTER (WHERE in_p AND in_r AND p{i} IS DISTINCT FROM r{i})"
                           for i in range(len(measures)))
        row = con.execute(f"""
            SELECT count(*) FILTER (WHERE in_p AND in_r), count(*) FILTER (WHERE NOT in_r),
                   count(*) FILTER (WHERE NOT in_p), {counts}
            FROM j
        """).fetchone()
        both, only_p, only_r, per = row[0], row[1], row[2], row[3:]
        version = con.execute("SELECT value FROM p.meta WHERE key = 'index_version'").fetchone()

        def paths(where: str) -> list[str]:
            rows = con.execute(f"SELECT path FROM j WHERE {where} ORDER BY path LIMIT {examples}").fetchall()
            return [r[0] for r in rows]

        mismatches = []
        for i, ((name, _, _), n) in enumerate(zip(measures, per, strict=True)):
            if not n:
                continue
            ex = con.execute(f"""
                SELECT path, p{i}, r{i} FROM j WHERE in_p AND in_r AND p{i} IS DISTINCT FROM r{i}
                ORDER BY path LIMIT {examples}
            """).fetchall()
            mismatches.append(Mismatch(name, n, [{"path": p, "pinax": a, "rollup": b} for p, a, b in ex]))
        return CheckReport(
            dirs=both, only_pinax=paths("NOT in_r") if only_p else [], only_rollup=paths("NOT in_p") if only_r else [],
            only_pinax_count=only_p, only_rollup_count=only_r, measures=[m[0] for m in measures],
            mismatches=mismatches, age_depth=age_depth, edges=rollup_edges(),
            index_version=int(json.loads(version[0])) if version else 0, compare_seconds=time.monotonic() - t0,
        )
    finally:
        con.close()


def check(scan_dir: Path, index_dir: Path, work_dir: Path, *, binary: str, examples: int = 10) -> CheckReport:
    """Run `fs-inventory rollup` on the scan and compare it with the index built from that scan."""
    t0 = time.monotonic()
    depth = scan_depth(scan_dir)
    shadow = run_rollup(scan_dir, work_dir, binary=binary, age_depth=depth)
    rollup_seconds = time.monotonic() - t0
    report = compare(index_dir, shadow, age_depth=depth, examples=examples)
    report.rollup_seconds = rollup_seconds
    return report
