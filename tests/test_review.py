# SPDX-License-Identifier: Apache-2.0
"""The access review (A7): who has access to what, with when each was given, what is worth a look, a CSV, and a
sign-off, for administrators and auditors."""

import csv
import io
from datetime import timedelta

import pytest
from conftest import ROOT, login

from pinax import config


@pytest.fixture
def people(client):
    """Alpha Lab in Basic Sciences (Ada, PI), Beta Lab (Ben, delegate: no PI); Ivy, an administrator; Nobody, with no
    access; an auditor, finance, and the head of Basic Sciences."""
    login(client, "ivy")
    client.put("/api/admin/labs/alpha", json={"name": "Alpha Lab", "division": "Basic Sciences"})
    for username, roles in (("aud", [{"role": "auditor"}]), ("fin", [{"role": "finance"}]),
                            ("head", [{"role": "division", "division": "Basic Sciences"}])):
        assert client.post("/api/admin/people", json={"username": username, "display_name": username.title(),
                                                      "email": f"{username}@example.org", "roles": roles}).status_code == 201
    return client


def _review(client, user="ivy"):
    login(client, user)
    r = client.get("/api/admin/access-review")
    assert r.status_code == 200, r.text
    return r.json()


def _set(model, where, **values):
    from pinax.db import session_scope

    with session_scope() as s:
        for row in s.query(model).filter_by(**where):
            for k, v in values.items():
                setattr(row, k, v)


def test_for_administrators_and_auditors_only(people):
    client = people
    for user, status in (("ivy", 200), ("aud", 200), ("ada", 403), ("fin", 403), ("head", 403)):
        login(client, user)
        assert client.get("/api/admin/access-review").status_code == status, user
        assert client.post("/api/admin/access-review/sign-off", json={}).status_code == (201 if status == 200 else 403)


def test_who_has_access_to_what(people):
    r = _review(people)
    assert [(x["label"], x["division"], x["person"]["username"]) for x in r["roles"]] == [
        ("Storage administrator", "", "ivy"), ("Auditor", "", "aud"), ("Finance", "", "fin"),
        ("Division head", "Basic Sciences", "head")]
    assert all(x["since"] for x in r["roles"])
    alpha, beta = r["labs"]
    assert [(m["username"], m["label"]) for m in alpha["members"]] == [("ada", "PI")] and alpha["heads"] == ["Head"]
    assert [f["path"] for f in alpha["folders"]] == ["/fh/fast/alpha_lab"]
    assert [(m["username"], m["label"]) for m in beta["members"]] == [("ben", "Delegate")]
    people_ = {p["username"]: p for p in r["people"]}
    assert people_["ivy"]["everything"] and people_["aud"]["everything"] and people_["fin"]["charges"]
    assert people_["head"]["access"] == ["Head of Basic Sciences"]
    assert [(f["path"], f["via"]) for f in people_["head"]["folders"]] == [("/fh/fast/alpha_lab", "head of Basic Sciences")]
    assert people_["ada"]["access"] == ["PI, Alpha Lab"] and people_["nobody"]["access"] == []
    texts = [a["text"] for a in r["attention"]]
    assert "Beta Lab has no PI" in texts and "Nobody can sign in and sees nothing" in texts
    assert r["counts"] == {"people": 6, "labs": 2, "roles": 4, "attention": len(texts), "no_access": 1,
                                "inactive_people": 0, "inactive_labs": 0}
    # Deactivated people and labs give no access: counted, not listed.
    people.post("/api/admin/people/ben/deactivate")
    people.post("/api/admin/labs/alpha/deactivate")
    r = _review(people)
    assert [lab["slug"] for lab in r["labs"]] == ["beta"] and r["labs"][0]["members"] == []
    assert "ben" not in {p["username"] for p in r["people"]}
    assert r["counts"]["inactive_people"] == 1 and r["counts"]["inactive_labs"] == 1
    assert {p["username"]: p for p in r["people"]}["head"]["folders"] == []


def test_when_access_was_given(people):
    from pinax.db import utcnow
    from pinax.models import LabMembership, User

    client = people
    before = _review(client)
    ada = next(m for m in before["labs"][0]["members"] if m["username"] == "ada")
    assert ada["since"] is not None  # given when the catalog was made
    _set(LabMembership, {}, granted_at=None)  # as if given before 0.12.0
    assert _review(client)["labs"][0]["members"][0]["since"] is None
    # Made PI of Beta: given now; the role changed: given again.
    ben = client.get("/api/admin/people/ben").json()
    fields = {k: ben[k] for k in ("display_name", "email", "title", "is_admin")}
    client.put("/api/admin/people/ben", json=fields | {"memberships": [{"lab": "beta", "role": "pi"}]})
    beta = _review(client)["labs"][1]
    assert beta["members"][0]["label"] == "PI" and beta["members"][0]["since"].startswith(utcnow().strftime("%Y-%m-%d"))
    # Made an administrator, and no longer.
    _set(User, {"username": "ivy"}, admin_since=None)
    client.put("/api/admin/people/ben", json=fields | {"is_admin": True})
    admins = {x["person"]["username"]: x["since"] for x in _review(client)["roles"] if x["role"] == "admin"}
    assert admins["ivy"] is None and admins["ben"] is not None
    client.put("/api/admin/people/ben", json=fields | {"is_admin": False})
    from pinax.db import session_scope

    with session_scope() as s:
        assert s.query(User).filter_by(username="ben").one().admin_since is None


def test_when_a_folder_came_to_its_lab(people):
    client = people
    assert _review(client)["labs"][0]["folders"][0]["since"] is None  # mapped before Pinax recorded it
    assert client.post("/api/admin/folders", json={"fs": "fast", "folder": "orphans", "lab": "beta"}).status_code == 200
    beta = _review(client)["labs"][1]
    orphans = next(f for f in beta["folders"] if f["folder"] == "orphans")
    assert orphans["since"] is not None and orphans["path"] == "/fh/fast/orphans"
    client.post("/api/admin/folders", json={"fs": "fast", "folder": "orphans", "lab": "alpha"})
    folders = {f["folder"]: f for lab in _review(client)["labs"] for f in lab["folders"]}
    assert folders["orphans"]["since"] >= orphans["since"] and "orphans" not in [f["folder"] for f in _review(client)["labs"][1]["folders"]]


def test_tokens_and_idle_people_are_worth_a_look(people, monkeypatch):
    from pinax import review
    from pinax.db import session_scope, utcnow
    from pinax.models import User

    login(people, "ada")
    people.post("/api/tokens", json={"name": "Pipeline", "days": 30, "read_only": False})
    people.post("/api/tokens", json={"name": "Report", "days": 30})
    r = _review(people)
    token = next(a for a in r["attention"] if a["kind"] == "token")
    assert token["text"] == "The token “Pipeline” can make changes as Ada, not only read"
    assert token["href"] == "#/access/people/ada" and token["expires_at"] and token["last_used_at"] is None
    assert not any("Report" in a["text"] for a in r["attention"])  # reads only
    ada = {p["username"]: p for p in r["people"]}["ada"]
    assert ada["tokens"] == 2 and ada["tokens_with_access"] == 1
    assert not any(a["kind"] == "idle" for a in r["attention"])  # sign-ins are not known behind single sign-on
    _set(User, {}, created_at=utcnow() - timedelta(days=200))
    _set(User, {"username": "ada"}, last_login_at=utcnow() - timedelta(days=1))
    monkeypatch.setenv("PINAX_AUTH_MODE", "local")
    config.get_settings.cache_clear()
    with session_scope() as s:
        idle = [a["text"] for a in review.gather(s)["attention"] if a["kind"] == "idle"]
    assert "Ben has not signed in for 90 days (never)" in idle and not any(t.startswith("Ada") for t in idle)
    assert not any(t.startswith("Nobody") for t in idle)  # sees nothing: listed as that instead


def test_sign_off_is_audited_and_listed(people):
    client = people
    login(client, "aud")
    r = client.post("/api/admin/access-review/sign-off", json={"note": "Quarterly review, Q4 2026"})
    assert r.status_code == 201
    latest = r.json()["reviews"][0]
    assert latest["by"] == "Aud" and latest["note"] == "Quarterly review, Q4 2026"
    assert latest["summary"] == "Aud reviewed access: 6 people with access, 2 labs and departments, 4 site roles"
    login(client, "ivy")
    event = client.get("/api/admin/audit", params={"action": "access.reviewed"}).json()["events"][0]
    assert event["detail"]["counts"]["people"] == 6 and event["who"]["username"] == "aud"
    assert client.post("/api/admin/access-review/sign-off", json={"note": "x" * 2001}).status_code == 422
    # It changes no one's access.
    assert [x["person"]["username"] for x in _review(client)["roles"]] == ["ivy", "aud", "fin", "head"]


def test_csv_has_a_row_for_each_piece_of_access(people):
    login(people, "aud")
    r = people.get("/api/admin/access-review", params={"fmt": "csv"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'filename="pinax-access-review-' in r.headers["content-disposition"]
    rows = [(x["username"], x["access"], x["lab"], x["division"], x["sees"]) for x in csv.DictReader(io.StringIO(r.text))]
    assert ("ada", "PI", "alpha", "Basic Sciences", "/fh/fast/alpha_lab") in rows
    assert ("ben", "Delegate", "beta", "", "/fh/fast/beta_lab") in rows
    assert ("head", "Division head", "", "Basic Sciences", "/fh/fast/alpha_lab") in rows
    assert ("ivy", "Storage administrator", "", "", "everything") in rows and ("fin", "Finance", "", "", "every lab's charges") in rows
    assert ("nobody", "none", "", "", "") in rows
    assert ROOT not in r.text  # paths as people know them
