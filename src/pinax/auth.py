# SPDX-License-Identifier: Apache-2.0
"""Identity and authorization.

Demo mode identifies the user from a persona cookie set by the UI switcher. Local mode finds them from a
sign-in session (signin.py). Header mode trusts a username injected by an SSO reverse proxy (for example
oauth2-proxy in front of Entra ID). Every
authorization decision happens here, server-side: IT administrators see everything; everyone else
sees only the top-level folders mapped to their active labs and departments.
"""

import ipaddress
import logging
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .db import get_session
from .models import Lab, LabFolder, LabMembership, User
from .signin import COOKIE as SESSION_COOKIE
from .signin import find_session

DEMO_COOKIE = "pinax_user"
# What someone signed in with a temporary password may do before changing it.
WHILE_CHANGING_PASSWORD = {"/api/me", "/api/auth/password", "/api/auth/logout"}
log = logging.getLogger("pinax.auth")


@dataclass
class Identity:
    user: User
    lab_roles: dict[int, str] = field(default_factory=dict)  # lab_id -> role
    # (filesystem_id, top-level folder name) -> the lab or department that owns it
    folders: dict[tuple[int, str], Lab] = field(default_factory=dict)

    @property
    def is_admin(self) -> bool:
        return self.user.is_admin

    def can_view_folder(self, filesystem_id: int, folder: str | None) -> bool:
        """`folder` None means the filesystem root itself (administrators only)."""
        if self.is_admin:
            return True
        return folder is not None and (filesystem_id, folder) in self.folders


def _load_user(session: Session, username: str) -> User | None:
    return session.scalar(
        select(User)
        .where(User.username == username)
        .options(selectinload(User.memberships).selectinload(LabMembership.lab).selectinload(Lab.folders))
    )


def current_identity(request: Request, session: Session = Depends(get_session)) -> Identity:
    settings = get_settings()
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
    return identity_for(user)


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
    """Folder access comes only from active labs."""
    ident = Identity(user=user, lab_roles={m.lab_id: m.role for m in user.memberships if m.lab.active})
    for m in user.memberships:
        if not m.lab.active:
            continue
        for f in m.lab.folders:
            ident.folders[(f.filesystem_id, f.path)] = m.lab
    return ident


def require_admin(identity: Identity = Depends(current_identity)) -> Identity:
    if not identity.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Storage administrator role required")
    return identity


def folder_owners(session: Session) -> dict[tuple[int, str], Lab]:
    """Every mapped top-level folder and its owning lab (for administrators' views)."""
    rows = session.scalars(select(LabFolder).options(selectinload(LabFolder.lab))).all()
    return {(f.filesystem_id, f.path): f.lab for f in rows}
