# SPDX-License-Identifier: Apache-2.0
"""Site roles (A10): an auditor sees everything and changes nothing; finance sees every charge and no folders; a
division head sees the labs of their division, as their members do, and changes nothing. Checked on the server, for
each role, as tests/test_api.py checks labs."""

import pytest
from conftest import ROOT, login

ALPHA = ROOT + "/alpha_lab"
BETA = ROOT + "/beta_lab"


def _person(client, username, roles, memberships=None):
    login(client, "ivy")
    body = {"username": username, "display_name": username.title(), "email": f"{username}@example.org", "roles": roles}
    if memberships is not None:
        body["memberships"] = memberships
    return client.post("/api/admin/people", json=body)


@pytest.fixture
def people(client):
    """Alpha Lab in Basic Sciences, Beta Lab in Public Health; an auditor, a finance officer, and a division head."""
    login(client, "ivy")
    for slug, name, division in (("alpha", "Alpha Lab", "Basic Sciences"), ("beta", "Beta Lab", "Public Health")):
        assert client.put(f"/api/admin/labs/{slug}", json={"name": name, "division": division}).status_code == 200
    assert _person(client, "aud", [{"role": "auditor"}]).status_code == 201
    assert _person(client, "fin", [{"role": "finance"}]).status_code == 201
    assert _person(client, "head", [{"role": "division", "division": "Basic Sciences"}]).status_code == 201
    return client


def _status(client, user, method, url, **kw):
    login(client, user)
    return client.request(method, url, **kw).status_code


def test_roles_are_given_and_taken_away_and_audited(people):
    client = people
    login(client, "ivy")
    made = client.get("/api/admin/people/head").json()
    assert [(r["role"], r["division"]) for r in made["roles"]] == [("division", "Basic Sciences")] and made["roles"][0]["since"]
    bad = [[{"role": "owner"}], [{"role": "division"}], [{"role": "division", "division": "Astrophysics"}]]
    for roles in bad:
        assert client.put("/api/admin/people/head", json={**_fields(made), "roles": roles}).status_code == 422, roles
    both = [{"role": "finance"}, {"role": "division", "division": "Public Health"}]
    changed = client.put("/api/admin/people/head", json={**_fields(made), "roles": both}).json()
    assert {(r["role"], r["division"]) for r in changed["roles"]} == {("finance", ""), ("division", "Public Health")}
    kept = client.put("/api/admin/people/head", json=_fields(made)).json()  # no roles given: kept
    assert len(kept["roles"]) == 2
    history = [h["summary"] for h in client.get("/api/admin/people/head").json()["history"]]
    assert "Head is no longer head of Basic Sciences" in history and "Head is now finance" in history
    assert "Head is now head of Public Health" in history
    # Only administrators give roles, auditors included.
    assert _status(client, "aud", "PUT", "/api/admin/people/fin", json={**_fields(made), "roles": []}) == 403
    assert _status(client, "head", "POST", "/api/admin/people", json={"username": "x", "display_name": "X",
                                                                      "email": "x@example.org"}) == 403


def _fields(person):
    return {k: person[k] for k in ("display_name", "email", "title", "is_admin")}


READS = ["/api/admin/overview", "/api/admin/health?fs=fast", "/api/admin/snapshots", "/api/admin/people",
         "/api/admin/labs", "/api/admin/tags", "/api/plans/all", "/api/admin/costs", "/api/admin/costs/charges",
         "/api/admin/costs/ahead", "/api/admin/capacity", f"/api/tree?fs=fast&path={ROOT}/orphans"]
CHARGES = ["/api/admin/costs", "/api/admin/costs/charges", "/api/admin/costs/charges?fmt=csv", "/api/admin/costs/ahead",
           "/api/admin/capacity", "/api/charges?lab=alpha", "/api/charges?lab=beta"]


def test_an_auditor_sees_everything_and_changes_nothing(people):
    client = people
    for url in READS:
        assert _status(client, "aud", "GET", url) == 200, url
    me = client.get("/api/me").json()
    assert me["sees_all"] and me["sees_charges"] and not me["is_admin"]
    tree = client.get("/api/tree", params={"fs": "fast"}).json()
    assert {"alpha_lab", "beta_lab", "orphans"} <= {t["name"] for t in tree["tiles"]}
    assert [c["label"] for c in tree["breadcrumbs"]][:1] == ["All data"]
    stage = next(t["id"] for t in client.get("/api/admin/tags").json()["standard"] if t["key"] == "stage")
    changes = [("POST", "/api/admin/folders", {"fs": "fast", "folder": "orphans", "lab": "beta"}),
               ("POST", "/api/admin/tags", {"label": "Mine", "type": "flag"}),
               ("POST", f"/api/admin/tags/{stage}/active", {"active": False}),
               ("POST", "/api/admin/costs/fast/prices", {"starts": "2026-01-01", "amount": "5", "unit": "TiB"}),
               ("POST", "/api/admin/labs", {"slug": "gamma", "name": "Gamma"}),
               ("POST", "/api/admin/people/fin/deactivate", None)]
    for method, url, body in changes:
        assert _status(client, "aud", method, url, json=body) == 403, url
    # A lab's folders are its PI's and delegates' to act on.
    assert _status(client, "aud", "POST", "/api/tags/folders",
                   json={"fs": "fast", "path": ALPHA, "tag": stage, "value": "raw"}) in (403, 404)
    assert _status(client, "aud", "POST", "/api/plans", json={"fs": "fast", "path": ALPHA, "action": "delete"}) in (403, 404)


def test_finance_sees_every_charge_and_no_folder(people):
    client = people
    for url in CHARGES:
        assert _status(client, "fin", "GET", url) == 200, url
    me = client.get("/api/me").json()
    assert me["sees_charges"] and not me["sees_all"] and not me["sees_labs"]
    beta = client.get("/api/charges", params={"lab": "beta"}).json()
    assert beta["labs"][0]["lab"]["slug"] == "beta" and {c["slug"] for c in beta["choices"]} == {"alpha", "beta"}
    assert all(not f["open"] for line in beta["labs"][0]["filesystems"] for f in line["folders"])
    assert client.get("/api/tree").json()["tiles"] == []
    for url in (f"/api/tree?fs=fast&path={ALPHA}", "/api/tree?fs=fast", f"/api/files?fs=fast&path={ALPHA}"):
        assert _status(client, "fin", "GET", url) == 404, url
    for url in ("/api/admin/overview", "/api/admin/people", "/api/admin/tags", "/api/plans/all", "/api/admin/health?fs=fast"):
        assert _status(client, "fin", "GET", url) == 403, url
    assert _status(client, "fin", "POST", "/api/admin/costs/fast/prices",
                   json={"starts": "2026-01-01", "amount": "5", "unit": "TiB"}) == 403


def test_a_division_head_sees_their_divisions_labs(people):
    client = people
    login(client, "head")
    me = client.get("/api/me").json()
    assert me["divisions"] == ["Basic Sciences"] and me["sees_labs"] and not me["sees_charges"]
    assert [t["path"] for t in client.get("/api/tree").json()["tiles"]] == [ALPHA]
    assert [c["lab"]["slug"] for c in client.get("/api/charges").json()["labs"]] == ["alpha"]  # their division's, unasked
    assert [p["lab"]["slug"] for p in client.get("/api/plans").json()["labs"]] == ["alpha"]
    for url in (f"/api/tree?fs=fast&path={ALPHA}/projects", "/api/charges?lab=alpha", "/api/plans?lab=alpha",
                "/api/plans/suggestions?lab=alpha", f"/api/history?fs=fast&path={ALPHA}"):
        assert _status(client, "head", "GET", url) == 200, url
    for url in (f"/api/tree?fs=fast&path={BETA}", "/api/charges?lab=beta", "/api/plans?lab=beta", "/api/tree?fs=fast"):
        assert _status(client, "head", "GET", url) == 404, url
    for url in ("/api/admin/overview", "/api/admin/costs/charges", "/api/plans/all"):
        assert _status(client, "head", "GET", url) == 403, url
    login(client, "head")
    tagging = client.get("/api/tags/here", params={"fs": "fast", "path": ALPHA}).json()
    assert tagging["available"] == [] and not tagging["can_make"]  # their tags shown, none to add
    planning = client.get("/api/plans/here", params={"fs": "fast", "path": ALPHA}).json()
    assert planning["plannable"] and not planning["can_change"]
    assert not client.get("/api/plans", params={"lab": "alpha"}).json()["labs"][0]["can_change"]
    assert _status(client, "head", "POST", "/api/plans", json={"fs": "fast", "path": ALPHA, "action": "delete"}) == 403
    # The division follows the lab: moved to another division, it is no longer theirs to see.
    login(client, "ivy")
    client.put("/api/admin/labs/alpha", json={"name": "Alpha Lab", "division": "Public Health"})
    assert _status(client, "head", "GET", f"/api/tree?fs=fast&path={ALPHA}") == 404
    login(client, "head")
    assert client.get("/api/tree").json()["tiles"] == []


def test_a_role_beside_a_lab(people):
    """A PI who is also finance acts for their own lab, and sees every lab's charges."""
    client = people
    login(client, "ivy")
    ada = client.get("/api/admin/people/ada").json()
    client.put("/api/admin/people/ada", json={**_fields(ada), "roles": [{"role": "finance"}]})
    login(client, "ada")
    assert [t["path"] for t in client.get("/api/tree").json()["tiles"]] == [ALPHA]
    assert _status(client, "ada", "GET", "/api/charges?lab=beta") == 200
    assert _status(client, "ada", "GET", f"/api/tree?fs=fast&path={BETA}") == 404
    assert _status(client, "ada", "GET", "/api/admin/costs/charges") == 200
