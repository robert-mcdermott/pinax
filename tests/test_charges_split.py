# SPDX-License-Identifier: Apache-2.0
"""Charges by tag (T4): a lab's month split by a tag's values, checked against brute force hour by hour: nested
tagged folders, a folder with two values, sizes changing between scans, and another lab's folders left out."""

from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

TB = 10**12
ROOT = "/research/fast"
SCANS = [datetime(2026, 8, 28, 3), datetime(2026, 9, 4, 3), datetime(2026, 9, 11, 3), datetime(2026, 9, 18, 3),
         datetime(2026, 9, 25, 3)]
# Per scan: the Alpha Lab's top-level folder, and its tagged folders (grant_a holds sub; both is in no other).
SIZES = {"alpha_lab": [40, 44, 47, 50, 52], "alpha_lab/grant_a": [20, 22, 25, 25, 26],
         "alpha_lab/grant_a/sub": [5, 6, 6, 9, 10], "alpha_lab/both": [8, 8, 9, 9, 12], "beta_lab/x": [3, 3, 3, 3, 3]}
VALUES = {"alpha_lab/grant_a": ["A"], "alpha_lab/grant_a/sub": ["B"], "alpha_lab/both": ["A", "C"], "beta_lab/x": ["A"]}


@pytest.fixture
def split(env):
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from pinax import costs
    from pinax.db import session_scope
    from pinax.main import app
    from pinax.models import (
        Filesystem,
        FolderStat,
        FolderTag,
        Lab,
        LabFolder,
        LabMembership,
        MarkedFolder,
        MarkedFolderStat,
        Snapshot,
        Tag,
        User,
    )

    with session_scope() as s:
        fs = Filesystem(slug="fast", name="Fast", root=ROOT)
        alpha, beta = Lab(slug="alpha", name="Alpha Lab"), Lab(slug="beta", name="Beta Lab")
        ada = User(username="ada", display_name="Ada", email="ada@example.org")
        s.add_all([fs, alpha, beta, ada, User(username="ben", display_name="Ben", email="ben@example.org")])
        s.flush()
        s.add_all([LabFolder(lab_id=alpha.id, filesystem_id=fs.id, path="alpha_lab"),
                   LabFolder(lab_id=beta.id, filesystem_id=fs.id, path="beta_lab"),
                   LabMembership(user_id=ada.id, lab_id=alpha.id, role="pi")])
        grant = s.scalar(select(Tag).where(Tag.key == "grant"))
        marks = {}
        for rel, vals in VALUES.items():
            m = MarkedFolder(filesystem_id=fs.id, path=f"{ROOT}/{rel}")
            s.add(m)
            s.flush()
            marks[rel] = m
            s.add_all([FolderTag(tag_id=grant.id, mark_id=m.id, value=v) for v in vals])
        for k, when in enumerate(SCANS):
            snap = Snapshot(filesystem_id=fs.id, scan_id=f"s{k}", scan_dir="/x", started_at=when, status="pruned")
            s.add(snap)
            s.flush()
            s.add_all([FolderStat(snapshot_id=snap.id, filesystem_id=fs.id, folder=f, taken_at=when, bytes=SIZES[f][k] * TB)
                       for f in ("alpha_lab",)] + [FolderStat(snapshot_id=snap.id, filesystem_id=fs.id, folder="beta_lab",
                                                              taken_at=when, bytes=5 * TB)])
            s.add_all([MarkedFolderStat(mark_id=m.id, snapshot_id=snap.id, taken_at=when, present=True, bytes=SIZES[rel][k] * TB,
                                        age_bytes=[]) for rel, m in marks.items()])
        costs.add_price(s, None, fs, "2026-01-01", "10", "TB")
    with TestClient(app) as c:
        yield c


def _brute() -> dict[str, Fraction]:
    """Hour by hour through September: each value's exclusive size, and the lab's."""
    start, end = datetime(2026, 9, 1), datetime(2026, 10, 1)
    acc = {"A": Fraction(0), "B": Fraction(0), "C": Fraction(0), "lab": Fraction(0)}
    t = start
    while t < end:
        k = max(i for i, w in enumerate(SCANS) if w <= t)
        size = {rel: SIZES[rel][k] for rel in SIZES}
        acc["lab"] += size["alpha_lab"]
        acc["A"] += size["alpha_lab/grant_a"] - size["alpha_lab/grant_a/sub"] + Fraction(size["alpha_lab/both"], 2)
        acc["B"] += size["alpha_lab/grant_a/sub"]
        acc["C"] += Fraction(size["alpha_lab/both"], 2)
        t += timedelta(hours=1)
    return acc


def test_a_labs_month_split_by_grant(split):
    split.cookies.set("pinax_user", "ada")
    r = split.get("/api/charges/split", params={"lab": "alpha", "month": "2026-09"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["options"][0]["label"] == "Grant or award"
    got = {v["value"]: Decimal(v["charge"]) for v in body["split"]["values"]}
    total = Decimal(body["split"]["total"])
    brute = _brute()
    for v in ("A", "B", "C"):
        want = Fraction(total) * brute[v] / brute["lab"]
        assert abs(got[v] - Decimal(float(want)).quantize(Decimal("0.01"))) <= Decimal("0.01"), v
    untagged = Decimal(body["split"]["untagged"])
    assert abs(total - sum(got.values()) - untagged) <= Decimal("0.02")
    assert untagged > 0  # the Alpha Lab's folder holds more than its tagged folders


def test_other_labs_cannot_see_the_split(split):
    split.cookies.set("pinax_user", "ben")
    assert split.get("/api/charges/split", params={"lab": "alpha", "month": "2026-09"}).status_code == 404
