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
