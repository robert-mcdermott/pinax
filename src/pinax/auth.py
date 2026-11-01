# SPDX-License-Identifier: Apache-2.0
"""Identity and authorization.

Demo mode identifies the user from a persona cookie set by the UI switcher. Local mode finds them from a
sign-in session (signin.py). Header mode trusts a username injected by an SSO reverse proxy (for example
oauth2-proxy in front of Entra ID). In every mode, a script sends an API token instead (tokens.py): a person's, with
their access or reading only, or a service's, reading as an auditor or as finance. Behind a sign-on proxy, scripts
use /script/api/..., which the proxy lets through and Pinax answers only with a token. Every authorization decision
happens here, server-side.

Seeing and changing are separate. Storage administrators and auditors see everything, and finance every lab's
charges; everyone else sees the top-level folders of their active labs and departments, and of the labs in the
divisions they head. Only storage administrators change the site's settings, and only a lab's PI and delegates (or
an administrator) act on its folders.
"""

import ipaddress
import logging
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session, selectinload

from . import logs, tokens
from .config import get_settings
from .db import get_session
from .models import ApiToken, Lab, LabFolder, LabMembership, User
from .signin import COOKIE as SESSION_COOKIE
from .signin import find_session

DEMO_COOKIE = "pinax_user"
# What someone signed in with a temporary password may do before changing it.
WHILE_CHANGING_PASSWORD = {"/api/me", "/api/auth/password", "/api/auth/logout"}
SCRIPTS = "/script"  # /script/api/... is /api/..., answered only with an API token
TOKEN_ONLY = "pinax.token_only"
log = logging.getLogger("pinax.auth")


@dataclass
class Identity:
    user: User
    lab_roles: dict[int, str] = field(default_factory=dict)  # the labs they act for: lab_id -> role
    # (filesystem_id, top-level folder name) -> the lab or department that owns it, for each folder they see
    folders: dict[tuple[int, str], Lab] = field(default_factory=dict)
    labs_seen: set[int] = field(default_factory=set)  # their labs, and those of the divisions they head
    roles: frozenset[str] = frozenset()  # site roles (models.SITE_ROLES)
    divisions: frozenset[str] = frozenset()  # the divisions they head
    token: ApiToken | None = None  # the API token the request came with

    @property
    def is_admin(self) -> bool:
        """A storage administrator: sees everything, and changes the site's settings."""
        return self.user.is_admin

    @property
    def sees_all(self) -> bool:
        """Every filesystem, folder, lab, and person, and the audit log: administrators and auditors."""
        return self.is_admin or "auditor" in self.roles

    @property
    def sees_charges(self) -> bool:
        """Every lab's charges, and the prices, free tiers, and capacities behind them."""
        return self.sees_all or "finance" in self.roles

    def can_view_folder(self, filesystem_id: int, folder: str | None) -> bool:
        """`folder` None means the filesystem root itself (those who see everything only)."""
        if self.sees_all:
            return True
        return folder is not None and (filesystem_id, folder) in self.folders

    def can_view_lab(self, lab_id: int) -> bool:
        return self.sees_all or lab_id in self.labs_seen


def _load_user(session: Session, username: str) -> User | None:
    return session.scalar(
        select(User)
        .where(User.username == username)
        .options(selectinload(User.memberships).selectinload(LabMembership.lab).selectinload(Lab.folders),
                 selectinload(User.roles))
    )


def current_identity(request: Request, session: Session = Depends(get_session)) -> Identity:
    settings = get_settings()
    scheme, _, secret = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" or request.scope.get(TOKEN_ONLY):
        return _token_identity(request, session, secret.strip() if scheme.lower() == "bearer" else "")
    if settings.auth_mode == "header":
        if not from_trusted_proxy(request, settings.trusted_proxies):
            # Anyone could send the header; only the sign-on proxy's word counts.
            log.warning("ignored a sign-on header from %s, which is not a trusted proxy",
                        request.client.host if request.client else "?")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in through the single sign-on proxy")
        username = request.headers.get(settings.auth_header, "")
    elif settings.auth_mode == "local":
        signed_in = find_session(session, request.cookies.get(SESSION_COOKIE))
        if signed_in is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
        if signed_in.user.must_change_password and request.url.path not in WHILE_CHANGING_PASSWORD:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Change your password before going on")
        username = signed_in.user.username
    else:
        username = request.cookies.get(DEMO_COOKIE, "") or request.headers.get("X-Pinax-User", "")
    if not username:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    user = _load_user(session, username)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")
    if not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This account has been deactivated")
    logs.set_user(user.username)
    return identity_for(user)


def _token_identity(request: Request, session: Session, secret: str) -> Identity:
    """A script's request: the token's person, as they are now, or for a service token, its role alone."""
    info = logs.current.get()
    t = tokens.find(session, secret, info.address if info else "") if secret else None
    if t is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Send a live API token as Authorization: Bearer <token>",
                            headers={"WWW-Authenticate": "Bearer"})
    if problem := tokens.may(t, request.method, request.url.path):
        raise HTTPException(status.HTTP_403_FORBIDDEN, problem)
    logs.set_token(t.name)
    if t.user is None:
        logs.set_user(f"service:{t.name}")
        # A service is no one: a person never stored, with no labs, and the token's role.
        service = User(username=f"service:{t.name}", display_name=t.name, email="", title="Service token",
                       is_admin=False, active=True)
        return Identity(user=service, roles=frozenset({t.role}), token=t)
    user = _load_user(session, t.user.username)
    logs.set_user(user.username)
    ident = identity_for(user)
    ident.token = t
    return ident


class ScriptPrefix:
    """ASGI middleware: /script/api/... is /api/..., for scripts behind a sign-on proxy, which lets that path through
    without signing in (deploy/oauth2-proxy/pinax.cfg); Pinax answers it only with an API token."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith(SCRIPTS + "/api/"):
            scope["path"] = scope["path"][len(SCRIPTS):]
            scope["raw_path"] = scope["path"].encode()
            scope[TOKEN_ONLY] = True
        await self.app(scope, receive, send)


def from_trusted_proxy(request: Request, trusted: list[str]) -> bool:
    """Whether the request came straight from one of the `trusted` addresses or networks. A request over a Unix
    socket has no address, and only local processes can make one."""
    if request.client is None:
        return True
    try:
        addr = ipaddress.ip_address(request.client.host)
    except ValueError:
        return False
    for net in trusted:
        try:
            if addr in ipaddress.ip_network(net, strict=False):
                return True
        except ValueError:
            log.error("PINAX_TRUSTED_PROXIES entry %r is not an address or network", net)
    return False


def identity_for(user: User) -> Identity:
    """Folder access comes only from active labs: the person's own, and those of the divisions they head."""
    divisions = frozenset(r.division for r in user.roles if r.role == "division" and r.division)
    ident = Identity(user=user, lab_roles={m.lab_id: m.role for m in user.memberships if m.lab.active},
                     roles=frozenset(r.role for r in user.roles), divisions=divisions)
    labs = [m.lab for m in user.memberships if m.lab.active]
    if divisions:
        labs += object_session(user).scalars(
            select(Lab).where(Lab.active.is_(True), Lab.division.in_(divisions)).options(selectinload(Lab.folders))).all()
    for lab in labs:
        ident.labs_seen.add(lab.id)
        for f in lab.folders:
            ident.folders.setdefault((f.filesystem_id, f.path), lab)
    return ident


def require_admin(identity: Identity = Depends(current_identity)) -> Identity:
    """Changing the site's settings: storage administrators."""
    if not identity.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Storage administrator role required")
    return identity


def require_sees_all(identity: Identity = Depends(current_identity)) -> Identity:
    """Reading what administrators read: storage administrators and auditors."""
    if not identity.sees_all:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Storage administrator or auditor role required")
    return identity


def require_sees_charges(identity: Identity = Depends(current_identity)) -> Identity:
    """Every lab's charges and the prices behind them: administrators, auditors, and finance."""
    if not identity.sees_charges:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Storage administrator, auditor, or finance role required")
    return identity


def folder_owners(session: Session) -> dict[tuple[int, str], Lab]:
    """Every mapped top-level folder and its owning lab (for administrators' views)."""
    rows = session.scalars(select(LabFolder).options(selectinload(LabFolder.lab))).all()
    return {(f.filesystem_id, f.path): f.lab for f in rows}
