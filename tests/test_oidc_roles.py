# SPDX-License-Identifier: Apache-2.0
"""Roles from Entra ID (wave 13, slice 4): with sign-in through the provider and a role map, each role the map names
is set from the ID token at every sign-in, and only there; roles it does not name stay as the People page gave them;
local sign-in ignores the map. Against the stand-in provider of test_oidc."""

import json
import logging
import sys

import httpx2
import pytest
from fastapi.testclient import TestClient
from test_oidc import PUBLIC_URL, StandInProvider, _oidc_settings, sign_in

from pinax import cli, config, oidc, people
from pinax.config import Settings

ROLE_MAP = {"admin": ["Pinax.Admin"], "auditor": ["Pinax.Auditor"], "division:Basic Sciences": ["Pinax.Head.Basic"]}


@pytest.fixture
def provider(catalog, monkeypatch):
    from pinax.db import session_scope
    from pinax.models import Lab

    with session_scope() as s:
        s.query(Lab).filter_by(slug="alpha").one().division = "Basic Sciences"
    stand_in = StandInProvider()
    _oidc_settings(monkeypatch, OIDC_ROLE_MAP=json.dumps(ROLE_MAP))
    monkeypatch.setattr(oidc, "HTTP_TRANSPORT", httpx2.ASGITransport(app=stand_in.app))
    monkeypatch.setattr(oidc, "_clients", {})
    return stand_in


@pytest.fixture
def browser(provider):
    from pinax.main import app

    with TestClient(app, base_url=PUBLIC_URL) as client:
        yield client


def _as(browser, provider, username: str, **claims):
    browser.cookies.clear()
    done = sign_in(browser, provider, preferred_username=f"{username}@example.org", **claims)
    assert done.status_code == 303, done.text
    return browser.get("/api/me").json()


def _roles(me: dict) -> set:
    return {(r["role"], r["division"]) for r in me["roles"]}


def _events(action: str) -> list[dict]:
    from pinax.db import session_scope
    from pinax.models import AuditEvent

    with session_scope() as s:
        return [e.detail for e in s.query(AuditEvent).filter_by(action=action).order_by(AuditEvent.id)]


def test_mapped_roles_follow_the_token_at_each_sign_in(browser, provider):
    me = _as(browser, provider, "ada", roles=["Pinax.Auditor", "Something.Else"])
    assert _roles(me) == {("auditor", "")} and browser.get("/api/admin/people").status_code == 200
    given = _events("person.role")[-1]
    assert given["source"] == "oidc" and given["given"] is True and given["username"] == "ada"
    me = _as(browser, provider, "ada")  # no longer assigned the app role: Entra ID sends no "roles"
    assert _roles(me) == set() and _events("person.role")[-1]["given"] is False
    assert browser.get("/api/admin/people").status_code == 403


def test_the_administrator_role_comes_from_the_provider(browser, provider, caplog):
    me = _as(browser, provider, "ben", roles=["Pinax.Admin"])
    assert me["is_admin"] is True
    with caplog.at_level(logging.WARNING, logger="pinax.people"):
        assert _as(browser, provider, "ivy")["is_admin"] is False  # not assigned it in Entra ID
    updated = _events("person.updated")[-1]
    assert updated["source"] == "oidc" and updated["fields"]["is_admin"] == {"before": True, "after": False}
    assert not any("No active storage administrator" in r.getMessage() for r in caplog.records)  # Ben is one
    with caplog.at_level(logging.WARNING, logger="pinax.people"):
        _as(browser, provider, "ben")
    assert any("No active storage administrator remains" in r.getMessage() for r in caplog.records)


def test_a_division_head_from_the_provider(browser, provider):
    me = _as(browser, provider, "ben", roles=["Pinax.Head.Basic"])
    assert _roles(me) == {("division", "Basic Sciences")}
    assert {h["slug"] for h in browser.get("/api/home").json()["labs"]} >= {"alpha", "beta"}  # beta is his own


def test_roles_the_map_does_not_name_stay_as_given(browser, provider):
    from pinax.db import session_scope
    from pinax.models import User, UserRole

    with session_scope() as s:
        s.add(UserRole(user_id=s.query(User).filter_by(username="ada").one().id, role="finance", division=""))
    assert _roles(_as(browser, provider, "ada")) == {("finance", "")}


def test_the_people_page_leaves_mapped_roles_alone(browser, provider):
    _as(browser, provider, "ben", roles=["Pinax.Admin"])
    assert browser.get("/api/config").json()["mapped_roles"] == ["admin", "auditor", "division:Basic Sciences"]
    ada = browser.get("/api/admin/people/ada").json()
    fields = {k: ada[k] for k in ("display_name", "email", "title")}
    changed = browser.put("/api/admin/people/ada", headers={"Origin": PUBLIC_URL}, json=fields | {
        "is_admin": True, "roles": [{"role": "auditor"}, {"role": "finance"}, {"role": "division", "division": "Basic Sciences"}]})
    assert changed.status_code == 200, changed.text
    ada = browser.get("/api/admin/people/ada").json()
    assert ada["is_admin"] is False and _roles(ada) == {("finance", "")}  # only the role the map does not name


def test_groups_and_too_many_to_list(catalog, monkeypatch, caplog):
    from pinax.main import app

    stand_in = StandInProvider()
    _oidc_settings(monkeypatch, OIDC_ROLES_CLAIM="groups", OIDC_ROLE_MAP=json.dumps({"admin": "1c2d-group-id"}))
    monkeypatch.setattr(oidc, "HTTP_TRANSPORT", httpx2.ASGITransport(app=stand_in.app))
    monkeypatch.setattr(oidc, "_clients", {})
    with TestClient(app, base_url=PUBLIC_URL) as browser:
        assert _as(browser, stand_in, "ben", groups=["9f8e-other", "1c2d-group-id"])["is_admin"] is True
        with caplog.at_level(logging.WARNING, logger="pinax.people"):
            me = _as(browser, stand_in, "ben", _claim_names={"groups": "src1"})  # too many groups to list
        assert me["is_admin"] is False  # not guessed: taken away until Entra ID can send them
        assert _events("auth.roles_unknown")[-1]["reason"] == "group overage"
        assert any("could not be read" in r.getMessage() for r in caplog.records)


def test_the_command_line_still_gives_any_role(provider, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pinax", "user", "add", "emergency-1", "--name", "Emergency 1",
                                      "--email", "it@example.org", "--admin"])
    cli.main()
    assert "a storage administrator" in capsys.readouterr().out


def test_local_sign_in_ignores_the_map(catalog, monkeypatch):
    from pinax.db import session_scope
    from pinax.models import User

    monkeypatch.setenv("PINAX_AUTH_MODE", "local")
    monkeypatch.setenv("PINAX_OIDC_ROLE_MAP", json.dumps(ROLE_MAP))
    config.get_settings.cache_clear()
    assert config.get_settings().mapped_roles == {}
    with session_scope() as s:
        ivy, ada = (s.query(User).filter_by(username=u).one() for u in ("ivy", "ada"))
        people.save_person(s, ivy, ada, {"display_name": "Ada", "email": "ada@example.org", "title": "",
                                         "is_admin": True, "roles": [{"role": "auditor"}]})
    with session_scope() as s:
        ada = s.query(User).filter_by(username="ada").one()
        assert ada.is_admin and {r.role for r in ada.roles} == {"auditor"}


def test_a_wrong_map_stops_pinax():
    base = {"auth_mode": "oidc", "oidc_issuer": "https://login.example.test/t/v2.0", "oidc_client_id": "c",
            "oidc_client_secret": "s", "public_url": "https://pinax.test", "session_secret": "x" * 32}
    assert oidc.problems(Settings(**base, oidc_role_map=ROLE_MAP)) == []
    for wrong, said in (({"boss": ["X"]}, "'boss' is not a role"), ({"division": ["X"]}, "'division' is not a role"),
                        ({"admin:Basic Sciences": ["X"]}, "is not a role"), ({"auditor": []}, "needs the values")):
        assert any(said in p for p in oidc.problems(Settings(**base, oidc_role_map=wrong))), wrong
