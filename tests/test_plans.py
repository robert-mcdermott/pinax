# SPDX-License-Identifier: Apache-2.0
"""Cleanup plans (C1): a lab's PI, delegates, and administrators add folders to the lab's plan, to delete, move,
or compress; others' folders cannot be planned; a folder inside a planned one counts once; every change audited."""

import pytest
from conftest import ROOT, login

ALPHA = f"{ROOT}/alpha_lab"


@pytest.fixture
def planner(client):
    from pinax.db import session_scope
    from pinax.models import Filesystem

    with session_scope() as s:
        s.add(Filesystem(slug="slow", name="Slow", root="/research/slow"))
    return client


def _add(client, user: str, path: str, action: str = "delete", **body):
    login(client, user)
    return client.post("/api/plans", json={"fs": "fast", "path": path, "action": action, **body})


def test_a_pi_plans_their_labs_folders(planner):
    r = _add(planner, "ada", f"{ALPHA}/projects/2015_old", note="Published in 2016; raw data is in SRA")
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["lab"] == "alpha" and item["action"] == "delete" and item["added_by"] == "Ada"
    assert item["baseline"]["bytes"] == 8050 and item["now"]["bytes"] == 8050 and item["mark"]["status"] == "here"
    assert _add(planner, "ada", f"{ALPHA}/projects/2015_old").status_code == 409


def test_a_delegate_plans_directly_and_no_one_plans_others_folders(planner):
    assert _add(planner, "ben", f"{ROOT}/beta_lab/imaging", "compress").status_code == 200
    assert _add(planner, "ada", f"{ROOT}/beta_lab/imaging").status_code == 404
    assert _add(planner, "nobody", f"{ALPHA}/projects").status_code == 404
    for path in (ROOT, f"{ALPHA}/../beta_lab", f"{ALPHA}/nowhere"):
        assert _add(planner, "ada", path).status_code == 404, path
    unowned = _add(planner, "ivy", f"{ROOT}/orphans/old")
    assert unowned.status_code == 422 and "No lab or department owns" in unowned.json()["detail"]


def test_moves_name_another_filesystem(planner):
    for to, code in ((None, 422), ("fast", 422), ("nowhere", 422), ("slow", 200)):
        r = _add(planner, "ada", f"{ALPHA}/projects/2025_new", "move", to=to)
        assert r.status_code == code, (to, r.text)
    assert r.json()["to"] == {"slug": "slow", "name": "Slow"}
    assert _add(planner, "ada", f"{ALPHA}/projects/2015_old", "shred").status_code == 422


def test_a_folder_inside_a_planned_one_counts_once(planner):
    _add(planner, "ada", f"{ALPHA}/projects/2015_old")
    _add(planner, "ada", f"{ALPHA}/projects", "move", to="slow")
    plan = planner.get("/api/plans").json()["labs"][0]
    inner, outer = plan["items"]
    assert inner["inside"] == outer["id"] and outer["inside"] is None
    assert plan["total"] == {"folders": 1, "bytes": 19060}
    here = planner.get("/api/plans/here", params={"fs": "fast", "path": f"{ALPHA}/projects/2015_old/reads"}).json()
    assert here["plannable"] and here["item"] is None and here["inside"]["path"] == f"{ALPHA}/projects"
    assert here["targets"] == [{"slug": "slow", "name": "Slow"}] and here["total"]["folders"] == 1
    login(planner, "ben")
    assert planner.get("/api/plans/here", params={"fs": "fast", "path": f"{ALPHA}/projects"}).json() == {
        "plannable": False, "lab": None}


def test_changing_and_removing_are_audited(planner):
    from sqlalchemy import select

    from pinax.db import session_scope
    from pinax.models import AuditEvent

    item = _add(planner, "ada", f"{ALPHA}/projects/2015_old").json()["id"]
    r = planner.put(f"/api/plans/{item}", json={"action": "compress", "note": "gzip the BAMs? no: CRAM"})
    assert r.status_code == 200 and r.json()["action"] == "compress"
    login(planner, "ben")
    assert planner.delete(f"/api/plans/{item}").status_code == 404  # not his lab's
    login(planner, "ivy")
    assert planner.get("/api/plans", params={"lab": "alpha"}).json()["labs"][0]["total"]["folders"] == 1
    assert planner.delete(f"/api/plans/{item}").status_code == 200
    assert planner.post(f"/api/plans/{item}/moved", json={"accept": True}).status_code == 404
    with session_scope() as s:
        assert [e.action for e in s.scalars(select(AuditEvent).where(AuditEvent.action.like("plan.%"))
                                            .order_by(AuditEvent.id))] == ["plan.added", "plan.changed", "plan.removed"]


def test_a_folder_that_has_not_moved_cannot_be_confirmed_moved(planner):
    item = _add(planner, "ada", f"{ALPHA}/projects/2015_old").json()["id"]
    r = planner.post(f"/api/plans/{item}/moved", json={"accept": True})
    assert r.status_code == 409 and "has not moved" in r.json()["detail"]
