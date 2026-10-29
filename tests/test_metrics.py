# SPDX-License-Identifier: Apache-2.0
"""Prometheus metrics (O4, O5): off until a token is set, then only with it; what the catalog says about each
filesystem's scans, size, and capacity, and what the web app did; filesystems named, never labs, people, or paths."""

import pytest
from conftest import ROOT, login
from prometheus_client.parser import text_string_to_metric_families

TOKEN = "a-long-enough-metrics-token-1234"


@pytest.fixture
def metrics_on(client, monkeypatch):
    from pinax import config, metrics

    monkeypatch.setenv("PINAX_METRICS_TOKEN", TOKEN)
    config.get_settings.cache_clear()
    monkeypatch.setattr(metrics, "CACHE_SECONDS", 0)
    return client


def _scrape(client) -> dict[str, list]:
    r = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    return {f.name: f.samples for f in text_string_to_metric_families(r.text)}, r.text


def _value(samples, **labels) -> float:
    (v,) = [s.value for s in samples if all(s.labels.get(k) == v for k, v in labels.items())]
    return v


def test_off_until_a_token_is_set_and_then_only_with_it(client, metrics_on, monkeypatch):
    from pinax import config

    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    monkeypatch.delenv("PINAX_METRICS_TOKEN")
    config.get_settings.cache_clear()
    assert client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404


def test_a_short_token_is_refused():
    from pinax.config import Settings, unsafe

    assert "PINAX_METRICS_TOKEN" in unsafe(Settings(metrics_token="short"))
    assert unsafe(Settings(metrics_token=TOKEN)) is None


def test_each_filesystems_scans_and_size(metrics_on):
    from sqlalchemy import select

    from pinax.db import session_scope
    from pinax.models import Snapshot

    families, text = _scrape(metrics_on)
    with session_scope() as s:
        snap = s.scalar(select(Snapshot))
        want = {"bytes": snap.bytes, "files": snap.files, "dirs": snap.dirs, "at": snap.started_at}
    assert _value(families["pinax_catalog_up"]) == 1
    assert _value(families["pinax_filesystem_bytes"], filesystem="fast") == want["bytes"]
    assert _value(families["pinax_filesystem_files"], filesystem="fast") == want["files"]
    assert _value(families["pinax_filesystem_directories"], filesystem="fast") == want["dirs"]
    assert _value(families["pinax_snapshots"], filesystem="fast", status="ready") == 1
    assert _value(families["pinax_scan_last_success_timestamp_seconds"], filesystem="fast") > 0
    assert _value(families["pinax_filesystem_cold_bytes"], filesystem="fast") > 0
    assert "pinax_scan_last_failure_timestamp_seconds" not in families or not families[
        "pinax_scan_last_failure_timestamp_seconds"]
    assert _value(families["pinax_users"], state="active") == 4
    (info,) = families["pinax_info"]
    assert info.labels["database"] in ("sqlite", "postgresql") and info.labels["version"]
    for private in ("alpha_lab", "Alpha Lab", "ada", ROOT):  # labs, people, and paths are never named
        assert private not in text


def test_a_failed_scan_is_newer_than_the_last_success(metrics_on, tmp_path):
    from datetime import UTC, datetime

    from conftest import FILES
    from scanfix import write_scan

    from pinax.db import session_scope
    from pinax.models import Filesystem
    from pinax.snapshots import SnapshotStatus, record_scan

    later = tmp_path / "later"
    manifest = write_scan(later, ROOT, FILES, started=datetime(2026, 10, 8, tzinfo=UTC), status="incomplete")
    with session_scope() as s:
        record_scan(s, later, manifest, s.query(Filesystem).one(), SnapshotStatus.SKIPPED, "status incomplete")
    families, _ = _scrape(metrics_on)
    failed = _value(families["pinax_scan_last_failure_timestamp_seconds"], filesystem="fast")
    assert failed > _value(families["pinax_scan_last_success_timestamp_seconds"], filesystem="fast")
    assert _value(families["pinax_snapshots"], filesystem="fast", status="skipped") == 1


def _or_zero(samples, **labels) -> float:
    found = [s.value for s in samples if all(s.labels.get(k) == v for k, v in labels.items())]
    return found[0] if found else 0.0


def test_what_the_web_app_did(metrics_on):
    """Counted since the web app started (here, since the test process did): what this test adds."""
    keys = {"tree": ("pinax_http_requests", {"method": "GET", "route": "/api/tree", "status": "200"}),
            "views": ("pinax_http_requests", {"method": "POST", "route": "/api/views", "status": "200"}),
            "timed": ("pinax_http_request_duration_seconds", {"method": "GET", "route": "/api/tree", "le": "+Inf"}),
            "queries": ("pinax_index_queries", {}),
            "saved": ("pinax_audit_events", {"action": "view.saved"})}

    def now() -> dict[str, float]:
        families, _ = _scrape(metrics_on)
        return {k: _or_zero(families.get(name, []), **labels) for k, (name, labels) in keys.items()}

    before = now()
    login(metrics_on, "ada")
    metrics_on.get("/api/tree", params={"fs": "fast", "path": f"{ROOT}/alpha_lab"})
    metrics_on.post("/api/views", json={"name": "Mine", "address": "#/explore?fs=fast"})
    after = now()
    added = {k: after[k] - before[k] for k in keys}
    assert added["tree"] == 1 and added["views"] == 1 and added["timed"] == 1 and added["saved"] == 1
    assert added["queries"] >= 1
    families, _ = _scrape(metrics_on)
    assert _value(families["pinax_wide_queries"], state="waiting") == 0


def test_capacity_and_its_fill_date(metrics_on):
    from datetime import date

    from pinax import capacity
    from pinax.db import session_scope
    from pinax.models import Filesystem

    with session_scope() as s:
        capacity.set_capacity(s, None, s.query(Filesystem).one(), date(2020, 1, 1), 1, "GB")
    families, _ = _scrape(metrics_on)
    assert _value(families["pinax_filesystem_capacity_bytes"], filesystem="fast") == 10**9
    assert _value(families["pinax_filesystem_used_bytes"], filesystem="fast") > 0
