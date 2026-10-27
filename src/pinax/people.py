# SPDX-License-Identifier: Apache-2.0
"""People: who can sign in to Pinax, which labs and departments they belong to, and who administers it.

The People page and `pinax import` follow the same rules (`username_problem`, `email_problem`). People are never
deleted, so the audit log keeps every reference: a deactivated person cannot sign in, and keeps their lab
memberships and administrator role, unused, so reactivating them restores exactly what they had. Every change is
audited with the fields before and after. At least one active storage administrator always remains, and no one
can remove their own administrator role or deactivate themselves.
"""

import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import AuditEvent, Lab, LabMembership, User

ROLES = ("pi", "delegate")
PROFILE = ("display_name", "email", "title", "is_admin")
LIMITS = {"username": 64, "display_name": 200, "email": 200, "title": 200}


class PeopleError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def username_problem(name: str, taken: Iterable[str]) -> str | None:
    """Why `name` cannot be a new person's username, given the usernames `taken`, or None."""
    if not name:
        return "a username is required"
    if re.search(r"\s", name):
        return f"username {name!r} contains a space"
    if len(name) > LIMITS["username"]:
        return f"username {name!r} is longer than {LIMITS['username']} characters"
    other = next((t for t in taken if t.lower() == name.lower() and t != name), None)
    if other is not None:
        return f"username {name!r} differs only in case from {other!r}, so which person is meant is ambiguous"
    return None


def email_problem(email: str) -> str | None:
    if "@" not in email or re.search(r"\s", email) or email.startswith("@") or email.endswith("@"):
        return f"{email!r} is not an email address"
    return None


def active_admins(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(User).where(User.is_admin, User.active)) or 0


def _audit(session: Session, actor: User, action: str, summary: str, detail: dict) -> None:
    session.add(AuditEvent(actor_id=actor.id, action=action, summary=summary, detail={**detail, "source": "app"}))


def _text(data: dict[str, Any], key: str) -> str:
    value = str(data.get(key) or "").strip()
    if len(value) > LIMITS[key]:
        raise PeopleError(f"{key.replace('_', ' ').capitalize()} is longer than {LIMITS[key]} characters")
    return value


def _memberships(session: Session, wanted: list[dict[str, Any]]) -> dict[int, tuple[Lab, str]]:
    out: dict[int, tuple[Lab, str]] = {}
    for item in wanted:
        lab = session.scalar(select(Lab).where(Lab.slug == str(item.get("lab") or "")))
        role = item.get("role")
        if lab is None:
            raise PeopleError(f"No lab or department {item.get('lab')!r}", 404)
        if not lab.active:
            raise PeopleError(f"{lab.name} is inactive, so membership would give no access; reactivate it first", 409)
        if role not in ROLES:
            raise PeopleError("A role is pi (a lab's PI or a department's owner) or delegate")
        if lab.id in out:
            raise PeopleError(f"{lab.name} is listed twice; a person has one role in each lab or department")
        out[lab.id] = (lab, role)
    return out


def save_person(session: Session, actor: User, user: User | None, data: dict[str, Any]) -> User:
    """Add a person (`user` None) or change one: profile, administrator role, and, if given, lab memberships
    (`memberships`: [{lab: slug, role}], the full list)."""
    creating = user is None
    profile = {"display_name": _text(data, "display_name"), "email": _text(data, "email"),
               "title": _text(data, "title"), "is_admin": bool(data.get("is_admin"))}
    if not profile["display_name"]:
        raise PeopleError("A name is required")
    if problem := email_problem(profile["email"]):
        raise PeopleError(problem[0].upper() + problem[1:])
    if creating:
        username = str(data.get("username") or "").strip()
        taken = list(session.scalars(select(User.username)))
        if username in taken:
            raise PeopleError(f"The username {username!r} is already taken", 409)
        if problem := username_problem(username, taken):
            raise PeopleError(problem[0].upper() + problem[1:], 409 if "differs only in case" in problem else 422)
        user = User(username=username, display_name=profile["display_name"], email=profile["email"])
        session.add(user)
        before: dict[str, Any] = {}
    else:
        if not user.active:
            raise PeopleError(f"Reactivate {user.display_name} before changing their details", 409)
        if "username" in data and str(data["username"]).strip() != user.username:
            raise PeopleError("A username cannot be changed: it is the name the sign-on sends")
        if user.is_admin and not profile["is_admin"]:
            if user.id == actor.id:
                raise PeopleError("You cannot remove your own administrator role", 409)
            if active_admins(session) <= 1:
                raise PeopleError("At least one active storage administrator is required", 409)
        before = {k: getattr(user, k) for k in PROFILE}
    wanted = _memberships(session, data["memberships"]) if "memberships" in data else None
    for k, v in profile.items():
        setattr(user, k, v)
    session.flush()

    fields = {k: {"before": before.get(k), "after": v} for k, v in profile.items() if creating or before[k] != v}
    if creating:
        _audit(session, actor, "person.created", f"Added {user.display_name} ({user.username})",
               {"username": user.username, "fields": fields})
    elif fields:
        what = ", ".join(
            ("granted the storage administrator role" if v["after"] else "removed the storage administrator role")
            if k == "is_admin" else k.replace("_", " ") for k, v in fields.items())
        _audit(session, actor, "person.updated", f"{user.display_name}: {what}", {"username": user.username, "fields": fields})
    if wanted is not None:
        _set_memberships(session, actor, user, wanted)
    session.flush()
    return user


def _set_memberships(session: Session, actor: User, user: User, wanted: dict[int, tuple[Lab, str]]) -> None:
    """Make the person's memberships of active labs exactly `wanted`, auditing each change (memberships of inactive
    labs, which give no access and are not shown, are left as they are)."""
    current = {m.lab_id: m for m in session.scalars(select(LabMembership).where(LabMembership.user_id == user.id))}
    for lab_id, m in current.items():
        if lab_id not in wanted and m.lab.active:
            session.delete(m)
            _audit(session, actor, "lab.member", f"{user.display_name} removed from {m.lab.name}",
                   {"lab": m.lab.slug, "username": user.username, "role": None, "before": m.role})
    for lab_id, (lab, role) in wanted.items():
        m = current.get(lab_id)
        was = m.role if m is not None else None
        if was == role:
            continue
        if m is None:
            session.add(LabMembership(lab_id=lab_id, user_id=user.id, role=role))
        else:
            m.role = role
        _audit(session, actor, "lab.member", f"{user.display_name} set as {role} in {lab.name}",
               {"lab": lab.slug, "username": user.username, "role": role, "before": was})


def set_active(session: Session, actor: User, user: User, active: bool) -> None:
    """Deactivate a person (they cannot sign in; their memberships and role are kept, unused) or reactivate them."""
    if user.active == active:
        raise PeopleError(f"{user.display_name} is already {'active' if active else 'inactive'}", 409)
    if not active:
        if user.id == actor.id:
            raise PeopleError("You cannot deactivate yourself", 409)
        if user.is_admin and active_admins(session) <= 1:
            raise PeopleError("At least one active storage administrator is required", 409)
    user.active = active
    labs = sorted(f"{m.lab.name} ({m.role})" for m in user.memberships if m.lab.active)
    kept = ", ".join(labs + (["storage administrator"] if user.is_admin else [])) or "no access"
    _audit(session, actor, "person.reactivated" if active else "person.deactivated",
           f"{'Reactivated' if active else 'Deactivated'} {user.display_name}"
           + (f"; access restored: {kept}" if active else f"; cannot sign in (access kept, unused: {kept})"),
           {"username": user.username, "fields": {"active": {"before": not active, "after": active}},
            "labs": labs, "is_admin": user.is_admin})
    session.flush()
