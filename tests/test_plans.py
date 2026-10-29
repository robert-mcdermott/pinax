# SPDX-License-Identifier: Apache-2.0
"""Cleanup plans (C1): a lab's PI, delegates, and administrators add folders to the lab's plan, to delete, move,
or compress; others' folders cannot be planned; a folder inside a planned one counts once; every change audited."""

import zlib

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


# ---------- Progress from later scans (C3) ----------

def _later(tmp_path, name: str, files: list[dict], inodes: dict | None = None):
    from datetime import UTC, datetime

    from scanfix import write_scan

    from pinax.db import session_scope
    from pinax.snapshots import ingest_scan

    # Directories keep inodes of their own across this scan's changes (in the first scan they are by position).
    dirs = {"/".join(f["path"].split("/")[:i]) for f in files for i in range(1, f["path"].count("/") + 1)}
    stable = {d: 1_000_000 + zlib.crc32(d.encode()) for d in dirs}
    write_scan(tmp_path / name, ROOT, files, started=datetime(2026, 10, 20, tzinfo=UTC), inodes=stable | (inodes or {}))
    with session_scope() as s:
        ingest_scan(s, tmp_path / name, progress=lambda m: None)


def _files(sam_as_bam: int | None = None, drop_old: bool = False, rename_old: bool = False) -> list[dict]:
    from conftest import FILES

    out = []
    for f in FILES:
        p = f["path"]
        if drop_old and "/2015_old/" in p:
            continue
        if p.endswith("raw_2014.sam") and sam_as_bam is not None:
            f = dict(f, path=p.replace(".sam", ".bam"), size=sam_as_bam)
        if rename_old:
            f = dict(f, path=f["path"].replace("/2015_old/", "/2015_archive/"))
        out.append(f)
    return out


def _progress(client) -> dict[str, dict]:
    plan = _savings(client)
    return {i["name"]: i["progress"] for i in plan["items"]} | {"_done": plan["savings"]["done"]}


def test_done_and_what_it_saves(priced, tmp_path):
    _add(priced, "ada", f"{ALPHA}/projects/2015_old")
    item = _add(priced, "ada", f"{ALPHA}/projects/2025_new", "compress").json()
    assert item["estimate"] == 6750 and item["progress"] == {"status": "to do", "freed": 0, "since": None}
    _later(tmp_path, "s1", _files(sam_as_bam=2250, drop_old=True))  # the SAM made a BAM a quarter its size
    p = _progress(priced)
    assert p["2015_old"]["status"] == "done" and p["2015_old"]["freed"] == 8050
    assert p["2015_old"]["since"].startswith("2026-10-20")
    assert p["2025_new"]["status"] == "done" and p["2025_new"]["freed"] == 6750
    assert p["_done"] == {"freed": 14800, "monthly": "14.80"}
    s = _savings(priced)["savings"]
    assert s["freed"] == 0 and s["monthly"] == "0.00"  # nothing left to free


def test_partly_done_and_moved(priced, tmp_path):
    from sqlalchemy import select

    from pinax.db import session_scope
    from pinax.models import MarkedFolder

    _add(priced, "ada", f"{ALPHA}/projects/2015_old")
    _add(priced, "ada", f"{ALPHA}/projects/2025_new", "compress")
    with session_scope() as s:
        ino = s.scalar(select(MarkedFolder.ino).where(MarkedFolder.path.endswith("2015_old")))
    _later(tmp_path, "s1", _files(sam_as_bam=5000, rename_old=True), inodes={"alpha_lab/projects/2015_archive": ino})
    p = _progress(priced)
    assert p["2025_new"] == {"status": "partly done", "freed": 4000, "since": p["2025_new"]["since"]}
    assert p["2015_old"] == {"status": "moved", "freed": 0, "since": None}  # renamed: nothing freed
    assert p["_done"]["freed"] == 4000


# ---------- Suggestions (C4) ----------

WORKFLOW_FILES = [
    {"path": "alpha_lab/pipelines/run1/.nextflow/history", "size": 10, "age_days": 30},
    {"path": "alpha_lab/pipelines/run1/work/ab/12cd/sample.bam", "size": 6000, "age_days": 30},
    {"path": "alpha_lab/pipelines/run1/work/ef/34ab/sample.sam", "size": 4000, "age_days": 30},
    {"path": "alpha_lab/pipelines/run2/.snakemake/log/run.log", "size": 500, "age_days": 40},
    {"path": "alpha_lab/tools/miniconda3/pkgs/numpy-1.26.tar.bz2", "size": 3000, "age_days": 200},
    {"path": "alpha_lab/home/.singularity/cache/oci/blob1", "size": 2500, "age_days": 100},
    {"path": "alpha_lab/notes/work/ideas.txt", "size": 5, "age_days": 1},  # a "work" folder, not Nextflow's
    {"path": "alpha_lab/sequencing/run7/S7_R1.fastq", "size": 8000, "age_days": 10},
]


@pytest.fixture
def workflows(env, tmp_path):
    from conftest import FILES
    from fastapi.testclient import TestClient
    from scanfix import write_scan

    from pinax.db import session_scope
    from pinax.main import app
    from pinax.models import Filesystem, Lab, LabFolder, LabMembership, User
    from pinax.snapshots import ingest_scan

    with session_scope() as s:
        fs = Filesystem(slug="fast", name="Fast", root=ROOT)
        alpha, ada = Lab(slug="alpha", name="Alpha Lab"), User(username="ada", display_name="Ada", email="ada@example.org")
        s.add_all([fs, alpha, ada, User(username="ben", display_name="Ben", email="ben@example.org")])
        s.flush()
        s.add_all([LabFolder(lab_id=alpha.id, filesystem_id=fs.id, path="alpha_lab"),
                   LabMembership(user_id=ada.id, lab_id=alpha.id, role="pi")])
    write_scan(tmp_path / "scan", ROOT, FILES + WORKFLOW_FILES)
    with session_scope() as s:
        ingest_scan(s, tmp_path / "scan", progress=lambda m: None)
    with TestClient(app) as c:
        yield c


def test_the_index_finds_workflow_working_folders(workflows):
    from pinax.db import session_scope
    from pinax.snapshots import indexes, latest_snapshot

    with session_scope() as s:
        from pinax.models import Filesystem

        idx = indexes.get(latest_snapshot(s, s.query(Filesystem).one()))
        rows = idx.work_dirs(ALPHA)
    assert {(r["path"].removeprefix(ALPHA + "/"), r["kind"], r["bytes"]) for r in rows} == {
        ("pipelines/run1/work", "nextflow", 10000), ("pipelines/run2/.snakemake", "snakemake", 500),
        ("tools/miniconda3/pkgs", "conda", 3000), ("home/.singularity/cache", "containers", 2500)}


def test_suggestions_for_a_lab(workflows):
    login(workflows, "ada")
    got = workflows.get("/api/plans/suggestions", params={"lab": "alpha"}).json()["suggestions"]
    by = {(s["kind"], s["path"].removeprefix(ALPHA + "/")): s for s in got}
    assert by[("workflow", "pipelines/run1/work")]["what"] == "Nextflow work folder"
    assert by[("workflow", "pipelines/run1/work")]["action"] == "delete"
    assert not any(p.startswith("notes/") for _, p in by)  # not a Nextflow work folder
    fastq = by[("compress", "sequencing/run7")]
    assert fastq["freed"] == 6000 and fastq["estimate"] and fastq["action"] == "compress"
    assert by[("cold", "projects/2015_old")]["freed"] == 8050  # cold for ten years: one unit
    # The 2014 SAM could be compressed (6,750 freed) or, being cold, moved or deleted (9,000): the larger.
    assert by[("cold", "projects/2025_new/legacy")]["freed"] == 9000 and ("compress", "projects/2025_new/legacy") not in by
    # The work folder's BAM and SAM compress too, but the work folder itself is suggested.
    assert not any(k == "compress" and p.startswith("pipelines/") for k, p in by)
    # Planned folders, and folders inside them, are no longer suggested.
    workflows.post("/api/plans", json={"fs": "fast", "path": f"{ALPHA}/pipelines", "action": "delete"})
    again = workflows.get("/api/plans/suggestions", params={"lab": "alpha"}).json()["suggestions"]
    assert not any(s["path"].startswith(f"{ALPHA}/pipelines") for s in again)
    login(workflows, "ben")
    assert workflows.get("/api/plans/suggestions", params={"lab": "alpha"}).status_code == 404


# ---------- Every lab's plan (C5) ----------

def test_administrators_see_every_plan(priced, tmp_path):
    _add(priced, "ada", f"{ALPHA}/projects/2015_old")
    _add(priced, "ada", f"{ALPHA}/projects/2025_new", "compress")
    _add(priced, "ben", f"{ROOT}/beta_lab/archive", "move", to="slow")
    _later(tmp_path, "s1", _files(drop_old=True))
    login(priced, "ada")
    assert priced.get("/api/plans/all").status_code == 403
    login(priced, "ivy")
    every = priced.get("/api/plans/all").json()
    assert [p["lab"]["slug"] for p in every["labs"]] == ["alpha", "beta"]
    fast = next(f for f in every["filesystems"] if f["fs"] == "fast")
    assert fast["freed"] == 8050  # 2015_old, done
    assert fast["to_free"] == 6750 + 8000  # the compression estimate, and the archive to move
    assert next(f for f in every["filesystems"] if f["fs"] == "slow")["moved_in"] == 8000
    assert [i["name"] for i in every["largest"]] == ["archive", "2025_new"]  # by what each would free
    assert every["done"]["freed"] == 8050


# ---------- Protection (T5) ----------

def _protect(client, user: str, path: str, key: str, value=None):
    login(client, "ivy")
    tag = next(t["id"] for t in client.get("/api/admin/tags").json()["standard"] if t["key"] == key)
    login(client, user)
    r = client.post("/api/tags/folders", json={"fs": "fast", "path": path, "tag": tag, "value": value})
    assert r.status_code == 200, r.text


def test_protected_folders_cannot_be_planned(planner):
    _protect(planner, "ada", f"{ALPHA}/projects/2015_old", "keep_until", "2099-01-01")
    for path, says in ((f"{ALPHA}/projects/2015_old", "It is kept until 2099-01-01"),
                       (f"{ALPHA}/projects/2015_old/reads", "It is inside"),
                       (f"{ALPHA}/projects", "It holds")):
        r = _add(planner, "ada", path)
        assert r.status_code == 409 and r.json()["detail"].startswith(says), (path, r.json())
    assert _add(planner, "ada", f"{ALPHA}/projects/2025_new").status_code == 200  # beside it, not in it
    here = planner.get("/api/plans/here", params={"fs": "fast", "path": f"{ALPHA}/projects/2015_old"}).json()
    assert here["protected"]["tag"] == "Keep until"


def test_a_date_past_protects_nothing_and_legal_hold_always(planner):
    _protect(planner, "ada", f"{ALPHA}/projects/2015_old", "keep_until", "2020-01-01")
    assert _add(planner, "ada", f"{ALPHA}/projects/2015_old").status_code == 200
    _protect(planner, "ivy", f"{ROOT}/beta_lab", "legal_hold")
    r = _add(planner, "ben", f"{ROOT}/beta_lab/imaging")
    assert r.status_code == 409 and "legal hold" in r.json()["detail"]


def test_a_planned_folder_protected_later_is_flagged(planner):
    _add(planner, "ada", f"{ALPHA}/projects/2015_old")
    _protect(planner, "ivy", f"{ALPHA}/projects", "legal_hold")
    login(planner, "ada")
    (item,) = planner.get("/api/plans").json()["labs"][0]["items"]
    assert item["protected"]["where"] == "above" and "legal hold" in item["protected"]["why"]


def test_suggestions_leave_protected_folders_out(planner):
    login(planner, "ada")
    before = {s["path"] for s in planner.get("/api/plans/suggestions", params={"lab": "alpha"}).json()["suggestions"]}
    assert f"{ALPHA}/projects/2015_old" in before
    _protect(planner, "ada", f"{ALPHA}/projects/2015_old", "keep_until", "2099-01-01")
    login(planner, "ada")
    after = {s["path"] for s in planner.get("/api/plans/suggestions", params={"lab": "alpha"}).json()["suggestions"]}
    assert f"{ALPHA}/projects/2015_old" not in after
