# SPDX-License-Identifier: Apache-2.0
"""Monthly charges: what each lab or department owes for its storage on each filesystem, month by month.

A month (a calendar month, in UTC) is charged on its time-weighted average, like a utility bill. Each scan's folder
totals (folder history, kept for every scan) hold from that scan until the next. The month is cut wherever
something changes: a scan, a folder's owner (ownership.py), a price, or a free tier (costs.py; prices and free tiers
change at the start of their day). In each stretch, a lab's storage on the filesystem is the size of the folders it
owned then; its free tier comes off that, and what is left is charged at the price then, for that stretch's share
of the month:

    charge = sum over stretches of max(0, size - free) * price per byte per month * (stretch / month)

Before a filesystem's first scan there is nothing to charge, and before its first price, no price. Folders no lab
owns, and the files directly in the root, are charged to no one (unbilled). The month in progress is an estimate:
the latest scan holds until the month's end, at the prices and free tiers scheduled. Everything is worked out
exactly (as fractions) and rounded to the currency's minor unit only at the end, line by line, so a statement adds
up to its lines.
"""

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from itertools import pairwise

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .costs import UNITS, price_text, shown
from .db import utcnow
from .models import Filesystem, FolderStat, FreeTier, Lab, Price, Snapshot, SnapshotStatus
from .ownership import Owners

SCANNED = (SnapshotStatus.READY, SnapshotStatus.PRUNED)
# Currencies whose smallest unit is the whole unit (ISO 4217 minor units of 0); others have hundredths.
WHOLE = {"BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF"}
US = timedelta(microseconds=1)


def month_bounds(month: str) -> tuple[datetime, datetime]:
    """'2026-09' -> (2026-09-01 00:00, 2026-10-01 00:00), in UTC."""
    try:
        y, m = (int(x) for x in month.split("-"))
        start = datetime(y, m, 1)
    except ValueError:
        raise ValueError(f"A month is written YYYY-MM, not {month!r}") from None
    return start, datetime(y + (m == 12), m % 12 + 1, 1)


def money(value: Fraction, currency: str) -> Decimal:
    """An exact amount rounded to the currency's minor unit, half up."""
    with localcontext() as ctx:
        ctx.prec = 60
        d = Decimal(value.numerator) / Decimal(value.denominator)
        return d.quantize(Decimal(1) if currency in WHOLE else Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class Period:
    """A run of a line's month with one price and one free tier: how its charge is worked out. Sizes and the
    charge are its part of the month's (as in Line); `share` is its length as a share of the month."""

    start: datetime
    end: datetime
    price: Price | None
    free: int
    free_unit: str | None  # as the free tier was given; None: no free tier
    share: Fraction = Fraction(0)
    size: Fraction = Fraction(0)
    billable: Fraction = Fraction(0)
    charge: Fraction = Fraction(0)


@dataclass
class Line:
    """One lab's storage and charge on one filesystem for one month. Sizes are averages over the month, in bytes."""

    size: Fraction = Fraction(0)
    billable: Fraction = Fraction(0)  # after the free tier
    charge: Fraction = Fraction(0)  # in the currency, exact
    folders: dict[str, Fraction] = field(default_factory=lambda: defaultdict(Fraction))
    free: set[int] = field(default_factory=set)  # free tiers (bytes) that applied
    unpriced: Fraction = Fraction(0)  # share of the month with storage but no price
    periods: list[Period] = field(default_factory=list)

    def period(self, start: datetime, end: datetime, price: Price | None, free: int, unit: str | None) -> Period:
        """The period [start, end) belongs to: the last one, when it runs on with the same price and free tier."""
        last = self.periods[-1] if self.periods else None
        if last is None or last.end != start or last.price is not price or last.free != free:
            last = Period(start, end, price, free, unit)
            self.periods.append(last)
        last.end = end
        return last


class _Schedule:
    """Values that apply from a day on, until the next (prices; free tiers)."""

    def __init__(self, rows: list[tuple[date, object]]):
        rows = sorted(rows, key=lambda r: r[0])
        self.starts = [datetime.combine(d, time.min) for d, _ in rows]
        self.values = [v for _, v in rows]

    def at(self, when: datetime):
        i = bisect_right(self.starts, when) - 1
        return self.values[i] if i >= 0 else None


class _FreeTiers:
    def __init__(self, rows: list[FreeTier]):
        self.kinds = {k: _Schedule([(r.starts, (r.bytes or 0, r.unit)) for r in rows if r.lab_id is None and r.kind == k])
                      for k in {r.kind for r in rows if r.lab_id is None}}
        self.own = {lab: _Schedule([(r.starts, None if r.bytes is None else (r.bytes, r.unit))
                                    for r in rows if r.lab_id == lab])
                    for lab in {r.lab_id for r in rows if r.lab_id is not None}}

    def at(self, lab: Lab, when: datetime) -> tuple[int, str | None]:
        """Bytes free for `lab` at `when`, and the unit the tier was given in (None when no tier applies)."""
        own = self.own.get(lab.id)
        if own is not None and (t := own.at(when)) is not None:
            return t
        kind = self.kinds.get(lab.kind)
        return (kind.at(when) if kind is not None else None) or (0, None)

    def starts(self) -> set[datetime]:
        return {t for s in [*self.kinds.values(), *self.own.values()] for t in s.starts}


def filesystem_lines(session: Session, fs: Filesystem, start: datetime, end: datetime,
                     labs: dict[int, Lab]) -> tuple[dict[int, Line], Line, list[dict]]:
    """Each lab's line on `fs` for [start, end), the unbilled line (lab None), and the prices that applied."""
    snaps = session.execute(select(Snapshot.id, Snapshot.started_at).where(
        Snapshot.filesystem_id == fs.id, Snapshot.status.in_(SCANNED), Snapshot.started_at < end)
        .order_by(Snapshot.started_at)).all()
    first = max((i for i, s in enumerate(snaps) if s.started_at <= start), default=0)
    snaps = snaps[first:]
    sizes: dict[int, dict[str, int]] = defaultdict(dict)
    if snaps:
        for sid, folder, b in session.execute(select(FolderStat.snapshot_id, FolderStat.folder, FolderStat.bytes)
                                              .where(FolderStat.snapshot_id.in_([s.id for s in snaps]))):
            sizes[sid][folder] = b
    scan_times = [s.started_at for s in snaps]
    owners = Owners(session, fs.id)
    price_rows = session.scalars(select(Price).where(Price.filesystem_id == fs.id)).all()
    prices = _Schedule([(p.starts, p) for p in price_rows])
    per_byte = {p.id: Fraction(p.micros, 10**6 * UNITS[p.unit]) for p in price_rows}  # per month
    tiers = _FreeTiers(session.scalars(select(FreeTier).where(FreeTier.filesystem_id == fs.id)).all())

    cuts = {start, end} | {t for t in [*scan_times, *owners.changes(), *prices.starts, *tiers.starts()] if start < t < end}
    cuts = sorted(cuts)
    month = (end - start) // US
    lines: dict[int, Line] = defaultdict(Line)
    unbilled = Line()
    for a, b in pairwise(cuts):
        i = bisect_right(scan_times, a) - 1
        if i < 0:
            continue  # before the filesystem's first scan
        share = Fraction((b - a) // US, month)
        price = prices.at(a)
        held: dict[int, int] = defaultdict(int)
        for folder, size in sizes[snaps[i].id].items():
            lab_id = owners.at(folder, a) if folder else None
            line = lines[lab_id] if lab_id in labs else unbilled
            line.folders[folder] += size * share
            if line is unbilled:
                unbilled.size += size * share
            else:
                held[lab_id] += size
        for lab_id, size in held.items():
            line, (free, unit) = lines[lab_id], tiers.at(labs[lab_id], a)
            billable = max(0, size - free)
            charge = 0 if price is None else billable * per_byte[price.id] * share
            line.size += size * share
            line.billable += billable * share
            line.charge += charge
            line.free.add(free)
            if price is None and size:
                line.unpriced += share
            period = line.period(a, b, price, free, unit)
            period.share += share
            period.size += size * share
            period.billable += billable * share
            period.charge += charge
    applied = []  # the prices in effect during the month, each for the part of it it covered
    ordered = sorted(price_rows, key=lambda r: r.starts)
    for p, nxt in pairwise([*ordered, None]):
        p_start = datetime.combine(p.starts, time.min)
        p_end = datetime.combine(nxt.starts, time.min) if nxt else end
        if p_start < end and p_end > start:
            applied.append({"price": price_text(p.micros), "unit": p.unit, "from": max(p_start, start).date().isoformat(),
                            "to": min(p_end, end).date().isoformat()})
    return dict(lines), unbilled, applied


def _bytes(f: Fraction) -> int:
    return int(f + Fraction(1, 2)) if f >= 0 else -int(-f + Fraction(1, 2))


def _iso(t: datetime) -> str:
    return t.isoformat() + "Z"


def _period_json(p: Period, currency: str) -> dict:
    """A period with its sizes as averages over the period itself, so that charge = billable x price x share."""
    return {"from": _iso(p.start), "to": _iso(p.end), "days": round((p.end - p.start) / timedelta(days=1), 2),
            "share": float(p.share), "size": _bytes(p.size / p.share), "billable": _bytes(p.billable / p.share),
            "free": p.free, "free_amount": shown(p.free, p.free_unit) if p.free_unit else None, "free_unit": p.free_unit,
            "price": price_text(p.price.micros) if p.price else None, "unit": p.price.unit if p.price else None,
            "charge": str(money(p.charge, currency))}


def _line_json(line: Line, fs: Filesystem, prices: list[dict], currency: str) -> dict:
    return {"fs": fs.slug, "fs_name": fs.name, "size": _bytes(line.size), "billable": _bytes(line.billable),
            "free": sorted(line.free),
            "charge": str(money(line.charge, currency)), "unpriced": line.unpriced > 0, "prices": prices,
            "periods": [_period_json(p, currency) for p in line.periods],
            "folders": sorted(({"folder": k, "size": _bytes(v)} for k, v in line.folders.items() if v),
                              key=lambda x: -x["size"])}


def monthly_charges(session: Session, month: str, *, now: datetime | None = None,
                    labs_only: set[int] | None = None) -> dict:
    """Every lab's and department's charges for `month` ('YYYY-MM'), by filesystem, with what no one owns. With
    `labs_only`, those labs alone (and no unbilled storage)."""
    start, end = month_bounds(month)
    now = now or utcnow()
    currency = get_settings().currency
    labs = {lab.id: lab for lab in session.scalars(select(Lab))}
    by_lab: dict[int, list[dict]] = defaultdict(list)
    zero = money(Fraction(0), currency)
    totals: dict[int, Decimal] = defaultdict(lambda: zero)
    unbilled, by_fs = [], []
    for fs in session.scalars(select(Filesystem).where(Filesystem.active.is_(True)).order_by(Filesystem.name)):
        lines, nobody, prices = filesystem_lines(session, fs, start, end, labs)
        fs_total = zero
        for lab_id, line in lines.items():
            if labs_only is not None and lab_id not in labs_only:
                continue
            j = _line_json(line, fs, prices, currency)
            by_lab[lab_id].append(j)
            totals[lab_id] += Decimal(j["charge"])
            fs_total += Decimal(j["charge"])
        if labs_only is None:
            if nobody.size:
                unbilled.append(_line_json(nobody, fs, prices, currency))
            by_fs.append({"fs": fs.slug, "fs_name": fs.name, "charge": str(fs_total), "prices": prices})
    rows = [{"lab": {"slug": labs[i].slug, "name": labs[i].name, "kind": labs[i].kind, "active": labs[i].active},
             "total": str(totals[i]), "filesystems": by_lab[i]} for i in by_lab]
    rows.sort(key=lambda r: (-Decimal(r["total"]), r["lab"]["name"]))
    return {"month": month, "currency": currency, "start": _iso(start), "end": _iso(end),
            "days": (end - start).days, "estimated": now < end, "through": _iso(now) if start <= now < end else None,
            "labs": rows, "unbilled": unbilled, "by_filesystem": by_fs,
            "total": str(sum((Decimal(r["total"]) for r in rows), zero))}


def months(session: Session, now: datetime | None = None) -> list[str]:
    """Every month from the first scan's to now, newest first."""
    first = session.scalar(select(Snapshot.started_at).where(Snapshot.status.in_(SCANNED))
                           .order_by(Snapshot.started_at).limit(1))
    if first is None:
        return []
    now = now or utcnow()
    out, y, m = [], first.year, first.month
    while (y, m) <= (now.year, now.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = y + (m == 12), m % 12 + 1
    return out[::-1]


def lab_history(session: Session, lab_ids: set[int], *, now: datetime | None = None,
                count: int = 12) -> dict[str, list[dict]]:
    """Each lab's charge and average storage, month by month, newest first, over the last `count` months with
    scans; a month the lab stored nothing in is left out."""
    out: dict[str, list[dict]] = defaultdict(list)
    for month in months(session, now)[:count]:
        m = monthly_charges(session, month, now=now, labs_only=lab_ids)
        for row in m["labs"]:
            out[row["lab"]["slug"]].append({"month": month, "total": row["total"], "estimated": m["estimated"],
                                            "size": sum(f["size"] for f in row["filesystems"])})
    return dict(out)
