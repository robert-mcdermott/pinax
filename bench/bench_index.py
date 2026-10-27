# SPDX-License-Identifier: Apache-2.0
"""Build a Pinax index from a scan and time the queries the interface makes.

Prints the scan's shape and the timings without any path or name, so the output of a run on a real
filesystem can be recorded as it is.

    uv run python bench/bench_index.py /scratch/synth-10m --index /scratch/synth-10m-index
"""

import argparse
import json
import shutil
import statistics
import time
from pathlib import Path

from pinax.index import SnapshotIndex
from pinax.ingest.build import build_index


def timed(fn, repeat: int = 5) -> tuple[float, object]:
    out, times = None, []
    for _ in range(repeat):
        s = time.perf_counter()
        out = fn()
        times.append((time.perf_counter() - s) * 1000)
    return statistics.median(times), out


def shape(idx: SnapshotIndex) -> dict:
    """What makes a filesystem hard to index, as counts only."""
    one = lambda sql: idx._rows(sql)[0]
    files = f"read_parquet('{idx.index_dir}/files/*/*.parquet')"
    out = one("""SELECT count(*) AS dirs, max(depth) AS max_depth, max(strlen(path)) AS longest_path_bytes,
                        max(d_files) AS most_files_in_a_folder, max(incomplete) AS incomplete_dirs,
                        count(*) FILTER (WHERE is_mount_point) AS mount_points FROM dirs""")
    out |= one("SELECT max(n) AS most_subfolders_in_a_folder FROM (SELECT count(*) AS n FROM dirs GROUP BY parent_id)")
    out |= one(f"""SELECT count(*) AS files, count(*) FILTER (WHERE kind = 'symlink') AS symlinks,
                          count(*) FILTER (WHERE kind NOT IN ('file', 'symlink')) AS other_kinds,
                          count(*) FILTER (WHERE nlink > 1) AS hard_linked_files, count(DISTINCT ext) AS file_types,
                          count(DISTINCT uid) AS owners FROM {files}""")
    out |= one("SELECT count(*) AS errors FROM errors")
    out |= one("SELECT count(*) AS top_level_folders FROM tops")
    flags = idx._rows("SELECT flag, sum(entries) AS n FROM name_flags GROUP BY flag ORDER BY flag")
    out["names_by_flag"] = {int(r["flag"]): int(r["n"]) for r in flags}
    return {k: (int(v) if isinstance(v, (int, float)) and v is not None else v) for k, v in out.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scan_dir", type=Path)
    ap.add_argument("--index", type=Path, required=True)
    ap.add_argument("--memory", default="8GB", help="Build memory limit")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--json", type=Path, help="Also write the results to this file")
    args = ap.parse_args()

    result: dict = {}
    if not args.skip_build:
        if args.index.exists():
            shutil.rmtree(args.index)
        r = build_index(args.scan_dir, args.index, memory_limit=args.memory, progress=lambda m: None)
        size = sum(p.stat().st_size for p in args.index.rglob("*") if p.is_file())
        result["build"] = {"seconds": round(r.seconds, 1), "index_bytes": size, "steps": r.steps}
        print(f"build: {r.seconds:.1f}s for {r.totals['files']:,} files and {r.totals['dirs']:,} directories; "
              f"index {size / 1e9:.2f} GB")
        print("steps:", json.dumps(r.steps))

    idx = SnapshotIndex(args.index)
    result["shape"] = shape(idx)
    print("shape:", json.dumps(result["shape"]))

    root = idx.root
    top = idx.top_path(next(iter(idx.top_ids)))
    pick = lambda sql: idx._rows(sql)[0]["path"]
    deep3 = pick("SELECT path FROM dirs WHERE depth = least(3, (SELECT max(depth) FROM dirs)) ORDER BY bytes DESC LIMIT 1")
    deepest = pick("SELECT path FROM dirs ORDER BY depth DESC LIMIT 1")
    most_files = pick("SELECT path FROM dirs ORDER BY d_files DESC LIMIT 1")
    most_folders = pick("""SELECT d.path FROM dirs d JOIN (SELECT parent_id, count(*) AS n FROM dirs GROUP BY parent_id
                           ORDER BY n DESC LIMIT 1) w ON w.parent_id = d.dir_id""")
    three_years = 4  # age buckets 3-5y and older
    cases = [
        ("treemap: filesystem root, two levels", lambda: idx.children(root, grand_limit=12)),
        ("treemap: top-level folder, two levels", lambda: idx.children(top, grand_limit=12)),
        ("treemap: depth-3 folder, two levels", lambda: idx.children(deep3, grand_limit=12)),
        ("treemap: folder with most subfolders", lambda: idx.children(most_folders, grand_limit=12)),
        ("treemap: deepest folder", lambda: idx.children(deepest, grand_limit=12)),
        ("file types: filesystem root", lambda: idx.types(root)),
        ("file types: top-level folder", lambda: idx.types(top)),
        ("file types: depth-3 folder", lambda: idx.types(deep3)),
        ("owners: top-level folder", lambda: idx.owners(top)),
        ("cold folders (3y): filesystem root", lambda: idx.cold_dirs(root, three_years)),
        ("cold folders (3y): top-level folder", lambda: idx.cold_dirs(top, three_years)),
        ("largest files: top-level folder", lambda: idx.files(top, limit=100)),
        ("largest files: depth-3 folder", lambda: idx.files(deep3, limit=100)),
        ("largest files: folder with most files", lambda: idx.files(most_files, limit=100)),
        ("name search: top-level folder", lambda: idx.files(top, where="name ILIKE ?", params=["%00012%"], limit=100)),
        ("name search: filesystem root", lambda: idx.files(root, where="name ILIKE ?", params=["%00012%"], limit=100)),
        ("type search (.bam): filesystem root", lambda: idx.files(root, where="ext = ?", params=["bam"], limit=100)),
    ]
    result["queries_ms"] = {}
    print(f"{'query':<42} {'median ms':>10}")
    for name, fn in cases:
        ms, _ = timed(fn)
        result["queries_ms"][name] = round(ms, 1)
        print(f"{name:<42} {ms:>10.1f}")
    if args.json:
        args.json.write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
