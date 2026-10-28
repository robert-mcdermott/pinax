# SPDX-License-Identifier: Apache-2.0
"""Cleanup plans (C1): a lab's PI, delegates, and administrators add folders to the lab's plan, to delete, move,
or compress; others' folders cannot be planned; a folder inside a planned one counts once; every change audited."""

import pytest
from conftest import ROOT, login

ALPHA = f"{ROOT}/alpha_lab"


@pytest.fixture
def planner(client):
    from pinax.db import session_scope
    from pinax.models import Filesystem

    with session_scope() as s:
        s.add(Filesystem(slug="slow", name="Slow", root="/research/slow"))
    return client


def _add(client, user: str, path: str, action: str = "delete", **body):
    login(client, user)
    return client.post("/api/plans", json={"fs": "fast", "path": path, "action": action, **body})


def test_a_pi_plans_their_labs_folders(planner):
    r = _add(planner, "ada", f"{ALPHA}/projects/2015_old", note="Published in 2016; raw data is in SRA")
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["lab"] == "alpha" and item["action"] == "delete" and item["added_by"] == "Ada"
    assert item["baseline"]["bytes"] == 8050 and item["now"]["bytes"] == 8050 and item["mark"]["status"] == "here"
    assert _add(planner, "ada", f"{ALPHA}/projects/2015_old").status_code == 409


def test_a_delegate_plans_directly_and_no_one_plans_others_folders(planner):
    assert _add(planner, "ben", f"{ROOT}/beta_lab/imaging", "compress").status_code == 200
    assert _add(planner, "ada", f"{ROOT}/beta_lab/imaging").status_code == 404
    assert _add(planner, "nobody", f"{ALPHA}/projects").status_code == 404
    for path in (ROOT, f"{ALPHA}/../beta_lab", f"{ALPHA}/nowhere"):
        assert _add(planner, "ada", path).status_code == 404, path
    unowned = _add(planner, "ivy", f"{ROOT}/orphans/old")
    assert unowned.status_code == 422 and "No lab or department owns" in unowned.json()["detail"]


def test_moves_name_another_filesystem(planner):
    for to, code in ((None, 422), ("fast", 422), ("nowhere", 422), ("slow", 200)):
        r = _add(planner, "ada", f"{ALPHA}/projects/2025_new", "move", to=to)
        assert r.status_code == code, (to, r.text)
    assert r.json()["to"] == {"slug": "slow", "name": "Slow"}
    assert _add(planner, "ada", f"{ALPHA}/projects/2015_old", "shred").status_code == 422


def test_a_folder_inside_a_planned_one_counts_once(planner):
    _add(planner, "ada", f"{ALPHA}/projects/2015_old")
    _add(planner, "ada", f"{ALPHA}/projects", "move", to="slow")
    plan = planner.get("/api/plans").json()["labs"][0]
    inner, outer = plan["items"]
    assert inner["inside"] == outer["id"] and outer["inside"] is None
    assert plan["total"] == {"folders": 1, "bytes": 19060}
    here = planner.get("/api/plans/here", params={"fs": "fast", "path": f"{ALPHA}/projects/2015_old/reads"}).json()
    assert here["plannable"] and here["item"] is None and here["inside"]["path"] == f"{ALPHA}/projects"
    assert here["targets"] == [{"slug": "slow", "name": "Slow"}] and here["total"]["folders"] == 1
    login(planner, "ben")
    assert planner.get("/api/plans/here", params={"fs": "fast", "path": f"{ALPHA}/projects"}).json() == {
        "plannable": False, "lab": None}


def test_changing_and_removing_are_audited(planner):
    from sqlalchemy import select

    from pinax.db import session_scope
    from pinax.models import AuditEvent

    item = _add(planner, "ada", f"{ALPHA}/projects/2015_old").json()["id"]
    r = planner.put(f"/api/plans/{item}", json={"action": "compress", "note": "gzip the BAMs? no: CRAM"})
    assert r.status_code == 200 and r.json()["action"] == "compress"
    login(planner, "ben")
    assert planner.delete(f"/api/plans/{item}").status_code == 404  # not his lab's
    login(planner, "ivy")
    assert planner.get("/api/plans", params={"lab": "alpha"}).json()["labs"][0]["total"]["folders"] == 1
    assert planner.delete(f"/api/plans/{item}").status_code == 200
    assert planner.post(f"/api/plans/{item}/moved", json={"accept": True}).status_code == 404
    with session_scope() as s:
        assert [e.action for e in s.scalars(select(AuditEvent).where(AuditEvent.action.like("plan.%"))
                                            .order_by(AuditEvent.id))] == ["plan.added", "plan.changed", "plan.removed"]


def test_a_folder_that_has_not_moved_cannot_be_confirmed_moved(planner):
    item = _add(planner, "ada", f"{ALPHA}/projects/2015_old").json()["id"]
    r = planner.post(f"/api/plans/{item}/moved", json={"accept": True})
    assert r.status_code == 409 and "has not moved" in r.json()["detail"]


# ---------- What a plan saves (C2) ----------

@pytest.fixture
def priced(planner):
    """Fast at 1,000,000 per GB a month (so these tiny folders cost dollars), 2,000 bytes free for every lab;
    Slow at half that, with nothing free."""
    from sqlalchemy import select

    from pinax import costs
    from pinax.db import session_scope
    from pinax.models import Filesystem, LabKind

    with session_scope() as s:
        fast, slow = (s.scalar(select(Filesystem).where(Filesystem.slug == x)) for x in ("fast", "slow"))
        costs.add_price(s, None, fast, "2026-01-01", "1000000", "GB")
        costs.set_free_tier(s, None, fast, "2026-01-01", "0.000002", "GB", kind=LabKind.LAB)
        costs.add_price(s, None, slow, "2026-01-01", "500000", "GB")
    return planner


def _savings(client):
    login(client, "ada")
    return client.get("/api/plans").json()["labs"][0]


def _charge(month: str) -> str:
    from pinax import charges
    from pinax.db import session_scope

    with session_scope() as s:
        out = charges.monthly_charges(s, month)
    (alpha,) = [r for r in out["labs"] if r["lab"]["slug"] == "alpha"]
    return alpha["total"]


def test_what_deleting_saves_is_the_fall_in_the_charge(priced, tmp_path):
    """Before: the Alpha Lab's 19,061 bytes less 2,000 free, at 0.001 a byte. After deleting 2015_old (8,050
    bytes), worked out by the charges themselves once a later scan shows it gone."""
    from datetime import UTC, datetime

    from conftest import FILES
    from scanfix import write_scan

    from pinax.db import session_scope
    from pinax.snapshots import ingest_scan

    _add(priced, "ada", f"{ALPHA}/projects/2015_old")
    plan = _savings(priced)
    (fs,) = plan["savings"]["filesystems"]
    assert (fs["size"], fs["freed"], fs["now"], fs["after"]) == (19061, 8050, "17.06", "9.01")
    assert fs["now"] == _charge("2026-11")  # a whole month after the scan, at its size
    assert plan["savings"]["monthly"] == "8.05" and plan["savings"]["yearly"] == "96.60"
    assert plan["items"][0]["saving"] == {"freed": 8050, "estimate": False, "saves": "8.05"}
    write_scan(tmp_path / "later", ROOT, [f for f in FILES if "/2015_old/" not in f["path"]],
               started=datetime(2026, 10, 20, tzinfo=UTC))
    with session_scope() as s:
        ingest_scan(s, tmp_path / "later", progress=lambda m: None)
    assert _charge("2026-12") == fs["after"]


def test_within_the_free_tier_freeing_saves_nothing(priced):
    from sqlalchemy import select

    from pinax import costs
    from pinax.db import session_scope
    from pinax.models import Filesystem, Lab

    with session_scope() as s:
        fast, alpha = s.scalar(select(Filesystem).where(Filesystem.slug == "fast")), s.scalar(select(Lab).where(Lab.slug == "alpha"))
        costs.set_free_tier(s, None, fast, "2026-02-01", "1", "GB", lab=alpha)
    _add(priced, "ada", f"{ALPHA}/projects/2015_old")
    plan = _savings(priced)
    assert plan["savings"]["monthly"] == "0.00" and plan["savings"]["filesystems"][0]["within_free"]


def test_compressing_is_an_estimate_from_its_types(priced):
    """2025_new holds a 9,000-byte SAM (three quarters freed as BAM), a FASTQ already gzipped, and a note."""
    _add(priced, "ada", f"{ALPHA}/projects/2025_new", "compress")
    plan = _savings(priced)
    assert plan["items"][0]["saving"]["freed"] == 6750 and plan["items"][0]["saving"]["estimate"]
    assert plan["savings"]["estimate"] and plan["savings"]["monthly"] == "6.75"


def test_moving_frees_here_and_costs_there(priced):
    _add(priced, "ada", f"{ALPHA}/projects/2015_old", "move", to="slow")
    s = _savings(priced)["savings"]
    fast, slow = (next(f for f in s["filesystems"] if f["fs"] == k) for k in ("fast", "slow"))
    assert (fast["freed"], slow["added"], slow["now"], slow["after"]) == (8050, 8050, "0.00", "4.03")
    assert s["monthly"] == "4.03"  # 8.05 less on Fast, 4.025 more on Slow: worked out exactly, rounded half up once


def test_nested_folders_are_counted_once_in_the_savings(priced):
    _add(priced, "ada", f"{ALPHA}/projects/2015_old")
    _add(priced, "ada", f"{ALPHA}/projects")
    s = _savings(priced)["savings"]
    assert s["freed"] == 19060 and s["filesystems"][0]["after"] == "0.00"
