# SPDX-License-Identifier: Apache-2.0
"""Saved views (E9): a person keeps a page under a name, to themselves or shared with one of their labs."""

import pytest
from conftest import ROOT, login

PAGE = f"#/explore?fs=fast&path={ROOT}/alpha_lab/projects&size=files&levels=1"


@pytest.fixture
def team(client):
    """Ada (the Alpha Lab's PI) and Abe, also in the Alpha Lab; Ben in the Beta Lab."""
    from sqlalchemy import select

    from pinax.db import session_scope
    from pinax.models import Lab, LabMembership, User

    with session_scope() as s:
        abe = User(username="abe", display_name="Abe", email="abe@example.org")
        s.add(abe)
        s.flush()
        s.add(LabMembership(user_id=abe.id, lab_id=s.scalar(select(Lab.id).where(Lab.slug == "alpha")), role="delegate"))
    return client


def _views(client, user: str) -> list[dict]:
    login(client, user)
    return client.get("/api/views").json()["views"]


def test_a_view_is_kept_to_oneself_until_shared(team):
    login(team, "ada")
    r = team.post("/api/views", json={"name": "Projects by files", "address": PAGE})
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["address"] == PAGE and view["mine"] and view["lab"] is None
    assert _views(team, "abe") == []
    login(team, "ada")
    shared = team.put(f"/api/views/{view['id']}", json={"lab": "alpha"}).json()
    assert shared["lab"]["slug"] == "alpha"
    (seen,) = _views(team, "abe")
    assert seen["name"] == "Projects by files" and not seen["mine"] and seen["by"] == "Ada"
    assert _views(team, "ben") == []
    login(team, "ada")
    team.put(f"/api/views/{view['id']}", json={"unshare": True})
    assert _views(team, "abe") == []


def test_only_ones_own_labs_and_pages_of_pinax(team):
    login(team, "ada")
    assert team.post("/api/views", json={"name": "Theirs", "address": PAGE, "lab": "beta"}).status_code == 404
    for address in ("javascript:alert(1)", "https://example.org/", "#/explore?x=<script>", "", "#explore"):
        assert team.post("/api/views", json={"name": "Odd", "address": address}).status_code == 422, address
    assert team.post("/api/views", json={"name": "  ", "address": PAGE}).status_code == 422


def test_only_whoever_saved_a_view_changes_it(team):
    from sqlalchemy import select

    from pinax.db import session_scope
    from pinax.models import AuditEvent

    login(team, "ada")
    view = team.post("/api/views", json={"name": "Mine", "address": PAGE, "lab": "alpha"}).json()["id"]
    login(team, "abe")
    assert team.put(f"/api/views/{view}", json={"name": "Taken"}).status_code == 403
    assert team.delete(f"/api/views/{view}").status_code == 403
    login(team, "ben")
    assert team.delete(f"/api/views/{view}").status_code == 404
    login(team, "ada")
    assert team.put(f"/api/views/{view}", json={"name": "Renamed"}).json()["name"] == "Renamed"
    assert team.delete(f"/api/views/{view}").status_code == 200
    with session_scope() as s:
        assert [e.action for e in s.scalars(select(AuditEvent).where(AuditEvent.action.like("view.%")))] == [
            "view.saved", "view.changed", "view.deleted"]
