# SPDX-License-Identifier: Apache-2.0
"""System and audit logs (O5): a line per request with its ID, person, and address; slow requests flagged; audit
events logged once committed, to a file of their own too; text or JSON; never a password or a query string."""

import json
import logging

import pytest
from conftest import ROOT, login


@pytest.fixture
def records(caplog):
    caplog.set_level(logging.INFO)
    return caplog


def _lines(caplog, name: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == name]


def test_a_line_for_each_request(client, records):
    login(client, "ada")
    records.clear()
    r = client.get("/api/tree", params={"fs": "fast", "path": f"{ROOT}/alpha_lab"})
    assert r.status_code == 200
    (line,) = [x for x in _lines(records, "pinax.request") if x.http["path"] == "/api/tree"]
    assert line.levelno == logging.INFO and line.user == "ada" and line.request_id == r.headers["x-request-id"]
    assert line.http["route"] == "/api/tree" and line.http["status"] == 200 and line.http["queries"] >= 1
    assert "alpha_lab" not in line.getMessage()  # never the query string, which can name files
    assert line.getMessage().startswith("GET /api/tree 200 in ")


def test_routes_are_templates(client, records):
    login(client, "ada")
    view = client.post("/api/views", json={"name": "Mine", "address": "#/explore?fs=fast"}).json()["id"]
    client.delete(f"/api/views/{view}")
    client.get("/api/nowhere")
    routes = {(x.http["method"], x.http["route"]) for x in _lines(records, "pinax.request")}
    assert {("POST", "/api/views"), ("DELETE", "/api/views/{view_id}"), ("GET", "unmatched")} <= routes


def test_the_load_balancers_checks_and_static_files_are_quiet(client, records):
    client.get("/api/health")
    client.get("/app/")
    assert _lines(records, "pinax.request") == []


def test_a_slow_request_is_a_warning(client, records, monkeypatch):
    from pinax import config

    monkeypatch.setenv("PINAX_SLOW_REQUEST_SECONDS", "0.000001")
    config.get_settings.cache_clear()
    login(client, "ada").get("/api/me")
    (line,) = [x for x in _lines(records, "pinax.request") if x.http["path"] == "/api/me"]
    assert line.levelno == logging.WARNING and line.http["slow"] and "(slow)" in line.getMessage()


def test_a_request_id_from_a_trusted_proxy_is_kept(client, records):
    """The test client is not a trusted proxy: its ID is replaced, and a bad one never kept."""
    r = client.get("/api/me", headers={"X-Request-ID": "from-proxy-1"})
    assert r.headers["x-request-id"] != "from-proxy-1" and len(r.headers["x-request-id"]) == 16


def test_the_persons_address_behind_trusted_proxies():
    from pinax.logs import client_address

    trusted = ["127.0.0.1", "10.0.0.0/8"]

    def scope(peer, forwarded=None):
        headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
        return {"client": (peer, 5000), "headers": headers}

    assert client_address(scope("127.0.0.1", "203.0.113.9, 10.1.2.3"), trusted) == "203.0.113.9"
    assert client_address(scope("127.0.0.1", "198.51.100.1, 203.0.113.9"), trusted) == "203.0.113.9"  # nearest
    assert client_address(scope("198.51.100.7", "203.0.113.9"), trusted) == "198.51.100.7"  # not from a proxy
    assert client_address(scope("127.0.0.1"), trusted) == "127.0.0.1"


def test_audit_events_are_logged_once_committed(client, records):
    from pinax.db import session_scope
    from pinax.models import AuditEvent

    login(client, "ada")
    r = client.post("/api/views", json={"name": "Mine", "address": "#/explore?fs=fast"})
    assert r.status_code == 200
    (line,) = _lines(records, "pinax.audit")
    assert line.audit["action"] == "view.saved" and line.user == "ada" and line.request_id
    assert line.getMessage().startswith("view.saved: ")
    records.clear()
    with pytest.raises(RuntimeError), session_scope() as s:
        s.add(AuditEvent(action="test.never", summary="Rolled back", detail={}))
        s.flush()
        raise RuntimeError("rolled back")
    assert _lines(records, "pinax.audit") == []


def test_audit_events_from_the_command_line_name_the_account(env, records):
    from pinax.db import session_scope
    from pinax.models import AuditEvent

    with session_scope() as s:
        s.add(AuditEvent(action="test.cli", summary="From the command line", detail={"n": 1}))
    (line,) = _lines(records, "pinax.audit")
    assert line.source == "cli" and line.os_user and line.audit["detail"] == {"n": 1}


def test_json_lines_and_an_audit_file(env, tmp_path, monkeypatch):
    from pinax import config, logs
    from pinax.db import session_scope
    from pinax.models import AuditEvent

    monkeypatch.setenv("PINAX_LOG_FORMAT", "json")
    monkeypatch.setenv("PINAX_LOG_FILE", str(tmp_path / "pinax.log"))
    monkeypatch.setenv("PINAX_AUDIT_LOG", str(tmp_path / "audit.log"))
    config.get_settings.cache_clear()
    root = logging.getLogger()
    level = root.level
    try:
        logs.configure(config.get_settings())
        logging.getLogger("pinax.test").info("Hello %s", "there")
        with session_scope() as s:
            s.add(AuditEvent(action="test.file", summary="To the file", detail={}))
    finally:
        for h in [h for h in root.handlers + logs.audit_log.handlers if getattr(h, logs._OURS, False)]:
            (root if h in root.handlers else logs.audit_log).removeHandler(h)
            h.close()
        root.setLevel(level)
    lines = [json.loads(x) for x in (tmp_path / "pinax.log").read_text().splitlines()]
    assert {"logger": "pinax.test", "message": "Hello there", "level": "INFO"}.items() <= lines[0].items()
    assert lines[0]["ts"].endswith("Z") and any(x["logger"] == "pinax.audit" for x in lines)
    (audit,) = [json.loads(x) for x in (tmp_path / "audit.log").read_text().splitlines()]
    assert audit["audit"]["action"] == "test.file" and audit["source"] == "cli"


def test_text_lines_carry_the_requests_context():
    from pinax.logs import TextFormatter

    record = logging.LogRecord("pinax.request", logging.INFO, __file__, 1, "GET /api/me 200 in 3 ms", (), None)
    record.user, record.address, record.request_id = "ada", "203.0.113.9", "abc123"
    assert TextFormatter().format(record).endswith("pinax.request: GET /api/me 200 in 3 ms "
                                                   "[user=ada address=203.0.113.9 request=abc123]")


def test_a_password_is_never_logged(env, records, monkeypatch):
    from fastapi.testclient import TestClient

    from pinax import config, signin
    from pinax.db import session_scope
    from pinax.main import app
    from pinax.models import User

    monkeypatch.setenv("PINAX_AUTH_MODE", "local")
    config.get_settings.cache_clear()
    password = "correct-horse-battery-staple"
    with session_scope() as s:
        user = User(username="lou", display_name="Lou", email="lou@example.org")
        s.add(user)
        s.flush()
        user.password_hash = signin.hash_password(password)
    with TestClient(app) as client:
        assert client.post("/api/auth/login", json={"username": "lou", "password": "wrong-password-here"}).status_code == 401
        r = client.post("/api/auth/login", json={"username": "lou", "password": password})
        assert r.status_code == 200
        token = r.cookies.get(signin.COOKIE)
    text = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in records.records)
    assert "auth.signed_in" in text and "auth.sign_in_failed" in text
    assert password not in text and "wrong-password-here" not in text and token and token not in text
