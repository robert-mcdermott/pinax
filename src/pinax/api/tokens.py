# SPDX-License-Identifier: Apache-2.0
"""API tokens (tokens.py): a person's own, for everyone; service tokens, made by administrators; every token, for
administrators and auditors to see."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import tokens
from ..auth import Identity, current_identity, require_admin, require_sees_all
from ..config import get_settings
from ..db import get_session
from ..models import ApiToken

router = APIRouter(tags=["tokens"])


class TokenBody(BaseModel):
    name: str = ""
    days: int = 90
    read_only: bool = True
    role: str | None = None  # a service token's: auditor or finance


def _made(session: Session, make) -> dict:
    try:
        t, secret = make()
    except tokens.TokenError as e:
        session.rollback()
        raise HTTPException(e.status, str(e)) from e
    session.commit()
    return {"token": tokens.token_json(t), "secret": secret}


@router.get("/tokens")
def mine(identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    """The person's own tokens (ended ones for 90 days), and the longest a token may last."""
    return {"tokens": [tokens.token_json(t) for t in tokens.listed(session, identity.user)],
            "max_days": get_settings().token_max_days}


@router.post("/tokens", status_code=201)
def make(body: TokenBody, identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    """A token of the person's own, with their access or reading only; its secret is returned this once."""
    return _made(session, lambda: tokens.make(session, identity.user, body.name, body.days, read_only=body.read_only))


@router.delete("/tokens/{token_id}")
def revoke(token_id: int, identity: Identity = Depends(current_identity), session: Session = Depends(get_session)):
    """Revoke a token: the person's own, or, for administrators, anyone's or a service's."""
    t = session.get(ApiToken, token_id)
    if t is None or not (identity.is_admin or (t.user_id is not None and t.user_id == identity.user.id)):
        raise HTTPException(404, "No such token of yours")
    try:
        tokens.revoke(session, identity.user, t)
    except tokens.TokenError as e:
        raise HTTPException(e.status, str(e)) from e
    session.commit()
    return tokens.token_json(t)


@router.get("/admin/tokens")
def every(_: Identity = Depends(require_sees_all), session: Session = Depends(get_session)):
    """Every person's tokens and every service token (ended ones for 90 days)."""
    return {"tokens": [tokens.token_json(t) for t in tokens.listed(session)],
            "roles": tokens.SERVICE_ROLES, "max_days": get_settings().token_max_days}


@router.post("/admin/tokens", status_code=201)
def make_service(body: TokenBody, identity: Identity = Depends(require_admin), session: Session = Depends(get_session)):
    """A service token for a named service, reading as an auditor or as finance; its secret is returned this once."""
    return _made(session, lambda: tokens.make(session, identity.user, body.name, body.days, role=body.role or ""))
