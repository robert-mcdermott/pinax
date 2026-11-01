# SPDX-License-Identifier: Apache-2.0
"""JSON views of catalog objects."""

from datetime import datetime

from .models import Filesystem, FolderStat, Lab, LabFolder, User


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper() if parts else "?"


def lab_brief(lab: Lab | None) -> dict | None:
    if lab is None:
        return None
    return {"slug": lab.slug, "name": lab.name, "kind": lab.kind, "pi": lab.pi_name, "active": lab.active}


def lab_json(lab: Lab) -> dict:
    return {
        **lab_brief(lab),
        "division": lab.division, "research_area": lab.research_area,
        "folders": [folder_json(f) for f in lab.folders],
        "members": [
            {"username": m.user.username, "display_name": m.user.display_name, "role": m.role,
             "initials": initials(m.user.display_name), "active": m.user.active}
            for m in sorted(lab.memberships, key=lambda m: (m.role != "pi", m.user.display_name))
        ],
    }


def folder_json(f: LabFolder) -> dict:
    return {"filesystem": f.filesystem.slug, "folder": f.path, "path": join_root(f.filesystem.root, f.path)}


def join_root(root: str, name: str) -> str:
    return (root if root.endswith("/") else root + "/") + name if name else root


def fs_json(fs: Filesystem) -> dict:
    return {"slug": fs.slug, "name": fs.name, "root": fs.root, "display_root": fs.display_root or fs.root,
            "description": fs.description, "active": fs.active}


def user_json(u: User) -> dict:
    labs = [
        {"slug": m.lab.slug, "name": m.lab.name, "kind": m.lab.kind, "role": m.role,
         "folders": [folder_json(f) for f in m.lab.folders]}
        for m in sorted(u.memberships, key=lambda m: m.lab.name) if m.lab.active
    ]
    return {
        "username": u.username, "display_name": u.display_name, "email": u.email, "title": u.title,
        "is_admin": u.is_admin, "active": u.active, "initials": initials(u.display_name), "labs": labs,
        "roles": [{"role": r.role, "division": r.division, "since": iso(r.granted_at)}
                  for r in sorted(u.roles, key=lambda r: (r.role, r.division))],
        # Local sign-in: whether a password is set, must be changed, and until when the account is locked.
        "has_password": bool(u.password_hash), "must_change_password": bool(u.must_change_password),
        "password_set_at": iso(u.password_set_at), "locked_until": iso(u.locked_until),
        "last_login_at": iso(u.last_login_at),
    }


def folder_stat_json(s: FolderStat | None) -> dict | None:
    if s is None:
        return None
    return {
        "snapshot_id": s.snapshot_id, "taken_at": iso(s.taken_at), "bytes": s.bytes, "alloc_bytes": s.alloc_bytes,
        "files": s.files, "dirs": s.dirs, "cold_bytes": s.cold_bytes, "mean_age_days": s.mean_age_days,
        "newest": iso(s.newest_mtime), "oldest": iso(s.oldest_mtime), "incomplete": s.incomplete,
        "age_bytes": s.age_bytes, "cat_bytes": s.cat_bytes,
    }
