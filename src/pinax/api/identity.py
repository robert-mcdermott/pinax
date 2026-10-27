# SPDX-License-Identifier: Apache-2.0
"""Who am I, the interface's configuration, and the demo persona switcher (off when AUTH_MODE=header)."""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import __version__
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
        "cold_bucket": cold_bucket(s), "metrics": list(METRICS), "search_max_rows": s.search_max_rows,
        "export_max_rows": s.export_max_rows, "password_min_length": s.password_min_length, **dimensions(),
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
    return user_json(identity.user)
