# SPDX-License-Identifier: Apache-2.0
"""Local sign-in (PINAX_AUTH_MODE=local) for sites without single sign-on, and the demo mode it leaves as it was:
passwords, temporary passwords that must be changed, lockout, sessions, the first administrator from the command
line, and refusing the demo's sign-in-as-anyone in production."""

import json
import sys
from datetime import timedelta

import pytest
from sqlalchemy import select

ADA_PASSWORD = "correct horse battery staple"
IVY_PASSWORD = "ivy's long admin passphrase"


@pytest.fixture
def local(catalog, monkeypatch):
    """The usual catalog (Ada in the Alpha Lab, Ivy an administrator), signing in with passwords."""
    from fastapi.testclient import TestClient

    from pinax import config
    from pinax.db import session_scope
    from pinax.main import app
    from pinax.models import User
    from pinax.signin import hash_password

    monkeypatch.setenv("PINAX_AUTH_MODE", "local")
    config.get_settings.cache_clear()
    with session_scope() as s:
        for username, password in (("ada", ADA_PASSWORD), ("ivy", IVY_PASSWORD)):
            s.scalar(select(User).where(User.username == username)).password_hash = hash_password(password)
    with TestClient(app) as c:
        yield c


def _login(client, username: str, password: str):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _events(action: str | None = None) -> list:
    from pinax.db import session_scope
    from pinax.models import AuditEvent

    with session_scope() as s:
        q = select(AuditEvent).order_by(AuditEvent.id)
        if action:
            q = q.where(AuditEvent.action == action)
        return [(e.action, e.summary, e.detail) for e in s.scalars(q)]


def _user(username: str):
    from pinax.db import session_scope
    from pinax.models import User

    with session_scope() as s:
        u = s.scalar(select(User).where(User.username == username))
        s.expunge(u)
        return u


# ---------- The demo, as it was ----------

def test_the_demo_is_unchanged(client):
    """The default: pick anyone from the seeded people, no passwords."""
    people = client.get("/api/demo/users").json()
    assert {"ada", "ivy"} <= {p["username"] for p in people}
    assert client.post("/api/demo/session", json={"username": "ada"}).status_code == 200
    assert client.get("/api/me").json()["username"] == "ada"
    assert client.post("/api/auth/login", json={"username": "ada", "password": "x"}).status_code == 404
    assert client.get("/api/config").json()["auth_mode"] == "demo"


def test_local_sign_in_is_the_default_outside_the_demo(env, monkeypatch):
    """Out of the box, a real install signs people in with passwords Pinax keeps; the demo keeps its persona
    switcher, and a site chooses single sign-on."""
    from fastapi.testclient import TestClient

    from pinax import config
    from pinax.main import app

    monkeypatch.delenv("PINAX_AUTH_MODE", raising=False)
    for environment, mode in (("demo", "demo"), ("dev", "local"), ("prod", "local")):
        monkeypatch.setenv("PINAX_ENVIRONMENT", environment)
        config.get_settings.cache_clear()
        assert config.get_settings().auth_mode == mode, environment
        assert config.unsafe(config.get_settings()) is None, environment
    with TestClient(app) as c:  # prod, unset
        assert c.get("/api/config").json()["auth_mode"] == "local"
        assert c.get("/api/me").status_code == 401 and c.get("/api/demo/users").status_code == 404
    monkeypatch.setenv("PINAX_AUTH_MODE", "header")
    config.get_settings.cache_clear()
    assert config.get_settings().auth_mode == "header"


def test_the_demo_is_refused_in_production(env, monkeypatch, capsys):
    from fastapi.testclient import TestClient

    from pinax import cli, config
    from pinax.main import app

    monkeypatch.setenv("PINAX_ENVIRONMENT", "prod")
    monkeypatch.setenv("PINAX_AUTH_MODE", "demo")
    config.get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="anyone sign in as anyone"), TestClient(app):
        pass
    monkeypatch.setattr(sys, "argv", ["pinax", "serve"])
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == 2 and "PINAX_AUTH_MODE=demo" in capsys.readouterr().err
    for mode in ("local", "header"):
        monkeypatch.setenv("PINAX_AUTH_MODE", mode)
        config.get_settings.cache_clear()
        assert config.unsafe(config.get_settings()) is None
        with TestClient(app) as c:
            assert c.get("/api/config").json()["auth_mode"] == mode
    assert config.get_settings().secure_cookies  # sent only over HTTPS in production


# ---------- Signing in ----------

def test_signing_in_and_out(local):
    assert local.get("/api/me").status_code == 401
    r = _login(local, "ada", ADA_PASSWORD)
    assert r.status_code == 200 and r.json() == {"ok": True, "must_change_password": False}
    cookie = r.cookies.get("pinax_session")
    assert cookie and "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
    assert local.get("/api/me").json()["username"] == "ada"
    assert [f["folder"] for f in local.get("/api/home").json()["folders"]] == ["alpha_lab"]
    assert local.get("/api/demo/users").status_code == 404 and local.get("/api/admin/people").status_code == 403
    assert local.post("/api/auth/logout").status_code == 200
    local.cookies.set("pinax_session", cookie)  # the old cookie, replayed
    assert local.get("/api/me").status_code == 401
    assert [a for a, _, _ in _events() if a.startswith("auth.")] == ["auth.signed_in", "auth.signed_out"]


def test_a_failure_says_nothing_about_why(local):
    wrong = _login(local, "ada", "not the password")
    unknown = _login(local, "nobody-at-all", ADA_PASSWORD)
    no_password = _login(local, "ben", ADA_PASSWORD)  # in the catalog, but no password set
    assert wrong.status_code == unknown.status_code == no_password.status_code == 401
    assert wrong.json() == unknown.json() == no_password.json()
    assert len(_events("auth.sign_in_failed")) == 3


def test_repeated_failures_lock_the_account(local):
    from pinax.db import session_scope
    from pinax.models import User

    for _ in range(4):
        assert _login(local, "ada", "guess").status_code == 401
    assert _user("ada").failed_logins == 4
    assert _login(local, "ada", "guess").status_code == 401  # the fifth: locked
    assert _events("auth.locked") and _user("ada").locked_until is not None
    assert _login(local, "ada", ADA_PASSWORD).status_code == 401  # even the right password, while locked
    with session_scope() as s:
        u = s.scalar(select(User).where(User.username == "ada"))
        u.locked_until -= timedelta(minutes=16)
    assert _login(local, "ada", ADA_PASSWORD).status_code == 200  # the lock has run out
    assert _user("ada").failed_logins == 0


def test_sessions_expire(local):
    from pinax.db import session_scope
    from pinax.models import LoginSession

    _login(local, "ada", ADA_PASSWORD)
    with session_scope() as s:
        for row in s.scalars(select(LoginSession)):
            row.expires_at -= timedelta(hours=13)
    assert local.get("/api/me").status_code == 401


# ---------- Temporary passwords ----------

def test_a_temporary_password_must_be_changed_first(local):
    _login(local, "ivy", IVY_PASSWORD)
    r = local.post("/api/admin/people/ada/password")
    assert r.status_code == 200
    temporary = r.json()["temporary_password"]
    assert len(temporary) == 19 and temporary.count("-") == 3 and r.json()["person"]["must_change_password"]
    local.post("/api/auth/logout")
    assert _login(local, "ada", ADA_PASSWORD).status_code == 401  # the old password no longer works
    r = _login(local, "ada", temporary)
    assert r.json()["must_change_password"]
    assert local.get("/api/me").json()["must_change_password"]
    assert local.get("/api/home").status_code == 403  # nothing else until it is changed
    change = lambda current, new: local.post("/api/auth/password", json={"current_password": current, "new_password": new})
    assert change("wrong", "a much better password").status_code == 400
    assert change(temporary, "short").status_code == 422
    assert change(temporary, "ada-and-her-password").status_code == 422  # contains the username
    assert change(temporary, "aaaaaaaaaaaaaaaa").status_code == 422  # too few different characters
    assert change(temporary, temporary).status_code == 422
    r = change(temporary, "a much better password")
    assert r.status_code == 200 and not r.json()["must_change_password"]
    assert local.get("/api/home").status_code == 200
    local.post("/api/auth/logout")
    assert _login(local, "ada", "a much better password").status_code == 200


def test_setting_a_password_unlocks_and_signs_out(local, monkeypatch):
    from fastapi.testclient import TestClient

    from pinax.main import app

    with TestClient(app) as ada:
        _login(ada, "ada", ADA_PASSWORD)
        for _ in range(5):
            _login(local, "ada", "guess")  # someone else locks her account
        assert _user("ada").locked_until is not None
        assert ada.get("/api/me").status_code == 200  # a lock stops new sign-ins, not sessions
        _login(local, "ivy", IVY_PASSWORD)
        temporary = local.post("/api/admin/people/ada/password").json()["temporary_password"]
        assert _user("ada").locked_until is None
        assert ada.get("/api/me").status_code == 401  # her sessions ended
        assert _login(ada, "ada", temporary).status_code == 200


def test_changing_your_password_ends_your_other_sessions(local):
    from fastapi.testclient import TestClient

    from pinax.main import app

    with TestClient(app) as laptop, TestClient(app) as phone:
        _login(laptop, "ada", ADA_PASSWORD)
        _login(phone, "ada", ADA_PASSWORD)
        r = laptop.post("/api/auth/password", json={"current_password": ADA_PASSWORD, "new_password": "a new long passphrase"})
        assert r.status_code == 200
        assert laptop.get("/api/me").status_code == 200 and phone.get("/api/me").status_code == 401
    assert _events("auth.password_changed")[-1][2]["ended_sessions"] == 1


def test_deactivating_someone_signs_them_out(local):
    from fastapi.testclient import TestClient

    from pinax.main import app

    with TestClient(app) as ada:
        _login(ada, "ada", ADA_PASSWORD)
        _login(local, "ivy", IVY_PASSWORD)
        assert local.post("/api/admin/people/ada/deactivate").status_code == 200
        assert ada.get("/api/me").status_code == 401
        assert local.post("/api/admin/people/ada/password").status_code == 409  # reactivate first


def test_only_administrators_set_passwords(local):
    _login(local, "ada", ADA_PASSWORD)
    assert local.post("/api/admin/people/ivy/password").status_code == 403


def test_passwords_exist_only_with_local_sign_in(client):
    client.cookies.set("pinax_user", "ivy")
    r = client.post("/api/admin/people/ada/password")
    assert r.status_code == 409 and "PINAX_AUTH_MODE" in r.json()["detail"]


def test_passwords_are_never_in_the_audit_log(local):
    _login(local, "ivy", IVY_PASSWORD)
    temporary = local.post("/api/admin/people/ada/password").json()["temporary_password"]
    _login(local, "ada", "a wrong guess at it")
    _login(local, "ada", temporary)
    local.post("/api/auth/password", json={"current_password": temporary, "new_password": "brand new passphrase"})
    text = json.dumps(_events())
    for secret in (IVY_PASSWORD, temporary, "a wrong guess at it", "brand new passphrase"):
        assert secret not in text
    assert [a for a, _, _ in _events()][-5:] == [
        "auth.signed_in", "person.password_set", "auth.sign_in_failed", "auth.signed_in", "auth.password_changed"]


def test_changes_must_come_from_a_page_of_pinax(local):
    _login(local, "ivy", IVY_PASSWORD)
    r = local.post("/api/admin/people/ada/deactivate", headers={"Origin": "https://elsewhere.example"})
    assert r.status_code == 403 and _user("ada").active
    r = local.post("/api/admin/people/ada/deactivate", headers={"Origin": "http://testserver"})
    assert r.status_code == 200 and not _user("ada").active


# ---------- The first administrator, from the command line ----------

def test_the_first_administrator_from_the_command_line(local, monkeypatch, capsys):
    from pinax import cli

    monkeypatch.setattr(sys, "argv", ["pinax", "user", "add", "root@example.org", "--name", "Root Admin",
                                      "--email", "root@example.org", "--admin"])
    cli.main()
    out = capsys.readouterr().out
    assert "a storage administrator" in out
    temporary = out.split("Temporary password for root@example.org: ")[1].split()[0]
    r = _login(local, "root@example.org", temporary)
    assert r.json()["must_change_password"]
    local.post("/api/auth/password", json={"current_password": temporary, "new_password": "the real admin passphrase"})
    assert local.get("/api/admin/people").status_code == 200
    created, password_set = _events("person.created")[-1][2], _events("person.password_set")[-1][2]
    assert created["source"] == password_set["source"] == "cli" and created["by"] and password_set["by"]
    # A new temporary password from the command line: for a forgotten one, or a locked account.
    monkeypatch.setattr(sys, "argv", ["pinax", "user", "password", "ada"])
    cli.main()
    assert "Temporary password for ada:" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["pinax", "user", "list"])
    cli.main()
    listing = capsys.readouterr().out
    assert "root@example.org" in listing and "must change password" in listing and "no password" in listing


def test_the_command_line_follows_the_same_rules(local, monkeypatch, capsys):
    from pinax import cli

    monkeypatch.setattr(sys, "argv", ["pinax", "user", "add", "Ada", "--name", "A", "--email", "a@example.org"])
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == 1 and "differs only in case" in capsys.readouterr().err


# ---------- Passwords ----------

def test_password_hashes():
    from pinax.signin import hash_password, temporary_password, verify_password

    h = hash_password("a passphrase")
    assert h.startswith("scrypt$32768$8$3$") and h != hash_password("a passphrase")  # salted
    assert verify_password("a passphrase", h) and not verify_password("a passphrase!", h)
    assert not verify_password("anything", None) and not verify_password("anything", "md5$abc")
    assert len({temporary_password() for _ in range(50)}) == 50


@pytest.mark.parametrize("environment", ["dev", "prod"])
def test_seeding_runs_only_in_the_demo(env, monkeypatch, capsys, environment):
    """Seeding deletes the catalog and indexes, so a real-data (dev) or production setting refuses it."""
    from pinax import cli, config, seed

    ran = []
    monkeypatch.setattr(seed, "run_seed", lambda scale: ran.append(scale))
    monkeypatch.setattr(sys, "argv", ["pinax", "seed"])
    monkeypatch.setenv("PINAX_ENVIRONMENT", environment)
    config.get_settings.cache_clear()
    with pytest.raises(SystemExit) as done:
        cli.main()
    assert done.value.code == 2 and f"PINAX_ENVIRONMENT=demo, not {environment}" in capsys.readouterr().err
    assert not ran
    monkeypatch.setenv("PINAX_ENVIRONMENT", "demo")
    config.get_settings.cache_clear()
    cli.main()
    assert ran == [1.0]
