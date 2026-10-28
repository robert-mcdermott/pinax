# SPDX-License-Identifier: Apache-2.0
"""Monthly charges (R8, wave 5 slice 2): every lab's exact charge, size, and billable size for a month against brute
force, hour by hour, over a month in which scans come and go, a folder changes owner, a price changes, a lab's own
free tier starts and ends, and a department has none. Every change falls on the hour, so the hourly sum is exact."""

import math
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest
from conftest import login
from sqlalchemy import select

TB, GB = 10**12, 10**9
SEPT = (datetime(2026, 9, 1), datetime(2026, 10, 1))
OCT = (datetime(2026, 10, 1), datetime(2026, 11, 1))
MOVED = datetime(2026, 9, 18, 15)  # beta_lab moves from the Beta Lab to the Alpha Lab

# Scans of Fast: (time, status, {folder: bytes}); "" is the files directly in the root.
SCANS = [
    (datetime(2026, 8, 28, 10), "ready", {"alpha_lab": TB // 2, "beta_lab": 12 * TB // 10, "beta_two": 3 * TB // 10,
                                          "core_seq": 5 * TB, "orphans": 2 * TB, "": GB}),
    (datetime(2026, 9, 5, 13), "pruned", {"alpha_lab": 8 * TB // 10, "beta_lab": 12 * TB // 10, "beta_two": 3 * TB // 10,
                                          "core_seq": 6 * TB, "orphans": 2 * TB, "": GB}),
    (datetime(2026, 9, 12, 7), "ready", {"alpha_lab": 25 * TB // 10, "alpha_new": TB // 5, "beta_lab": 13 * TB // 10,
                                         "core_seq": 6 * TB, "orphans": 2 * TB, "": GB}),  # beta_two gone for now
    (datetime(2026, 9, 16), "failed", {"alpha_lab": 999 * TB}),  # never counted
    (datetime(2026, 9, 20, 22), "ready", {"alpha_lab": 4 * TB, "alpha_new": TB // 5, "beta_lab": 13 * TB // 10,
                                          "beta_two": 6 * TB // 10, "core_seq": 7 * TB, "orphans": 2 * TB, "": GB}),
    (datetime(2026, 10, 2, 3), "ready", {"alpha_lab": 4 * TB, "alpha_new": TB // 5, "beta_lab": 13 * TB // 10,
                                         "beta_two": 6 * TB // 10, "core_seq": 7 * TB, "orphans": 2 * TB, "": GB}),
]
OWNERS_NOW = {"alpha_lab": "alpha", "alpha_new": "alpha", "beta_lab": "alpha", "beta_two": "beta", "core_seq": "core"}


@pytest.fixture
def billing(env):
    """Fast (priced, with free tiers) and Secure (no price), the labs, their folders, and the scans above."""
    from pinax import costs
    from pinax.db import session_scope
    from pinax.models import Filesystem, FolderStat, Lab, LabFolder, LabKind, LabMembership, Snapshot, User
    from pinax.ownership import record_change

    with session_scope() as s:
        fast = Filesystem(slug="fast", name="Fast", root="/research/fast")
        secure = Filesystem(slug="secure", name="Secure", root="/research/secure")
        labs = {slug: Lab(slug=slug, name=f"{slug.title()} Lab", kind=kind) for slug, kind in
                (("alpha", LabKind.LAB), ("beta", LabKind.LAB), ("core", LabKind.DEPARTMENT))}
        people = {u: User(username=u, display_name=u.title(), email=f"{u}@example.org", is_admin=u == "ivy")
                  for u in ("ivy", "ada", "ben", "nobody")}
        s.add_all([fast, secure, *labs.values(), *people.values()])
        s.flush()
        s.add_all([LabMembership(user_id=people["ada"].id, lab_id=labs["alpha"].id, role="pi"),
                   LabMembership(user_id=people["ben"].id, lab_id=labs["beta"].id, role="delegate")])
        for folder, slug in OWNERS_NOW.items():
            s.add(LabFolder(lab_id=labs[slug].id, filesystem_id=fast.id, path=folder))
        s.add(LabFolder(lab_id=labs["alpha"].id, filesystem_id=secure.id, path="alpha_lab"))
        record_change(s, fast.id, "beta_lab", labs["beta"].id, labs["alpha"].id, when=MOVED)
        for i, (when, status, sizes) in enumerate(SCANS):
            snap = Snapshot(filesystem_id=fast.id, scan_id=f"fast-{i}", scan_dir="/x", started_at=when, status=status)
            s.add(snap)
            s.flush()
            s.add_all([FolderStat(snapshot_id=snap.id, filesystem_id=fast.id, folder=f, taken_at=when, bytes=b)
                       for f, b in sizes.items()])
        snap = Snapshot(filesystem_id=secure.id, scan_id="secure-0", scan_dir="/x", started_at=datetime(2026, 9, 1),
                        status="ready")
        s.add(snap)
        s.flush()
        s.add(FolderStat(snapshot_id=snap.id, filesystem_id=secure.id, folder="alpha_lab", taken_at=snap.started_at,
                         bytes=4 * TB // 10))
        costs.add_price(s, None, fast, "2026-01-01", "30", "TB")
        costs.add_price(s, None, fast, "2026-09-16", "24", "TB")
        costs.set_free_tier(s, None, fast, "2026-01-01", "1", "TB", kind=LabKind.LAB)
        costs.set_free_tier(s, None, fast, "2026-01-01", "0", "TB", kind=LabKind.DEPARTMENT)
        costs.set_free_tier(s, None, fast, "2026-09-10", "3", "TB", lab=labs["alpha"])
        costs.set_free_tier(s, None, fast, "2026-09-25", None, "TB", lab=labs["alpha"])
    return env


def _brute(month: tuple[datetime, datetime]) -> dict[str, dict]:
    """Hour by hour, from the scans, owners, prices, and free tiers above, written out again independently."""
    start, end = month
    hours = int((end - start) / timedelta(hours=1))
    counted = [(t, sizes) for t, status, sizes in SCANS if status in ("ready", "pruned")]
    out: dict[str, dict] = {k: {"size": Fraction(0), "billable": Fraction(0), "charge": Fraction(0)}
                            for k in ("alpha", "beta", "core", None)}
    for h in range(hours):
        at = start + timedelta(hours=h)
        sizes = next((s for t, s in reversed(counted) if t <= at), None)
        if sizes is None:
            continue
        price = Fraction(30 if at < datetime(2026, 9, 16) else 24, TB)
        held = {"alpha": 0, "beta": 0, "core": 0}
        for folder, size in sizes.items():
            owner = ("beta" if at < MOVED else "alpha") if folder == "beta_lab" else OWNERS_NOW.get(folder)
            if owner is None:
                out[None]["size"] += Fraction(size, hours)
            else:
                held[owner] += size
        for lab, size in held.items():
            free = 0 if lab == "core" else 3 * TB if lab == "alpha" and datetime(2026, 9, 10) <= at < datetime(2026, 9, 25) else TB
            out[lab]["size"] += Fraction(size, hours)
            out[lab]["billable"] += Fraction(max(0, size - free), hours)
            out[lab]["charge"] += Fraction(max(0, size - free), hours) * price
    return out


def _lines(month):
    from pinax.charges import filesystem_lines
    from pinax.db import session_scope
    from pinax.models import Filesystem, Lab

    with session_scope() as s:
        labs = {lab.id: lab for lab in s.scalars(select(Lab))}
        slug = {lab.id: lab.slug for lab in labs.values()}
        fast = s.scalar(select(Filesystem).where(Filesystem.slug == "fast"))
        lines, nobody, prices = filesystem_lines(s, fast, *month, labs)
        return {slug[k]: v for k, v in lines.items()}, nobody, prices


@pytest.mark.parametrize("month", [SEPT, OCT], ids=["a month of changes", "the month after"])
def test_every_charge_equals_the_hourly_sum(billing, month):
    lines, nobody, _ = _lines(month)
    want = _brute(month)
    for lab in ("alpha", "beta", "core"):
        got = lines[lab]
        assert (got.size, got.billable, got.charge) == (want[lab]["size"], want[lab]["billable"], want[lab]["charge"]), lab
    assert nobody.size == want[None]["size"]  # orphans and the files in the root
    assert nobody.charge == 0


def test_what_the_month_shows(billing):
    from pinax.charges import monthly_charges
    from pinax.db import session_scope

    with session_scope() as s:
        sept = monthly_charges(s, "2026-09", now=datetime(2026, 10, 10, 12))
    assert not sept["estimated"] and sept["currency"] == "USD"
    labs = {r["lab"]["slug"]: r for r in sept["labs"]}
    fast = {k: next(f for f in r["filesystems"] if f["fs"] == "fast") for k, r in labs.items()}
    want = _brute(SEPT)
    for lab, line in fast.items():
        cents = math.floor(want[lab]["charge"] * 100 + Fraction(1, 2))  # to the cent, half up
        assert Decimal(line["charge"]) == Decimal(cents) / 100, lab
    assert fast["alpha"]["prices"] == [{"price": "30", "unit": "TB", "from": "2026-09-01", "to": "2026-09-16"},
                                       {"price": "24", "unit": "TB", "from": "2026-09-16", "to": "2026-10-01"}]
    assert sorted(fast["alpha"]["free"]) == [TB, 3 * TB]  # its own tier for part of the month
    assert fast["core"]["free"] == [0]
    assert {f["folder"] for f in fast["beta"]["folders"]} == {"beta_lab", "beta_two"}  # beta_lab until it moved
    # A lab's total is its lines, rounded; the month's total is the labs'.
    for r in sept["labs"]:
        assert Decimal(r["total"]) == sum(Decimal(f["charge"]) for f in r["filesystems"])
    assert Decimal(sept["total"]) == sum(Decimal(r["total"]) for r in sept["labs"])
    totals = [Decimal(r["total"]) for r in sept["labs"]]
    assert totals == sorted(totals, reverse=True)  # the most first
    # Secure has no price: its storage is shown, not charged.
    secure = next(f for f in labs["alpha"]["filesystems"] if f["fs"] == "secure")
    assert secure["unpriced"] and secure["charge"] == "0.00" and secure["size"] == 4 * TB // 10
    assert {f["folder"] for u in sept["unbilled"] for f in u["folders"]} == {"orphans", ""}


def test_the_month_in_progress_is_an_estimate(billing):
    """The latest scan holds until the month's end."""
    from pinax.charges import monthly_charges, months
    from pinax.db import session_scope

    now = datetime(2026, 10, 10, 12)
    with session_scope() as s:
        oct_ = monthly_charges(s, "2026-10", now=now)
        assert months(s, now=now) == ["2026-10", "2026-09", "2026-08"]
    assert oct_["estimated"] and oct_["through"] == "2026-10-10T12:00:00Z"
    alpha = next(f for f in next(r for r in oct_["labs"] if r["lab"]["slug"] == "alpha")["filesystems"] if f["fs"] == "fast")
    # 4 + 0.2 + 1.3 TB all month, less 1 TB free, at 24 per TB: 4.5 * 24 = 108.
    assert alpha["size"] == 55 * TB // 10 and alpha["charge"] == "108.00"


def test_before_the_first_scan_and_the_first_price(billing):
    from pinax.charges import monthly_charges
    from pinax.db import session_scope

    with session_scope() as s:
        july = monthly_charges(s, "2026-07", now=datetime(2026, 10, 10))
        aug = monthly_charges(s, "2026-08", now=datetime(2026, 10, 10))
    assert july["labs"] == [] and july["total"] == "0.00"  # nothing scanned yet
    alpha = next(r for r in aug["labs"] if r["lab"]["slug"] == "alpha")["filesystems"][0]
    # From the first scan (Aug 28, 10:00) to the month's end: 86 of 744 hours, 0.5 TB, under the 1 TB free tier.
    assert alpha["size"] == round(TB // 2 * Fraction(86, 744)) and alpha["charge"] == "0.00"


def test_rounding_and_months():
    from pinax.charges import money, month_bounds

    assert str(money(Fraction(12345, 1000), "USD")) == "12.35" and str(money(Fraction(5, 2), "JPY")) == "3"
    assert month_bounds("2026-12") == (datetime(2026, 12, 1), datetime(2027, 1, 1))
    with pytest.raises(ValueError):
        month_bounds("Sept 2026")


def test_owners_are_recorded_by_the_app(catalog):
    """On Folders and labs: the first change of a folder also records who had it until then."""
    from fastapi.testclient import TestClient

    from pinax.db import session_scope
    from pinax.main import app
    from pinax.models import FolderOwner
    from pinax.ownership import SINCE_ALWAYS

    with TestClient(app) as c:
        login(c, "ivy")
        assert c.post("/api/admin/folders", json={"fs": "fast", "folder": "alpha_lab", "lab": "beta"}).status_code == 200
        assert c.post("/api/admin/folders", json={"fs": "fast", "folder": "alpha_lab", "lab": None}).status_code == 200
        assert c.post("/api/admin/folders", json={"fs": "fast", "folder": "orphans", "lab": "alpha"}).status_code == 200
    with session_scope() as s:
        rows = [(r.folder, r.lab_id, r.starts == SINCE_ALWAYS) for r in s.scalars(select(FolderOwner).order_by(FolderOwner.id))]
    alpha, beta = rows[0][1], rows[1][1]
    assert rows == [("alpha_lab", alpha, True), ("alpha_lab", beta, False), ("alpha_lab", None, False),
                    ("orphans", None, True), ("orphans", alpha, False)]


def test_owners_are_recorded_by_import(catalog, tmp_path):
    from pinax.db import session_scope
    from pinax.models import FolderOwner, Lab
    from pinax.onboard import apply_import, plan_import
    from pinax.ownership import SINCE_ALWAYS, Owners

    folders = tmp_path / "folders.csv"
    folders.write_text("filesystem,folder,lab\nfast,beta_lab,alpha\n")
    with session_scope() as s:
        assert apply_import(s, plan_import(s, folders=folders)) == 1
    with session_scope() as s:
        ids = {lab.slug: lab.id for lab in s.scalars(select(Lab))}
        rows = s.scalars(select(FolderOwner).order_by(FolderOwner.id)).all()
        assert [(r.folder, r.lab_id, r.starts == SINCE_ALWAYS) for r in rows] == [("beta_lab", ids["beta"], True),
                                                                                  ("beta_lab", ids["alpha"], False)]
        owners = Owners(s, rows[0].filesystem_id)
        moved = rows[1].starts
        assert owners.at("beta_lab", moved - timedelta(seconds=1)) == ids["beta"]
        assert owners.at("beta_lab", moved) == ids["alpha"]
        assert owners.at("alpha_lab", datetime(2000, 1, 1)) == ids["alpha"]  # never changed: its owner now


def test_only_administrators_see_every_lab_charges(billing):
    from fastapi.testclient import TestClient

    from pinax.main import app

    with TestClient(app) as c:
        login(c, "ada")
        assert c.get("/api/admin/costs/charges", params={"month": "2026-09"}).status_code == 403
        login(c, "ivy")
        r = c.get("/api/admin/costs/charges", params={"month": "2026-09"})
        assert r.status_code == 200 and r.json()["months"][-1] == "2026-08"
        assert c.get("/api/admin/costs/charges", params={"month": "September"}).status_code == 422


@pytest.fixture
def members(billing, monkeypatch):
    """The billing catalog on 10 October 2026, mid-month, for the people in it."""
    from fastapi.testclient import TestClient

    from pinax import charges
    from pinax.api import costs as costs_api
    from pinax.main import app

    now = lambda: datetime(2026, 10, 10, 12)
    monkeypatch.setattr(charges, "utcnow", now)
    monkeypatch.setattr(costs_api, "utcnow", now)
    with TestClient(app) as c:
        yield c


def _mine(client, who, **query):
    login(client, who)
    return client.get("/api/charges", params=query)


def test_members_see_their_own_labs_charges(members):
    sept = _mine(members, "ada", month="2026-09").json()
    assert [r["lab"]["slug"] for r in sept["labs"]] == ["alpha"] and sept["labs"][0]["role"] == "pi"
    assert [c["slug"] for c in sept["choices"]] == ["alpha"] and sept["priced"] and "unbilled" not in sept
    admin = _mine(members, "ivy", month="2026-09", lab="alpha").json()
    assert admin["labs"][0]["total"] == sept["labs"][0]["total"] == sept["total"]
    assert [c["slug"] for c in admin["choices"]] == ["alpha", "beta", "core"]
    assert _mine(members, "ivy").json()["labs"][0]["lab"]["slug"] == "alpha"  # an administrator in no lab: the first
    # Beta Lab is billed for beta_lab until it moved, but may no longer open it.
    beta = _mine(members, "ben", month="2026-09").json()["labs"][0]
    folders = {f["folder"]: f["open"] for f in beta["filesystems"][0]["folders"]}
    assert folders == {"beta_lab": False, "beta_two": True}
    for who, lab in (("ada", "beta"), ("ben", "core"), ("ada", "nowhere")):
        assert _mine(members, who, lab=lab).status_code == 404
    nobody = _mine(members, "nobody").json()
    assert nobody["labs"] == [] and nobody["choices"] == [] and nobody["total"] == "0.00"
    assert _mine(members, "ada", month="2026-13").status_code == 422


def test_how_each_charge_is_worked_out(members):
    """Each period has one price and one free tier; its charge is its average billable size at its price for its
    share of the month, and the periods add up to the line."""
    alpha = _mine(members, "ada", month="2026-09").json()["labs"][0]
    fast, secure = (next(f for f in alpha["filesystems"] if f["fs"] == fs) for fs in ("fast", "secure"))
    got = [(p["from"][:10], p["to"][:10], p["price"], p["free_amount"]) for p in fast["periods"]]
    assert got == [("2026-09-01", "2026-09-10", "30", "1"), ("2026-09-10", "2026-09-16", "30", "3"),
                   ("2026-09-16", "2026-09-25", "24", "3"), ("2026-09-25", "2026-10-01", "24", "1")]
    for p in fast["periods"]:
        worked = Decimal(p["billable"]) * Decimal(p["price"]) / TB * Decimal(p["share"])
        assert abs(worked - Decimal(p["charge"])) < Decimal("0.01")
        assert p["days"] == round(p["share"] * 30, 2)
    assert abs(sum(Decimal(p["charge"]) for p in fast["periods"]) - Decimal(fast["charge"])) <= Decimal("0.02")
    assert secure["unpriced"] and [(p["price"], p["days"]) for p in secure["periods"]] == [(None, 30)]
    core = _mine(members, "ivy", month="2026-09", lab="core").json()["labs"][0]["filesystems"][0]
    assert {(p["free"], p["free_amount"]) for p in core["periods"]} == {(0, "0")}  # departments: none


def test_this_month_and_the_months_before(members):
    oct_ = _mine(members, "ada").json()
    assert oct_["month"] == "2026-10" and oct_["estimated"] and oct_["through"] == "2026-10-10T12:00:00Z"
    assert oct_["months"] == ["2026-10", "2026-09", "2026-08"] and oct_["days"] == 31
    history = oct_["labs"][0]["history"]
    assert [(h["month"], h["estimated"]) for h in history] == [("2026-10", True), ("2026-09", False), ("2026-08", False)]
    assert history[0]["total"] == oct_["labs"][0]["total"] == "108.00"
    sept = _mine(members, "ada", month="2026-09").json()["labs"][0]
    assert history[1]["total"] == sept["total"] and history[1]["size"] == sum(f["size"] for f in sept["filesystems"])
    assert _mine(members, "ada", history="false").json()["labs"][0]["history"] == []  # for the home page
