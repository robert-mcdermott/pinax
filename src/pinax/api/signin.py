# SPDX-License-Identifier: Apache-2.0
"""Local sign-in (PINAX_AUTH_MODE=local): signing in and out, and changing one's own password. In the other modes
these answer 404: single sign-on and the demo persona switcher have their own ways in."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import logs, signin
from ..auth import Identity, current_identity
from ..config import get_settings
from ..db import get_session
from ..serialize import user_json

router = APIRouter(prefix="/auth", tags=["sign-in"])


def _local() -> None:
    if get_settings().auth_mode != "local":
        raise HTTPException(404)


def _address(request: Request) -> str:
    """The person's address, behind a trusted proxy too (logs.client_address)."""
    info = logs.current.get()
    return info.address if info else (request.client.host if request.client else "")


class Credentials(BaseModel):
    username: str
    password: str


class NewPassword(BaseModel):
    current_password: str
    new_password: str


@router.post("/login", dependencies=[Depends(_local)])
def login(body: Credentials, request: Request, response: Response, session: Session = Depends(get_session)):
    """Sign in with a username and password. If the password was set by an administrator, it must be changed
    before anything else (`must_change_password`)."""
    try:
        user, token = signin.sign_in(session, body.username, body.password, _address(request))
    except signin.SignInError as e:
        raise HTTPException(e.status, str(e)) from e
    s = get_settings()
    response.set_cookie(signin.COOKIE, token, httponly=True, samesite="lax", secure=s.secure_cookies,
                        max_age=s.session_hours * 3600, path="/")
    return {"ok": True, "must_change_password": user.must_change_password}


@router.post("/logout", dependencies=[Depends(_local)])
def logout(request: Request, response: Response, session: Session = Depends(get_session)):
    ended = signin.end_session(session, request.cookies.get(signin.COOKIE))
    if ended is not None:
        signin.audit(session, "auth.signed_out", f"{ended.user.display_name} signed out", ended.user,
                      address=_address(request))
    session.commit()
    response.delete_cookie(signin.COOKIE, path="/")
    return {"ok": True}


@router.post("/password", dependencies=[Depends(_local)])
def change_password(body: NewPassword, request: Request, identity: Identity = Depends(current_identity),
                    session: Session = Depends(get_session)):
    """Change one's own password; one's other sessions end."""
    user = session.merge(identity.user)
    try:
        signin.change_password(session, user, body.current_password, body.new_password,
                               request.cookies.get(signin.COOKIE))
    except signin.SignInError as e:
        session.rollback()
        raise HTTPException(e.status, str(e)) from e
    return user_json(user)
