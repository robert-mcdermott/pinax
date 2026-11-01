# SPDX-License-Identifier: Apache-2.0
"""API tokens (A11): what a script sends instead of signing in, with every way of signing in.

- **Personal tokens.** Anyone makes their own, named for what uses it, with their access or reading only. Their
  access is looked up on every request, so a token follows the person's labs and roles as they change.
- **Service tokens.** An administrator makes one for a named service (a finance system, a dashboard). It belongs
  to no one, so it outlives whoever made it, and it only reads: everything, as an auditor, or every lab's charges,
  as finance.
- **Shown once.** A token is `pinax_` and 43 random characters; only its SHA-256 is stored, with its first
  characters to tell tokens apart. A script sends it as `Authorization: Bearer <token>`.
- **Ending.** Every token expires (after at most PINAX_TOKEN_MAX_DAYS), and its person or an administrator can
  revoke it sooner. Deactivating a person revokes their tokens.
- **Reading only.** A read-only token may make GET requests and, if it is a person's, start a search in the
  background, and nothing else.
  No token can make or revoke tokens, or sign in, sign out, or change a password: those are done in Pinax's pages.
- **Audited.** Making and revoking a token is audited, and every change made with one is audited with the token's
  name (logs.py). Its last use, and the address it came from, are kept.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import utcnow
from .models import ApiToken, AuditEvent, User
from .serialize import iso

PREFIX = "pinax_"
SERVICE_ROLES = {"auditor": "reads everything, as an auditor", "finance": "reads every lab's charges, as finance"}
# Requests a read-only token may make beside GETs: they change nothing a person sees.
READING = {("POST", "/api/files/jobs")}
# Paths no token may use.
NOT_WITH_A_TOKEN = ("/api/tokens", "/api/admin/tokens", "/api/auth/")


class TokenError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def state(t: ApiToken) -> str:
    if t.revoked_at is not None:
        return "revoked"
    if t.expires_at <= utcnow():
        return "expired"
    return "active"


def access_text(t: ApiToken) -> str:
    if t.user_id is None:
        return SERVICE_ROLES.get(t.role, t.role).capitalize()
    return "Reads what its person sees" if t.read_only else "Its person's access"


def token_json(t: ApiToken) -> dict:
    return {
        "id": t.id, "name": t.name, "prefix": t.prefix, "kind": "personal" if t.user_id else "service",
        "person": {"username": t.user.username, "display_name": t.user.display_name, "active": t.user.active}
        if t.user else None,
        "role": t.role, "read_only": t.read_only, "access": access_text(t), "state": state(t),
        "created_at": iso(t.created_at), "created_by": t.created_by.display_name if t.created_by else None,
        "expires_at": iso(t.expires_at), "last_used_at": iso(t.last_used_at), "last_used_address": t.last_used_address,
        "revoked_at": iso(t.revoked_at), "revoked_by": t.revoked_by.display_name if t.revoked_by else None,
    }


def _audit(session: Session, actor: User | None, action: str, summary: str, t: ApiToken, ts=None) -> None:
    detail = {"token_id": t.id, "token_name": t.name, "kind": "personal" if t.user_id else "service", "source": "app"}
    if t.user is not None:
        detail["username"] = t.user.username  # so it shows in their history
    session.add(AuditEvent(actor_id=actor.id if actor else None, action=action, summary=summary, detail=detail,
                           ts=ts or utcnow()))


def _name(session: Session, name: str, user: User | None) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise TokenError("Name the token for what will use it, such as “Monthly chargeback”")
    if len(name) > 100:
        raise TokenError("A token's name can have at most 100 characters")
    live = select(ApiToken.id).where(ApiToken.name == name, ApiToken.revoked_at.is_(None), ApiToken.expires_at > utcnow(),
                                     ApiToken.user_id == user.id if user else ApiToken.user_id.is_(None))
    if session.scalar(live) is not None:
        raise TokenError(f"{'You have' if user else 'There is'} a live token named {name!r} already", 409)
    return name


def _days(days: int) -> int:
    most = get_settings().token_max_days
    if not 1 <= days <= most:
        raise TokenError(f"A token lasts from 1 to {most} days")
    return days


def make(session: Session, actor: User, name: str, days: int, *, read_only: bool = True, role: str | None = None,
         at: datetime | None = None) -> tuple[ApiToken, str]:
    """A new token and its secret, shown this once: the actor's own, or with `role`, a service token. Made now, or
    `at` (the demo's)."""
    if role is not None and role not in SERVICE_ROLES:
        raise TokenError("A service token reads as an auditor or as finance: choose which")
    owner = None if role is not None else actor
    secret = PREFIX + secrets.token_urlsafe(32)
    now = at or utcnow()
    t = ApiToken(name=_name(session, name, owner), token_hash=digest(secret), prefix=secret[:len(PREFIX) + 4],
                 user=owner, role=role or "", read_only=True if role is not None else read_only, created_at=now,
                 created_by=actor, expires_at=now + timedelta(days=_days(days)))
    session.add(t)
    session.flush()
    what = (f"service token {t.name!r}, which {SERVICE_ROLES[role]}" if role is not None
            else f"API token {t.name!r}, {'reading only' if read_only else 'with their access'}")
    _audit(session, actor, "token.made", f"{actor.display_name} made the {what}, until {t.expires_at:%Y-%m-%d}", t, now)
    return t, secret


def find(session: Session, secret: str, address: str = "") -> ApiToken | None:
    """The live token for a secret, or None (unknown, expired, revoked, or its person deactivated). Notes its use."""
    if not secret.startswith(PREFIX):
        return None
    t = session.scalar(select(ApiToken).where(ApiToken.token_hash == digest(secret)))
    now = utcnow()
    if t is None or state(t) != "active" or (t.user is not None and not t.user.active):
        return None
    if t.last_used_at is None or now - t.last_used_at > timedelta(minutes=1) or t.last_used_address != address[:64]:
        t.last_used_at, t.last_used_address = now, address[:64]
        session.commit()
    return t


def may(t: ApiToken, method: str, path: str) -> str | None:
    """Why the token may not make this request, or None."""
    if path.startswith(NOT_WITH_A_TOKEN):
        return "Tokens are made and revoked, and passwords changed, in Pinax's pages, not with a token"
    if t.read_only and method not in ("GET", "HEAD"):
        if (method, path) not in READING:
            return f"The token {t.name!r} only reads"
        if t.user_id is None:
            return "A search in the background is a person's: start it with a personal token"
    return None


def revoke(session: Session, actor: User, t: ApiToken) -> None:
    if t.revoked_at is not None:
        raise TokenError(f"The token {t.name!r} was revoked already", 409)
    t.revoked_at, t.revoked_by = utcnow(), actor
    whose = "their" if t.user_id == actor.id else f"{t.user.display_name}'s" if t.user else "the service"
    _audit(session, actor, "token.revoked", f"{actor.display_name} revoked {whose} token {t.name!r}", t)


def revoke_all(session: Session, user: User, actor: User | None) -> int:
    """End a person's live tokens (when they are deactivated); returns how many."""
    live = session.scalars(select(ApiToken).where(ApiToken.user_id == user.id, ApiToken.revoked_at.is_(None),
                                                  ApiToken.expires_at > utcnow())).all()
    for t in live:
        t.revoked_at, t.revoked_by = utcnow(), actor
    return len(live)


def listed(session: Session, user: User | None = None) -> list[ApiToken]:
    """A person's tokens, or every token; live ones first, newest first. Ended ones are listed for 90 days."""
    since = utcnow() - timedelta(days=90)
    q = select(ApiToken).where(or_(ApiToken.revoked_at.is_(None), ApiToken.revoked_at > since),
                               ApiToken.expires_at > since)
    if user is not None:
        q = q.where(ApiToken.user_id == user.id)
    rows = session.scalars(q.order_by(ApiToken.created_at.desc(), ApiToken.id.desc())).all()
    return sorted(rows, key=lambda t: state(t) != "active")
