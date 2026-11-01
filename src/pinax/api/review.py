# SPDX-License-Identifier: Apache-2.0
"""The access review (review.py), for administrators and auditors: who has access to what, its CSV, and sign-off."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import review
from ..auth import Identity, require_sees_all
from ..db import get_session, utcnow

router = APIRouter(prefix="/admin/access-review", tags=["access"])


@router.get("")
def access_review(fmt: str = "json", _: Identity = Depends(require_sees_all), session: Session = Depends(get_session)):
    """Site roles, labs and their members and folders, people and what they see, what is worth a look, and the
    latest sign-offs. fmt=csv downloads a row for each piece of access each person has."""
    data = review.gather(session)
    if fmt == "csv":
        name = f"pinax-access-review-{utcnow():%Y-%m-%d}.csv"
        return Response(review.csv_text(data), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})
    return data


class SignOff(BaseModel):
    note: str = ""


@router.post("/sign-off", status_code=201)
def sign_off(body: SignOff, identity: Identity = Depends(require_sees_all), session: Session = Depends(get_session)):
    """Record that the access was reviewed as it is now, with a note; audited. It changes no one's access, so
    auditors may record it too."""
    try:
        review.sign_off(session, identity.user, body.note)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    session.commit()
    return review.gather(session)
