# SPDX-License-Identifier: Apache-2.0
"""What labs will pay (R12): each lab's charges for the next twelve months at its trend, checked against charges
worked out hour by hour on the forecast sizes, with a price change scheduled ahead and a lab growing past its free
tier; and on Storage charges and Chargeback."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

TB = 10**12
START = datetime(2026, 1, 5, 2)
WEEKS = 26
LAST = START + timedelta(weeks=WEEKS - 1)
NOW = LAST + timedelta(days=2)  # 1 July 2026
GROWTH = {"alpha_lab": (50 * TB, TB), "gamma_lab": (2 * TB, TB // 10)}  # at the first scan, and a week


@pytest.fixture
def priced(env):
    """Fast, scanned weekly for half a year: the Alpha Lab grows a terabyte a week from 50 TB, the Gamma Lab a
    tenth of one from 2 TB (past its 5 TB free tier during August), and the Delta Lab's folder is a month old. Fast
    costs 20 per TB until 16 October, then 30."""
    from pinax import costs
    from pinax.db import session_scope
    from pinax.models import Filesystem, FolderStat, Lab, LabFolder, LabKind, LabMembership, Snapshot, User

    with session_scope() as s:
        fs = Filesystem(slug="fast", name="Fast", root="/research/fast")
        labs = {slug: Lab(slug=slug, name=f"{slug.title()} Lab") for slug in ("alpha", "gamma", "delta")}
        ada = User(username="ada", display_name="Ada", email="ada@example.org")
        s.add_all([fs, *labs.values(), ada, User(username="ivy", display_name="Ivy", email="ivy@example.org", is_admin=True)])
        s.flush()
        s.add_all([LabFolder(lab_id=lab.id, filesystem_id=fs.id, path=f"{slug}_lab") for slug, lab in labs.items()])
        s.add(LabMembership(user_id=ada.id, lab_id=labs["alpha"].id, role="pi"))
        for w in range(WEEKS):
            when = START + timedelta(weeks=w)
            snap = Snapshot(filesystem_id=fs.id, scan_id=f"fast-{w}", scan_dir="/x", started_at=when, status="pruned")
            s.add(snap)
            s.flush()
            sizes = {f: base + w * per_week for f, (base, per_week) in GROWTH.items()}
            if w >= WEEKS - 4:
                sizes["delta_lab"] = 3 * TB
            s.add_all([FolderStat(snapshot_id=snap.id, filesystem_id=fs.id, folder=f, taken_at=when, bytes=b)
                       for f, b in sizes.items()])
        costs.add_price(s, None, fs, "2026-01-01", "20", "TB")
        costs.add_price(s, None, fs, "2026-10-16", "30", "TB")
        costs.set_free_tier(s, None, fs, "2026-01-01", "5", "TB", kind=LabKind.LAB)
    return env


def _ahead():
    from pinax import charges
    from pinax.db import session_scope

    with session_scope() as s:
        return {e["lab"]["slug"]: e for e in charges.charges_ahead(s, now=NOW).values()}


def _hourly(month: str, size_at) -> Decimal:
    """Brute force: a month's charge, hour by hour, at the size `size_at(t)`, less 5 TB free, at Fast's price."""
    y, m = map(int, month.split("-"))
    start = datetime(y, m, 1)
    end = datetime(y + (m == 12), m % 12 + 1, 1)
    hours = int((end - start).total_seconds() // 3600)
    total = 0.0
    for h in range(hours):
        t = start + timedelta(hours=h, minutes=30)
        price = 20 if t < datetime(2026, 10, 16) else 30
        total += max(0.0, size_at(t) - 5 * TB) * price / TB / hours
    return Decimal(str(round(total, 2)))


def _trend(folder: str):
    base, per_week = GROWTH[folder]
    return lambda t: base + per_week * (t - START).total_seconds() / (7 * 86400)


def test_twelve_months_after_this_one(priced):
    a = _ahead()["alpha"]
    assert [m["month"] for m in a["months"]] == [f"2026-{m:02d}" for m in range(8, 13)] + [f"2027-{m:02d}" for m in range(1, 8)]


def test_a_growing_lab_is_charged_at_its_trend(priced):
    a = _ahead()["alpha"]
    (line,) = a["filesystems"]
    assert line["trend"] and line["priced"] and line["per_month"] == pytest.approx(TB * 365.25 / 12 / 7, rel=1e-9)
    for month in line["months"]:
        want = _hourly(month["month"], _trend("alpha_lab"))
        assert abs(Decimal(month["charge"]) - want) <= Decimal("0.01"), month
        assert month["low"] == month["charge"] == month["high"]  # an exact line: no range
    assert Decimal(a["total"]["charge"]) == sum(Decimal(m["charge"]) for m in a["months"])


def test_a_lab_growing_past_its_free_tier(priced):
    g = _ahead()["gamma"]["filesystems"][0]
    size = _trend("gamma_lab")
    assert size(datetime(2026, 8, 1)) < 5 * TB < size(datetime(2026, 9, 1))  # it passes the free tier in August
    for month in g["months"]:
        assert abs(Decimal(month["charge"]) - _hourly(month["month"], _trend("gamma_lab"))) <= Decimal("0.01"), month
    assert Decimal(g["months"][-1]["charge"]) > 0


def test_a_lab_without_a_trend_is_charged_at_its_latest_size(priced):
    d = _ahead()["delta"]["filesystems"][0]
    assert not d["trend"] and d["why"] == "scans" and d["scans"] == 4 and d["per_month"] is None
    for month in d["months"]:
        assert Decimal(month["charge"]) == _hourly(month["month"], lambda t: 3 * TB) == 0  # within its free tier


def test_members_see_their_labs_ahead_and_administrators_every_lab(priced):
    from fastapi.testclient import TestClient

    from pinax import charges
    from pinax.db import session_scope
    from pinax.main import app

    with TestClient(app) as c:
        c.cookies.set("pinax_user", "ada")
        (mine,) = c.get("/api/charges").json()["labs"]
        assert mine["lab"]["slug"] == "alpha" and len(mine["ahead"]["months"]) == 12
        c.cookies.set("pinax_user", "ivy")
        assert "ahead" not in c.get("/api/admin/costs/charges").json()  # asked for apart, so the month is quick
        ahead = c.get("/api/admin/costs/ahead").json()
        c.cookies.set("pinax_user", "ada")
        assert c.get("/api/admin/costs/ahead").status_code == 403
    with session_scope() as s:
        per_lab = charges.charges_ahead(s)
    assert ahead["labs"] == 3 and ahead["without_trend"] == 1
    for i, month in enumerate(ahead["months"]):
        assert Decimal(month["charge"]) == sum(Decimal(e["months"][i]["charge"]) for e in per_lab.values())
