# SPDX-License-Identifier: Apache-2.0
"""Command line: `uv run pinax <command>`."""

import argparse
import logging
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(prog="pinax", description="Pinax: explore your data")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the web app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8001)

    seed = sub.add_parser("seed", help="Reset runtime data and build demo filesystems, people, and scans")
    seed.add_argument("--scale", type=float, default=1.0, help="Multiply the number of demo files (1.0 is about 40,000)")

    fs = sub.add_parser("filesystem", help="Register or list filesystems")
    fs_sub = fs.add_subparsers(dest="action", required=True)
    fs_add = fs_sub.add_parser("add", help="Register a filesystem by its scan root")
    fs_add.add_argument("slug", help="Short ID, such as fast")
    fs_add.add_argument("root", help="Absolute path of the scan root, such as /fh/fast")
    fs_add.add_argument("--name", help="Display name (default: the slug)")
    fs_add.add_argument("--display-root", default="", help="Root as people know it, if the scan host mounts it elsewhere")
    fs_add.add_argument("--description", default="")
    fs_sub.add_parser("list", help="List filesystems and their latest snapshot")

    scan = sub.add_parser("scan", help="Scan a registered filesystem with fs-inventory, then ingest the scan")
    scan.add_argument("filesystem", help="Filesystem slug")
    scan.add_argument("--no-ingest", action="store_true", help="Only scan; ingest later with `pinax ingest`")
    scan.add_argument("extra", nargs=argparse.REMAINDER, help="Extra fs-inventory scan options, after --")

    ingest = sub.add_parser("ingest", help="Build a snapshot index from a finished fs-inventory scan")
    ingest.add_argument("scan_dir", type=Path)
    ingest.add_argument("--fs", dest="fs_slug", help="Filesystem slug (default: the one whose root matches the scan)")
    ingest.add_argument("--allow-incomplete", action="store_true",
                        help="Accept scans with status incomplete (stalled directories are missing)")

    sub.add_parser("snapshots", help="List snapshots")

    check = sub.add_parser("check", help="Check an index against fs-inventory rollup, directory by directory")
    check.add_argument("target", help="Snapshot ID, or an fs-inventory scan directory")
    check.add_argument("--index", type=Path,
                       help="With a scan directory: the index built from it (default: build one, then delete it)")
    check.add_argument("--allow-incomplete", action="store_true",
                       help="With a scan directory: accept a scan with status incomplete")
    check.add_argument("--examples", type=int, default=10, help="Directories to list for each difference")
    check.add_argument("--age-depth", type=int,
                       help="Compare age histograms down to this depth (default: the whole scan; rollup needs about "
                            "0.5 KB of memory per directory down to it)")
    check.add_argument("--json", action="store_true", help="Print the report as JSON")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.command == "serve":
        import uvicorn

        uvicorn.run("pinax.main:app", host=args.host, port=args.port, log_level="info")
    elif args.command == "seed":
        from .config import get_settings
        from .seed import run_seed

        if get_settings().environment == "prod":
            print("error: refusing to seed when PINAX_ENVIRONMENT=prod", file=sys.stderr)
            sys.exit(2)
        run_seed(scale=args.scale)
    elif args.command == "filesystem":
        sys.exit(_filesystem(args))
    elif args.command == "scan":
        sys.exit(_scan(args))
    elif args.command == "ingest":
        sys.exit(_ingest(args.scan_dir, args.fs_slug, args.allow_incomplete))
    elif args.command == "snapshots":
        _snapshots()
    elif args.command == "check":
        sys.exit(_check(args))


def _filesystem(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from .db import init_db, session_scope
    from .models import Filesystem
    from .snapshots import latest_snapshot

    init_db()
    with session_scope() as s:
        if args.action == "add":
            if not args.root.startswith("/"):
                print("error: the root must be an absolute path", file=sys.stderr)
                return 2
            if s.scalar(select(Filesystem).where(Filesystem.slug == args.slug)):
                print(f"error: filesystem {args.slug} already exists", file=sys.stderr)
                return 2
            s.add(Filesystem(slug=args.slug, name=args.name or args.slug, root=args.root.rstrip("/") or "/",
                             display_root=args.display_root.rstrip("/"), description=args.description))
            print(f"Added filesystem {args.slug} at {args.root}")
        else:
            for f in s.scalars(select(Filesystem).order_by(Filesystem.slug)):
                snap = latest_snapshot(s, f)
                when = f"latest scan {snap.started_at:%Y-%m-%d %H:%M} UTC" if snap else "not scanned yet"
                print(f"{f.slug:<12} {f.root:<40} {when}")
    return 0


def _scan(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from .config import get_settings
    from .db import init_db, session_scope
    from .models import Filesystem
    from .snapshots import IngestError, run_scan

    init_db()
    with session_scope() as s:
        fs = s.scalar(select(Filesystem).where(Filesystem.slug == args.filesystem))
        if fs is None:
            print(f"error: no filesystem {args.filesystem}; add it with `pinax filesystem add`", file=sys.stderr)
            return 2
        extra = [a for a in args.extra if a != "--"]
        try:
            out = run_scan(fs, get_settings(), extra_args=extra)
        except IngestError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    return 0 if args.no_ingest else _ingest(out, args.filesystem, False)


def _ingest(scan_dir: Path, fs_slug: str | None, allow_incomplete: bool) -> int:
    from .db import init_db, session_scope
    from .snapshots import IngestError, ingest_scan

    init_db()
    try:
        with session_scope() as s:
            snap = ingest_scan(s, scan_dir, fs_slug=fs_slug, allow_incomplete=allow_incomplete)
            print(f"Snapshot {snap.id}: {snap.files:,} files, {snap.dirs:,} directories, {snap.bytes:,} bytes; "
                  f"index built in {snap.build_seconds}s")
    except IngestError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def _snapshots() -> None:
    from sqlalchemy import select

    from .db import init_db, session_scope
    from .models import Snapshot

    init_db()
    with session_scope() as s:
        for snap in s.scalars(select(Snapshot).order_by(Snapshot.started_at)):
            print(f"{snap.id:>4} {snap.filesystem.slug:<10} {snap.started_at:%Y-%m-%d %H:%M} {snap.status:<9} "
                  f"{snap.files:>14,} files {snap.bytes:>22,} bytes  {snap.scan_dir}")


def _check(args: argparse.Namespace) -> int:
    import json
    import shutil
    import tempfile

    from .config import get_settings
    from .crosscheck import CheckError, check
    from .ingest.build import BuildError, build_index

    settings = get_settings()
    settings.ensure_dirs()
    if args.target.isdigit():
        from .db import init_db, session_scope
        from .models import Snapshot, SnapshotStatus

        init_db()
        with session_scope() as s:
            snap = s.get(Snapshot, int(args.target))
            if snap is None or snap.status != SnapshotStatus.READY:
                print(f"error: no ready snapshot {args.target} (see `pinax snapshots`)", file=sys.stderr)
                return 2
            scan_dir, index_dir = Path(snap.scan_dir), Path(snap.index_dir)
    else:
        scan_dir, index_dir = Path(args.target), args.index
    if not (scan_dir / "manifest.json").exists():
        print(f"error: {scan_dir} is not an fs-inventory scan directory", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="check-", dir=settings.var_root))
    try:
        if index_dir is None:
            index_dir = work / "index"
            build_index(scan_dir, index_dir, memory_limit=settings.build_memory_limit, threads=settings.build_threads,
                        allow_incomplete=args.allow_incomplete)
        report = check(scan_dir, index_dir, work, binary=settings.fs_inventory_path(), examples=args.examples,
                       age_depth=args.age_depth, memory_limit=settings.build_memory_limit)
    except (BuildError, CheckError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(report.json(), indent=2, default=str) if args.json else report.text())
    return 0 if report.ok else 1
