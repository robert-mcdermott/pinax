# SPDX-License-Identifier: Apache-2.0
"""API tokens (A11): a person's own, reading as them or with their access; service tokens, reading as an auditor or
as finance; stored hashed and shown once, expiring, revocable, and audited; and, behind a sign-on proxy, let through
on /script/api/ and answered only with a token."""

import hashlib
import logging
import time
from datetime import timedelta

import pytest
from conftest import ROOT, login

from pinax import config

ALPHA = ROOT + "/alpha_lab"
BETA = ROOT + "/beta_lab"


def _bare():
    """A client with no cookie, as a script is."""
    from fastapi.testclient import TestClient

    from pinax.main import app

    return TestClient(app)


def _make(client, user, **body):
    login(client, user)
    return client.post("/api/tokens", json={"name": "Nightly report", "days": 30, **body})


def _wait(script, job_id, headers):
    end = time.monotonic() + 20
    while time.monotonic() < end:
        if script.get(f"/api/files/jobs/{job_id}", headers=headers).json()["status"] not in ("queued", "running"):
            return
        time.sleep(0.05)
    raise AssertionError("the search did not finish")


def _bearer(secret):
    return {"Authorization": f"Bearer {secret}"}


def _token(token_id):
    from pinax.db import session_scope
    from pinax.models import ApiToken

    with session_scope() as s:
        t = s.get(ApiToken, token_id)
        s.expunge(t)
        return t


def _events(action=None):
    from pinax.db import session_scope
    from pinax.models import AuditEvent

    with session_scope() as s:
        q = s.query(AuditEvent).order_by(AuditEvent.id)
        rows = q.filter(AuditEvent.action == action).all() if action else q.all()
        return [(e.action, e.summary, dict(e.detail or {})) for e in rows]


def test_a_personal_token_reads_as_its_person(client, caplog):
    made = _make(client, "ada")
    assert made.status_code == 201
    secret, token = made.json()["secret"], made.json()["token"]
    assert secret.startswith("pinax_") and len(secret) > 40 and token["prefix"] == secret[:10]
    assert token["read_only"] and token["state"] == "active" and token["kind"] == "personal"
    # Shown once: listed without it, and only its hash is kept.
    listed = client.get("/api/tokens").json()["tokens"]
    assert [t["name"] for t in listed] == ["Nightly report"] and secret not in str(listed)
    assert _token(token["id"]).token_hash == hashlib.sha256(secret.encode()).hexdigest()
    script = _bare()
    with caplog.at_level(logging.DEBUG):
        me = script.get("/api/me", headers=_bearer(secret))
    assert me.status_code == 200 and me.json()["username"] == "ada"
    assert secret not in caplog.text
    assert [t["path"] for t in script.get("/api/tree", headers=_bearer(secret)).json()["tiles"]] == [ALPHA]
    assert script.get("/api/tree", params={"fs": "fast", "path": BETA}, headers=_bearer(secret)).status_code == 404
    # Reading only: no changes, but a search in the background changes nothing.
    r = script.post("/api/plans", json={"fs": "fast", "path": ALPHA + "/projects", "action": "delete"}, headers=_bearer(secret))
    assert r.status_code == 403 and "only reads" in r.json()["detail"]
    job = script.post("/api/files/jobs", params={"fs": "fast", "path": ALPHA}, headers=_bearer(secret))
    assert job.status_code == 200
    _wait(script, job.json()["id"], _bearer(secret))
    used = _token(token["id"])
    assert used.last_used_at is not None and used.last_used_address
    assert _events("token.made")[-1][1] == "Ada made the API token 'Nightly report', reading only, until " \
        + f"{used.expires_at:%Y-%m-%d}"


def test_a_token_with_its_persons_access_changes_and_is_audited_with_its_name(client):
    secret = _make(client, "ada", name="Lab pipeline", read_only=False).json()["secret"]
    script = _bare()
    r = script.post("/api/plans", json={"fs": "fast", "path": ALPHA + "/projects", "action": "delete"}, headers=_bearer(secret))
    assert r.status_code == 200, r.text
    action, _, detail = _events()[-1]
    assert action.startswith("plan.") and detail["token"] == "Lab pipeline"
    # Its person's access as it is now: the plan of a lab they no longer act for is not theirs to change.
    login(client, "ivy")
    ada = client.get("/api/admin/people/ada").json()
    client.put("/api/admin/people/ada", json={k: ada[k] for k in ("display_name", "email", "title", "is_admin")}
               | {"memberships": []})
    assert script.get("/api/tree", headers=_bearer(secret)).json()["tiles"] == []
    # Changes made in the pages are not.
    login(client, "ivy")
    client.post("/api/admin/labs", json={"slug": "gamma", "name": "Gamma"})
    assert "token" not in _events()[-1][2]


def test_tokens_end(client):
    from pinax.db import session_scope, utcnow
    from pinax.models import ApiToken

    script = _bare()
    one = _make(client, "ada", name="One").json()
    two = _make(client, "ada", name="Two").json()
    three = _make(client, "ada", name="Three").json()
    for made in (one, two, three):
        assert script.get("/api/me", headers=_bearer(made["secret"])).status_code == 200
    # Expired.
    with session_scope() as s:
        s.get(ApiToken, one["token"]["id"]).expires_at = utcnow() - timedelta(seconds=1)
    r = script.get("/api/me", headers=_bearer(one["secret"]))
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    # Revoked by its person; someone else's is not theirs to revoke; an administrator revokes anyone's.
    login(client, "ben")
    assert client.delete(f"/api/tokens/{two['token']['id']}").status_code == 404
    login(client, "ada")
    revoked = client.delete(f"/api/tokens/{two['token']['id']}")
    assert revoked.status_code == 200 and revoked.json()["state"] == "revoked" and revoked.json()["revoked_by"] == "Ada"
    assert client.delete(f"/api/tokens/{two['token']['id']}").status_code == 409
    assert script.get("/api/me", headers=_bearer(two["secret"])).status_code == 401
    assert _events("token.revoked")[-1][1] == "Ada revoked their token 'Two'"
    # Deactivating the person revokes the rest.
    login(client, "ivy")
    gone = client.post("/api/admin/people/ada/deactivate").json()
    assert "1 API token revoked" in gone["history"][0]["summary"]
    assert [t["state"] for t in gone["tokens"]] == ["revoked", "revoked", "expired"]
    assert script.get("/api/me", headers=_bearer(three["secret"])).status_code == 401
    client.post("/api/admin/people/ada/reactivate")
    assert script.get("/api/me", headers=_bearer(three["secret"])).status_code == 401  # still revoked
    assert script.get("/api/me", headers=_bearer("pinax_not-a-token")).status_code == 401
    assert script.get("/api/me", headers=_bearer("")).status_code == 401
    assert script.get("/api/me").status_code == 401  # nor anyone, in the demo, without a persona


def test_a_token_makes_no_tokens_and_changes_no_password(client, monkeypatch):
    secret = _make(client, "ivy", name="Everything", read_only=False).json()["secret"]
    script = _bare()
    for method, url in (("GET", "/api/tokens"), ("POST", "/api/tokens"), ("DELETE", "/api/tokens/1"),
                        ("GET", "/api/admin/tokens"), ("POST", "/api/admin/tokens")):
        r = script.request(method, url, json={"name": "x", "days": 1}, headers=_bearer(secret))
        assert r.status_code == 403 and "Pinax's pages" in r.json()["detail"], url
    assert script.get("/api/admin/people", headers=_bearer(secret)).status_code == 200  # an administrator's, otherwise
    monkeypatch.setenv("PINAX_AUTH_MODE", "local")
    config.get_settings.cache_clear()
    r = script.post("/api/auth/password", json={"current_password": "x", "new_password": "y" * 20}, headers=_bearer(secret))
    assert r.status_code == 403 and "Pinax's pages" in r.json()["detail"]
    assert script.get("/api/me", headers=_bearer(secret)).json()["username"] == "ivy"  # with local sign-in too


@pytest.mark.parametrize("body, status", [({"name": "  "}, 422), ({"days": 0}, 422), ({"days": 366}, 422),
                                          ({"name": "x" * 101}, 422)])
def test_a_token_needs_a_name_and_a_lifetime(client, body, status):
    assert _make(client, "ada", **body).status_code == status


def test_names_are_unique_among_live_tokens(client, monkeypatch):
    assert _make(client, "ada").status_code == 201
    assert _make(client, "ada").status_code == 409
    assert _make(client, "ben").status_code == 201  # someone else's may share it
    monkeypatch.setenv("PINAX_TOKEN_MAX_DAYS", "7")
    config.get_settings.cache_clear()
    assert _make(client, "ben", name="Weekly", days=8).status_code == 422
    assert client.get("/api/tokens").json()["max_days"] == 7


def test_service_tokens_belong_to_no_one_and_only_read(client):
    login(client, "ivy")
    client.post("/api/admin/people", json={"username": "max", "display_name": "Max", "email": "max@example.org",
                                           "is_admin": True})
    login(client, "max")
    finance = client.post("/api/admin/tokens", json={"name": "Ledger", "days": 365, "role": "finance"})
    audit = client.post("/api/admin/tokens", json={"name": "Dashboards", "days": 90, "role": "auditor"})
    assert finance.status_code == audit.status_code == 201
    assert finance.json()["token"]["kind"] == "service" and finance.json()["token"]["person"] is None
    for bad in ({"name": "X", "role": "admin"}, {"name": "X"}):
        assert client.post("/api/admin/tokens", json=bad).status_code == 422, bad
    login(client, "ada")
    assert client.post("/api/admin/tokens", json={"name": "Mine", "role": "auditor"}).status_code == 403
    assert client.get("/api/admin/tokens").status_code == 403
    # It outlives whoever made it.
    login(client, "ivy")
    client.post("/api/admin/people/max/deactivate")
    script, ledger, dashboards = _bare(), _bearer(finance.json()["secret"]), _bearer(audit.json()["secret"])
    me = script.get("/api/me", headers=ledger).json()
    assert me["display_name"] == "Ledger" and me["sees_charges"] and not me["sees_all"] and not me["is_admin"]
    csv = script.get("/api/admin/costs/charges", params={"fmt": "csv"}, headers=ledger)
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert script.get("/api/admin/people", headers=ledger).status_code == 403
    assert script.get("/api/tree", headers=ledger).json()["tiles"] == []
    for url in ("/api/admin/overview", "/api/admin/people", "/api/admin/labs", "/api/plans/all"):
        assert script.get(url, headers=dashboards).status_code == 200, url
    assert script.post("/api/admin/labs", json={"slug": "gamma", "name": "Gamma"}, headers=dashboards).status_code == 403
    assert script.post("/api/files/jobs", params={"fs": "fast", "path": ALPHA}, headers=dashboards).status_code == 403
    # Every token, for administrators and auditors; a person's, on their page.
    login(client, "ivy")
    _make(client, "ada")
    login(client, "ivy")
    every = client.get("/api/admin/tokens").json()["tokens"]
    assert {(t["name"], t["kind"]) for t in every} == {("Ledger", "service"), ("Dashboards", "service"),
                                                       ("Nightly report", "personal")}
    assert [t["name"] for t in client.get("/api/admin/people/ada").json()["tokens"]] == ["Nightly report"]
    revoked = client.delete(f"/api/tokens/{finance.json()['token']['id']}")
    assert revoked.status_code == 200 and _events("token.revoked")[-1][1] == "Ivy revoked the service token 'Ledger'"
    assert script.get("/api/me", headers=ledger).status_code == 401


def test_scripts_through_the_sign_on_proxy(catalog, monkeypatch):
    """The proxy lets /script/api/ through without signing in; there, Pinax answers only with a token."""
    from fastapi.testclient import TestClient

    from pinax.main import app

    with TestClient(app) as demo:
        secret = _make(demo, "ada").json()["secret"]
    monkeypatch.setenv("PINAX_AUTH_MODE", "header")
    monkeypatch.setenv("PINAX_AUTH_HEADER", "X-Forwarded-User")
    config.get_settings.cache_clear()
    proxy = TestClient(app, client=("127.0.0.1", 41000))
    assert proxy.get("/api/me", headers={"X-Forwarded-User": "ivy"}).json()["username"] == "ivy"
    r = proxy.get("/script/api/me", headers=_bearer(secret))
    assert r.status_code == 200 and r.json()["username"] == "ada"
    assert proxy.get("/script/api/tree", headers=_bearer(secret)).json()["tiles"][0]["path"] == ALPHA
    # A sign-on header does not count there: the proxy did not sign anyone in.
    assert proxy.get("/script/api/me", headers={"X-Forwarded-User": "ivy"}).status_code == 401
    # Nor anywhere else from a script that is not the proxy, but a token does.
    elsewhere = TestClient(app, client=("10.1.2.3", 41000))
    assert elsewhere.get("/api/me", headers={"X-Forwarded-User": "ivy"}).status_code == 401
    assert elsewhere.get("/api/me", headers=_bearer(secret)).json()["username"] == "ada"
    assert proxy.get("/script/app/").status_code == 404  # only the API


def test_the_proxy_configuration_lets_scripts_through():
    from pathlib import Path

    cfg = (Path(__file__).parents[1] / "deploy" / "oauth2-proxy" / "pinax.cfg").read_text()
    routes = next(line for line in cfg.splitlines() if line.startswith("skip_auth_routes"))
    assert '"^/script/api/"' in routes and "skip_auth_strip_headers = true" in cfg
