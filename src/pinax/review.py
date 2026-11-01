# SPDX-License-Identifier: Apache-2.0
"""The access review (A7): who has access to what, as of now, for administrators and auditors to review and sign off.

- **Site roles**: storage administrators, auditors, finance, and division heads, each with when it was given.
- **Labs and departments**: each one's folders (with when it came to own each, where Pinax recorded the change),
  members and their roles (with when each was given), and the heads of its division.
- **People**: each active person's access, the folders they see and why, when they last signed in (local sign-in),
  and their live API tokens.
- **Worth a look**: active labs with no PI or owner, people who can sign in and see nothing, tokens that can make
  changes as their person (a credential kept by a script, with whatever access its person has now), and, with local
  sign-in, people with access who have not signed in for 90 days.
- **Sign-off**: recording that the access was reviewed, with a note, is audited (`access.reviewed`) and listed;
  it changes no one's access, so auditors may record it too.

Access given before 0.12.0 has no date: Pinax did not record it. Deactivated people and labs give no access, and
are counted, not listed.
"""

import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .db import utcnow
from .models import ApiToken, AuditEvent, FolderOwner, Lab, LabFolder, LabMembership, User
from .ownership import SINCE_ALWAYS
from .serialize import iso, join_root

ROLE_LABEL = {"admin": "Storage administrator", "auditor": "Auditor", "finance": "Finance", "division": "Division head"}
MEMBER_LABEL = {"pi": "PI", "delegate": "Delegate"}
IDLE_DAYS = 90
CSV_FIELDS = ["username", "name", "access", "lab", "lab_name", "division", "sees", "since", "last_signed_in",
              "live_tokens"]


def _person(u: User) -> dict:
    return {"username": u.username, "display_name": u.display_name, "title": u.title}


def _member_label(role: str, lab: Lab) -> str:
    return "Owner" if role == "pi" and lab.kind == "department" else MEMBER_LABEL.get(role, role)


def _folder_since(session: Session) -> dict[tuple[int, str], datetime]:
    """When each folder came to its current owner, where Pinax recorded the change."""
    current = {(f.filesystem_id, f.path): f.lab_id for f in session.scalars(select(LabFolder))}
    out: dict[tuple[int, str], datetime] = {}
    for o in session.scalars(select(FolderOwner).order_by(FolderOwner.starts, FolderOwner.id)):
        key = (o.filesystem_id, o.folder)
        if current.get(key) is not None and o.lab_id == current[key] and o.starts > SINCE_ALWAYS:
            out[key] = o.starts
        elif key in out and o.lab_id != current.get(key):
            del out[key]
    return out


def gather(session: Session) -> dict:
    now = utcnow()
    local = get_settings().auth_mode == "local"
    labs = session.scalars(select(Lab).options(
        selectinload(Lab.folders).selectinload(LabFolder.filesystem),
        selectinload(Lab.memberships).selectinload(LabMembership.user)).order_by(Lab.name)).all()
    users = session.scalars(select(User).options(selectinload(User.memberships).selectinload(LabMembership.lab),
                                                 selectinload(User.roles)).order_by(User.display_name)).all()
    active_users = [u for u in users if u.active]
    since = _folder_since(session)
    by_division: dict[str, list[Lab]] = defaultdict(list)
    for lab in labs:
        if lab.active and lab.division:
            by_division[lab.division].append(lab)
    tokens: dict[int, list[ApiToken]] = defaultdict(list)
    for t in session.scalars(select(ApiToken).where(ApiToken.revoked_at.is_(None), ApiToken.expires_at > now,
                                                    ApiToken.user_id.is_not(None))):
        tokens[t.user_id].append(t)

    def folders_of(lab: Lab) -> list[dict]:
        return [{"fs": f.filesystem.slug, "folder": f.path,
                 "path": join_root(f.filesystem.display_root or f.filesystem.root, f.path),
                 "since": iso(since.get((f.filesystem_id, f.path)))} for f in lab.folders]

    roles = []
    for u in active_users:
        if u.is_admin:
            roles.append({"role": "admin", "label": ROLE_LABEL["admin"], "division": "", "person": _person(u),
                          "since": iso(u.admin_since)})
        for r in sorted(u.roles, key=lambda r: (r.role, r.division)):
            roles.append({"role": r.role, "label": ROLE_LABEL.get(r.role, r.role), "division": r.division,
                          "person": _person(u), "since": iso(r.granted_at)})
    roles.sort(key=lambda r: (list(ROLE_LABEL).index(r["role"]) if r["role"] in ROLE_LABEL else 9, r["division"],
                              r["person"]["display_name"]))
    heads: dict[str, list[str]] = defaultdict(list)
    for r in roles:
        if r["role"] == "division":
            heads[r["division"]].append(r["person"]["display_name"])

    lab_rows = []
    for lab in sorted(labs, key=lambda lab: (not lab.active, lab.name)):
        if not lab.active:
            continue
        members = sorted((m for m in lab.memberships if m.user.active), key=lambda m: (m.role != "pi", m.user.display_name))
        lab_rows.append({
            "slug": lab.slug, "name": lab.name, "kind": lab.kind, "division": lab.division, "folders": folders_of(lab),
            "members": [{**_person(m.user), "role": m.role, "label": _member_label(m.role, lab),
                         "since": iso(m.granted_at), "last_login_at": iso(m.user.last_login_at)} for m in members],
            "heads": sorted(heads.get(lab.division, [])) if lab.division else []})

    people = []
    for u in active_users:
        own = [m for m in u.memberships if m.lab.active]
        divisions = sorted(r.division for r in u.roles if r.role == "division")
        role_names = {r.role for r in u.roles}
        access = (["Storage administrator"] if u.is_admin else []) + \
            [ROLE_LABEL[r] for r in ("auditor", "finance") if r in role_names] + \
            [f"Head of {d}" for d in divisions] + \
            [f"{_member_label(m.role, m.lab)}, {m.lab.name}" for m in sorted(own, key=lambda m: m.lab.name)]
        sees: list[dict] = []
        seen: set[str] = set()
        for lab, via in [(m.lab, m.lab.name) for m in own] + \
                [(lab, f"head of {d}") for d in divisions for lab in by_division.get(d, [])]:
            for f in folders_of(lab):
                if f["path"] not in seen:
                    seen.add(f["path"])
                    sees.append({"path": f["path"], "lab": lab.name, "via": via})
        live = tokens.get(u.id, [])
        people.append({
            **_person(u), "access": access, "everything": u.is_admin or "auditor" in role_names,
            "charges": "finance" in role_names, "folders": sorted(sees, key=lambda f: f["path"]),
            "created_at": iso(u.created_at), "last_login_at": iso(u.last_login_at),
            "tokens": len(live), "tokens_with_access": sum(1 for t in live if not t.read_only)})

    attention = []
    for lab in lab_rows:
        if not any(m["role"] == "pi" for m in lab["members"]):
            attention.append({"kind": "no_pi", "text": f"{lab['name']} has no {'owner' if lab['kind'] == 'department' else 'PI'}",
                              "href": f"#/access/labs/{lab['slug']}"})
    for p in people:
        if not p["access"]:
            attention.append({"kind": "no_access", "text": f"{p['display_name']} can sign in and sees nothing",
                              "href": f"#/access/people/{p['username']}"})
    for u in active_users:
        for t in tokens.get(u.id, []):
            if not t.read_only:
                attention.append({"kind": "token", "text": f"The token “{t.name}” can make changes as {u.display_name}, "
                                  "not only read", "href": f"#/access/people/{u.username}",
                                  "expires_at": iso(t.expires_at), "last_used_at": iso(t.last_used_at)})
    if local:
        idle = now - timedelta(days=IDLE_DAYS)
        for p, u in zip(people, active_users, strict=True):
            if p["access"] and (u.last_login_at or u.created_at) < idle:
                attention.append({"kind": "idle", "text": f"{u.display_name} has not signed in for {IDLE_DAYS} days"
                                  + ("" if u.last_login_at else " (never)"), "href": f"#/access/people/{u.username}"})

    reviews = session.scalars(select(AuditEvent).where(AuditEvent.action == "access.reviewed")
                              .options(selectinload(AuditEvent.actor)).order_by(AuditEvent.ts.desc()).limit(10)).all()
    return {
        "as_of": iso(now), "local": local, "idle_days": IDLE_DAYS,
        "counts": {"people": sum(1 for p in people if p["access"]), "labs": len(lab_rows), "roles": len(roles),
                   "attention": len(attention), "no_access": sum(1 for p in people if not p["access"]),
                   "inactive_people": len(users) - len(active_users),
                   "inactive_labs": sum(1 for lab in labs if not lab.active)},
        "roles": roles, "labs": lab_rows, "people": people, "attention": attention,
        "reviews": [{"ts": iso(e.ts), "by": e.actor.display_name if e.actor else None, "summary": e.summary,
                     "note": (e.detail or {}).get("note", "")} for e in reviews],
    }


def sign_off(session: Session, actor: User, note: str = "") -> None:
    """Record that the access was reviewed, as it is now: audited, with the counts and a note."""
    note = (note or "").strip()
    if len(note) > 2000:
        raise ValueError("A note can have at most 2,000 characters")
    counts = gather(session)["counts"]
    session.add(AuditEvent(actor_id=actor.id, action="access.reviewed",
                           summary=f"{actor.display_name} reviewed access: {counts['people']} people with access, "
                                   f"{counts['labs']} labs and departments, {counts['roles']} site roles",
                           detail={"note": note, "counts": counts, "source": "app"}))


def csv_text(data: dict) -> str:
    """One row for each piece of access each active person has (or one saying they have none)."""
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(CSV_FIELDS)
    roles = defaultdict(list)
    for r in data["roles"]:
        roles[r["person"]["username"]].append(r)
    members = defaultdict(list)
    for lab in data["labs"]:
        for m in lab["members"]:
            members[m["username"]].append((lab, m))
    def row(p: dict, access: str, lab: str = "", lab_name: str = "", division: str = "", sees: str = "",
            since: str | None = None) -> None:
        out.writerow([p["username"], p["display_name"], access, lab, lab_name, division, sees, since or "",
                      p["last_login_at"] or "", p["tokens"]])

    for p in data["people"]:
        for r in roles[p["username"]]:
            sees = "everything" if r["role"] in ("admin", "auditor") else "every lab's charges" \
                if r["role"] == "finance" else "; ".join(f["path"] for f in p["folders"] if f["via"] == f"head of {r['division']}")
            row(p, r["label"], division=r["division"], sees=sees, since=r["since"])
        for lab, m in members[p["username"]]:
            row(p, m["label"], lab["slug"], lab["name"], lab["division"], "; ".join(f["path"] for f in lab["folders"]),
                m["since"])
        if not p["access"]:
            row(p, "none")
    return buf.getvalue()
