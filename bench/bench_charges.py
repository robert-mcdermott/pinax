# SPDX-License-Identifier: Apache-2.0
"""Time monthly charges and forecasts on a synthetic catalog: a year of weekly scans of one filesystem's top-level
folders, owned by labs and departments, with price changes, free tiers, and folders changing owner.

Builds the catalog in a temporary folder (nothing else is touched) and prints the timings as JSON.

    uv run python bench/bench_charges.py --folders 2000 --weeks 52
"""

import argparse
import json
import os
import random
import statistics
import tempfile
import time
from datetime import datetime, timedelta


def timed(fn, repeat: int = 5) -> tuple[float, object]:
    out, times = None, []
    for _ in range(repeat):
        s = time.perf_counter()
        out = fn()
        times.append((time.perf_counter() - s) * 1000)
    return statistics.median(times), out


def build(folders: int, weeks: int, labs: int, seed: int = 1) -> datetime:
    """The catalog; returns the time of the last scan."""
    from sqlalchemy import insert

    from pinax import costs
    from pinax.db import session_scope
    from pinax.models import Filesystem, FolderStat, Lab, LabFolder, LabKind, Snapshot
    from pinax.ownership import record_change

    rng = random.Random(seed)
    first = datetime(2025, 10, 6, 2)
    with session_scope() as s:
        fs = Filesystem(slug="fast", name="Fast", root="/research/fast")
        groups = [Lab(slug=f"lab{i:04d}", name=f"Lab {i:04d}", kind=LabKind.DEPARTMENT if i % 10 == 0 else LabKind.LAB)
                  for i in range(labs)]
        s.add_all([fs, *groups])
        s.flush()
        names = [f"folder_{i:05d}" for i in range(folders)]
        owner = {n: groups[i % labs] for i, n in enumerate(names[: folders * 9 // 10])}  # a tenth unowned
        s.add_all([LabFolder(lab_id=lab.id, filesystem_id=fs.id, path=n) for n, lab in owner.items()])
        for n in rng.sample(sorted(owner), folders // 100):  # one in a hundred changes owner during the year
            before, after = owner[n], rng.choice(groups)
            record_change(s, fs.id, n, before.id, after.id, when=first + timedelta(days=rng.randrange(365)))
        size = {n: rng.randrange(10**9, 50 * 10**12) for n in names}
        for w in range(weeks):
            when = first + timedelta(weeks=w, minutes=rng.randrange(120))
            for n in names:
                size[n] = max(0, int(size[n] * rng.uniform(0.97, 1.05)))
            # As ingest records them: the whole filesystem's totals with each scan.
            totals = {"bytes": sum(size.values()), "alloc_bytes": 0, "files": 0, "age_bytes": [0] * 7, "mean_age_days": None}
            snap = Snapshot(filesystem_id=fs.id, scan_id=f"fast-{w}", scan_dir="/x", started_at=when,
                            status="ready" if w >= weeks - 6 else "pruned", info={"totals": totals})
            s.add(snap)
            s.flush()
            s.execute(insert(FolderStat), [{"snapshot_id": snap.id, "filesystem_id": fs.id, "folder": n,
                                            "taken_at": when, "bytes": b} for n, b in size.items()])
        costs.add_price(s, None, fs, "2025-01-01", "25", "TB")
        costs.add_price(s, None, fs, "2026-04-16", "22.50", "TB")
        costs.set_free_tier(s, None, fs, "2025-01-01", "1", "TB", kind=LabKind.LAB)
        costs.set_free_tier(s, None, fs, "2025-01-01", "0", "TB", kind=LabKind.DEPARTMENT)
        for lab in rng.sample(groups, labs // 20):
            costs.set_free_tier(s, None, fs, f"2026-0{rng.randrange(1, 10)}-01", "5", "TB", lab=lab)
    return first + timedelta(weeks=weeks - 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folders", type=int, default=2000)
    ap.add_argument("--weeks", type=int, default=52)
    ap.add_argument("--labs", type=int, default=200)
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["PINAX_VAR_ROOT"] = tmp
        from sqlalchemy import select

        from pinax import charges, explore
        from pinax.auth import identity_for
        from pinax.db import init_db, session_scope
        from pinax.models import Lab, User

        init_db()
        s0 = time.perf_counter()
        last = build(args.folders, args.weeks, args.labs)
        built = time.perf_counter() - s0
        month = (last - timedelta(days=40)).strftime("%Y-%m")  # a month long past
        now = last + timedelta(days=1)
        with session_scope() as s:
            lab = s.scalar(select(Lab.id).where(Lab.slug == "lab0001"))
            t_month, out = timed(lambda: charges.monthly_charges(s, month, now=now))
            t_current, _ = timed(lambda: charges.monthly_charges(s, now.strftime("%Y-%m"), now=now))
            t_lab, _ = timed(lambda: charges.monthly_charges(s, month, now=now, labs_only={lab}))
            t_history, _ = timed(lambda: charges.lab_history(s, {lab}, now=now), repeat=3)
            admin = User(username="ivy", display_name="Ivy", email="ivy@example.org", is_admin=True)
            s.add(admin)
            s.flush()
            ident = identity_for(admin)
            t_lab_ahead, lab_ahead = timed(lambda: explore.history(s, ident, None, None, lab_slug="lab0001"))
            t_all_ahead, _ = timed(lambda: explore.history(s, ident, None, None))
            t_lab_charges_ahead, _ = timed(lambda: charges.charges_ahead(s, {lab}, now=now))
            t_all_charges_ahead, _ = timed(lambda: charges.charges_ahead(s, now=now), repeat=3)
        print(json.dumps({
            "folders": args.folders, "weekly_scans": args.weeks, "labs": args.labs, "catalog_built_s": round(built, 1),
            "month": month, "lines": sum(len(r["filesystems"]) for r in out["labs"]), "unbilled": len(out["unbilled"]),
            "ms": {"every_lab_for_a_month": round(t_month, 1), "every_lab_this_month": round(t_current, 1),
                   "one_lab_for_a_month": round(t_lab, 1), "one_lab_twelve_months": round(t_history, 1),
                   "one_lab_history_and_forecast": round(t_lab_ahead, 1),
                   "all_data_history_and_forecast": round(t_all_ahead, 1),
                   "one_lab_next_twelve_months": round(t_lab_charges_ahead, 1),
                   "every_lab_next_twelve_months": round(t_all_charges_ahead, 1)},
            "lab_forecast_ok": lab_ahead["forecast"]["ok"],
        }, indent=2))


if __name__ == "__main__":
    main()
