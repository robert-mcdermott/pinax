# SPDX-License-Identifier: Apache-2.0
"""Local sign-in (PINAX_AUTH_MODE=local): passwords Pinax keeps, for sites without single sign-on.

- Passwords are hashed with scrypt (the standard library), with a random salt; the stored hash names its
  parameters, so they can be raised later and older hashes still verify.
- An administrator never chooses a person's password: Pinax generates a temporary one, shown once, which the person
  must change when they first sign in. Until then every request but changing it is refused.
- Sessions live on the server: the cookie holds a random token and only its SHA-256 is stored. A session ends when
  the person signs out, when it expires (SESSION_HOURS after signing in), and when the person is deactivated or
  their password is set or changed (other sessions only, for a change they make themselves).
- LOGIN_MAX_FAILURES failed sign-ins in a row lock the account for LOGIN_LOCKOUT_MINUTES; setting a temporary
  password unlocks it. A failure says the same whatever went wrong, and takes as long for an unknown username.
- Every sign-in, failure, lock, sign-out, and password change is audited; passwords never are.
- With sign-in through an identity provider, an emergency account (a storage administrator marked from the command
  line) signs in with a password at /auth/emergency when the provider fails (A12): a long password Pinax makes, the
  same lockout, an hour's session, and every sign-in and failure audited as `auth.emergency_*` and logged as a
  warning, for the site's monitoring to alert on.
"""

import base64
import getpass
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import release, utcnow
from .models import AuditEvent, LoginSession, User

COOKIE = "pinax_session"
SCRYPT = {"n": 2**15, "r": 8, "p": 3}  # 32 MiB, OWASP's equivalent of their recommended minimum
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i: read aloud or typed from a screen
FAILED = "Wrong username or password, or the account is locked for a while after repeated failures"
EMERGENCY_SESSION_HOURS = 1
log = logging.getLogger("pinax.signin")


class SignInError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------- Passwords ----------

def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = SCRYPT["n"], SCRYPT["r"], SCRYPT["p"]
    key = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=2**26)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        algo, n, r, p, salt, key = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(base64.b64decode(key)), maxmem=2**26)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, base64.b64decode(key))


_DUMMY = hash_password(secrets.token_hex(8))  # verified for unknown usernames, so they take as long


def temporary_password(length: int = 16) -> str:
    """Random characters in groups of four (sixteen are about 79 bits), easy to read out or copy."""
    chars = "".join(secrets.choice(ALPHABET) for _ in range(length))
    return "-".join(chars[i:i + 4] for i in range(0, length, 4))


def password_problem(password: str, user: User, settings: Settings | None = None) -> str | None:
    """Why `password` cannot be this person's new password, or None: length matters more than character classes."""
    settings = settings or get_settings()
    if len(password) < settings.password_min_length:
        return f"A password needs at least {settings.password_min_length} characters"
    if len(password) > 256:
        return "A password can have at most 256 characters"
    lowered = password.lower()
    for part in {user.username.lower(), user.username.split("@")[0].lower(), user.email.split("@")[0].lower()}:
        if len(part) >= 3 and part in lowered:
            return "A password cannot contain your username or email name"
    if len(set(password)) < 5:
        return "A password needs more than a few different characters"
    return None


# ---------- Audit ----------

def audit(session: Session, action: str, summary: str, user: User | None, *, actor: User | None = None,
           source: str = "app", **detail) -> None:
    """Sign-in events are the person's own (`actor` None means them); a password set from the command line records
    the operating-system user who ran it."""
    detail = {"username": user.username if user else detail.pop("username", None), **detail, "source": source}
    if source == "cli":
        detail["by"] = getpass.getuser()
    who = actor or (user if action.startswith("auth.") else None)
    session.add(AuditEvent(actor_id=who.id if who else None, action=action, summary=summary, detail=detail))


# ---------- Sessions ----------

def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(session: Session, user: User, address: str = "", hours: float | None = None) -> str:
    """A new session for `user`, for SESSION_HOURS or `hours`; returns the token for the cookie."""
    token = secrets.token_urlsafe(32)
    now = utcnow()
    hours = get_settings().session_hours if hours is None else hours
    session.add(LoginSession(token_hash=_digest(token), user_id=user.id, created_at=now, last_seen_at=now,
                             expires_at=now + timedelta(hours=hours), address=address[:64]))
    return token


def find_session(session: Session, token: str | None) -> LoginSession | None:
    """The live session for a cookie's token, or None (unknown, expired, or its person deactivated)."""
    if not token:
        return None
    s = session.scalar(select(LoginSession).where(LoginSession.token_hash == _digest(token)))
    now = utcnow()
    if s is None or s.expires_at <= now or not s.user.active:
        return None
    if now - s.last_seen_at > timedelta(minutes=5):  # not on every request
        s.last_seen_at = now
        session.commit()
    return s


def end_sessions(session: Session, user: User, keep: str | None = None) -> int:
    """End all of a person's sessions, but the one for `keep` (a token)."""
    q = delete(LoginSession).where(LoginSession.user_id == user.id)
    if keep:
        q = q.where(LoginSession.token_hash != _digest(keep))
    return session.execute(q).rowcount or 0


def end_session(session: Session, token: str | None) -> LoginSession | None:
    s = session.scalar(select(LoginSession).where(LoginSession.token_hash == _digest(token or "")))
    if s is not None:
        session.delete(s)
    return s


# ---------- Signing in and passwords ----------

def sign_in(session: Session, username: str, password: str, address: str = "", *,
            emergency: bool = False) -> tuple[User, str]:
    """Check a username and password; returns the person and a new session's token, or raises SignInError with the
    same message whatever went wrong. With `emergency`, only an active storage administrator marked as an emergency
    account, audited as such and logged as a warning, for EMERGENCY_SESSION_HOURS."""
    settings = get_settings()
    now = utcnow()
    failed, kind = ("auth.emergency_sign_in_failed", "emergency sign-in") if emergency else ("auth.sign_in_failed", "sign-in")
    user = session.scalar(select(User).where(User.username == username.strip()))
    release(session)  # checking a password is slow by design, and needs no catalog connection meanwhile
    if user is None or not user.active or not user.password_hash or (emergency and not (user.emergency and user.is_admin)):
        verify_password(password, _DUMMY)
        audit(session, failed, f"Failed {kind} as {username.strip()[:64]!r}", user,
               username=username.strip()[:64], address=address,
               reason="not an emergency account" if emergency and user is not None and user.active and user.password_hash
               else "unknown or inactive, or no password")
        _warn(emergency, "Failed emergency sign-in as %r from %s", username.strip()[:64], address)
        session.commit()
        raise SignInError(FAILED, 401)
    if user.locked_until and user.locked_until > now:
        verify_password(password, _DUMMY)
        audit(session, failed, f"Failed {kind} as {user.username}: locked", user, address=address, reason="locked")
        _warn(emergency, "Failed emergency sign-in as %s from %s: locked", user.username, address)
        session.commit()
        raise SignInError(FAILED, 401)
    if not verify_password(password, user.password_hash):
        user.failed_logins = (user.failed_logins or 0) + 1
        audit(session, failed, f"Failed {kind} as {user.username}", user, address=address,
               reason="wrong password", failures=user.failed_logins)
        _warn(emergency, "Failed emergency sign-in as %s from %s: wrong password", user.username, address)
        if user.failed_logins >= settings.login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            user.failed_logins = 0
            audit(session, "auth.locked", f"{user.display_name} locked for {settings.login_lockout_minutes} minutes "
                   f"after {settings.login_max_failures} failed sign-ins", user, address=address)
        session.commit()
        raise SignInError(FAILED, 401)
    user.failed_logins, user.locked_until, user.last_login_at = 0, None, now
    if emergency:
        token = start_session(session, user, address, hours=EMERGENCY_SESSION_HOURS)
        audit(session, "auth.emergency_signed_in", f"{user.display_name} signed in with the emergency sign-in", user,
               address=address)
        log.warning("Emergency sign-in by %s from %s", user.username, address)
    else:
        token = start_session(session, user, address)
        audit(session, "auth.signed_in", f"{user.display_name} signed in", user, address=address)
    session.commit()
    return user, token


def _warn(emergency: bool, message: str, *args) -> None:
    if emergency:
        log.warning(message, *args)


def set_emergency(session: Session, user: User, on: bool = True) -> str | None:
    """From the command line only: mark a storage administrator as an emergency account, with a new password of 32
    characters (returned once), or no longer (their password goes, and their sessions end). Audited."""
    if on and not (user.active and user.is_admin):
        raise SignInError(f"Only an active storage administrator can be an emergency account, and {user.display_name} "
                          "is not one", 409)
    ended = end_sessions(session, user)
    if not on:
        user.emergency, user.password_hash, user.password_set_at = False, None, None
        audit(session, "person.emergency_removed", f"{user.display_name} is no longer an emergency account", user,
               source="cli", ended_sessions=ended)
        session.commit()
        return None
    password = temporary_password(32)
    user.emergency, user.must_change_password = True, False
    user.password_hash, user.password_set_at = hash_password(password), utcnow()
    user.failed_logins, user.locked_until = 0, None
    audit(session, "person.emergency_set", f"{user.display_name} is an emergency account, with a new password", user,
           source="cli", ended_sessions=ended)
    session.commit()
    return password


def change_password(session: Session, user: User, current: str, new: str, token: str | None) -> None:
    """A person changes their own password; their other sessions end."""
    if not verify_password(current, user.password_hash):
        raise SignInError("Your current password is not right")
    if problem := password_problem(new, user):
        raise SignInError(problem, 422)
    if hmac.compare_digest(current, new):
        raise SignInError("Choose a password different from the current one", 422)
    user.password_hash, user.password_set_at, user.must_change_password = hash_password(new), utcnow(), False
    ended = end_sessions(session, user, keep=token)
    audit(session, "auth.password_changed", f"{user.display_name} changed their password", user, ended_sessions=ended)
    session.commit()


def set_temporary_password(session: Session, user: User, actor: User | None, source: str = "app") -> str:
    """Give a person a new temporary password (returned once), which they must change when they sign in. Unlocks
    the account and ends their sessions."""
    if not user.active:
        raise SignInError(f"Reactivate {user.display_name} before setting a password", 409)
    password = temporary_password()
    user.password_hash, user.password_set_at, user.must_change_password = hash_password(password), utcnow(), True
    user.failed_logins, user.locked_until = 0, None
    ended = end_sessions(session, user)
    audit(session, "person.password_set", f"Temporary password set for {user.display_name}", user, actor=actor,
           source=source, ended_sessions=ended)
    session.commit()
    return password
