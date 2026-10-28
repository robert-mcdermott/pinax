# SPDX-License-Identifier: Apache-2.0
"""Labs and departments in the app (A3): adding one, changing its details, and deactivating and reactivating it,
each audited, with the import's rules for slugs and kinds."""

from conftest import login
from sqlalchemy import select


def _events(action: str) -> list:
    from pinax.db import session_scope
    from pinax.models import AuditEvent

    with session_scope() as s:
        return [(e.summary, e.detail, e.actor.username if e.actor else None)
                for e in s.scalars(select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.id))]


def _add(client, **body):
    login(client, "ivy")
    return client.post("/api/admin/labs", json={"slug": "ortiz_v", "name": "Ortiz Lab", **body})


def test_only_administrators_manage_labs(client):
    login(client, "ada")
    assert client.get("/api/admin/labs/alpha").status_code == 403
    assert client.post("/api/admin/labs", json={"slug": "x", "name": "X"}).status_code == 403
    assert client.put("/api/admin/labs/alpha", json={"name": "A"}).status_code == 403
    assert client.post("/api/admin/labs/alpha/deactivate").status_code == 403


def test_a_lab_added_gets_members_and_folders(client):
    r = _add(client, kind="department", division="Public Health Sciences", research_area="Biostatistics")
    assert r.status_code == 201, r.text
    lab = r.json()
    assert (lab["slug"], lab["name"], lab["kind"], lab["active"]) == ("ortiz_v", "Ortiz Lab", "department", True)
    assert lab["members"] == [] and lab["folders"] == [] and lab["created_at"]
    summary, detail, actor = _events("lab.created")[-1]
    assert actor == "ivy" and detail["source"] == "app" and summary == "Added the Ortiz Lab (ortiz_v), a department"
    assert detail["fields"]["division"] == {"before": None, "after": "Public Health Sciences"}
    assert [h["action"] for h in client.get("/api/admin/labs/ortiz_v").json()["history"]] == ["lab.created"]
    # Then, as before: a member, and a folder it owns, which the member then sees.
    assert client.post("/api/admin/labs/ortiz_v/members", json={"username": "nobody", "role": "pi"}).status_code == 200
    assert client.post("/api/admin/folders", json={"fs": "fast", "folder": "orphans", "lab": "ortiz_v"}).status_code == 200
    login(client, "nobody")
    assert [f["folder"] for f in client.get("/api/home").json()["folders"]] == ["orphans"]


def test_adding_follows_the_import_rules(client):
    from pinax.labs import slug_problem

    assert _add(client, slug="alpha").status_code == 409  # taken
    for bad in ("Ortiz", "ortiz v", "-ortiz", "none", "", "x" * 65, "ortiz/v"):
        r = _add(client, slug=bad)
        assert r.status_code == 422 and "is not a usable slug" in r.json()["detail"], bad
        assert slug_problem(bad)
    assert _add(client, name="  ").status_code == 422
    assert _add(client, kind="team").status_code == 422
    assert _add(client, division="x" * 201).status_code == 422
    assert _add(client).json()["kind"] == "lab"  # the default


def test_changing_a_lab(client):
    login(client, "ivy")
    r = client.put("/api/admin/labs/alpha", json={"name": "Alpha Lab", "kind": "lab", "research_area": "Genomics"})
    assert r.status_code == 200 and r.json()["research_area"] == "Genomics"
    summary, detail, _ = _events("lab.updated")[-1]
    assert summary == "Alpha Lab: research area"
    assert detail["fields"] == {"research_area": {"before": "", "after": "Genomics"}}
    before = len(_events("lab.updated"))
    client.put("/api/admin/labs/alpha", json={"name": "Alpha Lab", "research_area": "Genomics"})
    assert len(_events("lab.updated")) == before  # nothing changed, nothing audited
    r = client.put("/api/admin/labs/alpha", json={"name": "Alpha Lab", "kind": "department"})
    assert r.json()["kind"] == "department" and _events("lab.updated")[-1][1]["fields"]["kind"]["after"] == "department"
    assert client.put("/api/admin/labs/alpha", json={"slug": "alpha2", "name": "A"}).status_code == 422
    assert client.put("/api/admin/labs/nowhere", json={"name": "N"}).status_code == 404


def test_deactivating_a_lab_ends_its_members_access_and_keeps_the_rest(client):
    login(client, "ivy")
    r = client.post("/api/admin/labs/alpha/deactivate")
    assert r.status_code == 200 and not r.json()["active"]
    lab = r.json()
    assert [m["username"] for m in lab["members"]] == ["ada"] and [f["folder"] for f in lab["folders"]] == ["alpha_lab"]
    summary, detail, _ = _events("lab.deactivated")[-1]
    assert "no longer see its folders" in summary and detail["members"] == ["Ada (pi)"]
    assert detail["folders"] == ["fast:alpha_lab"]
    assert client.post("/api/admin/labs/alpha/deactivate").status_code == 409  # already
    assert client.put("/api/admin/labs/alpha", json={"name": "A"}).status_code == 409  # reactivate first
    login(client, "ada")
    assert client.get("/api/home").json()["folders"] == []
    # Its folder is still its own, so it is still charged to it.
    from pinax.charges import monthly_charges
    from pinax.db import session_scope, utcnow

    with session_scope() as s:
        out = monthly_charges(s, utcnow().strftime("%Y-%m"))
    assert "alpha" in {r["lab"]["slug"] for r in out["labs"]}
    login(client, "ivy")
    assert client.post("/api/admin/labs/alpha/reactivate").json()["active"]
    assert "see its folders again" in _events("lab.reactivated")[-1][0]
    login(client, "ada")
    assert [f["folder"] for f in client.get("/api/home").json()["folders"]] == ["alpha_lab"]
