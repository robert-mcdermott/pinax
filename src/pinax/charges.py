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
from .forecast import HORIZON, MONTH_DAYS, WINDOW_DAYS, Z, fit
from .models import Filesystem, FolderStat, FreeTier, Lab, LabFolder, Price, Snapshot, SnapshotStatus
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


# While a month is worked out, sizes are summed as whole byte-microseconds (and lengths as microseconds), which
# is fast and exact; `Line.finish` divides them by the month's length once, into fractions.


@dataclass
class Period:
    """A run of a line's month with one price and one free tier: how its charge is worked out. Sizes and the
    charge are its part of the month's (as in Line); `share` is its length as a share of the month."""

    start: datetime
    end: datetime
    price: Price | None
    free: int
    free_unit: str | None  # as the free tier was given; None: no free tier
    share: Fraction | int = 0
    size: Fraction | int = 0
    billable: Fraction | int = 0
    charge: Fraction = Fraction(0)


@dataclass
class Line:
    """One lab's storage and charge on one filesystem for one month. Sizes are averages over the month, in bytes."""

    size: Fraction | int = 0
    billable: Fraction | int = 0  # after the free tier
    charge: Fraction = Fraction(0)  # in the currency, exact
    folders: dict[str, Fraction | int] = field(default_factory=lambda: defaultdict(int))
    free: set[int] = field(default_factory=set)  # free tiers (bytes) that applied
    unpriced: Fraction | int = 0  # share of the month with storage but no price
    periods: list[Period] = field(default_factory=list)

    def finish(self, month: int, per_byte: dict[int, Fraction]) -> None:
        """From sums over a month `month` microseconds long to averages over it, and the charge."""
        self.size, self.billable, self.unpriced = (Fraction(v, month) for v in (self.size, self.billable, self.unpriced))
        self.folders = {k: Fraction(v, month) for k, v in self.folders.items()}
        for p in self.periods:
            p.share, p.size, p.billable = (Fraction(v, month) for v in (p.share, p.size, p.billable))
            p.charge = p.billable * per_byte[p.price.id] if p.price else Fraction(0)
        self.charge = sum((p.charge for p in self.periods), Fraction(0))

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


def filesystem_lines(session: Session, fs: Filesystem, start: datetime, end: datetime, labs: dict[int, Lab],
                     only: set[int] | None = None) -> tuple[dict[int, Line], Line, list[dict]]:
    """Each lab's line on `fs` for [start, end), the unbilled line (lab None), and the prices that applied. With
    `only`, those labs' lines alone, and nothing unbilled."""
    snaps = session.execute(select(Snapshot.id, Snapshot.started_at).where(
        Snapshot.filesystem_id == fs.id, Snapshot.status.in_(SCANNED), Snapshot.started_at < end)
        .order_by(Snapshot.started_at)).all()
    first = max((i for i, s in enumerate(snaps) if s.started_at <= start), default=0)
    snaps = snaps[first:]
    owners = Owners(session, fs.id)
    sizes: dict[int, dict[str, int]] = defaultdict(dict)
    if snaps:
        stats = select(FolderStat.snapshot_id, FolderStat.folder, FolderStat.bytes).where(
            FolderStat.snapshot_id.in_([s.id for s in snaps]))
        if only is not None:  # just the folders those labs ever had
            stats = stats.where(FolderStat.folder.in_(owners.ever_owned_by(only)))
        for sid, folder, b in session.execute(stats):
            sizes[sid][folder] = b
    scan_times = [s.started_at for s in snaps]
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
        span = (b - a) // US
        price = prices.at(a)
        held: dict[int, int] = defaultdict(int)
        for folder, size in sizes[snaps[i].id].items():
            lab_id = owners.at(folder, a) if folder else None
            if lab_id in labs:
                if only is None or lab_id in only:
                    lines[lab_id].folders[folder] += size * span
                    held[lab_id] += size
            elif only is None:
                unbilled.folders[folder] += size * span
                unbilled.size += size * span
        for lab_id, size in held.items():
            line, (free, unit) = lines[lab_id], tiers.at(labs[lab_id], a)
            billable = max(0, size - free)
            line.size += size * span
            line.billable += billable * span
            line.free.add(free)
            if price is None and size:
                line.unpriced += span
            period = line.period(a, b, price, free, unit)
            period.share += span
            period.size += size * span
            period.billable += billable * span
    for line in [*lines.values(), unbilled]:
        line.finish(month, per_byte)
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
        lines, nobody, prices = filesystem_lines(session, fs, start, end, labs, labs_only)
        fs_total, held, billable = zero, Fraction(0), Fraction(0)
        for lab_id, line in lines.items():
            j = _line_json(line, fs, prices, currency)
            by_lab[lab_id].append(j)
            totals[lab_id] += Decimal(j["charge"])
            fs_total += Decimal(j["charge"])
            held += line.size
            billable += line.billable
        if labs_only is None:
            if nobody.size:
                unbilled.append(_line_json(nobody, fs, prices, currency))
            by_fs.append({"fs": fs.slug, "fs_name": fs.name, "charge": str(fs_total), "prices": prices,
                          "size": _bytes(held), "billable": _bytes(billable), "unbilled": _bytes(nobody.size),
                          "unpriced": any(line.unpriced for line in lines.values())})
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


# ---------- What labs will pay ----------

def _later_months(now: datetime, count: int) -> list[tuple[str, datetime, datetime]]:
    """The `count` calendar months after the one `now` is in."""
    out, y, m = [], now.year, now.month
    for _ in range(count):
        y, m = y + (m == 12), m % 12 + 1
        month = f"{y:04d}-{m:02d}"
        out.append((month, *month_bounds(month)))
    return out


def _above(c0: float, k: float, e0: float, e1: float) -> float:
    """The integral of max(0, c0 + k*e) over e from e0 to e1: the part of a straight line above zero."""
    g0, g1 = c0 + k * e0, c0 + k * e1
    if g0 >= 0 and g1 >= 0:
        return (g0 + g1) / 2 * (e1 - e0)
    if g0 <= 0 and g1 <= 0:
        return 0.0
    cross = -c0 / k
    return max(g0, g1) / 2 * (e1 - cross if g1 > 0 else cross - e0)


def charges_ahead(session: Session, lab_ids: set[int] | None = None, *, now: datetime | None = None,
                  count: int = HORIZON) -> dict[int, dict]:
    """Each lab's or department's charges for each of the `count` calendar months after this one (R12), per
    filesystem: day by day, the size of the folders it owns now at their trend (forecast.py), less the free tier
    scheduled for that day, at the price scheduled for it, as charges are worked out. With the low and high ends
    of the trend's range. Without a trend (too few scans, a step), at the size of its latest scan, saying why. These
    are estimates, worked out in floating point and rounded to the currency's unit; they are not bills."""
    now = now or utcnow()
    currency = get_settings().currency
    labs = {lab.id: lab for lab in session.scalars(select(Lab))}
    ahead_months = _later_months(now, count)
    out: dict[int, dict] = {}
    for fs in session.scalars(select(Filesystem).where(Filesystem.active.is_(True)).order_by(Filesystem.name)):
        snaps = session.execute(select(Snapshot.id, Snapshot.started_at).where(
            Snapshot.filesystem_id == fs.id, Snapshot.status.in_(SCANNED)).order_by(Snapshot.started_at)).all()
        if not snaps:
            continue
        snaps = [s for s in snaps if (snaps[-1].started_at - s.started_at).days <= WINDOW_DAYS]
        q = select(LabFolder.path, LabFolder.lab_id).where(LabFolder.filesystem_id == fs.id)
        if lab_ids is not None:
            q = q.where(LabFolder.lab_id.in_(lab_ids))
        owner = dict(session.execute(q).all())  # the folders labs own now
        if not owner:
            continue
        # Each lab's size in each recent scan, added up in one pass over the folders' history.
        held: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        for sid, folder, b in session.execute(select(FolderStat.snapshot_id, FolderStat.folder, FolderStat.bytes).where(
                FolderStat.snapshot_id.in_([s.id for s in snaps]), FolderStat.folder.in_(sorted(owner)))):
            held[owner[folder]][sid] += b
        price_rows = session.scalars(select(Price).where(Price.filesystem_id == fs.id)).all()
        prices = _Schedule([(r.starts, r) for r in price_rows])
        per_byte = {r.id: r.micros / 10**6 / UNITS[r.unit] for r in price_rows}  # per month
        tiers = _FreeTiers(session.scalars(select(FreeTier).where(FreeTier.filesystem_id == fs.id)).all())
        for lab_id, by_snap in held.items():
            points = [(s.started_at, by_snap[s.id]) for s in snaps if s.id in by_snap]
            if not points or points[-1][0] != snaps[-1].started_at:
                continue  # not in the latest scan
            trend = fit(points)
            (last_at, size_now), lab = points[-1], labs[lab_id]
            # The size as three straight lines in days since the latest scan, (at 0, per day): the trend and the
            # low and high ends of its range, as forecast.ahead draws them; flat without a trend.
            if trend["ok"]:
                lines = [(size_now, trend["slope"]), (size_now - Z * trend["scatter"], trend["low"]),
                         (size_now + Z * trend["scatter"], trend["high"])]
            else:
                lines = [(size_now, 0.0)] * 3
            cuts_at = sorted(set(prices.starts) | tiers.starts())
            rows, priced = [], False
            for month, start, end in ahead_months:
                days = (end - start).days
                sums = [0.0, 0.0, 0.0]
                cuts = [start, *(c for c in cuts_at if start < c < end), end]
                for a, b in pairwise(cuts):  # a stretch with one price and one free tier
                    price = prices.at(a)
                    if price is None:
                        continue
                    priced = True
                    free = tiers.at(lab, a)[0]
                    e0, e1 = ((x - last_at).total_seconds() / 86400 for x in (a, b))
                    for i, (c0, k) in enumerate(lines):
                        sums[i] += _above(c0 - free, k, e0, e1) * per_byte[price.id] / days
                at_end = max(0.0, lines[0][0] + lines[0][1] * (end - last_at).total_seconds() / 86400)
                rows.append({"month": month, "size": round(at_end),
                             **{k: str(money(Fraction(v), currency)) for k, v in zip(("charge", "low", "high"), sums, strict=True)}})
            entry = out.setdefault(lab_id, {"lab": lab, "filesystems": []})
            entry["filesystems"].append({
                "fs": fs.slug, "fs_name": fs.name, "size": size_now, "trend": trend["ok"],
                "why": None if trend["ok"] else trend["why"], "step": trend.get("step"), "scans": trend["scans"],
                "per_month": round(trend["slope"] * MONTH_DAYS) if trend["ok"] else None, "priced": priced,
                "months": rows})
    zero = money(Fraction(0), currency)
    for entry in out.values():
        entry["months"] = [{"month": m, **{k: str(sum((Decimal(f["months"][i][k]) for f in entry["filesystems"]), zero))
                                           for k in ("charge", "low", "high")}}
                           for i, (m, _, _) in enumerate(ahead_months)]
        entry["total"] = {k: str(sum((Decimal(r[k]) for r in entry["months"]), zero)) for k in ("charge", "low", "high")}
        lab = entry.pop("lab")
        entry["lab"] = {"slug": lab.slug, "name": lab.name, "kind": lab.kind, "active": lab.active}
    return out


def ahead_totals(per_lab: dict[int, dict], currency: str) -> dict:
    """Every lab's charges ahead, added up month by month (their ranges too, which overstates the range)."""
    zero = money(Fraction(0), currency)
    months_ = [r["month"] for r in next(iter(per_lab.values()))["months"]] if per_lab else []
    rows = [{"month": m, **{k: str(sum((Decimal(e["months"][i][k]) for e in per_lab.values()), zero))
                            for k in ("charge", "low", "high")}} for i, m in enumerate(months_)]
    return {"months": rows, "total": {k: str(sum((Decimal(r[k]) for r in rows), zero)) for k in ("charge", "low", "high")},
            "labs": len(per_lab), "without_trend": sum(1 for e in per_lab.values() if not all(f["trend"] for f in e["filesystems"]))}


STATEMENT_COLUMNS = ["month", "status", "lab", "lab_name", "kind", "filesystem", "average_bytes", "charged_bytes",
                     "unit", "average", "charged", "prices", "charge", "currency"]


def statement_rows(out: dict) -> list[list]:
    """A month's charges (from `monthly_charges`) as a statement: a row for each lab or department on each
    filesystem, and one for each filesystem's unbilled storage. Sizes are averages over the month, in bytes and in
    the unit the price is in; `prices` are the prices that applied, from their day on."""
    status = "estimate" if out["estimated"] else "final"

    def row(lab: dict | None, line: dict) -> list:
        unit = next((p["unit"] for p in reversed(line["periods"]) if p["unit"]),
                    line["prices"][-1]["unit"] if line["prices"] else None)
        prices = "; ".join(f"{p['price']} per {p['unit']} from {p['from']}" for p in line["prices"])
        return [out["month"], status, lab["slug"] if lab else "", lab["name"] if lab else "(no one)",
                lab["kind"] if lab else "unbilled", line["fs"], line["size"], line["billable"], unit or "",
                shown(line["size"], unit) if unit else "", shown(line["billable"], unit) if unit else "", prices,
                line["charge"], out["currency"]]

    rows = [row(r["lab"], line) for r in out["labs"] for line in r["filesystems"]]
    return rows + [row(None, line) for line in out["unbilled"]]


# ---------- Charges by tag (T4) ----------

def split_by_tag(session: Session, lab: Lab, month: str, tag_id: int, *, now: datetime | None = None) -> dict:
    """A lab's charges for `month` split by the values of a tag (a grant, a project): on each filesystem, each value
    gets the share of the lab's charge that its folders' time-weighted average size is of the lab's, and the rest is
    untagged. A folder's bytes belong to its nearest tagged folder (itself, or the nearest above it), and a folder
    with several values splits evenly between them. Sizes come from the totals recorded for tagged folders with each
    scan, each holding until the next, as charges are worked out."""
    from .models import FolderTag, MarkedFolder, MarkedFolderStat, Tag
    from .tags import ancestors

    start, end = month_bounds(month)
    month_us = (end - start) // US
    out = monthly_charges(session, month, now=now, labs_only={lab.id})
    currency = out["currency"]
    row = next((r for r in out["labs"] if r["lab"]["slug"] == lab.slug), None)
    tag = session.get(Tag, tag_id)
    values: dict[str, dict] = defaultdict(lambda: {"charge": Fraction(0), "size": Fraction(0)})
    untagged = Fraction(0)
    owned = {(f.filesystem_id, f.path) for f in session.scalars(select(LabFolder).where(LabFolder.lab_id == lab.id))}
    for line in (row["filesystems"] if row else []):
        fs = session.scalar(select(Filesystem).where(Filesystem.slug == line["fs"]))
        charge = Fraction(Decimal(line["charge"]))
        size = Fraction(line["size"])
        tagged = session.execute(select(MarkedFolder, FolderTag.value).join(FolderTag, FolderTag.mark_id == MarkedFolder.id)
                                 .where(FolderTag.tag_id == tag_id, MarkedFolder.filesystem_id == fs.id)).all()
        marks: dict[int, tuple[MarkedFolder, list[str]]] = {}
        for m, value in tagged:
            top = m.path[len(fs.root.rstrip("/")) + 1:].split("/", 1)[0]
            if (fs.id, top) in owned:
                marks.setdefault(m.id, (m, []))[1].append(value or tag.label)
        if not marks or not size:
            untagged += charge
            continue
        by_path = {m.path: mid for mid, (m, _) in marks.items()}
        # Each tagged folder's nearest tagged folder above it, whose bytes it takes out.
        parent = {mid: next((by_path[p] for p in ancestors(m.path, fs.root)[1:] if p in by_path), None)
                  for mid, (m, _) in marks.items()}
        stats = session.execute(select(MarkedFolderStat.mark_id, MarkedFolderStat.taken_at, MarkedFolderStat.present,
                                       MarkedFolderStat.bytes).where(MarkedFolderStat.mark_id.in_(list(marks)),
                                                                     MarkedFolderStat.taken_at < end)
                                .order_by(MarkedFolderStat.taken_at)).all()
        times = sorted({s.taken_at for s in stats})
        cuts = sorted({start, end} | {t for t in times if start < t < end})
        sums: dict[int, int] = defaultdict(int)  # byte-microseconds each tagged folder holds of its own
        i, latest = 0, {}
        for a, b in pairwise(cuts):
            while i < len(stats) and stats[i].taken_at <= a:
                latest[stats[i].mark_id] = stats[i].bytes if stats[i].present else 0
                i += 1
            own = dict(latest)
            for mid, up in parent.items():
                if up is not None and mid in latest and up in own:
                    own[up] -= latest[mid]
            span = (b - a) // US
            for mid, b_ in own.items():
                sums[mid] += max(0, b_) * span
        for mid, (_, vals) in marks.items():
            avg = Fraction(sums[mid], month_us)
            share = charge * avg / size if size else Fraction(0)
            for v in vals:
                values[v]["charge"] += share / len(vals)
                values[v]["size"] += avg / len(vals)
        untagged += charge - sum((charge * Fraction(sums[mid], month_us) / size for mid in marks), Fraction(0))
    rows = [{"value": v, "charge": str(money(d["charge"], currency)), "size": _bytes(d["size"])}
            for v, d in sorted(values.items(), key=lambda kv: -kv[1]["charge"])]
    return {"month": month, "currency": currency, "tag": {"id": tag.id, "label": tag.label} if tag else None,
            "total": row["total"] if row else str(money(Fraction(0), currency)), "values": rows,
            "untagged": str(money(max(Fraction(0), untagged), currency)), "estimated": out["estimated"]}
