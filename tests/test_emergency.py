# SPDX-License-Identifier: Apache-2.0
"""The emergency administrator sign-in (A12): with sign-in through Entra ID, a storage administrator marked from the
command line signs in with a password at /auth/emergency when the provider fails. Off unless such an account exists;
audited, logged as a warning, counted, and an hour long."""

import logging
import sys
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from test_oidc import PUBLIC_URL, _oidc_settings

from pinax import cli, metrics, people, signin


@pytest.fixture
def browser(catalog, monkeypatch):
    from pinax.main import app

    _oidc_settings(monkeypatch)
    with TestClient(app, base_url=PUBLIC_URL) as client:
        yield client


def _mark(username: str, on: bool = True) -> str | None:
    from pinax.db import session_scope
    from pinax.models import User

    with session_scope() as s:
        return signin.set_emergency(s, s.query(User).filter_by(username=username).one(), on)


def _set(username: str, **values) -> None:
    from pinax.db import session_scope
    from pinax.models import User

    with session_scope() as s:
        u = s.query(User).filter_by(username=username).one()
        for k, v in values.items():
            setattr(u, k, v)


def _events(action: str) -> list[dict]:
    from pinax.db import session_scope
    from pinax.models import AuditEvent

    with session_scope() as s:
        return [e.detail for e in s.query(AuditEvent).filter_by(action=action).order_by(AuditEvent.id)]


def _sign_in(browser, username: str, password: str, **headers):
    return browser.post("/auth/emergency", data={"username": username, "password": password}, headers=headers,
                        follow_redirects=False)


def test_off_unless_an_emergency_account_exists(browser):
    assert browser.get("/auth/emergency").status_code == 404
    assert _sign_in(browser, "ivy", "anything").status_code == 404
    _mark("ivy")
    page = browser.get("/auth/emergency")
    assert page.status_code == 200 and "Emergency sign-in" in page.text and 'name="password"' in page.text
    assert "/auth/emergency" not in browser.get("/auth/signed-out").text  # not linked from the way in
    _mark("ivy", on=False)
    assert browser.get("/auth/emergency").status_code == 404


def test_only_with_sign_in_through_an_identity_provider(client):
    _mark("ivy")  # marked, but this is the demo's sign-in
    assert client.get("/auth/emergency").status_code == 404


def test_an_emergency_sign_in_is_short_audited_logged_and_counted(browser, caplog):
    from pinax.db import session_scope
    from pinax.models import LoginSession

    password = _mark("ivy")
    assert len(password.replace("-", "")) == 32
    counted = metrics.AUDIT.labels("auth.emergency_signed_in")._value.get()
    with caplog.at_level(logging.WARNING, logger="pinax.signin"):
        done = _sign_in(browser, "ivy", password)
    assert done.status_code == 303 and done.headers["location"] == "/app/"
    cookie = next(c for c in done.headers.get_list("set-cookie") if c.startswith("pinax_session="))
    assert "max-age=3600" in cookie.lower() and "httponly" in cookie.lower()
    assert browser.get("/api/me").json()["username"] == "ivy"
    with session_scope() as s:
        live = s.query(LoginSession).one()
        assert live.expires_at - live.created_at == timedelta(hours=1)
    assert _events("auth.emergency_signed_in")[-1]["username"] == "ivy"
    assert any("Emergency sign-in by ivy" in r.getMessage() for r in caplog.records)
    assert metrics.AUDIT.labels("auth.emergency_signed_in")._value.get() == counted + 1


def test_failures_are_audited_and_lock_the_account(browser, caplog):
    password = _mark("ivy")
    with caplog.at_level(logging.WARNING, logger="pinax.signin"):
        wrong = _sign_in(browser, "ivy", "not-the-password")
    assert wrong.status_code == 401 and "Wrong username or password" in wrong.text
    assert _events("auth.emergency_sign_in_failed")[-1]["reason"] == "wrong password"
    assert any("Failed emergency sign-in as ivy" in r.getMessage() for r in caplog.records)
    for _ in range(4):
        _sign_in(browser, "ivy", "not-the-password")
    assert _events("auth.locked")
    assert _sign_in(browser, "ivy", password).status_code == 401  # locked, even with the right password
    assert browser.get("/api/me").status_code == 401


def test_only_emergency_accounts_get_in(browser):
    password = _mark("ivy")
    _set("ben", is_admin=True, password_hash=signin.hash_password("a password left from before"))
    refused = _sign_in(browser, "ben", "a password left from before")  # an administrator, but not marked
    assert refused.status_code == 401 and _events("auth.emergency_sign_in_failed")[-1]["reason"] == "not an emergency account"
    _set("ivy", is_admin=False)  # no longer an administrator, in the app
    _mark("ben")  # so the page stays open
    assert _sign_in(browser, "ivy", password).status_code == 401
    _set("ivy", is_admin=True, active=False)
    assert _sign_in(browser, "ivy", password).status_code == 401
    assert browser.get("/api/me").status_code == 401


def test_a_sign_in_from_another_site_is_refused(browser):
    password = _mark("ivy")
    refused = _sign_in(browser, "ivy", password, origin="https://elsewhere.example")
    assert refused.status_code == 403 and browser.get("/api/me").status_code == 401


def test_only_the_command_line_marks_an_account(catalog, monkeypatch, capsys):
    from pinax.db import session_scope
    from pinax.models import User

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["pinax", "user", *argv])
        try:
            cli.main()
        except SystemExit as e:
            return e.code or 0
        return 0

    assert run("emergency", "ivy") == 1 and "PINAX_AUTH_MODE=oidc" in capsys.readouterr().err  # the demo's sign-in
    _oidc_settings(monkeypatch)
    assert run("emergency", "ada") == 1 and "Only an active storage administrator" in capsys.readouterr().err
    assert run("emergency", "ivy") == 0
    out = capsys.readouterr().out
    assert "is an emergency account" in out and f"{PUBLIC_URL}/auth/emergency," in out
    assert _events("person.emergency_set")[-1]["source"] == "cli"
    assert run("list") == 0 and "administrator, emergency" in capsys.readouterr().out
    with session_scope() as s:  # the app's people page cannot mark anyone
        ben = s.query(User).filter_by(username="ben").one()
        people.save_person(s, s.query(User).filter_by(username="ivy").one(), ben,
                           {"display_name": "Ben", "email": "ben@example.org", "title": "", "is_admin": True,
                            "emergency": True})
    with session_scope() as s:
        assert not s.query(User).filter_by(username="ben").one().emergency
    assert run("emergency", "ivy", "--off") == 0 and "no longer an emergency account" in capsys.readouterr().out
    with session_scope() as s:
        ivy = s.query(User).filter_by(username="ivy").one()
        assert not ivy.emergency and ivy.password_hash is None
    assert _events("person.emergency_removed")


def test_the_access_review_lists_emergency_accounts(browser):
    password = _mark("ivy")
    _sign_in(browser, "ivy", password)
    review = browser.get("/api/admin/access-review").json()
    item = next(a for a in review["attention"] if a["kind"] == "emergency")
    assert item["text"].startswith("Ivy is an emergency account") and item["last_used_at"]
    assert browser.get("/api/admin/people/ivy").json()["emergency"] is True
