# SPDX-License-Identifier: Apache-2.0
"""Make an installation of Pinax for the load test (`bench/load.py`), in a folder of its own.

Three synthetic scans of one filesystem a month apart, the last one today (for history and the change map), ingested
in order; a lab for each top-level folder, with a PI and members, the labs spread over five divisions; storage
administrators, an auditor, finance, and a head for each division; a price and a free tier, so charges are not
nothing; and one password for everyone, as local sign-in keeps it. On SQLite in the folder, or on an empty PostgreSQL
database with `--database-url`.

    uv run python bench/load_setup.py /scratch/load-10m --files 10000000

Writes `pinax.env` (the settings to run Pinax with), `people.json` (who is who, for the load test), and `password`.
Nothing in it is real: the names, folders, and password are made up.
"""

import argparse
import csv
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = "/synth/research"
FS = "synth"
DIVISIONS = ["Basic Sciences", "Clinical Research", "Public Health", "Human Biology", "Vaccine and Infectious Disease"]
SCANS = 3
MONTH = 30


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", type=Path, help="A new or empty folder for the installation")
    ap.add_argument("--files", type=int, default=10_000_000, help="Files in each scan (directories are added)")
    ap.add_argument("--labs", type=int, default=200, help="Labs, one for each top-level folder")
    ap.add_argument("--members", type=int, default=2, help="Members of each lab besides its PI")
    ap.add_argument("--database-url", default="", help="An empty PostgreSQL database, instead of SQLite")
    args = ap.parse_args()

    folder = args.folder.resolve()
    if folder.exists() and any(folder.iterdir()):
        sys.exit(f"error: {folder} is not empty")
    folder.mkdir(parents=True, exist_ok=True)
    env = {"PINAX_ENVIRONMENT": "dev", "PINAX_AUTH_MODE": "local", "PINAX_VAR_ROOT": str(folder / "var"),
           "PINAX_SCAN_ROOT": str(folder / "scans")}
    if args.database_url:
        env["PINAX_DATABASE_URL"] = args.database_url
    (folder / "pinax.env").write_text("".join(f"{k}={v}\n" for k, v in env.items()))
    os.environ.update(env)
    t0 = time.monotonic()

    # ---- Scans: the first two months ago, each later one evolved from the one before ----
    synth = Path(__file__).with_name("synth_scan.py")
    today = datetime.now(UTC).replace(microsecond=0)
    scans = []
    for n in range(SCANS):
        started = today - timedelta(days=MONTH * (SCANS - 1 - n))
        out = folder / "scans" / FS / started.strftime("%Y%m%dT%H%M%SZ")
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(synth), "-o", str(out)]
        cmd += (["--files", str(args.files), "--tops", str(args.labs), "--root", ROOT, "--started", started.isoformat()]
                if not scans else ["--evolve", str(scans[-1]), "--days", str(MONTH)])
        _run(cmd)
        scans.append(out)

    # ---- The filesystem, labs, people, and folders, as `pinax import` takes them ----
    pinax = str(Path(sys.executable).with_name("pinax"))
    _run([pinax, "filesystem", "add", FS, ROOT, "--name", "Synthetic", "--description", "Load test"])
    people, labs = _write_import(folder / "import", args.labs, args.members)
    _run([pinax, "import", str(folder / "import"), "--apply"], quiet=True)
    for scan in scans:
        _run([pinax, "ingest", str(scan)])

    # ---- What `pinax import` does not set: site roles, prices, and passwords ----
    password = "load-" + secrets.token_urlsafe(18)
    _finish_catalog(people, password, scans[0])
    (folder / "password").write_text(password + "\n")
    (folder / "password").chmod(0o600)
    projects = _projects(scans[-1])
    for lab in labs:
        lab["projects"] = projects.get(lab["folder"], [])
    (folder / "people.json").write_text(json.dumps({"filesystem": FS, "root": ROOT, "labs": labs, "people": people},
                                                   indent=1))
    roles = {}
    for p in people:
        roles[p["role"]] = roles.get(p["role"], 0) + 1
    print(f"Ready in {time.monotonic() - t0:.0f} s: {folder}")
    print("  " + ", ".join(f"{n} {role}" for role, n in roles.items()))
    print(f"  Run: uv run python bench/load.py {folder}")


def _run(cmd: list[str], quiet: bool = False) -> None:
    print("$", " ".join(Path(c).name if i == 0 else c for i, c in enumerate(cmd)), flush=True)
    done = subprocess.run(cmd, check=False, capture_output=quiet, text=True)
    if done.returncode != 0:
        sys.exit(f"error: {Path(cmd[0]).name} failed ({done.returncode}){': ' + done.stderr if quiet else ''}")


def _write_import(out: Path, labs: int, members: int) -> tuple[list[dict], list[dict]]:
    """labs.csv, people.csv, memberships.csv, and folders.csv; returns who is who, and the labs."""
    out.mkdir()
    people, rows = [], {"labs": [], "people": [], "memberships": [], "folders": []}
    for n in range(labs):
        lab, top = f"lab{n:04d}", f"lab_{n:04d}"
        rows["labs"].append({"slug": lab, "name": f"Lab {n:04d}", "kind": "lab", "division": DIVISIONS[n % len(DIVISIONS)]})
        rows["folders"].append({"filesystem": FS, "folder": top, "lab": lab})
        for k in range(members + 1):
            username, role = (f"pi{n:04d}", "pi") if k == 0 else (f"member{n:04d}{k}", "member")
            rows["people"].append({"username": username, "display_name": f"PI {n:04d}" if k == 0 else f"Member {n:04d}-{k}",
                                   "email": f"{username}@example.org", "is_admin": "false"})
            rows["memberships"].append({"lab": lab, "username": username, "role": "pi" if k == 0 else "delegate"})
            people.append({"username": username, "role": role, "lab": lab, "folder": f"{ROOT}/{top}"})
    site = [("admin", n) for n in range(3)] + [("auditor", 0), ("finance", 0)] + [("division", n) for n in range(len(DIVISIONS))]
    for role, n in site:
        username = f"{role}{n}"
        rows["people"].append({"username": username, "display_name": f"{role.title()} {n}", "email": f"{username}@example.org",
                               "is_admin": "true" if role == "admin" else "false"})
        people.append({"username": username, "role": role, **({"division": DIVISIONS[n]} if role == "division" else {})})
    for name, data in rows.items():
        with open(out / f"{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in data for k in r)))
            w.writeheader()
            w.writerows(data)
    labs_out = [{"slug": r["slug"], "division": r["division"], "folder": f"{ROOT}/{f['folder']}"}
                for r, f in zip(rows["labs"], rows["folders"], strict=True)]
    return people, labs_out


def _projects(scan: Path) -> dict[str, list[str]]:
    """Each lab folder's projects in the latest scan (some earlier ones are deleted by then)."""
    import duckdb

    rows = duckdb.sql(f"SELECT parent_path, path FROM read_parquet('{scan}/entries/*.parquet') "
                      "WHERE kind = 'dir' AND depth = 2 ORDER BY path").fetchall()
    out: dict[str, list[str]] = {}
    for parent, path in rows:
        out.setdefault(parent, []).append(path)
    return out


def _finish_catalog(people: list[dict], password: str, first_scan: Path) -> None:
    from pinax.db import session_scope, utcnow
    from pinax.models import Filesystem, FolderOwner, FreeTier, Price, User, UserRole
    from pinax.signin import hash_password

    started = datetime.fromisoformat(json.loads((first_scan / "manifest.json").read_text())["started_at"]).date()
    hashed = hash_password(password)  # one hash for everyone: scrypt is slow by design
    with session_scope() as s:
        fs = s.query(Filesystem).filter_by(slug=FS).one()
        s.add(Price(filesystem_id=fs.id, starts=started.replace(day=1), micros=5_000_000, unit="TiB"))
        s.add(FreeTier(filesystem_id=fs.id, kind="lab", starts=started.replace(day=1), bytes=2**40, unit="TiB"))
        s.query(FolderOwner).delete()  # each lab has had its folder all along, so past months' charges are its own
        users = {u.username: u for u in s.query(User)}
        for p in people:
            u = users[p["username"]]
            u.password_hash, u.password_set_at, u.must_change_password = hashed, utcnow(), False
            if p["role"] in ("auditor", "finance", "division"):
                s.add(UserRole(user_id=u.id, role=p["role"], division=p.get("division", "")))


if __name__ == "__main__":
    main()
