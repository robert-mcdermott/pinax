# SPDX-License-Identifier: Apache-2.0
"""Isolated environments: every test gets its own catalog, snapshot folder, and scans."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from scanfix import write_scan

ROOT = "/research/fast"
DAY = 1

# A small filesystem: two labs, an unmapped folder, a file in the root, and an unreadable directory.
FILES = [
    {"path": "alpha_lab/projects/2015_old/run1.bam", "size": 5000, "age_days": 4000, "uid": 2001},
    {"path": "alpha_lab/projects/2015_old/run1.bam.bai", "size": 50, "age_days": 4000, "uid": 2001},
    {"path": "alpha_lab/projects/2015_old/reads/S1_R1.fastq.gz", "size": 3000, "age_days": 3900, "uid": 2002},
    {"path": "alpha_lab/projects/2025_new/S2_R1.fastq.gz", "size": 2000, "age_days": 20, "uid": 2001},
    {"path": "alpha_lab/projects/2025_new/notes 100%_done.txt", "size": 10, "age_days": 5, "uid": 2001},
    {"path": "alpha_lab/projects/2025_new/legacy/raw_2014.sam", "size": 9000, "age_days": 4500, "uid": 2999},
    {"path": "alpha_lab/README", "size": 1, "age_days": 100, "uid": 2001},
    {"path": "alpha_lab2/data/brain.nii.gz", "size": 700, "age_days": 800, "uid": 3001},
    {"path": "beta_lab/imaging/plate1.tif", "size": 400, "age_days": 1200, "atime_days": 2, "uid": 3001},
    {"path": "beta_lab/imaging/plate2.tif", "size": 600, "age_days": 1300, "uid": 3001},
    {"path": "beta_lab/archive/backup.tar.gz", "size": 8000, "age_days": 3000, "uid": 3002},
    {"path": "beta_lab/locked/visible.csv", "size": 5, "age_days": 10, "uid": 3001},
    {"path": "orphans/old/thing.dat", "size": 123, "age_days": 6000, "uid": 4000},
    {"path": "root_file.txt", "size": 7, "age_days": 1},
]
USERS = {2001: "alice", 2002: "abe", 3001: "bea", 3002: "bo", 4000: None, 1000: "svc"}


@pytest.fixture
def scan(tmp_path):
    out = tmp_path / "scan"
    write_scan(out, ROOT, FILES, errors={"beta_lab/locked": "open: EACCES"}, users=USERS)
    return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Settings pointed at a temporary folder, with a fresh catalog."""
    from pinax import config, db, snapshots

    monkeypatch.setenv("PINAX_VAR_ROOT", str(tmp_path / "var"))
    monkeypatch.setenv("PINAX_SCAN_ROOT", str(tmp_path / "scans"))
    monkeypatch.setenv("PINAX_DEMO_ROOT", str(tmp_path / "demo"))
    config.get_settings.cache_clear()
    db.reset_engine()
    snapshots.indexes.clear()
    db.init_db()
    yield config.get_settings()
    snapshots.indexes.clear()
    db.reset_engine()
    config.get_settings.cache_clear()


@pytest.fixture
def catalog(env, scan):
    """Two labs with members, one administrator, the filesystem, and one ingested snapshot."""
    from pinax.db import session_scope
    from pinax.models import Filesystem, Lab, LabFolder, LabMembership, User
    from pinax.snapshots import ingest_scan

    with session_scope() as s:
        fs = Filesystem(slug="fast", name="Fast", root=ROOT, display_root="/fh/fast")
        s.add(fs)
        s.flush()
        labs = {}
        for slug, name, folders in (("alpha", "Alpha Lab", ["alpha_lab"]), ("beta", "Beta Lab", ["beta_lab"])):
            lab = Lab(slug=slug, name=name)
            s.add(lab)
            s.flush()
            for f in folders:
                s.add(LabFolder(lab_id=lab.id, filesystem_id=fs.id, path=f))
            labs[slug] = lab
        for username, admin, memberships in (("ada", False, [("alpha", "pi")]), ("ben", False, [("beta", "delegate")]),
                                              ("ivy", True, []), ("nobody", False, [])):
            u = User(username=username, display_name=username.title(), email=f"{username}@example.org", is_admin=admin)
            s.add(u)
            s.flush()
            for slug, role in memberships:
                s.add(LabMembership(user_id=u.id, lab_id=labs[slug].id, role=role))
    with session_scope() as s:
        ingest_scan(s, scan, fs_slug="fast", progress=lambda m: None)
    return env


@pytest.fixture
def client(catalog):
    from fastapi.testclient import TestClient

    from pinax.main import app

    with TestClient(app) as c:
        yield c


def login(client, username):
    client.cookies.set("pinax_user", username)
    return client
