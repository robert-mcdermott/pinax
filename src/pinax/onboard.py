# SPDX-License-Identifier: Apache-2.0
"""Bulk onboarding: labs, people, memberships, and folder ownership from CSV files (`pinax import`).

Four files, each optional, in the format described in docs/IMPORT.md:

    labs.csv         slug, name, kind, division, research_area, active
    people.csv       username, display_name, email, title, is_admin, active
    memberships.csv  lab, username, role          (role pi, delegate, or none to remove)
    folders.csv      filesystem, folder, lab      (lab none to unmap)

Rows are matched to the catalog by their key (a lab's slug, a person's username, and so on) and either create,
update, or remove something, or change nothing. A column that is present sets its field on every row; a column
that is absent leaves the field as it is. Every change is listed first: the import is a dry run unless asked to
apply it, and it applies all of its changes or none. Any row that is ambiguous or invalid (a key listed twice,
a reference to something that does not exist, a value Pinax does not accept) refuses the whole import, with the
file and line of each problem. Applied changes are audited one by one, with the same actions as the interface.

This is a stop-gap until the authoritative source of folder ownership is chosen (pinax-spec/roadmap.md); that
source can write these files.
"""

import csv
import getpass
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .labs import NONE, slug_problem
from .models import AuditEvent, Filesystem, Lab, LabFolder, LabKind, LabMembership, User
from .ownership import record_change as record_owner_change
from .people import email_problem, username_problem

ROLES = ("pi", "delegate")
TRUE, FALSE = {"true", "yes", "1"}, {"false", "no", "0"}

# file -> (required columns, optional columns)
FORMATS = {
    "labs": (("slug", "name"), ("kind", "division", "research_area", "active")),
    "people": (("username", "display_name", "email"), ("title", "is_admin", "active")),
    "memberships": (("lab", "username", "role"), ()),
    "folders": (("filesystem", "folder", "lab"), ()),
}


@dataclass
class Change:
    kind: str  # lab, person, membership, folder
    op: str  # create, update, remove
    key: str
    fields: dict[str, tuple] = field(default_factory=dict)  # name -> (before, after)
    where: str = ""  # file:line

    def text(self) -> str:
        sign = {"create": "+", "update": "~", "remove": "-"}[self.op]
        parts = []
        for name, (before, after) in self.fields.items():
            parts.append(f"{name} {_show(after)}" if self.op == "create" else f"{name} {_show(before)} -> {_show(after)}")
        return f"  {sign} {self.kind} {self.key}" + (f": {', '.join(parts)}" if parts else "")


def _show(v) -> str:
    return "none" if v is None else repr(v) if isinstance(v, str) else str(v).lower() if isinstance(v, bool) else str(v)


@dataclass
class Plan:
    files: dict[str, Path]
    changes: list[Change] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    unchanged: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problems

    def text(self) -> str:
        lines = []
        for name in FORMATS:
            if name not in self.files:
                continue
            mine = [c for c in self.changes if c.where.startswith(self.files[name].name + ":")]
            counts = [f"{sum(c.op == op for c in mine)} to {op}" for op in ("create", "update", "remove")
                      if any(c.op == op for c in mine)]
            counts.append(f"{self.unchanged.get(name, 0)} unchanged")
            lines.append(f"{self.files[name]}: {', '.join(counts)}")
            lines += [c.text() for c in mine]
        if self.problems:
            lines.append(f"Refused: {len(self.problems)} problem(s); nothing changes until they are fixed:")
            lines += [f"  {p}" for p in self.problems]
        return "\n".join(lines)


def _read(path: Path, name: str, problems: list[str]) -> list[tuple[str, dict]]:
    """Rows of one file as (file:line, {column: value}); problems with the file itself are reported."""
    required, optional = FORMATS[name]
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = [h.strip().lower() for h in next(reader, [])]
            rows = [(reader.line_num, r) for r in reader]
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        problems.append(f"{path.name}: cannot read it: {e}")
        return []
    missing = [c for c in required if c not in header]
    unknown = [c for c in header if c not in required + optional]
    twice = sorted({c for c in header if header.count(c) > 1})
    if missing or unknown or twice:
        for what, cols in (("is missing column", missing), ("has unknown column", unknown), ("repeats column", twice)):
            if cols:
                problems.append(f"{path.name}: {what}{'s' if len(cols) > 1 else ''} {', '.join(cols)} "
                                f"(columns: {', '.join(required + optional)})")
        return []
    out = []
    for line, cells in rows:
        if not any(c.strip() for c in cells):
            continue
        where = f"{path.name}:{line}"
        if len(cells) != len(header):
            problems.append(f"{where}: {len(cells)} values for {len(header)} columns")
            continue
        row = {h: c.strip() for h, c in zip(header, cells, strict=True)}
        empty = [c for c in required if not row[c]]
        if empty:
            problems.append(f"{where}: {', '.join(empty)} {'is' if len(empty) == 1 else 'are'} empty")
            continue
        out.append((where, row))
    return out


def _bool(value: str, column: str, where: str, problems: list[str]) -> bool | None:
    v = value.lower()
    if v in TRUE:
        return True
    if v in FALSE:
        return False
    problems.append(f"{where}: {column} must be true or false, not {value!r}")
    return None


def _once(rows: list[tuple[str, dict]], key, problems: list[str], what: str) -> list[tuple[str, dict]]:
    """Refuse keys listed more than once: which row is meant would be ambiguous."""
    seen: dict = {}
    for where, row in rows:
        seen.setdefault(key(row), []).append(where)
    dup = {k for k, ws in seen.items() if len(ws) > 1}
    for k in sorted(dup, key=str):
        problems.append(f"{what} {k if isinstance(k, str) else ':'.join(k)} is listed more than once "
                        f"({', '.join(seen[k])})")
    return [(w, r) for w, r in rows if key(r) not in dup]


def plan_import(session: Session, *, labs: Path | None = None, people: Path | None = None,
                memberships: Path | None = None, folders: Path | None = None) -> Plan:
    """Work out every change the files would make, and every problem that refuses them."""
    files = {k: v for k, v in (("labs", labs), ("people", people), ("memberships", memberships),
                               ("folders", folders)) if v is not None}
    plan = Plan(files={k: Path(v) for k, v in files.items()}, unchanged=dict.fromkeys(files, 0))
    P = plan.problems
    cat_labs = {lab.slug: lab for lab in session.scalars(select(Lab))}
    cat_users = {u.username: u for u in session.scalars(select(User))}
    cat_fs = {f.slug: f for f in session.scalars(select(Filesystem))}
    lab_slugs, usernames = set(cat_labs), set(cat_users)

    if "labs" in files:
        rows = _once(_read(plan.files["labs"], "labs", P), lambda r: r["slug"], P, "lab")
        for where, r in rows:
            slug = r["slug"]
            if problem := slug_problem(slug):
                P.append(f"{where}: {problem}")
                continue
            want = {"name": r["name"]}
            if "kind" in r:
                if r["kind"] not in LabKind.ALL:
                    P.append(f"{where}: kind must be {' or '.join(LabKind.ALL)}, not {r['kind']!r}")
                    continue
                want["kind"] = r["kind"]
            for col in ("division", "research_area"):
                if col in r:
                    want[col] = r[col]
            if "active" in r:
                if (v := _bool(r["active"], "active", where, P)) is None:
                    continue
                want["active"] = v
            _diff(plan, "lab", "labs", slug, cat_labs.get(slug), want, where,
                  defaults={"kind": LabKind.LAB, "division": "", "research_area": "", "active": True})
            lab_slugs.add(slug)

    if "people" in files:
        rows = _once(_read(plan.files["people"], "people", P), lambda r: r["username"], P, "person")
        lower: dict[str, str] = {u.lower(): u for u in cat_users}
        for where, r in rows:
            name = r["username"]
            if problem := username_problem(name, lower.values()):
                P.append(f"{where}: {problem}")
                continue
            lower[name.lower()] = name
            if problem := email_problem(r["email"]):
                P.append(f"{where}: {problem}")
                continue
            want = {"display_name": r["display_name"], "email": r["email"]}
            if "title" in r:
                want["title"] = r["title"]
            bad = False
            for col in ("is_admin", "active"):
                if col in r:
                    v = _bool(r[col], col, where, P)
                    bad = bad or v is None
                    want[col] = v
            if bad:
                continue
            _diff(plan, "person", "people", name, cat_users.get(name), want, where,
                  defaults={"title": "", "is_admin": False, "active": True})
            usernames.add(name)

    if "memberships" in files:
        rows = _once(_read(plan.files["memberships"], "memberships", P), lambda r: (r["lab"], r["username"]), P,
                     "membership")
        current = {(m.lab.slug, m.user.username): m.role for m in session.scalars(
            select(LabMembership).options(selectinload(LabMembership.lab), selectinload(LabMembership.user)))}
        unchanged = 0
        for where, r in rows:
            lab, user, role = r["lab"], r["username"], r["role"].lower()
            if lab not in lab_slugs:
                P.append(f"{where}: no lab or department {lab!r} (not in the catalog or labs.csv)")
                continue
            if user not in usernames:
                P.append(f"{where}: no person {user!r} (not in the catalog or people.csv)")
                continue
            if role not in (*ROLES, NONE):
                P.append(f"{where}: role must be pi, delegate, or none, not {r['role']!r}")
                continue
            before = current.get((lab, user))
            after = None if role == NONE else role
            if before == after:
                unchanged += 1
            else:
                op = "create" if before is None else "remove" if after is None else "update"
                plan.changes.append(Change("membership", op, f"{user} in {lab}", {"role": (before, after)}, where))
        plan.unchanged["memberships"] = unchanged

    if "folders" in files:
        rows = _once(_read(plan.files["folders"], "folders", P), lambda r: (r["filesystem"], r["folder"]), P,
                     "folder")
        current = {(f.filesystem.slug, f.path): f.lab.slug for f in session.scalars(
            select(LabFolder).options(selectinload(LabFolder.lab), selectinload(LabFolder.filesystem)))}
        unchanged = 0
        for where, r in rows:
            fs, folder, lab = r["filesystem"], r["folder"], r["lab"]
            if fs not in cat_fs:
                P.append(f"{where}: no filesystem {fs!r}; register it with `pinax filesystem add`")
                continue
            if "/" in folder or folder in (".", ".."):
                P.append(f"{where}: {folder!r} is not a top-level folder name (one name, directly under the root)")
                continue
            if lab != NONE and lab not in lab_slugs:
                P.append(f"{where}: no lab or department {lab!r} (not in the catalog or labs.csv)")
                continue
            before = current.get((fs, folder))
            after = None if lab == NONE else lab
            if before == after:
                unchanged += 1
            else:
                op = "create" if before is None else "remove" if after is None else "update"
                plan.changes.append(Change("folder", op, f"{fs}:{folder}", {"owner": (before, after)}, where))
        plan.unchanged["folders"] = unchanged
    return plan


def _diff(plan: Plan, kind: str, file: str, key: str, existing, want: dict, where: str, defaults: dict) -> None:
    if existing is None:
        values = {**want, **{k: v for k, v in defaults.items() if k not in want}}
        plan.changes.append(Change(kind, "create", key, {k: (None, v) for k, v in values.items()}, where))
        return
    fields = {k: (getattr(existing, k), v) for k, v in want.items() if getattr(existing, k) != v}
    if fields:
        plan.changes.append(Change(kind, "update", key, fields, where))
    else:
        plan.unchanged[file] = plan.unchanged.get(file, 0) + 1


def apply_import(session: Session, plan: Plan) -> int:
    """Make the plan's changes in one transaction, auditing each. Refuses a plan with problems."""
    if not plan.ok:
        raise ValueError("the import has problems; fix them first")
    who = getpass.getuser()
    files = {k: {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for k, p in plan.files.items()}
    labs = {lab.slug: lab for lab in session.scalars(select(Lab))}
    users = {u.username: u for u in session.scalars(select(User))}

    def audit(action: str, summary: str, detail: dict) -> None:
        session.add(AuditEvent(action=action, summary=summary,
                               detail={**detail, "source": "import", "by": who}))

    for c in plan.changes:
        values = {k: after for k, (_, after) in c.fields.items()}
        if c.kind == "lab":
            if c.op == "create":
                labs[c.key] = lab = Lab(slug=c.key, **values)
                session.add(lab)
            else:
                for k, v in values.items():
                    setattr(labs[c.key], k, v)
            audit(f"lab.{c.op}d", f"Lab {c.key} {c.op}d by import", {"lab": c.key, "fields": _jsonable(c.fields)})
        elif c.kind == "person":
            if c.op == "create":
                users[c.key] = user = User(username=c.key, **values)
                session.add(user)
            else:
                for k, v in values.items():
                    setattr(users[c.key], k, v)
            audit(f"person.{c.op}d", f"{c.key} {c.op}d by import", {"username": c.key, "fields": _jsonable(c.fields)})
        session.flush()
        if c.kind == "membership":
            username, slug = c.key.split(" in ", 1)
            lab, user = labs[slug], users[username]
            m = session.scalar(select(LabMembership).where(LabMembership.lab_id == lab.id,
                                                           LabMembership.user_id == user.id))
            role = values["role"]
            if role is None:
                session.delete(m)
            elif m:
                m.role = role
            else:
                session.add(LabMembership(lab_id=lab.id, user_id=user.id, role=role))
            audit("lab.member", f"{user.display_name} {'removed from' if role is None else 'set as ' + role + ' in'} "
                  f"{lab.name} by import", {"lab": slug, "username": username, "role": role})
        elif c.kind == "folder":
            fs_slug, folder = c.key.split(":", 1)
            fs = session.scalar(select(Filesystem).where(Filesystem.slug == fs_slug))
            current = session.scalar(select(LabFolder).where(LabFolder.filesystem_id == fs.id,
                                                             LabFolder.path == folder))
            before, after = c.fields["owner"]
            before_id = current.lab_id if current else None
            if after is None:
                session.delete(current)
            elif current:
                current.lab_id = labs[after].id
            else:
                session.add(LabFolder(lab_id=labs[after].id, filesystem_id=fs.id, path=folder))
            record_owner_change(session, fs.id, folder, before_id, labs[after].id if after else None)
            audit("folder.mapped", f"{fs.name}: {folder} mapped to {after or 'no one'} (was {before or 'unmapped'}) "
                  "by import", {"fs": fs_slug, "folder": folder, "before": before, "after": after})
        session.flush()
    session.add(AuditEvent(action="import.applied", summary=f"Imported {len(plan.changes)} change(s) from CSV",
                           detail={"files": files, "changes": len(plan.changes), "by": who}))
    session.commit()
    return len(plan.changes)


def _jsonable(fields: dict[str, tuple]) -> dict:
    return {k: {"before": b, "after": a} for k, (b, a) in fields.items()}
