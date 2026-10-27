# SPDX-License-Identifier: Apache-2.0
"""Identity and authorization.

Demo mode identifies the user from a persona cookie set by the UI switcher. Header mode trusts a
username injected by an SSO reverse proxy (for example oauth2-proxy in front of Entra ID). Every
authorization decision happens here, server-side: IT administrators see everything; everyone else
sees only the top-level folders mapped to their active labs and departments.
"""

from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .config import get_settings
from .db import get_session
from .models import Lab, LabFolder, LabMembership, User

DEMO_COOKIE = "pinax_user"


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
        username = request.headers.get(settings.auth_header, "")
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
