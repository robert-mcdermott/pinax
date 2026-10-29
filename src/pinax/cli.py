# SPDX-License-Identifier: Apache-2.0
"""Command line: `uv run pinax <command>`."""

import argparse
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

    tags = sub.add_parser("tags", help="Folder tags in bulk")
    tags_sub = tags.add_subparsers(dest="tags_command", required=True)
    tags_imp = tags_sub.add_parser("import", help="Tag folders from a CSV (filesystem, path, tag, value; a lab's own tag "
                                                  "as lab:key), a dry run unless --apply")
    tags_imp.add_argument("file", type=Path)
    tags_imp.add_argument("--apply", action="store_true", help="Tag the folders (all of them, or none if any row is refused)")

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

    user = sub.add_parser("user", help="Add a person, give one a temporary password, or list people (for example "
                                       "the first administrator, before anyone can sign in)")
    user_sub = user.add_subparsers(dest="user_command", required=True)
    user_add = user_sub.add_parser("add", help="Add a person; with local sign-in, print a temporary password")
    user_add.add_argument("username", help="Their sign-in name (with single sign-on, the name the proxy sends)")
    user_add.add_argument("--name", required=True, help="Full name")
    user_add.add_argument("--email", required=True)
    user_add.add_argument("--title", default="")
    user_add.add_argument("--admin", action="store_true", help="Make them a storage administrator")
    user_password = user_sub.add_parser("password", help="Local sign-in: print a new temporary password for a person, "
                                                          "which unlocks their account and ends their sessions")
    user_password.add_argument("username")
    user_sub.add_parser("list", help="List people, their role, and whether they can sign in")

    db = sub.add_parser("db", help="The catalog's database: whether it is up to date, and upgrading it")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_sub.add_parser("status", help="Show the catalog's database and its revision (exits 1 unless up to date)")
    db_upgrade = db_sub.add_parser("upgrade", help="Bring the catalog up to date (back it up first)")
    db_upgrade.add_argument("--sql", action="store_true",
                            help="Print the SQL instead of running it, for a database administrator to apply")
    db_copy = db_sub.add_parser("copy", help="Copy the whole catalog into another, empty database, such as SQLite into "
                                             "PostgreSQL (stop Pinax first)")
    db_copy.add_argument("target", help="The database to copy into, such as postgresql://pinax@db.example.org/pinax "
                                        "(its password in ~/.pgpass)")
    db_copy.add_argument("--from", dest="source", help="The catalog to copy (default: this one, PINAX_DATABASE_URL)")
    db_revision = db_sub.add_parser("revision", help="For developers: write a migration for what changed in models.py "
                                                     "(against an up-to-date catalog)")
    db_revision.add_argument("message", help="What it changes, such as \"Add the audit event's address\"")

    args = parser.parse_args()
    from .config import get_settings
    from .logs import LogError, configure

    try:
        configure(get_settings())
    except LogError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)

    from .migrate import CatalogError

    try:
        if args.command == "serve":
            import uvicorn

            from .config import get_settings, unsafe
            from .db import init_db

            if problem := unsafe(get_settings()):
                print(f"error: {problem}", file=sys.stderr)
                sys.exit(2)
            init_db()  # here, so a catalog that cannot be used is reported plainly
            # Pinax's own logs (logs.py) and its own line per request; the person's address is worked out by Pinax
            # from PINAX_TRUSTED_PROXIES rather than by uvicorn, so the sign-on header's check sees the proxy itself.
            uvicorn.run("pinax.main:app", host=args.host, port=args.port, log_config=None, access_log=False,
                        proxy_headers=False, log_level=get_settings().log_level.lower())
        elif args.command == "user":
            _user(args)
        elif args.command == "seed":
            from .config import get_settings
            from .seed import run_seed

            env = get_settings().environment
            if env != "demo":  # a dev or prod catalog holds real data, which seeding would delete
                print(f"error: pinax seed deletes the catalog and indexes in PINAX_VAR_ROOT, so it runs only when "
                      f"PINAX_ENVIRONMENT=demo, not {env}", file=sys.stderr)
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
        elif args.command == "tags":
            sys.exit(_tags_import(args))
        elif args.command == "db":
            sys.exit(_db(args))
        elif args.command == "rebuild":
            sys.exit(_rebuild(args))
    except CatalogError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)


_DB_STATES = {"current": "up to date", "behind": "behind: `pinax db upgrade` brings it up to date",
              "new": "empty: `pinax db upgrade` creates it", "ahead": "newer than this Pinax",
              "legacy": "made before migrations (Pinax 0.9.0 or earlier): `pinax db upgrade` adopts it"}


def _db(args: argparse.Namespace) -> int:
    from . import migrate
    from .db import get_engine
    from .tags import ensure_defaults

    engine = get_engine()
    if args.db_command == "status":
        with engine.connect() as conn:
            found, rev = migrate.state(conn)
        print(f"Catalog:  {migrate.describe(engine)}")
        print(f"Revision: {rev or 'none'} ({_DB_STATES[found]})")
        print(f"Newest:   {migrate.head()}")
        return 0 if found == "current" else 1
    if args.db_command == "copy":
        return _db_copy(engine, args)
    if args.db_command == "revision":
        path = migrate.revision(engine, args.message)
        print(f"Wrote {path}: read it, then run the migration tests (on PostgreSQL too).")
        return 0
    if args.sql:
        print(migrate.sql(engine), end="")
        return 0
    applied = migrate.upgrade(engine)
    ensure_defaults()
    print(f"Upgraded the catalog to revision {migrate.head()} (applied {', '.join(applied)})." if applied
          else f"The catalog is up to date (revision {migrate.head()}).")
    return 0


def _db_copy(engine, args: argparse.Namespace) -> int:
    from . import dbcopy, migrate
    from .config import Settings
    from .db import make_engine

    def url(given: str) -> str:  # PostgreSQL's own form of URL, with Pinax's driver, as for PINAX_DATABASE_URL
        return Settings(database_url=given).db_url

    source = make_engine(url(args.source)) if args.source else engine
    target = make_engine(url(args.target))
    print(f"Copying {migrate.describe(source)} into {migrate.describe(target)}")
    try:
        copied = dbcopy.copy(source, target)
    except dbcopy.CopyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        target.dispose()
    print(f"Copied {sum(copied.values()):,} rows in {sum(1 for n in copied.values() if n)} tables, and every count "
          f"matches. Set PINAX_DATABASE_URL to the target (without its password) and start Pinax.")
    return 0


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


def _tags_import(args: argparse.Namespace) -> int:
    import csv

    from .db import init_db, session_scope
    from .snapshots import cold_bucket
    from .tags import import_rows

    init_db()
    try:
        with args.file.open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    with session_scope() as s:
        done, problems = import_rows(s, rows, apply_them=False, cold_from=cold_bucket())
        for line in done:
            print(("Tagged " if args.apply and not problems else "Would tag ") + line)
        for line in problems:
            print(f"Refused {line}", file=sys.stderr)
        if problems:
            print(f"{len(problems)} row(s) refused; nothing was changed.", file=sys.stderr)
            return 1
        if args.apply:
            import_rows(s, rows, apply_them=True, cold_from=cold_bucket())
        else:
            print("A dry run: nothing was changed. Run again with --apply to tag them.")
    return 0


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


def _user(args) -> None:
    """`pinax user add | password | list`: changes are audited as made from the command line, by the operating-system
    user who ran them."""
    from sqlalchemy import select

    from . import people, signin
    from .config import get_settings
    from .db import init_db, session_scope
    from .models import User

    init_db()
    local = get_settings().auth_mode == "local"
    with session_scope() as session:
        if args.user_command == "list":
            for u in session.scalars(select(User).order_by(User.is_admin.desc(), User.username)):
                state = "active" if u.active else "inactive"
                if local:
                    state += ", no password" if not u.password_hash else ", must change password" if u.must_change_password else ""
                print(f"{u.username:24} {u.display_name:28} {'administrator' if u.is_admin else '':14} {state}")
            return
        if args.user_command == "add":
            try:
                user = people.save_person(session, None, None, {
                    "username": args.username, "display_name": args.name, "email": args.email, "title": args.title,
                    "is_admin": args.admin})
            except people.PeopleError as e:
                print(f"error: {e}", file=sys.stderr)
                sys.exit(1)
            print(f"Added {user.display_name} ({user.username}){', a storage administrator' if user.is_admin else ''}.")
        else:
            user = session.scalar(select(User).where(User.username == args.username))
            if user is None:
                print(f"error: no person {args.username!r}", file=sys.stderr)
                sys.exit(1)
        if not local:
            mode = get_settings().auth_mode
            print("Pinax keeps no passwords unless PINAX_AUTH_MODE=local: "
                  + ("they sign in through single sign-on." if mode == "header" else "this is the demo, with no sign-in."))
            if args.user_command == "password":
                sys.exit(1)
            return
        try:
            password = signin.set_temporary_password(session, user, None, source="cli")
        except signin.SignInError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Temporary password for {user.username}: {password}")
        print("Give it to them privately. They must change it when they sign in; it is not shown again.")
