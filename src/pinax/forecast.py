# SPDX-License-Identifier: Apache-2.0
"""Where a size is heading (R10): a straight-line trend through the recent scans, carried forward from the latest
scan for the next twelve months, with a range and a backtest.

The trend is Sen's slope, the median of the slopes between every pair of scans, so one odd scan barely moves it.
Its range is the confidence interval of that median (from the ranks of the pairwise slopes, as in Kendall's test),
widened by how far the scans scatter about the trend; about four in five outcomes should fall within it, if the
place keeps growing as it has.

A forecast needs at least `MIN_SCANS` scans over `MIN_DAYS`, within the last `WINDOW_DAYS`, and after the last
step: a change between two scans far from the usual one, such as a cleanup or data brought in. A trend drawn across
a step describes neither side of it, so the trend starts after it."""

from calendar import monthrange
from datetime import datetime
from itertools import pairwise
from math import ceil, sqrt
from statistics import median

from .serialize import iso

DAY = 86400.0
MONTH_DAYS = 365.25 / 12
WINDOW_DAYS = 183  # the trend of the last six months
MIN_SCANS = 6
MIN_DAYS = 56
HORIZON = 12  # months ahead
BACKTEST_DAYS = 91
STEP_SHARE = 0.10  # a step: a change off the usual one by more than a tenth of the size
Z = 1.2816  # the range covers four outcomes in five

Point = tuple[datetime, float]


def add_months(t: datetime, k: int) -> datetime:
    """The same day `k` months later, or the month's last day if it is shorter."""
    y, m = divmod(t.month - 1 + k, 12)
    year, month = t.year + y, m + 1
    return t.replace(year=year, month=month, day=min(t.day, monthrange(year, month)[1]))


def _days(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / DAY


def _enough(points: list[Point]) -> bool:
    return len(points) >= MIN_SCANS and _days(points[0][0], points[-1][0]) >= MIN_DAYS


def _after_step(points: list[Point]) -> tuple[list[Point], dict | None]:
    """The points after the last step, and the step (None: no step)."""
    diffs = [b[1] - a[1] for a, b in pairwise(points)]
    if len(diffs) < 2:
        return points, None
    usual = median(diffs)
    spread = 1.4826 * median(abs(d - usual) for d in diffs)
    last = None
    for i, d in enumerate(diffs):
        off = abs(d - usual)
        if off > STEP_SHARE * max(abs(points[i][1]), abs(points[i + 1][1])) and off > 4 * spread:
            last = i + 1
    if last is None:
        return points, None
    return points[last:], {"at": iso(points[last][0]), "before": iso(points[last - 1][0]),
                           "change": round(diffs[last - 1])}


def fit(points: list[Point]) -> dict:
    """The trend of `points` (time, value; each scan with the place in it, oldest first), or why there is none.
    Slopes are per day."""
    if not points:
        return {"ok": False, "why": "none", "scans": 0}
    end = points[-1][0]
    recent = [p for p in points if _days(p[0], end) <= WINDOW_DAYS]
    used, step = _after_step(recent)
    out = {"since": iso(used[0][0]), "scans": len(used), "step": step}
    if not _enough(used):
        return {"ok": False, "why": "step" if step and _enough(recent) else "scans", **out}
    slopes = sorted((y2 - y1) / _days(t1, t2) for i, (t1, y1) in enumerate(used) for t2, y2 in used[i + 1:] if t2 > t1)
    n, total = len(used), len(slopes)
    slope = median(slopes)
    reach = Z * sqrt(n * (n - 1) * (2 * n + 5) / 18)
    low = slopes[max(0, min(total - 1, int((total - reach) / 2) - 1))]
    high = slopes[max(0, min(total - 1, ceil((total + reach) / 2)))]
    level = median(y - slope * _days(end, t) for t, y in used)
    scatter = 1.4826 * median(abs(y - (level + slope * _days(end, t))) for t, y in used)
    return {"ok": True, **out, "slope": slope, "low": min(low, slope), "high": max(high, slope), "scatter": scatter}


def _ahead(trend: dict, value: float, days: float) -> tuple[float, float, float]:
    mid = value + trend["slope"] * days
    low = value + trend["low"] * days - Z * trend["scatter"]
    high = value + trend["high"] * days + Z * trend["scatter"]
    return max(0.0, mid), max(0.0, low), max(0.0, high)


def forecast(points: list[Point], *, months: int = HORIZON) -> dict:
    """The value at the same day of each of the next `months` months, from the latest scan, with its range; the
    growth per month; and a backtest: what the same method forecast for the latest scan from the scans it had
    `BACKTEST_DAYS` earlier."""
    trend = fit(points)
    need = {"scans": MIN_SCANS, "days": MIN_DAYS, "window_days": WINDOW_DAYS}
    if not trend["ok"]:
        return {**trend, "need": need}
    end, value = points[-1]
    ahead = []
    for k in range(1, months + 1):
        at = add_months(end, k)
        mid, low, high = _ahead(trend, value, _days(end, at))
        ahead.append({"at": iso(at), "value": round(mid), "low": round(low), "high": round(high)})
    backtest = None
    earlier = [p for p in points if _days(p[0], end) >= BACKTEST_DAYS]
    then = fit(earlier)
    if then["ok"]:
        made, was = earlier[-1]
        predicted = _ahead(then, was, _days(made, end))[0]
        backtest = {"made_at": iso(made), "forecast": round(predicted), "actual": round(value),
                    "error": (predicted - value) / value if value else None}
    return {"ok": True, "since": trend["since"], "scans": trend["scans"], "step": trend["step"], "need": need,
            "per_month": round(trend["slope"] * MONTH_DAYS), "per_month_low": round(trend["low"] * MONTH_DAYS),
            "per_month_high": round(trend["high"] * MONTH_DAYS), "points": ahead, "backtest": backtest}
