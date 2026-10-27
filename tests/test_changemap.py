# SPDX-License-Identifier: Apache-2.0
"""The change map (E8): a folder's children in this scan and an earlier one, against brute force over the two
scans' files, both from the stored change since the previous scan and from an older scan joined when asked."""

from datetime import UTC, datetime, timedelta

import pytest
from conftest import FILES, ROOT
from scanfix import write_scan
from test_history import FAST_2, FAST_3

T1 = datetime(2026, 7, 1, 8, tzinfo=UTC)
SCANS = [(T1, FILES), (T1 + timedelta(days=30), FAST_2), (T1 + timedelta(days=60), FAST_3)]


@pytest.fixture
def client(env, tmp_path):
    """Three monthly scans of one filesystem, all kept: a folder appears in the second, one is deleted in the
    third. Ada's lab owns alpha_lab; Ivy is an administrator."""
    from fastapi.testclient import TestClient

    from pinax.db import session_scope
    from pinax.main import app
    from pinax.models import Filesystem, Lab, LabFolder, LabMembership, User
    from pinax.snapshots import ingest_scan

    with session_scope() as s:
        fs = Filesystem(slug="fast", name="Fast", root=ROOT)
        alpha = Lab(slug="alpha", name="Alpha Lab")
        ada = User(username="ada", display_name="Ada", email="ada@example.org")
        s.add_all([fs, alpha, ada, User(username="ivy", display_name="Ivy", email="ivy@example.org", is_admin=True)])
        s.flush()
        s.add_all([LabFolder(lab_id=alpha.id, filesystem_id=fs.id, path="alpha_lab"),
                   LabMembership(user_id=ada.id, lab_id=alpha.id, role="pi")])
    for i, (started, files) in enumerate(SCANS):
        write_scan(tmp_path / f"s{i}", ROOT, files, started=started)
        with session_scope() as s:
            ingest_scan(s, tmp_path / f"s{i}", fs_slug="fast", progress=lambda m: None)
    with TestClient(app) as c:
        yield c


def _get(client, user: str, **params) -> dict:
    client.cookies.set("pinax_user", user)
    r = client.get("/api/changes", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _sums(files: list[dict], prefix: str) -> dict[str, dict]:
    """Per child folder of `prefix` ("" for the root), and "" for the files directly in it: bytes, files, and
    mean age."""
    out: dict[str, list] = {}
    for f in files:
        if prefix and not f["path"].startswith(prefix + "/"):
            continue
        rel = f["path"][len(prefix) + 1:] if prefix else f["path"]
        key = rel.split("/")[0] if "/" in rel else ""
        o = out.setdefault(key, [0, 0, 0.0])
        o[0], o[1], o[2] = o[0] + f["size"], o[1] + 1, o[2] + f["size"] * f["age_days"]
    return {k: {"bytes": b, "files": n, "mean_age_days": w / b if b else None} for k, (b, n, w) in out.items()}


def _check(tiles: list[dict], old: list[dict], new: list[dict], prefix: str) -> None:
    then, now = _sums(old, prefix), _sums(new, prefix)
    got = {("" if t["kind"] == "files" else t["name"]): t for t in tiles if t["kind"] in ("dir", "files")}
    assert set(got) == set(then) | set(now)
    for name, t in got.items():
        for side, want in (("then", then.get(name)), ("now", now.get(name))):
            if want is None:
                assert t[side] is None, (name, side)
                continue
            assert (t[side]["bytes"], t[side]["files"]) == (want["bytes"], want["files"]), (name, side)
            assert t[side]["mean_age_days"] == pytest.approx(want["mean_age_days"], abs=0.02), (name, side)
        if t["kind"] == "dir":
            assert (t["in_old"], t["in_new"]) == (name in then, name in now), name


@pytest.mark.parametrize("since", [None, "2026-07-01"], ids=["previous scan", "an older scan"])
def test_children_in_both_scans_match_brute_force(client, since):
    """The previous scan comes from the stored changes; an older one from its index, attached and joined."""
    old = FAST_2 if since is None else FILES
    query = {"since": since} if since else {}
    root = _get(client, "ivy", fs="fast", **query)
    assert [c["previous"] for c in root["compared"]] == [since is None]
    _check(root["tiles"], old, FAST_3, "")
    assert root["node"]["now"]["bytes"] == sum(f["size"] for f in FAST_3)
    assert root["node"]["then"]["bytes"] == sum(f["size"] for f in old)
    for folder in ("alpha_lab", "alpha_lab/projects", "beta_lab", "gamma_lab"):
        res = _get(client, "ivy", fs="fast", path=f"{ROOT}/{folder}", **query)
        _check(res["tiles"], old, FAST_3, folder)


def test_new_and_removed_folders(client):
    gamma = next(t for t in _get(client, "ivy", fs="fast", since="2026-07-01")["tiles"] if t["name"] == "gamma_lab")
    assert gamma["in_new"] and not gamma["in_old"] and gamma["then"] is None
    beta = _get(client, "ivy", fs="fast", path=f"{ROOT}/beta_lab")
    imaging = next(t for t in beta["tiles"] if t["name"] == "imaging")
    assert imaging["in_old"] and not imaging["in_new"] and imaging["now"] is None
    assert beta["node"]["subfolders"] == {"n": 3, "new": 0, "removed": 1}
    # A removed folder can be opened: everything in it is in the earlier scan only.
    gone = _get(client, "ivy", fs="fast", path=f"{ROOT}/beta_lab/imaging")
    assert gone["node"]["in_old"] and not gone["node"]["in_new"] and gone["node"]["now"] is None
    _check(gone["tiles"], FAST_2, FAST_3, "beta_lab/imaging")
    client.cookies.set("pinax_user", "ivy")
    assert client.get("/api/changes", params={"fs": "fast", "path": f"{ROOT}/no_such"}).status_code == 404


def test_folding_and_two_levels_match_brute_force(client, monkeypatch):
    """With room for one child and one grandchild each, the rest fold into "smaller folders" with exact sums."""
    from pinax import explore

    monkeypatch.setattr(explore, "TILE_LIMIT", 1)
    monkeypatch.setattr(explore, "GRAND_LIMIT", 1)

    def folded(tiles: list[dict], old: list[dict], prefix: str) -> str:
        then, now = _sums(old, prefix), _sums(FAST_3, prefix)
        listed = next(t["name"] for t in tiles if t["kind"] == "dir")
        other = next(t for t in tiles if t["kind"] == "other")
        rest = (set(then) | set(now)) - {"", listed}
        assert other["count"] == len(rest)
        assert other["now"]["bytes"] == sum(now[k]["bytes"] for k in rest if k in now)
        assert other["then"]["bytes"] == sum(then[k]["bytes"] for k in rest if k in then)
        return listed

    for since, old in ((None, FAST_2), ("2026-07-01", FILES)):
        query = {"since": since} if since else {}
        root = _get(client, "ivy", fs="fast", **query)
        assert folded(root["tiles"], old, "") == "alpha_lab"  # the largest by the larger of then and now
        alpha = _get(client, "ivy", fs="fast", path=f"{ROOT}/alpha_lab", depth=2, **query)
        projects = next(t for t in alpha["tiles"] if t["kind"] == "dir")
        assert folded(projects["children"], old, "alpha_lab/projects") == "2025_new"


def test_sizing_and_access(client):
    by = {m: [t["name"] for t in _get(client, "ivy", fs="fast", path=f"{ROOT}/beta_lab", metric=m)["tiles"]]
          for m in ("max", "now", "then")}
    assert "imaging" in by["max"] and "imaging" in by["then"] and "imaging" not in by["now"]  # nothing now: not drawn
    client.cookies.set("pinax_user", "ada")
    assert client.get("/api/changes", params={"fs": "fast", "path": f"{ROOT}/beta_lab"}).status_code == 404
    assert client.get("/api/changes", params={"fs": "fast", "metric": "bogus"}).status_code == 422
    home = _get(client, "ada", depth=2)
    assert [t["name"] for t in home["tiles"]] == ["alpha_lab"] and home["choices"] == ["2026-07-31", "2026-07-01"]
    _check(home["tiles"][0]["children"], FAST_2, FAST_3, "alpha_lab")
