# SPDX-License-Identifier: Apache-2.0
"""Prices and free tiers (R8, wave 5 slice 1): schedules from a day on, which apply on any day, exact money and
units, refusals, and the audit of every change."""

from datetime import date

import pytest
from conftest import login
from sqlalchemy import select

TB, TIB = 10**12, 2**40


@pytest.fixture
def costs_client(client):
    """The usual catalog plus a department, with Ivy (an administrator) signed in."""
    from pinax.db import session_scope
    from pinax.models import Lab, LabKind

    with session_scope() as s:
        s.add(Lab(slug="core", name="Sequencing Core", kind=LabKind.DEPARTMENT))
    return login(client, "ivy")


def _post(client, path: str, **body):
    return client.post(f"/api/admin/costs/fast/{path}", json=body)


def _on(fn, *args):
    from pinax.db import session_scope
    from pinax.models import Filesystem, Lab

    with session_scope() as s:
        fs = s.scalar(select(Filesystem).where(Filesystem.slug == "fast"))
        resolved = [s.scalar(select(Lab).where(Lab.slug == a)) if isinstance(a, str) else a for a in args]
        return fn(s, fs, *resolved)


def _events(action: str) -> list:
    from pinax.db import session_scope
    from pinax.models import AuditEvent

    with session_scope() as s:
        return [(e.summary, e.detail) for e in s.scalars(select(AuditEvent).where(AuditEvent.action == action)
                                                         .order_by(AuditEvent.id))]


def test_only_administrators_change_costs(client):
    login(client, "ada")
    assert client.get("/api/admin/costs").status_code == 403
    assert _post(client, "prices", starts="2026-01-01", price="30").status_code == 403
    assert _post(client, "free-tiers", starts="2026-01-01", amount="1", kind="lab").status_code == 403


def test_money_and_storage_are_exact():
    from pinax.costs import CostError, price_micros, shown, storage_bytes

    assert price_micros("30") == 30_000_000 and price_micros("0.023456") == 23_456 and price_micros(0) == 0
    for bad in ("-1", "abc", "1.1234567", "1e7", "NaN"):
        with pytest.raises(CostError):
            price_micros(bad)
    assert storage_bytes("1.5", "TB") == 1_500_000_000_000 and storage_bytes("1", "TiB") == TIB
    assert storage_bytes("100", "GiB") == 100 * 2**30
    with pytest.raises(CostError):
        storage_bytes("1", "PB")
    assert shown(1_500_000_000_000, "TB") == "1.5" and shown(TIB, "TiB") == "1" and shown(0, "GB") == "0"


def test_a_price_applies_from_its_day_until_the_next(costs_client):
    from pinax.costs import price_on

    for starts, price in (("2026-01-01", "30"), ("2026-07-01", "25.50"), ("2027-01-01", "20")):
        assert _post(costs_client, "prices", starts=starts, price=price, unit="TB").status_code == 200
    on = lambda d: (r.micros, r.unit) if (r := _on(price_on, d)) else None
    assert on(date(2025, 12, 31)) is None  # before the first price: not charged
    assert on(date(2026, 1, 1)) == on(date(2026, 6, 30)) == (30_000_000, "TB")
    assert on(date(2026, 7, 1)) == (25_500_000, "TB")
    assert on(date(2027, 3, 1)) == (20_000_000, "TB")  # a scheduled price
    r = _post(costs_client, "prices", starts="2026-07-01", price="99")
    assert r.status_code == 409  # one price a day
    added = _events("cost.price_added")
    assert added[1][0] == "Fast: 25.5 per TB per month from 2026-07-01"
    assert added[1][1]["before"] == {"price": "30", "unit": "TB"} and added[0][1]["before"] is None


def test_removing_a_price_and_the_schedule_shown(costs_client, monkeypatch):
    from pinax.costs import price_on
    from pinax.db import utcnow

    for starts in ("2026-01-01", "2026-07-01", "2099-01-01"):
        _post(costs_client, "prices", starts=starts, price="10", unit="GiB")
    page = costs_client.get("/api/admin/costs").json()
    fast = next(f for f in page["filesystems"] if f["fs"] == "fast")
    today = utcnow().date().isoformat()
    assert page["today"] == today and page["units"] == ["GB", "GiB", "TB", "TiB"]
    assert [p["starts"] for p in fast["prices"]] == ["2099-01-01", "2026-07-01", "2026-01-01"]  # newest first
    assert [p["future"] for p in fast["prices"]] == [True, False, False]
    assert [p["current"] for p in fast["prices"]] == [False, True, False]
    assert fast["price"] == {"price": "10", "unit": "GiB", "starts": "2026-07-01"}
    r = costs_client.delete(f"/api/admin/costs/fast/prices/{fast['prices'][1]['id']}")
    assert r.status_code == 200 and r.json()["price"]["starts"] == "2026-01-01"
    assert _on(price_on, date(2026, 8, 1)).starts == date(2026, 1, 1)
    assert _events("cost.price_removed")[0][0] == "Fast: removed the price of 10 per GiB from 2026-07-01"
    assert costs_client.delete("/api/admin/costs/fast/prices/9999").status_code == 404


def test_free_tiers_by_kind_with_exceptions(costs_client):
    from pinax.costs import free_on

    assert _post(costs_client, "free-tiers", starts="2026-01-01", amount="1", unit="TB", kind="lab").status_code == 200
    assert _post(costs_client, "free-tiers", starts="2026-01-01", amount="0", unit="TB", kind="department").status_code == 200
    assert _post(costs_client, "free-tiers", starts="2026-06-01", amount="5", unit="TiB", lab="alpha").status_code == 200
    assert _post(costs_client, "free-tiers", starts="2026-09-01", amount=None, lab="alpha").status_code == 200  # ends
    free = lambda lab, d: _on(free_on, lab, d)
    assert free("alpha", date(2025, 12, 31)) == 0  # nothing set yet
    assert free("alpha", date(2026, 3, 1)) == TB  # its kind's
    assert free("alpha", date(2026, 7, 1)) == 5 * TIB  # its own
    assert free("alpha", date(2026, 10, 1)) == TB  # its own ended: its kind's again
    assert free("beta", date(2026, 7, 1)) == TB
    assert free("core", date(2026, 7, 1)) == 0  # departments: none
    summaries = [s for s, _ in _events("cost.free_tier_set")]
    assert summaries == ["Fast: every lab, 1 TB free, from 2026-01-01", "Fast: every department, 0 TB free, from 2026-01-01",
                         "Fast: Alpha Lab, 5 TiB free, from 2026-06-01",
                         "Fast: Alpha Lab, back to its kind's free tier, from 2026-09-01"]
    fast = next(f for f in costs_client.get("/api/admin/costs").json()["filesystems"] if f["fs"] == "fast")
    assert fast["free"]["lab"]["now"]["amount"] == "1" and fast["free"]["department"]["now"]["amount"] == "0"
    alpha = fast["exceptions"][0]
    assert alpha["lab"]["slug"] == "alpha" and alpha["now"] is None and len(alpha["history"]) == 2  # ended


def test_free_tier_refusals(costs_client):
    assert _post(costs_client, "free-tiers", starts="2026-01-01", amount=None, kind="lab").status_code == 422
    assert _post(costs_client, "free-tiers", starts="2026-01-01", amount="1", kind="lab", lab="alpha").status_code == 422
    assert _post(costs_client, "free-tiers", starts="2026-01-01", amount="1").status_code == 422  # for no one
    assert _post(costs_client, "free-tiers", starts="2026-01-01", amount="1", kind="team").status_code == 422
    assert _post(costs_client, "free-tiers", starts="2026-01-01", amount="1", lab="nowhere").status_code == 404
    assert _post(costs_client, "free-tiers", starts="01/02/2026", amount="1", kind="lab").status_code == 422
    assert _post(costs_client, "free-tiers", starts="2026-01-01", amount="-2", kind="lab").status_code == 422
    assert _post(costs_client, "free-tiers", starts="2026-01-01", amount="1", kind="lab").status_code == 200
    assert _post(costs_client, "free-tiers", starts="2026-01-01", amount="2", kind="lab").status_code == 409
    assert costs_client.post("/api/admin/costs/nowhere/prices", json={"starts": "2026-01-01", "price": "1"}).status_code == 404


def test_removing_a_free_tier(costs_client):
    from pinax.costs import free_on

    _post(costs_client, "free-tiers", starts="2026-01-01", amount="1", kind="lab")
    tier = _post(costs_client, "free-tiers", starts="2026-06-01", amount="3", lab="beta").json()["exceptions"][0]["history"][0]
    assert _on(free_on, "beta", date(2026, 7, 1)) == 3 * TB
    assert costs_client.delete(f"/api/admin/costs/fast/free-tiers/{tier['id']}").status_code == 200
    assert _on(free_on, "beta", date(2026, 7, 1)) == TB
    assert _events("cost.free_tier_removed")[0][0] == "Fast: removed Beta Lab's free tier from 2026-06-01"


def test_the_currency_is_a_setting(costs_client, monkeypatch):
    from pydantic import ValidationError

    from pinax import config

    assert costs_client.get("/api/config").json()["currency"] == "USD"
    monkeypatch.setenv("PINAX_CURRENCY", "EUR")
    config.get_settings.cache_clear()
    assert costs_client.get("/api/admin/costs").json()["currency"] == "EUR"
    monkeypatch.setenv("PINAX_CURRENCY", "euros")
    config.get_settings.cache_clear()
    with pytest.raises(ValidationError):
        config.get_settings()
