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

    ingest = sub.add_parser("ingest", help="Build a snapshot index from a finished fs-inventory scan, or watch a folder")
    ingest.add_argument("scan_dir", type=Path, nargs="?",
                        help="The scan folder; with --watch, the folder to watch (default: PINAX_SCAN_ROOT)")
    ingest.add_argument("--fs", dest="fs_slug", help="Filesystem slug (default: the one whose root matches the scan)")
    ingest.add_argument("--allow-incomplete", action="store_true",
                        help="Accept scans with status incomplete (stalled directories are missing)")
    ingest.add_argument("--watch", action="store_true",
                        help="Ingest each new scan under the folder once, checking every --interval seconds")
    ingest.add_argument("--interval", type=int, default=300, help="Seconds between checks with --watch (default 300)")
    ingest.add_argument("--once", action="store_true", help="With --watch: check once and exit (for cron or a timer)")
    ingest.add_argument("--no-verify", dest="verify", action="store_false",
                        help="With --watch: skip `fs-inventory verify` before ingesting")

    sub.add_parser("snapshots", help="List snapshots")

    rebuild = sub.add_parser("rebuild", help="Build ready snapshots' indexes again from their scans, in place")
    rebuild.add_argument("snapshot_ids", type=int, nargs="*", metavar="SNAPSHOT_ID")
    rebuild.add_argument("--outdated", action="store_true",
                         help="Every ready snapshot whose index is missing or in an older format (after an upgrade)")

    imp = sub.add_parser("import", help="Import labs, people, memberships, and folder ownership from CSV "
                                        "(a dry run unless --apply)")
    imp.add_argument("folder", type=Path, nargs="?",
                     help="A folder holding any of labs.csv, people.csv, memberships.csv, and folders.csv")
    for name in ("labs", "people", "memberships", "folders"):
        imp.add_argument(f"--{name}", type=Path, help=f"The {name} file (instead of {name}.csv in the folder)")
    imp.add_argument("--apply", action="store_true", help="Make the changes (all of them, or none if any row is refused)")

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
    check.add_argument("--previous-scan", type=Path,
                       help="With a scan directory and --index: the previous scan, to check the index's changes "
                            "(a snapshot ID finds it in the catalog)")
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
        if args.watch:
            sys.exit(_watch(args))
        if args.scan_dir is None:
            parser.error("ingest needs a scan folder, or --watch")
        sys.exit(_ingest(args.scan_dir, args.fs_slug, args.allow_incomplete))
    elif args.command == "snapshots":
        _snapshots()
    elif args.command == "check":
        sys.exit(_check(args))
    elif args.command == "import":
        sys.exit(_import(args))
    elif args.command == "rebuild":
        sys.exit(_rebuild(args))


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


def _watch(args: argparse.Namespace) -> int:
    from .config import get_settings
    from .db import init_db
    from .watch import watch

    settings = get_settings()
    folder = args.scan_dir or settings.scan_root
    if not folder.is_dir():
        print(f"error: {folder} is not a folder", file=sys.stderr)
        return 2
    init_db()
    try:
        result = watch(folder, interval=max(10, args.interval), once=args.once, fs_slug=args.fs_slug,
                       allow_incomplete=args.allow_incomplete, verify=args.verify, settings=settings)
    except KeyboardInterrupt:
        return 0
    return 1 if result.failed else 0


def _import(args: argparse.Namespace) -> int:
    from .db import init_db, session_scope
    from .onboard import FORMATS, apply_import, plan_import

    files = {}
    for name in FORMATS:
        given = getattr(args, name)
        if given is None and args.folder is not None and (args.folder / f"{name}.csv").exists():
            given = args.folder / f"{name}.csv"
        if given is not None:
            if not given.is_file():
                print(f"error: {given} is not a file", file=sys.stderr)
                return 2
            files[name] = given
    if not files:
        print("error: give a folder holding labs.csv, people.csv, memberships.csv, or folders.csv, or the files "
              "themselves (see docs/IMPORT.md)", file=sys.stderr)
        return 2
    init_db()
    with session_scope() as s:
        plan = plan_import(s, **files)
        print(plan.text())
        if not plan.ok:
            return 1
        if not plan.changes:
            print("Nothing to change.")
            return 0
        if not args.apply:
            print(f"Dry run: nothing changed. Run again with --apply to make these {len(plan.changes)} changes.")
            return 0
        n = apply_import(s, plan)
        print(f"Applied {n} changes; each is in the audit log.")
    return 0


def _rebuild(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from .db import init_db, session_scope
    from .ingest.build import INDEX_VERSION
    from .models import Snapshot, SnapshotStatus
    from .snapshots import IngestError, index_version, rebuild_snapshot

    if not args.snapshot_ids and not args.outdated:
        print("error: name the snapshots to rebuild, or use --outdated", file=sys.stderr)
        return 2
    init_db()
    failed = 0
    with session_scope() as s:
        ready = s.scalars(select(Snapshot).where(Snapshot.status == SnapshotStatus.READY)
                          .order_by(Snapshot.started_at)).all()
        targets = [snap for snap in ready if snap.id in set(args.snapshot_ids)]
        unknown = set(args.snapshot_ids) - {snap.id for snap in targets}
        if unknown:
            print(f"error: no ready snapshot {', '.join(map(str, sorted(unknown)))} (see `pinax snapshots`)",
                  file=sys.stderr)
            return 2
        if args.outdated:
            targets += [snap for snap in ready if snap not in targets and (index_version(snap) or 0) < INDEX_VERSION]
        if not targets:
            print(f"Every ready snapshot's index is current (version {INDEX_VERSION}).")
            return 0
        for snap in targets:
            try:
                rebuild_snapshot(s, snap, progress=lambda m: None)
                print(f"Snapshot {snap.id}: rebuilt in {snap.build_seconds}s")
            except IngestError as e:
                print(f"Snapshot {snap.id}: {e}", file=sys.stderr)
                failed += 1
            except Exception as e:  # noqa: BLE001 - report it and go on to the next snapshot
                print(f"Snapshot {snap.id}: rebuild failed: {e}", file=sys.stderr)
                failed += 1
    return 1 if failed else 0


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
    from .crosscheck import CheckError, check, check_changes
    from .index import SnapshotIndex
    from .ingest.build import BuildError, build_index

    settings = get_settings()
    settings.ensure_dirs()
    previous_scan = args.previous_scan
    if args.target.isdigit():
        from sqlalchemy import select

        from .db import init_db, session_scope
        from .models import Snapshot, SnapshotStatus

        init_db()
        with session_scope() as s:
            snap = s.get(Snapshot, int(args.target))
            if snap is None or snap.status != SnapshotStatus.READY:
                print(f"error: no ready snapshot {args.target} (see `pinax snapshots`)", file=sys.stderr)
                return 2
            scan_dir, index_dir = Path(snap.scan_dir), Path(snap.index_dir)
            idx = SnapshotIndex(index_dir, memory_limit="256MB", threads=1)
            prev_id = (idx.previous or {}).get("scan_id")
            idx.close()
            if prev_id:
                prev = s.scalar(select(Snapshot).where(Snapshot.scan_id == prev_id))
                previous_scan = Path(prev.scan_dir) if prev else None
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
        changes, note = None, ""
        idx = SnapshotIndex(index_dir, memory_limit="256MB", threads=1)
        has_changes = idx.has_changes
        idx.close()
        if has_changes and previous_scan and (previous_scan / "manifest.json").exists():
            changes = check_changes(index_dir, scan_dir, previous_scan, work / "changes",
                                    binary=settings.fs_inventory_path(), examples=args.examples,
                                    memory_limit=settings.build_memory_limit)
        elif has_changes:
            note = "The changes since the previous scan were not checked: its scan folder is gone."
    except (BuildError, CheckError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if args.json:
        print(json.dumps({**report.json(), "changes": changes.json() if changes else None}, indent=2, default=str))
    else:
        print(report.text())
        if changes:
            print("Changes since the previous scan:\n" + changes.text())
        elif note:
            print(note)
    return 0 if report.ok and (changes is None or changes.ok) else 1
