# SPDX-License-Identifier: Apache-2.0
"""Build a Pinax index from a scan and time the queries the interface makes.

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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scan_dir", type=Path)
    ap.add_argument("--index", type=Path, required=True)
    ap.add_argument("--memory", default="8GB")
    ap.add_argument("--skip-build", action="store_true")
    args = ap.parse_args()

    if not args.skip_build:
        if args.index.exists():
            shutil.rmtree(args.index)
        r = build_index(args.scan_dir, args.index, memory_limit=args.memory, progress=lambda m: None)
        size = sum(p.stat().st_size for p in args.index.rglob("*") if p.is_file())
        print(f"build: {r.seconds:.1f}s for {r.totals['files']:,} files and {r.totals['dirs']:,} directories; "
              f"index {size / 1e9:.2f} GB")
        print("steps:", json.dumps(r.steps))

    idx = SnapshotIndex(args.index)
    root = idx.root
    top = idx.top_path(next(iter(idx.top_ids)))
    deep = idx._rows("SELECT path FROM dirs WHERE depth = 3 ORDER BY bytes DESC LIMIT 1")[0]["path"]
    cutoff = idx.ref_epoch - 3 * 365.25 * 86400
    cases = [
        ("treemap: filesystem root, two levels", lambda: idx.children(root, grand_limit=12)),
        ("treemap: top-level folder, two levels", lambda: idx.children(top, grand_limit=12)),
        ("treemap: depth-3 folder, two levels", lambda: idx.children(deep, grand_limit=12)),
        ("file types: filesystem root", lambda: idx.types(root)),
        ("file types: top-level folder", lambda: idx.types(top)),
        ("file types: depth-3 folder", lambda: idx.types(deep)),
        ("owners: top-level folder", lambda: idx.owners(top)),
        ("cold folders (3y): filesystem root", lambda: idx.cold_dirs(root, cutoff)),
        ("cold folders (3y): top-level folder", lambda: idx.cold_dirs(top, cutoff)),
        ("largest files: top-level folder", lambda: idx.files(top, limit=100)),
        ("largest files: depth-3 folder", lambda: idx.files(deep, limit=100)),
        ("name search: top-level folder", lambda: idx.files(top, where="name ILIKE ?", params=["%00012%"], limit=100)),
        ("name search: filesystem root", lambda: idx.files(root, where="name ILIKE ?", params=["%00012%"], limit=100)),
        ("type search (.bam): filesystem root", lambda: idx.files(root, where="ext = ?", params=["bam"], limit=100)),
    ]
    print(f"{'query':<42} {'median ms':>10}")
    for name, fn in cases:
        ms, _ = timed(fn)
        print(f"{name:<42} {ms:>10.1f}")


if __name__ == "__main__":
    main()
