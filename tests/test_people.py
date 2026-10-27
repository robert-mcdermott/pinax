# SPDX-License-Identifier: Apache-2.0
"""People (A3, A4): adding people, changing their details, the administrator role, and deactivating and
reactivating them, each audited, with the rules that keep at least one administrator able to sign in."""

from conftest import ROOT, login
from sqlalchemy import select

ALPHA = f"{ROOT}/alpha_lab"


def _events(action: str) -> list:
    from pinax.db import session_scope
    from pinax.models import AuditEvent

    with session_scope() as s:
        return [(e.summary, e.detail, e.actor.username if e.actor else None)
                for e in s.scalars(select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.id))]


def _add(client, **body):
    login(client, "ivy")
    return client.post("/api/admin/people", json={"display_name": "Jane Doe", "email": "jdoe@example.org", **body})


def test_only_administrators_manage_people(client):
    login(client, "ada")
    assert client.get("/api/admin/people/ben").status_code == 403
    assert client.post("/api/admin/people", json={"username": "x", "display_name": "X", "email": "x@y"}).status_code == 403
    assert client.put("/api/admin/people/ben", json={"display_name": "B", "email": "b@y"}).status_code == 403
    assert client.post("/api/admin/people/ben/deactivate").status_code == 403


def test_a_person_added_can_sign_in_and_sees_their_labs(client):
    r = _add(client, username="jdoe", title="Postdoc", memberships=[{"lab": "alpha", "role": "delegate"}])
    assert r.status_code == 201, r.text
    p = r.json()
    assert (p["username"], p["title"], p["active"], p["is_admin"]) == ("jdoe", "Postdoc", True, False)
    assert [(lab["slug"], lab["role"]) for lab in p["labs"]] == [("alpha", "delegate")]
    _, detail, actor = _events("person.created")[-1]
    assert actor == "ivy" and detail["username"] == "jdoe" and detail["source"] == "app"
    assert detail["fields"]["email"] == {"before": None, "after": "jdoe@example.org"}
    assert [d["role"] for _, d, _ in _events("lab.member")] == ["delegate"]
    login(client, "jdoe")
    assert client.get("/api/me").json()["username"] == "jdoe"
    assert [f["folder"] for f in client.get("/api/home").json()["folders"]] == ["alpha_lab"]


def test_adding_follows_the_import_rules(client):
    assert _add(client, username="ada").status_code == 409  # taken
    r = _add(client, username="Ada")
    assert r.status_code == 409 and "differs only in case" in r.json()["detail"]
    assert _add(client, username="j doe").status_code == 422
    assert _add(client, username="").status_code == 422
    assert _add(client, username="x" * 65).status_code == 422
    assert _add(client, username="jdoe", email="not-an-email").status_code == 422
    assert _add(client, username="jdoe", display_name="  ").status_code == 422
    r = _add(client, username="jdoe", memberships=[{"lab": "nowhere", "role": "pi"}])
    assert r.status_code == 404
    r = _add(client, username="jdoe", memberships=[{"lab": "alpha", "role": "owner"}])
    assert r.status_code == 422
    r = _add(client, username="jdoe", memberships=[{"lab": "alpha", "role": "pi"}, {"lab": "alpha", "role": "delegate"}])
    assert r.status_code == 422
    # A refused request changes nothing.
    login(client, "ivy")
    assert client.get("/api/admin/people/jdoe").status_code == 404 and not _events("person.created")


def test_changing_details_and_labs_is_audited_field_by_field(client):
    login(client, "ivy")
    r = client.put("/api/admin/people/ben", json={"display_name": "Ben Okafor", "email": "ben@example.org",
                                                  "title": "Lab manager", "memberships": [{"lab": "alpha", "role": "pi"}]})
    assert r.status_code == 200, r.text
    assert [(lab["slug"], lab["role"]) for lab in r.json()["labs"]] == [("alpha", "pi")]  # out of beta, into alpha
    summary, detail, actor = _events("person.updated")[-1]
    assert summary == "Ben Okafor: display name, title" and actor == "ivy"
    assert detail["fields"] == {"display_name": {"before": "Ben", "after": "Ben Okafor"},
                                "title": {"before": "", "after": "Lab manager"}}
    assert sorted((d["lab"], d["role"], d["before"]) for _, d, _ in _events("lab.member")) == [
        ("alpha", "pi", None), ("beta", None, "delegate")]
    history = client.get("/api/admin/people/ben").json()["history"]
    assert {h["action"] for h in history} == {"person.updated", "lab.member"} and history[0]["by"] == "Ivy"
    # Saving without changes records nothing; without `memberships`, they are left as they are.
    n = len(_events("person.updated"))
    r = client.put("/api/admin/people/ben", json={"display_name": "Ben Okafor", "email": "ben@example.org",
                                                  "title": "Lab manager"})
    assert r.status_code == 200 and len(_events("person.updated")) == n and r.json()["labs"][0]["slug"] == "alpha"
    assert client.put("/api/admin/people/ben", json={"username": "benny", "display_name": "B", "email": "b@x.org"}
                      ).status_code == 422  # the username is the sign-on name


def test_the_administrator_role(client):
    login(client, "ivy")
    r = client.put("/api/admin/people/ada", json={"display_name": "Ada", "email": "ada@example.org", "is_admin": True})
    assert r.status_code == 200 and r.json()["is_admin"]
    assert _events("person.updated")[-1][0] == "Ada: granted the storage administrator role"
    login(client, "ada")
    assert client.get("/api/admin/people").status_code == 200
    # Nobody removes their own role; with two administrators, one can remove the other's.
    r = client.put("/api/admin/people/ada", json={"display_name": "Ada", "email": "ada@example.org", "is_admin": False})
    assert r.status_code == 409 and "your own" in r.json()["detail"]
    r = client.put("/api/admin/people/ivy", json={"display_name": "Ivy", "email": "ivy@example.org", "is_admin": False})
    assert r.status_code == 200 and not r.json()["is_admin"]
    assert _events("person.updated")[-1][0] == "Ivy: removed the storage administrator role"
    login(client, "ivy")
    assert client.get("/api/admin/people").status_code == 403


def test_deactivating_stops_sign_in_and_reactivating_restores_access(client):
    login(client, "ivy")
    r = client.post("/api/admin/people/ada/deactivate")
    assert r.status_code == 200 and not r.json()["active"]
    summary, detail, _ = _events("person.deactivated")[-1]
    assert summary == "Deactivated Ada; cannot sign in (access kept, unused: Alpha Lab (pi))"
    assert detail["fields"]["active"] == {"before": True, "after": False}
    login(client, "ada")
    assert client.get("/api/me").status_code == 401
    assert "ada" not in [u["username"] for u in client.get("/api/demo/users").json()]
    login(client, "ivy")
    assert client.put("/api/admin/people/ada", json={"display_name": "A", "email": "a@x.org"}).status_code == 409
    assert client.post("/api/admin/people/ada/deactivate").status_code == 409  # already inactive
    r = client.post("/api/admin/people/ada/reactivate")
    assert r.status_code == 200 and r.json()["active"]
    assert _events("person.reactivated")[-1][0] == "Reactivated Ada; access restored: Alpha Lab (pi)"
    login(client, "ada")
    assert [f["folder"] for f in client.get("/api/home").json()["folders"]] == ["alpha_lab"]


def test_an_administrator_always_remains(client):
    login(client, "ivy")
    r = client.post("/api/admin/people/ivy/deactivate")
    assert r.status_code == 409 and "yourself" in r.json()["detail"]
    _add(client, username="sam", display_name="Sam", email="sam@example.org", is_admin=True)
    login(client, "sam")
    r = client.post("/api/admin/people/ivy/deactivate")
    assert r.status_code == 200
    # Sam is now the only active administrator: no one can take that away.
    login(client, "ivy")
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/admin/people/nope/reactivate").status_code == 401
    login(client, "sam")
    assert client.post("/api/admin/people/nobody/archive").status_code in (404, 405)  # no such change
    r = client.put("/api/admin/people/sam", json={"display_name": "Sam", "email": "sam@example.org", "is_admin": False})
    assert r.status_code == 409  # Sam's own role, and the last active administrator's


def test_the_import_and_the_page_share_their_rules(client, tmp_path):
    """A username the page refuses, the import refuses for the same reason."""
    from pinax.db import session_scope
    from pinax.onboard import plan_import

    f = tmp_path / "people.csv"
    f.write_text("username,display_name,email\nAda,Ada Again,ada2@example.org\njo doe,Jo,jo@example.org\n"
                 "ok,Ok,not-an-email\n")
    with session_scope() as s:
        problems = plan_import(s, people=f).problems
    page = [_add(client, username=u, email=e).json()["detail"] for u, e in
            (("Ada", "a@b.org"), ("jo doe", "j@b.org"), ("ok", "not-an-email"))]
    for got, want in zip(problems, page, strict=True):
        assert got.split(": ", 1)[1].lower() == want.lower()
