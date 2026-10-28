# SPDX-License-Identifier: Apache-2.0
"""Capacity (R11): how much each filesystem holds, and when it fills at its trend.

A filesystem's capacity comes from one of two places. `pinax scan` records what the storage reports for the
mounted filesystem (`statvfs`: its total, used, and available space, asked of the filesystem itself, so no file is
read) in `pinax.json` beside the scan's manifest, and ingest keeps it with the snapshot. An administrator can set
the capacity instead, from a day on (storage expanded, or an expansion scheduled); from that day it takes the place
of what the storage reports. Scans written by fs-inventory directly need it set.

What fills the capacity is measured the best way the scans allow: the storage's own figure for space used, which
counts what scans cannot see (the storage's snapshots and overhead), once enough scans have it for a forecast; else
the scans' space on disk; else, for scans that record no space on disk (sparse files, as in the demo), the size of
the files. Its forecast (forecast.py) is carried forward against the capacity as scheduled to the day it reaches
it, with the earliest and latest days within its range, up to three years ahead."""

import json
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import __version__
from .config import get_settings
from .costs import UNITS as COST_UNITS
from .costs import CostError, _audit, _decimal, _starts
from .db import utcnow
from .forecast import MIN_SCANS, add_months, ahead, fit, forecast
from .models import Capacity, Filesystem, Snapshot, SnapshotStatus, User
from .serialize import iso

RECORD = "pinax.json"  # what `pinax scan` records beside fs-inventory's manifest
FILL_DAYS = 3 * 366  # how far ahead a filesystem's fill is looked for
SCANNED = (SnapshotStatus.READY, SnapshotStatus.PRUNED)
MEASURES = ("storage", "alloc", "size")  # the best first
UNITS = {**COST_UNITS, "PB": 10**15, "PiB": 2**50}
MAX_BYTES = 10**21  # a zettabyte: anything above is a typing mistake


def capacity_bytes(amount, unit: str) -> int:
    if unit not in UNITS:
        raise CostError(f"A capacity is given in {', '.join(UNITS)}, not {unit!r}")
    b = int(_decimal(amount, "A capacity") * UNITS[unit])
    if b <= 0:
        raise CostError("A capacity is more than nothing")
    if b > MAX_BYTES:
        raise CostError("A capacity above a zettabyte is surely a mistake")
    return b


def shown(bytes_: int, unit: str) -> str:
    """Bytes in the unit they were given in, without trailing zeros: '1.5'."""
    return format((Decimal(bytes_) / UNITS[unit]).normalize(), "f")


# ---------- What the storage reports ----------

def record_storage(scan_dir: Path, root: str) -> dict:
    """Ask the filesystem at `root` its total, used, and available space, and record them in the scan's folder."""
    st = os.statvfs(root)
    storage = {"total": st.f_blocks * st.f_frsize, "used": (st.f_blocks - st.f_bfree) * st.f_frsize,
               "available": st.f_bavail * st.f_frsize}
    (Path(scan_dir) / RECORD).write_text(json.dumps(
        {"recorded_by": f"pinax {__version__}", "recorded_at": iso(utcnow()), "root": root, "storage": storage}, indent=2))
    return storage


def scan_storage(scan_dir: Path) -> dict | None:
    """What `pinax scan` recorded about the storage with this scan, if anything."""
    try:
        storage = json.loads((Path(scan_dir) / RECORD).read_text())["storage"]
        return {k: int(storage[k]) for k in ("total", "used", "available")}
    except (OSError, ValueError, KeyError, TypeError):
        return None


# ---------- Setting it ----------

def set_capacity(session: Session, actor: User | None, fs: Filesystem, starts, amount, unit: str) -> Capacity:
    """`fs` holds `amount` `unit` from `starts` on (a past day says what it held then; a future one is scheduled)."""
    day, b = _starts(starts), capacity_bytes(amount, unit)
    if session.scalar(select(Capacity).where(Capacity.filesystem_id == fs.id, Capacity.starts == day)):
        raise CostError(f"{fs.name} already has a capacity from {day}; remove it first", 409)
    row = Capacity(filesystem_id=fs.id, starts=day, bytes=b, unit=unit)
    session.add(row)
    _audit(session, actor, "capacity.set", f"{fs.name}: capacity {shown(b, unit)} {unit} from {day}",
           {"filesystem": fs.slug, "starts": day.isoformat(), "bytes": b, "unit": unit})
    session.flush()
    return row


def remove_capacity(session: Session, actor: User | None, row: Capacity) -> None:
    fs = session.get(Filesystem, row.filesystem_id)
    _audit(session, actor, "capacity.removed", f"{fs.name}: removed the capacity of {shown(row.bytes, row.unit)} "
           f"{row.unit} from {row.starts}", {"filesystem": fs.slug, "starts": row.starts.isoformat(), "bytes": row.bytes})
    session.delete(row)
    session.flush()


# ---------- When it fills ----------

def _value(snap: Snapshot, measure: str) -> int | None:
    if measure == "storage":
        storage = (snap.info or {}).get("storage")
        return storage["used"] if storage else None
    return snap.alloc_bytes if measure == "alloc" else snap.bytes


def _measure(snaps: list[Snapshot]) -> str:
    latest = snaps[-1]
    if sum(1 for s in snaps if (s.info or {}).get("storage")) >= MIN_SCANS and (latest.info or {}).get("storage"):
        return "storage"
    return "alloc" if latest.alloc_bytes or not latest.bytes else "size"


class Schedule:
    """The capacity on any day: what an administrator set, from its day on; before the first, what the storage
    reported with the latest scan."""

    def __init__(self, rows: list[Capacity], reported: int | None):
        self.rows = sorted(rows, key=lambda r: r.starts)
        self.reported = reported

    def on(self, day: date) -> tuple[int | None, str | None, Capacity | None]:
        row = next((r for r in reversed(self.rows) if r.starts <= day), None)
        if row is not None:
            return row.bytes, "set", row
        return (self.reported, "scan", None) if self.reported else (None, None, None)


def _fills(trend: dict, end: datetime, value: float, schedule: Schedule) -> dict:
    """The first day the forecast reaches the capacity, and the earliest and latest within its range; None for one
    not reached within FILL_DAYS."""
    found: dict[str, str | None] = {"at": None, "earliest": None, "latest": None}
    for d in range(1, FILL_DAYS + 1):
        t = end + timedelta(days=d)
        cap = schedule.on(t.date())[0]
        if cap is None:
            continue
        for key, v in zip(("at", "latest", "earliest"), ahead(trend, value, d), strict=True):
            if found[key] is None and v >= cap:
                found[key] = iso(t)
        if found["latest"] is not None:
            break
    return found


def summary(session: Session, fs: Filesystem, *, today: date | None = None) -> dict:
    """A filesystem's capacity now, where it comes from, its schedule, what the storage reported with the latest
    scan, how much of it is used, and when it fills at its trend."""
    today = today or utcnow().date()
    rows = session.scalars(select(Capacity).where(Capacity.filesystem_id == fs.id).order_by(Capacity.starts)).all()
    snaps = session.scalars(select(Snapshot).where(Snapshot.filesystem_id == fs.id, Snapshot.status.in_(SCANNED))
                            .order_by(Snapshot.started_at)).all()
    latest = snaps[-1] if snaps else None
    storage = (latest.info or {}).get("storage") if latest else None
    schedule = Schedule(rows, storage["total"] if storage else None)
    now, source, row = schedule.on(today)
    current = row.id if row else None
    out = {
        "fs": fs.slug, "name": fs.name, "today": today.isoformat(),
        "capacity": {"bytes": now, "source": source, "since": row.starts.isoformat() if row else None},
        "schedule": [{"id": r.id, "starts": r.starts.isoformat(), "bytes": r.bytes, "unit": r.unit,
                      "amount": shown(r.bytes, r.unit), "current": r.id == current, "future": r.starts > today}
                     for r in rows],
        "storage": {**storage, "at": iso(latest.started_at)} if storage else None,
        "warning_months": get_settings().fill_warning_months,
        "measure": None, "used": None, "share": None, "forecast": None, "fills": None, "full": False, "soon": False,
    }
    if latest is None:
        return out
    measure = _measure(snaps)
    points = [(s.started_at, v) for s in snaps if (v := _value(s, measure)) is not None]
    used = points[-1][1] if points else None
    f = forecast(points)
    out.update(measure=measure, used=used, forecast={k: f.get(k) for k in ("ok", "why", "scans", "since", "step",
                                                                         "need", "per_month", "per_month_low",
                                                                         "per_month_high")})
    if now is None or used is None:
        return out
    out["share"] = used / now
    out["full"] = used >= now
    trend = fit(points)
    if trend["ok"] and not out["full"]:
        fills = _fills(trend, points[-1][0], used, schedule)
        out["fills"] = fills
        if fills["at"]:
            warn = add_months(points[-1][0], out["warning_months"])
            out["soon"] = datetime.fromisoformat(fills["at"].removesuffix("Z")) <= warn
    return out


def steps(summary_: dict) -> list[dict]:
    """The capacity as steps for a chart, as `Schedule.on` has it: what the storage reported (from the start), then
    each set capacity from its day."""
    out = []
    reported = summary_["storage"]["total"] if summary_["storage"] else None
    if reported:
        out.append({"from": None, "bytes": reported})
    for r in summary_["schedule"]:
        out.append({"from": f"{r['starts']}T00:00:00Z", "bytes": r["bytes"]})
    return out
