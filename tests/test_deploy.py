# SPDX-License-Identifier: Apache-2.0
"""The deployment kit: the health check, sign-on headers accepted only from the proxy, and the systemd units."""

import shlex
import shutil
from configparser import ConfigParser
from pathlib import Path

import pytest
from conftest import ROOT

from pinax import config

DEPLOY = Path(__file__).parents[1] / "deploy"


def _settings(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(f"PINAX_{k.upper()}", v)
    config.get_settings.cache_clear()


def _client(address: str = "127.0.0.1", **headers):
    from fastapi.testclient import TestClient

    from pinax.main import app

    return TestClient(app, client=(address, 41000), headers=headers)


# ---------- Health ----------

def test_health_needs_no_sign_in_and_names_nothing(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ok" and j["checks"] == {"catalog": "ok", "snapshots_ready": 1, "indexes_missing": 0,
                                                  "automatic_ingest": "not running"}
    assert ROOT not in r.text and "alpha" not in r.text


def test_health_is_degraded_when_an_index_is_missing(client):
    from pinax.db import session_scope
    from pinax.models import Snapshot

    with session_scope() as s:
        index_dir = s.query(Snapshot).one().index_dir
    shutil.rmtree(index_dir)
    j = client.get("/api/health").json()
    assert j["status"] == "degraded" and j["checks"]["indexes_missing"] == 1


def test_health_is_degraded_when_automatic_ingest_stops(client):
    from pinax.watch import beat

    beat({"folder": "/scans", "interval": 300, "state": "idle"})
    assert client.get("/api/health").json()["checks"]["automatic_ingest"] == "idle"
    beat({"folder": "/scans", "interval": 300, "state": "stopped"})
    j = client.get("/api/health").json()
    assert j["status"] == "degraded" and j["checks"]["automatic_ingest"] == "stopped"


def test_health_is_an_error_when_the_catalog_cannot_be_read(env, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from pinax.api import health

    def broken():
        raise OperationalError("SELECT 1", {}, Exception("disk I/O error"))

    monkeypatch.setattr(health, "session_scope", broken)
    r = _client().get("/api/health")
    assert r.status_code == 503 and r.json()["status"] == "error"


# ---------- Header mode ----------

def test_header_mode_trusts_the_proxy_only(catalog, monkeypatch):
    _settings(monkeypatch, auth_mode="header")
    assert _client("127.0.0.1", **{"X-Remote-User": "ivy"}).get("/api/me").json()["username"] == "ivy"
    assert _client("::1", **{"X-Remote-User": "ivy"}).get("/api/me").status_code == 200
    # The same header from anywhere else is somebody claiming to be ivy.
    assert _client("10.9.8.7", **{"X-Remote-User": "ivy"}).get("/api/me").status_code == 401
    _settings(monkeypatch, auth_mode="header", trusted_proxies='["10.9.0.0/16"]')
    assert _client("10.9.8.7", **{"X-Remote-User": "ivy"}).get("/api/me").status_code == 200
    assert _client("127.0.0.1", **{"X-Remote-User": "ivy"}).get("/api/me").status_code == 401


def test_header_mode_ignores_demo_sign_in(catalog, monkeypatch):
    _settings(monkeypatch, auth_mode="header", auth_header="X-Forwarded-User")
    c = _client()
    assert c.get("/api/demo/users").status_code == 404
    assert c.post("/api/demo/session", json={"username": "ivy"}).status_code == 404
    c.cookies.set("pinax_user", "ivy")
    assert c.get("/api/me").status_code == 401
    assert _client(**{"X-Forwarded-User": "ada"}).get("/api/me").json()["username"] == "ada"
    assert _client(**{"X-Forwarded-User": "nosuchperson"}).get("/api/me").status_code == 401


# ---------- systemd units ----------

UNITS = sorted(DEPLOY.glob("systemd/*.service"))


def _unit(path: Path) -> ConfigParser:
    p = ConfigParser(strict=False, interpolation=None)
    p.optionxform = str
    p.read(path)
    return p


@pytest.mark.parametrize("path", UNITS, ids=lambda p: p.name)
def test_units_run_commands_pinax_accepts(path, monkeypatch):
    """Each unit's ExecStart is a `pinax` command line that the CLI parses (templates with %i filled in)."""
    import argparse

    from pinax import cli

    exec_start = _unit(path)["Service"]["ExecStart"].replace("%i", "fast")
    argv = shlex.split(exec_start)
    assert argv[0].endswith("/bin/pinax"), argv
    parsed = {}
    original = argparse.ArgumentParser.parse_args

    def capture(self, args=None, namespace=None):
        parsed["args"] = original(self, args, namespace)
        raise SystemExit(0)  # parsed, not run

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", capture)
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit):
        cli.main()
    assert parsed["args"].command in {"serve", "ingest", "scan"}


def test_units_and_environment_name_real_settings():
    names = {f"PINAX_{k.upper()}" for k in config.Settings.model_fields}
    env = (DEPLOY / "pinax.env").read_text()
    used = {line.split("=", 1)[0].lstrip("# ").strip() for line in env.splitlines()
            if "=" in line and line.lstrip("# ").startswith("PINAX_")}
    assert used and used <= names, used - names
    for path in UNITS + sorted(DEPLOY.glob("systemd/*.timer")):
        unit = _unit(path)
        assert unit.has_section("Unit"), path.name
        # Templates for timers (pinax-scan@.service) are started by their timer, not at boot.
        assert unit.has_section("Install") or (path.suffix == ".service" and "@" in path.name), path.name
        if path.suffix == ".service":
            svc = unit["Service"]
            assert svc["User"] == "pinax" and svc["EnvironmentFile"] == "/etc/pinax/pinax.env", path.name
            assert svc.get("NoNewPrivileges") == "true" and svc.get("ProtectSystem") == "strict", path.name


# ---------- Rebuilding indexes (upgrades) ----------

def _snap():
    from pinax.db import session_scope
    from pinax.models import Snapshot

    with session_scope() as s:
        snap = s.query(Snapshot).one()
        s.expunge(snap)
        return snap


def test_rebuild_restores_a_lost_index(client):
    from pinax.db import session_scope
    from pinax.models import Snapshot
    from pinax.snapshots import rebuild_snapshot

    snap = _snap()
    files_before = snap.files
    shutil.rmtree(snap.index_dir)
    assert client.get("/api/health").json()["status"] == "degraded"
    with session_scope() as s:
        rebuild_snapshot(s, s.get(Snapshot, snap.id), progress=lambda m: None)
    assert client.get("/api/health").json()["status"] == "ok"
    assert _snap().files == files_before


def test_the_web_app_reopens_a_rebuilt_index(catalog):
    from pinax.db import session_scope
    from pinax.models import Snapshot
    from pinax.snapshots import indexes, rebuild_snapshot

    snap = _snap()
    first = indexes.get(snap)
    with session_scope() as s:
        rebuild_snapshot(s, s.get(Snapshot, snap.id), progress=lambda m: None)
    second = indexes.get(_snap())
    assert second is not first and second.node(second.root)["files"] == snap.files


def test_rebuild_outdated_after_an_upgrade(catalog, monkeypatch, capsys):
    import duckdb

    from pinax import cli
    from pinax.ingest.build import INDEX_VERSION
    from pinax.snapshots import index_version, indexes

    snap = _snap()
    indexes.clear()
    with duckdb.connect(str(Path(snap.index_dir) / "index.duckdb")) as con:  # as if built by an older Pinax
        con.execute("UPDATE meta SET value = '1' WHERE key = 'index_version'")
    assert index_version(snap) == 1
    for argv, code, says in ((["pinax", "rebuild", "--outdated"], 0, f"Snapshot {snap.id}: rebuilt"),
                             (["pinax", "rebuild", "--outdated"], 0, f"is current (version {INDEX_VERSION})"),
                             (["pinax", "rebuild", "999"], 2, "no ready snapshot 999")):
        monkeypatch.setattr("sys.argv", argv)
        with pytest.raises(SystemExit) as done:
            cli.main()
        out = capsys.readouterr()
        assert done.value.code == code and says in out.out + out.err
    assert index_version(_snap()) == INDEX_VERSION


def test_rebuild_keeps_the_old_index_if_the_scan_is_gone(catalog):
    from pinax.db import session_scope
    from pinax.models import Snapshot
    from pinax.snapshots import IngestError, rebuild_snapshot

    snap = _snap()
    shutil.rmtree(snap.scan_dir)
    with session_scope() as s, pytest.raises(IngestError, match="scan is gone"):
        rebuild_snapshot(s, s.get(Snapshot, snap.id), progress=lambda m: None)
    assert (Path(snap.index_dir) / "index.duckdb").exists() and _snap().status == "ready"
