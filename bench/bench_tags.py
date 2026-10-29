# SPDX-License-Identifier: Apache-2.0
"""Time finding data by tag (Collections) on a synthetic catalog: 2,000 tagged folders of 200 labs, tagged with one
of 20 projects (and a third of them a data stage), each with half a year of weekly totals.

Builds the catalog in a temporary folder (nothing else is touched) and prints the timings.

    uv run python bench/bench_tags.py
"""

import os
import random
import tempfile
import time
from datetime import datetime, timedelta

with tempfile.TemporaryDirectory() as tmp:
    os.environ["PINAX_VAR_ROOT"] = tmp
    from sqlalchemy import insert, select

    from pinax import tags
    from pinax.auth import identity_for
    from pinax.db import init_db, session_scope
    from pinax.models import Filesystem, FolderTag, Lab, LabFolder, MarkedFolder, MarkedFolderStat, Snapshot, Tag, User
    init_db()
    rng = random.Random(1)
    with session_scope() as s:
        fs = Filesystem(slug="fast", name="Fast", root="/r/fast"); s.add(fs)
        labs = [Lab(slug=f"lab{i}", name=f"Lab {i}") for i in range(200)]; s.add_all(labs)
        admin = User(username="ivy", display_name="Ivy", email="i@x", is_admin=True); s.add(admin); s.flush()
        s.add_all([LabFolder(lab_id=l.id, filesystem_id=fs.id, path=f"folder_{i:04d}") for i, l in enumerate(labs)])
        snaps = [Snapshot(filesystem_id=fs.id, scan_id=f"s{w}", scan_dir="/x", started_at=datetime(2026, 4, 1) + timedelta(weeks=w), status="pruned") for w in range(26)]
        s.add_all(snaps); s.flush()
        project = s.scalar(select(Tag).where(Tag.key == "project")); stage = s.scalar(select(Tag).where(Tag.key == "stage"))
        marks = [MarkedFolder(filesystem_id=fs.id, path=f"/r/fast/folder_{i % 200:04d}/p{i}") for i in range(2000)]
        s.add_all(marks); s.flush()
        s.execute(insert(FolderTag), [{"tag_id": project.id, "mark_id": m.id, "value": f"P{i % 20}"} for i, m in enumerate(marks)]
                  + [{"tag_id": stage.id, "mark_id": m.id, "value": "raw"} for i, m in enumerate(marks) if i % 3 == 0])
        s.execute(insert(MarkedFolderStat), [{"mark_id": m.id, "snapshot_id": sn.id, "taken_at": sn.started_at, "present": True,
                   "bytes": rng.randrange(10**9, 10**12), "files": 100, "dirs": 1, "cold_bytes": 0, "age_bytes": []} for m in marks for sn in snaps])
    with session_scope() as s:
        ident = identity_for(s.scalar(select(User)))
        for want in ([(project.id, "P3")], [(project.id, None)], [(project.id, "P3"), (stage.id, "raw")]):
            tags.collection(s, ident, want)
            t0 = time.perf_counter(); out = tags.collection(s, ident, want); ms = (time.perf_counter() - t0) * 1000
            print(want, len(out["folders"]), "folders", round(ms), "ms")
