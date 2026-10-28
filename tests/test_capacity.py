# SPDX-License-Identifier: Apache-2.0
"""Capacity (R11): set from a day on or reported by the storage with each scan, what fills it, and when it fills at
its trend, checked against the trend worked out by hand."""

from datetime import date, datetime, timedelta

import pytest
from conftest import FILES, ROOT
from scanfix import write_scan

TB = 10**12
START = datetime(2026, 1, 5, 2)
WEEKS = 26


@pytest.fixture
def fast(env):
    """Fast, scanned weekly for half a year, growing by a terabyte a week from 100 TB (space on disk the same)."""
    from pinax.db import session_scope
    from pinax.models import Filesystem, Snapshot, User

    with session_scope() as s:
        fs = Filesystem(slug="fast", name="Fast", root="/research/fast")
        s.add_all([fs, User(username="ivy", display_name="Ivy", email="ivy@example.org", is_admin=True),
                   User(username="ada", display_name="Ada", email="ada@example.org")])
        s.flush()
        for w in range(WEEKS):
            size = 100 * TB + w * TB
            s.add(Snapshot(filesystem_id=fs.id, scan_id=f"fast-{w}", scan_dir="/x", started_at=START + timedelta(weeks=w),
                           status="pruned", bytes=size, alloc_bytes=size))
    return env


def _summary(**kw):
    from sqlalchemy import select

    from pinax import capacity
    from pinax.db import session_scope
    from pinax.models import Filesystem

    with session_scope() as s:
        return capacity.summary(s, s.scalar(select(Filesystem)), **kw)


def _set(starts: str, amount: str, unit: str = "TB"):
    from sqlalchemy import select

    from pinax import capacity
    from pinax.db import session_scope
    from pinax.models import Filesystem

    with session_scope() as s:
        return capacity.set_capacity(s, None, s.scalar(select(Filesystem)), starts, amount, unit).id


LAST = START + timedelta(weeks=WEEKS - 1)
TODAY = LAST.date() + timedelta(days=1)


def test_no_capacity_until_one_is_known(fast):
    out = _summary(today=TODAY)
    assert out["capacity"] == {"bytes": None, "source": None, "since": None}
    assert out["measure"] == "alloc" and out["used"] == 125 * TB and out["forecast"]["ok"]
    assert out["share"] is None and out["fills"] is None and not out["soon"]


def test_when_a_filesystem_fills(fast):
    _set("2026-01-01", "200")
    out = _summary(today=TODAY)
    assert out["capacity"]["bytes"] == 200 * TB and out["capacity"]["source"] == "set"
    assert out["share"] == pytest.approx(125 / 200)
    # 75 TB to go at a terabyte a week: 75 weeks after the last scan.
    at = datetime.fromisoformat(out["fills"]["at"].removesuffix("Z"))
    assert at.date() == (LAST + timedelta(weeks=75)).date()
    assert out["fills"]["earliest"] <= out["fills"]["at"] <= out["fills"]["latest"]
    assert not out["soon"]  # more than twelve months away


def test_an_expansion_scheduled_moves_the_day(fast):
    _set("2026-01-01", "200")
    _set("2027-01-01", "250")  # before it would fill
    at = datetime.fromisoformat(_summary(today=TODAY)["fills"]["at"].removesuffix("Z"))
    assert at.date() == (LAST + timedelta(weeks=125)).date()
    out = _summary(today=TODAY)
    assert [r["future"] for r in out["schedule"]] == [False, True] and out["schedule"][0]["current"]


def test_soon_and_full(fast, monkeypatch):
    from pinax import config

    _set("2026-01-01", "130")
    out = _summary(today=TODAY)
    assert out["soon"] and out["fills"]["at"].startswith((LAST + timedelta(weeks=5)).date().isoformat())
    monkeypatch.setenv("PINAX_FILL_WARNING_MONTHS", "1")
    config.get_settings.cache_clear()
    assert not _summary(today=TODAY)["soon"]
    _set("2026-06-01", "120")
    full = _summary(today=TODAY)
    assert full["full"] and full["fills"] is None and full["share"] > 1


def test_what_the_storage_reports(fast):
    """With enough scans recording the storage's own figures, they are what fills it: here 10 TB more than the
    scans see (the storage's snapshots), and its total is the capacity until one is set."""
    from sqlalchemy import select

    from pinax.db import session_scope
    from pinax.models import Snapshot

    with session_scope() as s:
        for snap in s.scalars(select(Snapshot)):
            snap.info = {"storage": {"total": 400 * TB, "used": snap.bytes + 10 * TB, "available": 0}}
    out = _summary(today=TODAY)
    assert out["measure"] == "storage" and out["used"] == 135 * TB
    assert out["capacity"] == {"bytes": 400 * TB, "source": "scan", "since": None}
    assert out["storage"]["total"] == 400 * TB
    assert out["fills"]["at"] is None  # 265 TB to go at a terabyte a week: more than three years
    _set("2026-01-01", "200")
    assert _summary(today=TODAY)["capacity"]["source"] == "set"


def test_scans_without_space_on_disk_are_measured_by_size(fast):
    from sqlalchemy import update

    from pinax.db import session_scope
    from pinax.models import Snapshot

    with session_scope() as s:
        s.execute(update(Snapshot).values(alloc_bytes=0))
    assert _summary(today=TODAY)["measure"] == "size"


def test_changes_are_checked_and_audited(fast):
    from sqlalchemy import select

    from pinax import capacity
    from pinax.costs import CostError
    from pinax.db import session_scope
    from pinax.models import AuditEvent, Capacity

    _set("2026-01-01", "1.5", "TiB")
    for args, message in ((("2026-01-01", "2"), "already has a capacity"), (("2026-02-01", "0"), "more than nothing"),
                          (("01/02/2026", "2"), "YYYY-MM-DD"), (("2026-02-01", "lots"), "must be a number"),
                          (("2026-02-01", "2", "EB"), "given in"), (("2026-02-01", "2000000", "PB"), "zettabyte")):
        with pytest.raises(CostError, match=message):
            _set(*args)
    with session_scope() as s:
        row = s.scalar(select(Capacity))
        assert row.bytes == int(1.5 * 2**40) and row.unit == "TiB"
        capacity.remove_capacity(s, None, row)
    with session_scope() as s:
        assert s.scalar(select(Capacity)) is None
        assert [e.action for e in s.scalars(select(AuditEvent).order_by(AuditEvent.id))] == ["capacity.set", "capacity.removed"]


def test_administrators_set_it_in_the_app(fast):
    from fastapi.testclient import TestClient

    from pinax.main import app

    with TestClient(app) as c:
        c.cookies.set("pinax_user", "ada")
        assert c.get("/api/admin/capacity").status_code == 403
        c.cookies.set("pinax_user", "ivy")
        r = c.post("/api/admin/capacity/fast", json={"starts": "2026-01-01", "amount": "200", "unit": "TB"})
        assert r.status_code == 200, r.text
        assert r.json()["capacity"]["bytes"] == 200 * TB
        assert c.post("/api/admin/capacity/fast", json={"starts": "2026-01-01", "amount": "3"}).status_code == 409
        listed = c.get("/api/admin/capacity").json()
        assert listed["units"] == ["GB", "GiB", "TB", "TiB", "PB", "PiB"] and len(listed["filesystems"]) == 1
        row = listed["filesystems"][0]["schedule"][0]["id"]
        assert c.delete(f"/api/admin/capacity/fast/{row}").json()["capacity"]["bytes"] is None
        assert c.delete(f"/api/admin/capacity/fast/{row}").status_code == 404


def test_a_scan_records_what_the_storage_reports_and_ingest_keeps_it(env, tmp_path):
    from pinax import capacity
    from pinax.db import session_scope
    from pinax.models import Filesystem
    from pinax.snapshots import ingest_scan

    write_scan(tmp_path / "scan", ROOT, FILES)
    recorded = capacity.record_storage(tmp_path / "scan", str(tmp_path))
    assert recorded["total"] >= recorded["used"] > 0 and recorded["available"] >= 0
    assert capacity.scan_storage(tmp_path / "scan") == recorded
    assert capacity.scan_storage(tmp_path) is None
    with session_scope() as s:
        s.add(Filesystem(slug="fast", name="Fast", root=ROOT))
    with session_scope() as s:
        snap = ingest_scan(s, tmp_path / "scan", progress=lambda m: None)
        assert snap.info["storage"] == recorded


def test_a_filesystems_history_carries_its_capacity(client):
    from sqlalchemy import select

    from pinax import capacity
    from pinax.db import session_scope
    from pinax.models import Filesystem

    with session_scope() as s:
        capacity.set_capacity(s, None, s.scalar(select(Filesystem)), date(2026, 1, 1).isoformat(), "1", "GB")
    client.cookies.set("pinax_user", "ivy")
    h = client.get("/api/history", params={"fs": "fast"}).json()
    assert h["capacity"]["capacity"]["bytes"] == 10**9
    assert h["capacity"]["steps"] == [{"from": "2026-01-01T00:00:00Z", "bytes": 10**9}]
    assert client.get("/api/history", params={"fs": "fast", "path": f"{ROOT}/alpha_lab"}).json()["capacity"] is None
