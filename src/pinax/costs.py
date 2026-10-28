# SPDX-License-Identifier: Apache-2.0
"""Prices and free tiers: what storage costs, and how much of it is free, on each filesystem.

Both are schedules: each price or free tier applies from a day on, until the next one, and earlier ones are kept,
so past months keep what they had. A filesystem has no price until its first (its storage is then not charged).
Free tiers are storage: one for every lab and one for every department (none is allowed), and exceptions for
particular labs, which take the place of their kind's tier from their day on, until they end. Amounts are given
in GB, GiB, TB, or TiB and kept as given, so they show as entered; prices are kept in millionths of the currency,
so money is exact. Every change is audited with what it changed. Monthly charges come from these (wave 5, slice 2).
"""

import getpass
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent, Filesystem, FreeTier, Lab, LabKind, Price, User

UNITS = {"GB": 10**9, "GiB": 2**30, "TB": 10**12, "TiB": 2**40}
MICRO = Decimal(1_000_000)
MAX_PRICE = Decimal(1_000_000)  # per unit per month: anything above is a typing mistake
MAX_FREE = 10**18  # an exabyte


class CostError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


# ---------- Reading amounts ----------

def _unit(unit: str) -> str:
    if unit not in UNITS:
        raise CostError(f"A unit is one of {', '.join(UNITS)}, not {unit!r}")
    return unit


def _decimal(value, what: str) -> Decimal:
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise CostError(f"{what} must be a number, not {value!r}") from None
    if not d.is_finite() or d < 0:
        raise CostError(f"{what} must be zero or more")
    return d


def price_micros(amount) -> int:
    """A price as given (a number, up to six decimal places) in millionths of the currency."""
    d = _decimal(amount, "A price")
    if d > MAX_PRICE:
        raise CostError(f"A price above {MAX_PRICE:,} per unit per month is surely a mistake")
    micros = d * MICRO
    if micros != micros.to_integral_value():
        raise CostError("A price has at most six decimal places")
    return int(micros)


def storage_bytes(amount, unit: str) -> int:
    """An amount of storage as given, in bytes (whole bytes, rounded down)."""
    d = _decimal(amount, "An amount of storage")
    b = int(d * UNITS[_unit(unit)])
    if b > MAX_FREE:
        raise CostError("An amount of storage above an exabyte is surely a mistake")
    return b


def shown(bytes_: int | None, unit: str) -> str | None:
    """Bytes in the unit they were given in, without trailing zeros: '1.5'."""
    if bytes_ is None:
        return None
    d = (Decimal(bytes_) / UNITS[unit]).normalize()
    return format(d, "f")


def price_text(micros: int) -> str:
    return format((Decimal(micros) / MICRO).normalize(), "f")


# ---------- What applies on a day ----------

@dataclass(frozen=True)
class Rate:
    micros: int  # per unit per month
    unit: str
    starts: date

    def per_byte_month(self) -> Decimal:
        return Decimal(self.micros) / MICRO / UNITS[self.unit]


def price_on(session: Session, fs: Filesystem, day: date) -> Rate | None:
    """The filesystem's price on `day`, or None before its first."""
    p = session.scalar(select(Price).where(Price.filesystem_id == fs.id, Price.starts <= day)
                       .order_by(Price.starts.desc()).limit(1))
    return Rate(p.micros, p.unit, p.starts) if p else None


def free_on(session: Session, fs: Filesystem, lab: Lab, day: date) -> int:
    """Bytes free of charge for `lab` on `fs` on `day`: its own tier if one applies, else its kind's, else none."""
    own = session.scalar(select(FreeTier).where(FreeTier.filesystem_id == fs.id, FreeTier.lab_id == lab.id,
                                                FreeTier.starts <= day).order_by(FreeTier.starts.desc()).limit(1))
    if own is not None and own.bytes is not None:
        return own.bytes
    default = session.scalar(select(FreeTier).where(FreeTier.filesystem_id == fs.id, FreeTier.lab_id.is_(None),
                                                    FreeTier.kind == lab.kind, FreeTier.starts <= day)
                             .order_by(FreeTier.starts.desc()).limit(1))
    if default is None:
        return 0
    return default.bytes or 0


# ---------- Changing them ----------

def _audit(session: Session, actor: User | None, action: str, summary: str, detail: dict) -> None:
    source = {"source": "app"} if actor is not None else {"source": "cli", "by": getpass.getuser()}
    session.add(AuditEvent(actor_id=actor.id if actor else None, action=action, summary=summary,
                           detail={**detail, **source}))


def _starts(value) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise CostError(f"A day is written YYYY-MM-DD, not {value!r}") from None


def add_price(session: Session, actor: User | None, fs: Filesystem, starts, amount, unit: str) -> Price:
    """A new price for `fs` from `starts` on (a past day changes the charges since then; a future one is scheduled)."""
    day, micros, unit = _starts(starts), price_micros(amount), _unit(unit)
    if session.scalar(select(Price).where(Price.filesystem_id == fs.id, Price.starts == day)):
        raise CostError(f"{fs.name} already has a price from {day}; remove it first", 409)
    before = price_on(session, fs, day)
    p = Price(filesystem_id=fs.id, starts=day, micros=micros, unit=unit)
    session.add(p)
    _audit(session, actor, "cost.price_added",
           f"{fs.name}: {price_text(micros)} per {unit} per month from {day}",
           {"filesystem": fs.slug, "starts": day.isoformat(), "price": price_text(micros), "unit": unit,
            "before": {"price": price_text(before.micros), "unit": before.unit} if before else None})
    session.flush()
    return p


def remove_price(session: Session, actor: User | None, price: Price) -> None:
    fs = price.filesystem
    _audit(session, actor, "cost.price_removed",
           f"{fs.name}: removed the price of {price_text(price.micros)} per {price.unit} from {price.starts}",
           {"filesystem": fs.slug, "starts": price.starts.isoformat(), "price": price_text(price.micros),
            "unit": price.unit})
    session.delete(price)
    session.flush()


def set_free_tier(session: Session, actor: User | None, fs: Filesystem, starts, amount, unit: str, *,
                  kind: str | None = None, lab: Lab | None = None) -> FreeTier:
    """From `starts` on, `amount` `unit` free for every lab or department of `kind`, or for `lab` alone; for a lab,
    `amount` None ends its own tier, so its kind's applies again."""
    if (kind is None) == (lab is None):
        raise CostError("A free tier is for a kind (lab or department) or for one lab or department")
    if kind is not None and kind not in LabKind.ALL:
        raise CostError(f"A kind is {' or '.join(LabKind.ALL)}, not {kind!r}")
    if amount is None and lab is None:
        raise CostError("A kind's free tier cannot end; set it to 0 instead")
    day, unit = _starts(starts), _unit(unit)
    bytes_ = None if amount is None else storage_bytes(amount, unit)
    same = select(FreeTier).where(FreeTier.filesystem_id == fs.id, FreeTier.starts == day)
    same = same.where(FreeTier.lab_id == lab.id) if lab else same.where(FreeTier.lab_id.is_(None), FreeTier.kind == kind)
    if session.scalar(same):
        raise CostError(f"There is already a free tier for {lab.name if lab else 'every ' + kind} on {fs.name} from "
                        f"{day}; remove it first", 409)
    t = FreeTier(filesystem_id=fs.id, kind=kind, lab_id=lab.id if lab else None, starts=day, bytes=bytes_, unit=unit)
    session.add(t)
    who = lab.name if lab else f"every {kind}"
    what = f"{shown(bytes_, unit)} {unit} free" if bytes_ is not None else "back to its kind's free tier"
    _audit(session, actor, "cost.free_tier_set", f"{fs.name}: {who}, {what}, from {day}",
           {"filesystem": fs.slug, "starts": day.isoformat(), "kind": kind, "lab": lab.slug if lab else None,
            "amount": shown(bytes_, unit), "unit": unit, "bytes": bytes_})
    session.flush()
    return t


def remove_free_tier(session: Session, actor: User | None, tier: FreeTier) -> None:
    fs = tier.filesystem
    who = tier.lab.name if tier.lab else f"every {tier.kind}"
    _audit(session, actor, "cost.free_tier_removed", f"{fs.name}: removed {who}'s free tier from {tier.starts}",
           {"filesystem": fs.slug, "starts": tier.starts.isoformat(), "kind": tier.kind,
            "lab": tier.lab.slug if tier.lab else None, "amount": shown(tier.bytes, tier.unit), "unit": tier.unit})
    session.delete(tier)
    session.flush()


# ---------- For the Costs page ----------

def schedule_json(session: Session, fs: Filesystem, today: date) -> dict:
    """A filesystem's prices and free tiers, newest first, with which apply today."""
    prices = session.scalars(select(Price).where(Price.filesystem_id == fs.id).order_by(Price.starts.desc())).all()
    tiers = session.scalars(select(FreeTier).where(FreeTier.filesystem_id == fs.id)
                            .order_by(FreeTier.starts.desc())).all()
    now = price_on(session, fs, today)

    def tier_json(t: FreeTier) -> dict:
        return {"id": t.id, "starts": t.starts.isoformat(), "amount": shown(t.bytes, t.unit), "unit": t.unit,
                "bytes": t.bytes, "future": t.starts > today}

    def current(rows: list[FreeTier]) -> FreeTier | None:
        return next((t for t in rows if t.starts <= today), None)

    defaults = {k: [t for t in tiers if t.lab_id is None and t.kind == k] for k in LabKind.ALL}
    exceptions: dict[int, list[FreeTier]] = {}
    for t in tiers:
        if t.lab_id is not None:
            exceptions.setdefault(t.lab_id, []).append(t)
    return {
        "fs": fs.slug, "name": fs.name,
        "price": {"price": price_text(now.micros), "unit": now.unit, "starts": now.starts.isoformat()} if now else None,
        "prices": [{"id": p.id, "starts": p.starts.isoformat(), "price": price_text(p.micros), "unit": p.unit,
                    "future": p.starts > today, "current": now is not None and p.starts == now.starts} for p in prices],
        "free": {k: {"now": tier_json(c) if (c := current(rows)) else None, "history": [tier_json(t) for t in rows]}
                 for k, rows in defaults.items()},
        "exceptions": [{"lab": {"slug": rows[0].lab.slug, "name": rows[0].lab.name, "kind": rows[0].lab.kind},
                        "now": tier_json(c) if (c := current(rows)) and c.bytes is not None else None,
                        "history": [tier_json(t) for t in rows]}
                       for rows in sorted(exceptions.values(), key=lambda r: r[0].lab.name)],
    }
