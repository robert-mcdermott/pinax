# SPDX-License-Identifier: Apache-2.0
"""End to end with the real fs-inventory binary: scan a small tree, ingest it, and query it."""

import os
import shutil
import subprocess

import pytest

from pinax.config import get_settings


def _binary() -> str | None:
    path = get_settings().fs_inventory_path()
    return path if shutil.which(path) or os.path.exists(path) else None


pytestmark = pytest.mark.skipif(_binary() is None, reason="fs-inventory is not installed (scripts/install-fs-inventory.sh)")


def test_scan_ingest_and_query(env, tmp_path):
    from pinax.db import session_scope
    from pinax.index import Stats
    from pinax.models import Filesystem
    from pinax.snapshots import indexes, ingest_scan

    root = tmp_path / "fs"
    (root / "lab_a" / "run").mkdir(parents=True)
    (root / "lab_b" / "locked").mkdir(parents=True)
    for name, size, years in (("a.bam", 10_000, 6), ("b.fastq.gz", 2_000, 0), ("c.csv", 30, 2)):
        p = root / "lab_a" / "run" / name
        with open(p, "wb") as f:
            f.truncate(size)
        t = os.path.getmtime(p) - years * 365.25 * 86400
        os.utime(p, (t, t))
    (root / "lab_b" / "locked").chmod(0)
    out = tmp_path / "scan"
    try:
        proc = subprocess.run([_binary(), "scan", str(root), "-o", str(out), "--progress", "none", "--resolve-ids"],
                              capture_output=True, text=True, check=False)
    finally:
        (root / "lab_b" / "locked").chmod(0o755)
    assert proc.returncode in (0, 1), proc.stderr

    with session_scope() as s:
        s.add(Filesystem(slug="t", name="Test", root=str(root.resolve())))
    with session_scope() as s:
        snap = ingest_scan(s, out, fs_slug="t", progress=lambda m: None)
        assert snap.status == "ready" and snap.files == 3 and snap.errors == (0 if os.geteuid() == 0 else 1)
        idx = indexes.get(snap)
        st = Stats.from_row(idx.node(idx.top_path("lab_a"))).json(idx.ref_day)
        assert st["bytes"] == 12_030 and st["age_bytes"][5] == 10_000 and st["age_bytes"][0] == 2_000
        assert {r["ext"] for r in idx.types(idx.top_path("lab_a"))} == {"bam", "fastq.gz", "csv"}
