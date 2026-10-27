# SPDX-License-Identifier: Apache-2.0
"""Write small fs-inventory-shaped scans from a list of files, for exact tests."""

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import duckdb

COLUMNS = [
    "path", "parent_path", "name", "extension", "path_raw", "name_flags", "kind", "depth", "dir_id", "parent_id", "dev",
    "ino", "nlink", "uid", "gid", "mode", "size", "alloc_bytes", "atime", "mtime", "ctime", "btime", "rdev",
    "dir_entries", "dir_files", "dir_subdirs", "dir_bytes", "dir_alloc_bytes", "is_mount_point", "time_flags", "error",
    "symlink_target", "symlink_target_raw",
]


def _ext(name: str) -> str | None:
    if name.startswith(".") or name.endswith(".") or "." not in name:
        return None
    e = name.rsplit(".", 1)[1].lower()
    return e if len(e) <= 32 else None


def write_scan(out: Path, root: str, files: list[dict], *, started: datetime | None = None, status: str = "complete",
               errors: dict[str, str] | None = None, users: dict[int, str | None] | None = None) -> dict:
    """`files`: dicts with `path` (relative to root), `size`, `age_days` (since `started`), and optional
    `atime_days`, `uid`, `name_flags`. Directories are inferred; `errors` maps relative directory paths to
    an error string (the directory is recorded as unreadable)."""
    started = started or datetime(2026, 10, 1, tzinfo=UTC)
    ref = started.timestamp()
    root = root.rstrip("/")
    dirs: dict[str, int] = {"": 1}
    for f in files:
        parts = f["path"].split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.setdefault("/".join(parts[:i]), 0)
    for d in sorted(dirs):
        if d:
            dirs[d] = len(dirs) + 1 + sorted(dirs).index(d)
    errors = errors or {}
    rows = []

    def full(rel: str) -> str:
        return f"{root}/{rel}" if rel else root

    for rel, did in sorted(dirs.items()):
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        kids = [d for d in dirs if d and (d.rsplit("/", 1)[0] if "/" in d else "") == rel and d != rel]
        kfiles = [f for f in files if (f["path"].rsplit("/", 1)[0] if "/" in f["path"] else "") == rel]
        err = errors.get(rel)
        rows.append({
            "path": full(rel), "parent_path": full(parent) if rel else "", "name": rel.rsplit("/", 1)[-1] if rel else root.rsplit("/", 1)[-1],
            "extension": None, "kind": "dir", "depth": 0 if not rel else rel.count("/") + 1, "dir_id": did,
            "parent_id": dirs[parent] if rel else 0, "ino": did, "uid": 1000, "gid": 1000, "mode": 0o40755, "size": 4096,
            "alloc_bytes": 4096, "mtime": ref - 86400, "atime": ref - 86400,
            "dir_entries": None if err else len(kids) + len(kfiles), "error": err, "name_flags": 0,
        })
    for i, f in enumerate(files):
        rel = f["path"]
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        name = rel.rsplit("/", 1)[-1]
        mtime = ref - f.get("age_days", 0) * 86400
        rows.append({
            "path": full(rel), "parent_path": full(parent), "name": name, "extension": _ext(name), "kind": f.get("kind", "file"),
            "depth": rel.count("/") + 1, "dir_id": None, "parent_id": dirs[parent], "ino": 10_000 + i, "uid": f.get("uid", 1000),
            "gid": 1000, "mode": 0o100644, "size": f["size"], "alloc_bytes": f.get("alloc", f["size"]), "mtime": mtime,
            "atime": ref - f.get("atime_days", f.get("age_days", 0)) * 86400, "dir_entries": None, "error": None,
            "name_flags": f.get("name_flags", 0),
        })
    (out / "entries").mkdir(parents=True)
    con = duckdb.connect()
    con.execute("""CREATE TABLE e (path VARCHAR, parent_path VARCHAR, name VARCHAR, extension VARCHAR, kind VARCHAR,
        depth INTEGER, dir_id BIGINT, parent_id BIGINT, ino UBIGINT, uid BIGINT, gid BIGINT, mode INTEGER, size BIGINT,
        alloc_bytes BIGINT, mtime DOUBLE, atime DOUBLE, dir_entries BIGINT, error VARCHAR, name_flags INTEGER)""")
    keys = ["path", "parent_path", "name", "extension", "kind", "depth", "dir_id", "parent_id", "ino", "uid", "gid", "mode",
            "size", "alloc_bytes", "mtime", "atime", "dir_entries", "error", "name_flags"]
    con.executemany(f"INSERT INTO e VALUES ({', '.join('?' for _ in keys)})", [[r[k] for k in keys] for r in rows])
    con.execute(f"""COPY (SELECT path, parent_path, name, extension, NULL::BLOB AS path_raw, name_flags, kind, depth, dir_id,
        parent_id, 1::UBIGINT AS dev, ino, 1::BIGINT AS nlink, uid, gid, mode, size, alloc_bytes, to_timestamp(atime) AS atime,
        to_timestamp(mtime) AS mtime, to_timestamp(mtime) AS ctime, NULL::TIMESTAMPTZ AS btime, NULL::UBIGINT AS rdev,
        dir_entries, NULL::BIGINT AS dir_files, NULL::BIGINT AS dir_subdirs, NULL::BIGINT AS dir_bytes,
        NULL::BIGINT AS dir_alloc_bytes, false AS is_mount_point, 0 AS time_flags, error, NULL::VARCHAR AS symlink_target,
        NULL::BLOB AS symlink_target_raw FROM e) TO '{out / "entries" / "part-w00-000000.parquet"}' (FORMAT parquet)""")
    if users:
        (out / "users").mkdir()
        con.execute("CREATE TABLE u (uid BIGINT, name VARCHAR)")
        con.executemany("INSERT INTO u VALUES (?, ?)", list(users.items()))
        con.execute(f"COPY u TO '{out / 'users' / 'part-000000.parquet'}' (FORMAT parquet)")
    if errors:
        (out / "errors").mkdir()
        con.execute("CREATE TABLE er (path VARCHAR, path_raw BLOB, op VARCHAR, errno INTEGER, errno_name VARCHAR, message VARCHAR, time TIMESTAMPTZ)")
        con.executemany("INSERT INTO er VALUES (?, NULL, 'open', 13, 'EACCES', ?, ?)",
                        [(full(rel), e, started) for rel, e in errors.items()])
        con.execute(f"COPY er TO '{out / 'errors' / 'part-000000.parquet'}' (FORMAT parquet)")
    con.close()
    manifest = {
        "tool": "fs-inventory", "tool_version": "1.0.0", "schema_version": 3, "scan_id": str(uuid.uuid4()),
        "status": status, "host": os.uname().nodename, "roots": [{"path": root, "path_b64": None}], "format": "parquet",
        "columns": COLUMNS, "started_at": started.isoformat().replace("+00:00", "Z"),
        "finished_at": started.isoformat().replace("+00:00", "Z"), "duration_seconds": 1.0,
        "totals": {"entries": len(rows), "bytes": sum(f["size"] for f in files), "errors": len(errors)},
    }
    (out / "manifest.json").write_text(json.dumps(manifest))
    return manifest
