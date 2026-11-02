# SPDX-License-Identifier: Apache-2.0
"""Who am I, the interface's configuration, and the demo persona switcher (off when AUTH_MODE=header)."""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import __version__, categories
from ..auth import DEMO_COOKIE, Identity, current_identity
from ..config import get_settings
from ..db import get_session
from ..index import METRICS, dimensions
from ..models import Filesystem, Lab, LabMembership, User
from ..serialize import fs_json, user_json
from ..snapshots import cold_bucket

router = APIRouter(tags=["identity"])


class DemoLogin(BaseModel):
    username: str


@router.get("/config")
def public_config(session: Session = Depends(get_session)):
    s = get_settings()
    filesystems = session.scalars(select(Filesystem).where(Filesystem.active.is_(True)).order_by(Filesystem.name))
    return {
        "filesystems": [fs_json(f) for f in filesystems],
        "app_name": s.app_name, "version": __version__, "environment": s.environment, "auth_mode": s.auth_mode,
        "cold_bucket": cold_bucket(s), "search_max_rows": s.search_max_rows,
        "metrics": list(METRICS) + [f"cat:{c.key}" for c in categories.current(session)],
        "export_max_rows": s.export_max_rows, "password_min_length": s.password_min_length,
        "currency": s.currency, **dimensions(), "categories": categories.brief(categories.current(session)),
        # Roles set from the identity provider at sign-in, not here (oidc with OIDC_ROLE_MAP): "admin", "auditor", ...
        "mapped_roles": sorted(f"{r}:{d}" if d else r for r, d in s.mapped_roles),
    }


@router.get("/demo/users")
def demo_users(session: Session = Depends(get_session)):
    if get_settings().auth_mode != "demo":
        raise HTTPException(404)
    users = session.scalars(
        select(User)
        .where(User.active.is_(True))
        .options(selectinload(User.memberships).selectinload(LabMembership.lab).selectinload(Lab.folders))
        .order_by(User.is_admin.desc(), User.display_name)
    ).all()
    return [user_json(u) for u in users]


@router.post("/demo/session")
def demo_login(body: DemoLogin, response: Response, session: Session = Depends(get_session)):
    if get_settings().auth_mode != "demo":
        raise HTTPException(404)
    user = session.scalar(select(User).where(User.username == body.username))
    if user is None or not user.active:
        raise HTTPException(404, "Unknown user")
    response.set_cookie(DEMO_COOKIE, body.username, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return {"ok": True}


@router.get("/me")
def me(identity: Identity = Depends(current_identity)):
    """The person signed in, and what their roles let them see and change (the pages follow it; the server decides)."""
    return {**user_json(identity.user), "sees_all": identity.sees_all, "sees_charges": identity.sees_charges,
            "divisions": sorted(identity.divisions), "sees_labs": bool(identity.labs_seen)}
