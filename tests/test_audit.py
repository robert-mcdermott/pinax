# SPDX-License-Identifier: Apache-2.0
"""The audit log in the app (O6): every audited change, for administrators and auditors, found by person, action,
lab, folder, and day, newest first, a page at a time or as CSV, with where each change came from."""

import csv
import io
import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import ROOT, login

ALPHA = ROOT + "/alpha_lab"


@pytest.fixture
def changes(client):
    """An auditor and a finance officer; a folder mapped, a plan, a tag on a folder, a person changed, a token."""
    login(client, "ivy")
    for username, role in (("aud", "auditor"), ("fin", "finance")):
        client.post("/api/admin/people", json={"username": username, "display_name": username.title(),
                                               "email": f"{username}@example.org", "roles": [{"role": role}]})
    assert client.post("/api/admin/folders", json={"fs": "fast", "folder": "orphans", "lab": "beta"}).status_code == 200
    ben = client.get("/api/admin/people/ben").json()
    client.put("/api/admin/people/ben", json={k: ben[k] for k in ("display_name", "email", "is_admin")} | {"title": "Lab manager"})
    stage = next(t["id"] for t in client.get("/api/admin/tags").json()["standard"] if t["key"] == "stage")
    login(client, "ada")
    assert client.post("/api/plans", json={"fs": "fast", "path": ALPHA + "/projects", "action": "delete"}).status_code == 200
    assert client.post("/api/tags/folders", json={"fs": "fast", "path": ALPHA, "tag": stage, "value": "raw"}).status_code == 200
    assert client.post("/api/tokens", json={"name": "Report", "days": 7}).status_code == 201
    login(client, "ivy")
    return client


def _log(client, **params):
    r = client.get("/api/admin/audit", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _actions(client, **params):
    return [e["action"] for e in _log(client, **params)["events"]]


def test_for_administrators_and_auditors_only(changes):
    client = changes
    for user, status in (("ivy", 200), ("aud", 200), ("ada", 403), ("fin", 403)):
        login(client, user)
        assert client.get("/api/admin/audit").status_code == status, user


def test_every_change_newest_first_with_who_and_where(changes):
    log = _log(changes)
    assert [e["action"] for e in log["events"][:4]] == ["token.made", "folder.tagged", "plan.added", "person.updated"]
    newest = log["events"][0]
    assert newest["who"] == {"username": "ada", "display_name": "Ada"} and newest["address"] == "testclient"
    assert newest["source"] == "app" and log["total"] == len(log["events"])
    ingested = next(e for e in log["events"] if e["action"] == "snapshot.ingested")
    assert ingested["who"] is None and ingested["source"] == "pinax"
    # Paths as Pinax shows them: the filesystem's display root.
    tagged = log["events"][1]
    assert tagged["summary"].startswith("/fh/fast/alpha_lab tagged") and tagged["detail"]["path"] == "/fh/fast/alpha_lab"
    assert {"action": "plan.added", "kind": "plan", "count": 1} in log["actions"]
    assert {"username": "ada", "display_name": "Ada"} in log["people"] and log["kinds"]["plan"] == "Cleanup plans"


def test_by_person_action_lab_and_day(changes):
    client = changes
    assert _actions(client, person="ada") == ["token.made", "folder.tagged", "plan.added"]
    assert _actions(client, person="ben") == ["person.updated"]  # made by Ivy, about Ben
    assert "person.updated" in _actions(client, person="ivy") and "folder.mapped" in _actions(client, person="ivy")
    assert _actions(client, action="plan") == ["plan.added"] == _actions(client, action="plan.added")
    assert _actions(client, action="plan.removed") == []
    assert _actions(client, lab="alpha") == ["folder.tagged", "plan.added"]
    assert _actions(client, lab="beta") == ["folder.mapped"]  # a folder given to it
    assert _actions(client, person="ada", action="plan") == ["plan.added"]
    today = datetime.now(UTC).date()  # the log's days are UTC days
    assert _log(client, since=str(today + timedelta(days=1)))["total"] == 0
    assert _log(client, until=str(today - timedelta(days=1)))["total"] == 0
    assert _log(client, since=str(today), until=str(today))["total"] == _log(client)["total"]


def test_by_folder(changes):
    client = changes
    assert _actions(client, fs="fast", path=ALPHA) == ["folder.tagged", "plan.added"]
    assert _actions(client, fs="fast", path=ALPHA + "/projects") == ["plan.added"]
    assert _actions(client, fs="fast", path=ROOT + "/orphans") == ["folder.mapped"]  # who owns it
    assert _actions(client, fs="fast", path=ROOT + "/orphans/deeper") == ["folder.mapped"]  # who owns the folder it is in
    assert _actions(client, fs="fast") == ["folder.tagged", "plan.added", "folder.mapped"]  # the whole filesystem
    assert _actions(client, fs="fast", path=ROOT + "/alpha") == []  # not a prefix of alpha_lab
    # A path's wildcards are only characters.
    assert _actions(client, fs="fast", path=ROOT + "/alpha%") == [] and _actions(client, fs="fast", path=ROOT + "/alpha_lab_") == []


def test_mistakes_are_said(changes):
    client = changes
    assert client.get("/api/admin/audit", params={"person": "nobody-at-all"}).status_code == 404
    assert client.get("/api/admin/audit", params={"fs": "nope"}).status_code == 404
    assert client.get("/api/admin/audit", params={"lab": "nope"}).status_code == 404
    assert client.get("/api/admin/audit", params={"path": ALPHA}).status_code == 422  # no filesystem
    assert client.get("/api/admin/audit", params={"fs": "fast", "path": "/elsewhere"}).status_code == 422
    assert client.get("/api/admin/audit", params={"since": "yesterday"}).status_code == 422


def test_a_page_at_a_time(changes):
    client = changes
    every = _log(client)["events"]
    first, second = _log(client, limit=2), _log(client, limit=2, offset=2)
    assert [e["id"] for e in first["events"] + second["events"]] == [e["id"] for e in every[:4]]
    assert first["total"] == second["total"] == len(every)


def test_csv_has_every_event_as_recorded(changes):
    client = changes
    r = client.get("/api/admin/audit", params={"fmt": "csv", "lab": "alpha"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'filename="pinax-audit-' in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert [row["action"] for row in rows] == ["folder.tagged", "plan.added"]
    assert rows[0]["username"] == "ada" and rows[0]["address"] == "testclient"
    assert json.loads(rows[0]["detail"])["path"] == ALPHA  # as recorded
    assert client.get("/api/admin/audit", params={"fmt": "csv", "person": "nobody-at-all"}).status_code == 404
    login(client, "fin")
    assert client.get("/api/admin/audit", params={"fmt": "csv"}).status_code == 403


def test_a_change_made_with_a_token_names_it(changes):
    from fastapi.testclient import TestClient

    from pinax.main import app

    login(changes, "ada")
    secret = changes.post("/api/tokens", json={"name": "Pipeline", "days": 7, "read_only": False}).json()["secret"]
    TestClient(app).delete(f"/api/plans/{_plan_id(changes)}", headers={"Authorization": f"Bearer {secret}"})
    login(changes, "ivy")
    removed = _log(changes, action="plan.removed")["events"]
    assert len(removed) == 1 and removed[0]["token"] == "Pipeline" and removed[0]["who"]["username"] == "ada"


def _plan_id(client):
    return client.get("/api/plans", params={"lab": "alpha"}).json()["labs"][0]["items"][0]["id"]
