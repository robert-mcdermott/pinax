# SPDX-License-Identifier: Apache-2.0
"""Folder tags (T1): the standard tags administrators keep, with a default list added once."""

import pytest
from conftest import ROOT, login

from pinax.tags import DEFAULTS


def _tags(client) -> dict:
    login(client, "ivy")
    r = client.get("/api/admin/tags")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_default_list_is_added_once(client):
    from pinax.db import init_db

    got = _tags(client)["standard"]
    assert [t["key"] for t in got] == [d[0] for d in DEFAULTS]
    hold = next(t for t in got if t["key"] == "legal_hold")
    assert hold["type"] == "flag" and hold["applied_by"] == "admins" and hold["protects"]
    keep = next(t for t in got if t["key"] == "keep_until")
    assert keep["type"] == "date" and keep["protects"] and keep["applied_by"] == "owners"
    assert next(t for t in got if t["key"] == "stage")["choices"][0] == "raw"
    client.delete(f"/api/admin/tags/{keep['id']}")  # a site removes one: it stays removed
    init_db()
    assert "keep_until" not in [t["key"] for t in _tags(client)["standard"]]


def test_administrators_add_and_change_standard_tags(client):
    login(client, "ivy")
    r = client.post("/api/admin/tags", json={"label": "Instrument", "type": "choice", "choices": ["Krios G4", " Glacios ", "Krios G4"]})
    assert r.status_code == 200, r.text
    tag = r.json()
    assert tag["key"] == "instrument" and tag["choices"] == ["Krios G4", "Glacios"]
    assert client.post("/api/admin/tags", json={"label": "instrument!", "type": "text"}).status_code == 409
    for body in ({"label": "Empty", "type": "choice", "choices": []}, {"label": "Odd", "type": "colour"},
                 {"label": "Guard", "type": "text", "protects": True}, {"label": "!!", "type": "flag"},
                 {"label": "Who", "type": "flag", "applied_by": "anyone"}):
        assert client.post("/api/admin/tags", json=body).status_code == 422, body
    changed = client.put(f"/api/admin/tags/{tag['id']}", json={"choices": ["Glacios", "Krios G4", "Talos"],
                                                               "applied_by": "admins", "label": "Microscope"}).json()
    assert changed["choices"] == ["Glacios", "Krios G4", "Talos"] and changed["applied_by"] == "admins"
    assert changed["label"] == "Microscope" and changed["key"] == "instrument"  # the key stays


def test_a_tag_in_use_is_retired_not_deleted(client):
    from sqlalchemy import select

    from pinax import marks
    from pinax.db import session_scope
    from pinax.models import AuditEvent, Filesystem, FolderTag, Tag
    from pinax.snapshots import cold_bucket

    stage = next(t for t in _tags(client)["standard"] if t["key"] == "stage")
    with session_scope() as s:
        mark = marks.mark_folder(s, s.scalar(select(Filesystem)), f"{ROOT}/alpha_lab/projects", cold_from=cold_bucket())
        s.add(FolderTag(tag_id=stage["id"], mark_id=mark.id, value="raw"))
    r = client.put(f"/api/admin/tags/{stage['id']}", json={"choices": ["processed"]})
    assert r.status_code == 409 and "raw" in r.json()["detail"]
    assert client.delete(f"/api/admin/tags/{stage['id']}").status_code == 409
    assert client.post(f"/api/admin/tags/{stage['id']}/active", json={"active": False}).json()["active"] is False
    listed = next(t for t in _tags(client)["standard"] if t["key"] == "stage")
    assert not listed["active"] and listed["uses"] == 1
    assert client.post(f"/api/admin/tags/{stage['id']}/active", json={"active": True}).json()["active"]
    with session_scope() as s:
        assert s.scalar(select(Tag).where(Tag.key == "stage")) is not None
        actions = [e.action for e in s.scalars(select(AuditEvent).where(AuditEvent.action.like("tag.%")))]
    assert actions == ["tag.retired", "tag.reactivated"]


def test_only_administrators_keep_the_list(client):
    login(client, "ada")
    assert client.get("/api/admin/tags").status_code == 403
    assert client.post("/api/admin/tags", json={"label": "Mine", "type": "flag"}).status_code == 403


@pytest.mark.parametrize(("kind", "value", "kept"), [("flag", "anything", ""), ("date", " 2031-01-31 ", "2031-01-31"),
                                                      ("text", " R01-CA123456 ", "R01-CA123456")])
def test_values_are_kept_by_type(kind, value, kept):
    from pinax.models import Tag
    from pinax.tags import valid_value

    assert valid_value(Tag(label="T", value_type=kind, choices=[]), value) == kept


def test_values_that_do_not_fit_are_refused():
    from pinax.models import Tag
    from pinax.tags import TagError, valid_value

    for tag, value in ((Tag(label="When", value_type="date", choices=[]), "31/01/2031"),
                       (Tag(label="Stage", value_type="choice", choices=["raw"]), "cooked"),
                       (Tag(label="Grant", value_type="text", choices=[]), "  ")):
        with pytest.raises(TagError):
            valid_value(tag, value)


# ---------- Tagging folders (T2) ----------

ALPHA = f"{ROOT}/alpha_lab"


def _id(client, key: str) -> int:
    login(client, "ivy")
    tags = _tags(client)
    return next(t["id"] for t in tags["standard"] + tags["custom"] if t["key"] == key)


def _tag(client, user: str, path: str, tag: int, value=None):
    login(client, user)
    return client.post("/api/tags/folders", json={"fs": "fast", "path": path, "tag": tag, "value": value})


def _here(client, user: str, path: str) -> dict:
    login(client, user)
    return client.get("/api/tags/here", params={"fs": "fast", "path": path}).json()


def test_a_pi_tags_a_folder_and_what_is_below_inherits_it(client):
    project = _id(client, "project")
    r = _tag(client, "ada", f"{ALPHA}/projects", project, "Atlas")
    assert r.status_code == 200, r.text
    (own,) = r.json()["tags"]
    assert own["value"] == "Atlas" and own["from"] is None and own["can_remove"] and own["added_by"] == "Ada"
    below = _here(client, "ada", f"{ALPHA}/projects/2015_old/reads")
    (inherited,) = below["tags"]
    assert inherited["from"] == f"{ALPHA}/projects" and not inherited["can_remove"]
    assert _tag(client, "ada", f"{ALPHA}/projects", project, "Atlas").status_code == 409
    assert _tag(client, "ada", f"{ALPHA}/projects", project, "Cohort").status_code == 200  # more than one
    login(client, "ada")
    tagged = client.get("/api/tags/below", params={"fs": "fast", "path": ALPHA}).json()["tagged"]
    assert sorted(tagged[f"{ALPHA}/projects"]) == ["Project: Atlas", "Project: Cohort"]


def test_who_may_tag_what(client):
    hold, stage = _id(client, "legal_hold"), _id(client, "stage")
    assert _tag(client, "ada", f"{ALPHA}/projects", hold).status_code == 403  # administrators only
    assert _tag(client, "ivy", f"{ALPHA}/projects", hold).status_code == 200
    assert _tag(client, "ben", f"{ALPHA}/projects", stage, "raw").status_code == 404  # not his lab's
    assert _tag(client, "ada", f"{ALPHA}/projects", stage, "cooked").status_code == 422
    assert _tag(client, "ada", ROOT, stage, "raw").status_code == 404
    assert _tag(client, "ada", f"{ALPHA}/projects", stage, "raw").status_code == 200
    replaced = _tag(client, "ada", f"{ALPHA}/projects", stage, "processed").json()["tags"]
    assert [t["value"] for t in replaced if t["tag"]["key"] == "stage"] == ["processed"]  # one value: replaced
    login(client, "ivy")
    client.post(f"/api/admin/tags/{stage}/active", json={"active": False})
    assert _tag(client, "ada", f"{ALPHA}/projects/2025_new", stage, "raw").status_code == 403  # retired
    available = {t["key"] for t in _here(client, "ada", f"{ALPHA}/projects")["available"]}
    assert "stage" not in available and "legal_hold" not in available and "project" in available


def test_a_labs_own_tag_and_merging_it(client):
    login(client, "ada")
    made = client.post("/api/tags/custom", json={"lab": "alpha", "label": "Atlas project"})
    assert made.status_code == 200, made.text
    atlas = made.json()["id"]
    assert client.post("/api/tags/custom", json={"lab": "alpha", "label": "Atlas project"}).status_code == 409
    assert client.post("/api/tags/custom", json={"lab": "beta", "label": "Mine"}).status_code == 404
    assert _tag(client, "ada", f"{ALPHA}/projects", atlas).status_code == 200
    assert _tag(client, "ben", f"{ROOT}/beta_lab/imaging", atlas).status_code == 403  # another lab's tag
    assert "atlas_project" not in {t["key"] for t in _here(client, "ben", f"{ROOT}/beta_lab/imaging")["available"]}
    login(client, "ivy")
    project = _id(client, "project")
    assert client.post(f"/api/admin/tags/{atlas}/merge", json={"into": project}).json() == {"folders": 1}
    (tag,) = _here(client, "ada", f"{ALPHA}/projects")["tags"]
    assert tag["tag"]["key"] == "project" and tag["value"] == "Atlas project"
    assert not next(t for t in _tags(client)["custom"] if t["id"] == atlas)["active"]


def test_untagging_and_the_audit(client):
    from sqlalchemy import select

    from pinax.db import session_scope
    from pinax.models import AuditEvent

    grant = _id(client, "grant")
    ft = _tag(client, "ada", f"{ALPHA}/projects", grant, "R01-CA000001").json()["tags"][0]["id"]
    login(client, "ben")
    assert client.delete(f"/api/tags/folders/{ft}").status_code == 404
    login(client, "ada")
    assert client.delete(f"/api/tags/folders/{ft}").json()["tags"] == []
    with session_scope() as s:
        assert [e.action for e in s.scalars(select(AuditEvent).where(AuditEvent.action.like("folder.%")))] == [
            "folder.tagged", "folder.untagged"]


def test_tags_in_bulk_from_the_command_line(client, tmp_path, monkeypatch, capsys):
    import sys

    from pinax import cli

    good = tmp_path / "tags.csv"
    good.write_text("filesystem,path,tag,value\nfast,alpha_lab/projects,project,Atlas\n"
                    f"fast,{ALPHA}/projects/2015_old,keep_until,2031-01-31\n")
    bad = tmp_path / "bad.csv"
    bad.write_text(good.read_text() + "fast,alpha_lab/projects,stage,cooked\nfast,nowhere,project,X\n")
    for file, flags, code in ((good, [], 0), (bad, ["--apply"], 1)):
        monkeypatch.setattr(sys, "argv", ["pinax", "tags", "import", str(file), *flags])
        with pytest.raises(SystemExit) as done:
            cli.main()
        assert done.value.code == code
    out = capsys.readouterr()
    assert "Would tag line 2" in out.out and "Refused line 4" in out.err and "Refused line 5" in out.err
    assert _here(client, "ada", f"{ALPHA}/projects")["tags"] == []  # a dry run, then nothing when a row is refused
    monkeypatch.setattr(sys, "argv", ["pinax", "tags", "import", str(good), "--apply"])
    with pytest.raises(SystemExit):
        cli.main()
    keep = _here(client, "ada", f"{ALPHA}/projects/2015_old")["tags"]
    assert {(t["tag"]["key"], t["value"]) for t in keep} == {("project", "Atlas"), ("keep_until", "2031-01-31")}


# ---------- Finding data by tag (T3) ----------

def _collection(client, user: str, *want: str) -> dict:
    login(client, user)
    r = client.get("/api/tags/collection", params=[("want", w) for w in want])
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def tagged(client):
    project, stage = _id(client, "project"), _id(client, "stage")
    _tag(client, "ada", f"{ALPHA}/projects", project, "Atlas")
    _tag(client, "ada", f"{ALPHA}/projects/2015_old", stage, "raw")
    _tag(client, "ada", f"{ALPHA}/projects/2015_old/reads", project, "Atlas")  # inside one already tagged Atlas
    _tag(client, "ben", f"{ROOT}/beta_lab", project, "Atlas")
    _tag(client, "ben", f"{ROOT}/beta_lab/imaging", stage, "raw")
    return client, project, stage


def test_a_collection_is_the_outermost_folders_with_the_tag(tagged):
    client, project, _stage = tagged
    c = _collection(client, "ivy", f"{project}:Atlas")
    assert [(f["path"], f["lab"]["slug"]) for f in c["folders"]] == [(f"{ALPHA}/projects", "alpha"), (f"{ROOT}/beta_lab", "beta")]
    assert c["total"]["bytes"] == 19060 + 9005 and c["total"]["folders"] == 2
    assert "Project: Atlas" in c["folders"][0]["tags"]


def test_tags_combine_and_are_inherited(tagged):
    client, project, stage = tagged
    both = _collection(client, "ivy", f"{project}:Atlas", f"{stage}:raw")
    assert [f["path"] for f in both["folders"]] == [f"{ALPHA}/projects/2015_old", f"{ROOT}/beta_lab/imaging"]
    assert sorted(both["folders"][0]["tags"]) == ["Data stage: raw", "Project: Atlas"]
    assert [f["path"] for f in _collection(client, "ivy", str(stage))["folders"]] == [
        f"{ALPHA}/projects/2015_old", f"{ROOT}/beta_lab/imaging"]  # any value
    assert _collection(client, "ivy", f"{project}:Nothing")["folders"] == []


def test_a_collection_holds_only_what_the_person_may_see(tagged):
    client, project, _ = tagged
    assert [f["lab"]["slug"] for f in _collection(client, "ada", f"{project}:Atlas")["folders"]] == ["alpha"]
    assert _collection(client, "nobody", f"{project}:Atlas")["folders"] == []
    login(client, "ada")
    values = {t["key"]: t["values"] for t in client.get("/api/tags/filters").json()["tags"]}
    assert values["project"] == ["Atlas"] and values["stage"] == ["raw"] and "legal_hold" in values


def test_what_a_collection_costs_is_its_share_of_the_labs_charge(tagged):
    from sqlalchemy import select

    from pinax import costs
    from pinax.db import session_scope
    from pinax.models import Filesystem

    client, project, _ = tagged
    with session_scope() as s:
        costs.add_price(s, None, s.scalar(select(Filesystem)), "2026-01-01", "1000000", "GB")
    alpha = _collection(client, "ada", f"{project}:Atlas")
    # The Alpha Lab's 19,061 bytes cost 19.061 a month; projects holds 19,060 of them.
    assert alpha["folders"][0]["monthly"] == "19.06" and alpha["total"]["monthly"] == "19.06"


def test_a_collection_has_a_history(tagged, tmp_path):
    from datetime import UTC, datetime

    from conftest import FILES
    from scanfix import write_scan

    from pinax.db import session_scope
    from pinax.snapshots import ingest_scan

    client, project, _ = tagged
    write_scan(tmp_path / "later", ROOT, FILES + [{"path": "alpha_lab/projects/new/more.bam", "size": 1000, "age_days": 1}],
               started=datetime(2026, 10, 20, tzinfo=UTC))
    with session_scope() as s:
        ingest_scan(s, tmp_path / "later", progress=lambda m: None)
    c = _collection(client, "ivy", f"{project}:Atlas")
    assert [p["bytes"] for p in c["points"]] == [28065, 29065]
    assert c["forecast"]["ok"] is False and c["forecast"]["why"] == "scans"
