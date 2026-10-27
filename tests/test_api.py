# SPDX-License-Identifier: Apache-2.0
"""The HTTP API: who can see what, and that reports answer correctly."""

from conftest import ROOT, login

ALPHA = ROOT + "/alpha_lab"
BETA = ROOT + "/beta_lab"


def test_sign_in_required(client):
    client.cookies.clear()
    assert client.get("/api/tree").status_code == 401
    assert client.get("/api/config").status_code == 200


def test_owner_sees_only_their_folders(client):
    login(client, "ada")
    home = client.get("/api/home").json()
    assert [f["folder"] for f in home["folders"]] == ["alpha_lab"]
    tree = client.get("/api/tree").json()
    assert [t["path"] for t in tree["tiles"]] == [ALPHA]
    assert client.get("/api/tree", params={"fs": "fast", "path": ALPHA + "/projects"}).status_code == 200
    for path in (BETA, ROOT, ROOT + "/alpha_lab2", ROOT + "/orphans/old", "/etc", ALPHA + "/../beta_lab"):
        assert client.get("/api/tree", params={"fs": "fast", "path": path}).status_code == 404, path
        assert client.get("/api/files", params={"fs": "fast", "path": path}).status_code == 404, path
    # Searches without a place cover only the person's folders.
    names = {f["name"] for f in client.get("/api/files", params={"limit": 100}).json()["files"]}
    assert "plate1.tif" not in names and "run1.bam" in names


def test_admin_sees_everything_and_owners_cannot_administer(client):
    login(client, "ben")
    assert client.get("/api/admin/overview").status_code == 403
    assert client.post("/api/admin/folders", json={"fs": "fast", "folder": "orphans", "lab": "beta"}).status_code == 403
    login(client, "ivy")
    tree = client.get("/api/tree", params={"fs": "fast"}).json()
    names = {t["name"] for t in tree["tiles"]}
    assert {"alpha_lab", "beta_lab", "orphans", "Files in this folder"} <= names
    assert client.get("/api/admin/health", params={"fs": "fast"}).json()["errors"]["by_kind"][0]["errno_name"] == "EACCES"


def test_mapping_a_folder_grants_access(client):
    login(client, "ben")
    assert client.get("/api/tree", params={"fs": "fast", "path": ROOT + "/orphans"}).status_code == 404
    login(client, "ivy")
    assert client.post("/api/admin/folders", json={"fs": "fast", "folder": "orphans", "lab": "beta"}).json() == {"ok": True}
    login(client, "ben")
    assert client.get("/api/tree", params={"fs": "fast", "path": ROOT + "/orphans"}).status_code == 200


def test_person_without_labs_sees_nothing(client):
    login(client, "nobody")
    assert client.get("/api/home").json()["folders"] == []
    assert client.get("/api/tree").json()["tiles"] == []


def test_age_and_type_reports(client):
    login(client, "ada")
    t = client.get("/api/tree", params={"fs": "fast", "path": ALPHA + "/projects", "depth": 2}).json()
    tiles = {x["name"]: x for x in t["tiles"]}
    assert tiles["2015_old"]["stats"]["mean_age_days"] > 3900
    assert tiles["2025_new"]["stats"]["bytes"] == 11010
    assert any(c["name"] == "legacy" for c in tiles["2025_new"]["children"])
    types = client.get("/api/types", params={"fs": "fast", "path": ALPHA}).json()
    genomics = next(c for c in types["categories"] if c["key"] == "genomics")
    assert genomics["bytes"] == 5000 + 50 + 3000 + 2000 + 9000
    cold = client.get("/api/cold", params={"fs": "fast", "path": ALPHA, "older": 4}).json()
    assert {d["name"] for d in cold["dirs"]} == {"2015_old", "legacy"}


def test_owner_report_flags_deleted_accounts(client):
    login(client, "ivy")
    owners = {o["uid"]: o for o in client.get("/api/owners", params={"fs": "fast"}).json()["owners"]}
    assert owners[2001]["name"] == "alice" and owners[4000]["known"] is False
    assert owners[2999]["known"] is False  # not in the users table at all


def test_file_search_and_csv(client):
    login(client, "ada")
    res = client.get("/api/files", params={"q": "*.fastq.gz", "sort": "oldest"}).json()
    assert [f["name"] for f in res["files"]] == ["S1_R1.fastq.gz", "S2_R1.fastq.gz"]
    assert res["total"] == {"files": 2, "bytes": 5000, "shown": 2}
    old = client.get("/api/files", params={"fs": "fast", "path": ALPHA, "older": 6}).json()
    assert {f["name"] for f in old["files"]} == {"run1.bam", "run1.bam.bai", "S1_R1.fastq.gz", "raw_2014.sam"}
    csv = client.get("/api/files", params={"category": "genomics", "fmt": "csv"})
    assert csv.headers["content-type"].startswith("text/csv")
    assert len(csv.text.strip().splitlines()) == 1 + 5


def test_history_and_snapshots(client):
    login(client, "ada")
    pts = client.get("/api/history", params={"fs": "fast", "path": ALPHA}).json()["points"]
    assert len(pts) == 1 and pts[0]["bytes"] == 19061
    assert client.get("/api/history", params={"fs": "fast", "path": BETA}).status_code == 404
    login(client, "ivy")
    snaps = client.get("/api/admin/snapshots").json()
    assert snaps[0]["status"] == "ready" and snaps[0]["errors"] == 1
