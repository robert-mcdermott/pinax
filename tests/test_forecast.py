# SPDX-License-Identifier: Apache-2.0
"""Forecasts (R10): the trend of a place's recent scans carried forward, checked on synthetic histories whose growth
is known, with noise, steps, gaps, and too few scans; and in History, for labs and for all data."""

import random
from datetime import datetime, timedelta

import pytest

from pinax.forecast import BACKTEST_DAYS, MIN_DAYS, MIN_SCANS, WINDOW_DAYS, add_months, fit, forecast

TB = 10**12
START = datetime(2026, 1, 5, 2)


def _weekly(n: int, per_week: float, *, base: float = 100 * TB, noise: float = 0.0, seed: int = 1) -> list:
    rng = random.Random(seed)
    return [(START + timedelta(weeks=i), base + per_week * i + rng.gauss(0, noise)) for i in range(n)]


def _truth(base: float, per_week: float, at: datetime) -> float:
    return base + per_week * (at - START).total_seconds() / (7 * 86400)


def test_steady_growth_is_found():
    pts = _weekly(30, TB)
    f = forecast(pts)
    assert f["ok"] and f["step"] is None
    assert f["scans"] == 27  # the last six months of weekly scans
    assert f["per_month"] == pytest.approx(TB * 365.25 / 12 / 7, rel=1e-9)
    assert [p["at"][:10] for p in f["points"][:3]] == ["2026-08-27", "2026-09-27", "2026-10-27"]
    assert len(f["points"]) == 12
    for p in f["points"]:
        at = datetime.fromisoformat(p["at"].removesuffix("Z"))
        assert p["value"] == pytest.approx(_truth(100 * TB, TB, at), rel=1e-9)
        assert p["low"] <= p["value"] <= p["high"]


def test_one_odd_scan_barely_moves_the_trend():
    pts = _weekly(26, TB, noise=0.05 * TB)
    odd = list(pts)
    odd[20] = (odd[20][0], odd[20][1] + 8 * TB)  # a scan that caught a big temporary copy
    assert forecast(odd)["per_month"] == pytest.approx(forecast(pts)["per_month"], rel=0.05)


def test_the_range_holds_the_outcome_about_four_times_in_five():
    rng = random.Random(7)
    hits = 0
    for trial in range(300):
        per_week = rng.uniform(0, 0.35) * TB
        pts = _weekly(26, per_week, noise=rng.choice([0.1, 0.5, 2]) * TB, seed=trial)
        last = forecast(pts)["points"][-1]
        hits += last["low"] <= _truth(100 * TB, per_week, datetime.fromisoformat(last["at"].removesuffix("Z"))) <= last["high"]
    assert 0.75 <= hits / 300 <= 0.97


def test_a_shrinking_place_never_goes_below_nothing():
    f = forecast(_weekly(26, -5 * TB, noise=TB))
    assert f["ok"] and f["per_month"] < 0
    assert f["points"][-1]["value"] == 0 and f["points"][-1]["low"] == 0


def test_the_trend_starts_after_a_step():
    pts = _weekly(30, TB)
    cleaned = pts[:18] + [(t, v - 40 * TB) for t, v in pts[18:]]
    f = forecast(cleaned)
    assert f["ok"] and f["scans"] == 12 and f["since"] == f["step"]["at"]
    assert f["step"]["change"] == pytest.approx(-39 * TB, rel=1e-9)
    assert f["per_month"] == pytest.approx(forecast(pts)["per_month"], rel=1e-9)
    # Too soon after the step: no forecast, and the step is why.
    early = forecast(cleaned[:21])
    assert not early["ok"] and early["why"] == "step" and early["scans"] == 3


def test_steady_fast_growth_is_not_a_step():
    pts = [(START + timedelta(weeks=i), 10 * TB * 1.08**i) for i in range(26)]
    assert forecast(pts)["step"] is None


def test_too_few_scans_or_too_short_a_time():
    assert forecast([])["why"] == "none"
    few = forecast(_weekly(MIN_SCANS - 1, TB))
    assert not few["ok"] and few["why"] == "scans" and few["scans"] == MIN_SCANS - 1
    assert few["need"] == {"scans": MIN_SCANS, "days": MIN_DAYS, "window_days": WINDOW_DAYS}
    daily = [(START + timedelta(days=i), 100 * TB + i * TB) for i in range(MIN_DAYS - 1)]
    assert forecast(daily)["why"] == "scans"  # many scans, but under eight weeks
    assert forecast(_weekly(9, TB))["ok"]  # eight weeks


def test_scans_need_not_be_regular():
    rng = random.Random(3)
    times = sorted(START + timedelta(days=rng.uniform(0, 180)) for _ in range(15))
    pts = [(t, 100 * TB + 2 * TB * (t - START).total_seconds() / (7 * 86400)) for t in times]
    assert forecast(pts)["per_month"] == pytest.approx(2 * TB * 365.25 / 12 / 7, rel=1e-9)


def test_the_backtest_is_the_forecast_from_three_months_before():
    pts = _weekly(40, TB, noise=0.3 * TB)
    f = forecast(pts)
    earlier = [p for p in pts if (pts[-1][0] - p[0]).days >= BACKTEST_DAYS]
    made, was = earlier[-1]
    then = fit(earlier)
    want = was + then["slope"] * (pts[-1][0] - made).total_seconds() / 86400
    assert f["backtest"]["made_at"].startswith(made.date().isoformat())
    assert f["backtest"]["forecast"] == round(want) and f["backtest"]["actual"] == round(pts[-1][1])
    assert f["backtest"]["error"] == pytest.approx((want - pts[-1][1]) / pts[-1][1])
    assert abs(f["backtest"]["error"]) < 0.02
    assert forecast(_weekly(16, TB))["backtest"] is None  # three months earlier, too few scans


def test_months_ahead_keep_the_day_or_the_months_last():
    assert add_months(datetime(2026, 1, 31, 3), 1) == datetime(2026, 2, 28, 3)
    assert add_months(datetime(2026, 11, 30), 3) == datetime(2027, 2, 28)
    assert add_months(datetime(2027, 12, 15), 14) == datetime(2029, 2, 15)


# ---------- In History ----------

WEEKS = 12


@pytest.fixture
def weekly(env):
    """Twelve weekly scans of Fast: the Alpha Lab grows steadily, the Beta Lab cleaned up three weeks ago, and the
    Gamma Lab's folder appeared a month ago."""
    from fastapi.testclient import TestClient

    from pinax.db import session_scope
    from pinax.main import app
    from pinax.models import Filesystem, FolderStat, Lab, LabFolder, LabMembership, Snapshot, User

    with session_scope() as s:
        fs = Filesystem(slug="fast", name="Fast", root="/research/fast")
        labs = {slug: Lab(slug=slug, name=f"{slug.title()} Lab") for slug in ("alpha", "beta", "gamma")}
        ada = User(username="ada", display_name="Ada", email="ada@example.org")
        s.add_all([fs, *labs.values(), ada, User(username="ivy", display_name="Ivy", email="ivy@example.org", is_admin=True)])
        s.flush()
        s.add_all([LabFolder(lab_id=lab.id, filesystem_id=fs.id, path=f"{slug}_lab") for slug, lab in labs.items()])
        s.add(LabMembership(user_id=ada.id, lab_id=labs["alpha"].id, role="pi"))
        for w in range(WEEKS):
            when = START + timedelta(weeks=w)
            snap = Snapshot(filesystem_id=fs.id, scan_id=f"fast-{w}", scan_dir="/x", started_at=when, status="pruned")
            s.add(snap)
            s.flush()
            sizes = {"alpha_lab": 50 * TB + w * TB, "beta_lab": (80 if w < 9 else 20) * TB + w * TB // 10}
            if w >= 8:
                sizes["gamma_lab"] = (w - 7) * 2 * TB
            s.add_all([FolderStat(snapshot_id=snap.id, filesystem_id=fs.id, folder=f, taken_at=when, bytes=b, alloc_bytes=b)
                       for f, b in sizes.items()])
    with TestClient(app) as c:
        yield c


def _forecast(client, user: str, **params) -> dict:
    client.cookies.set("pinax_user", user)
    r = client.get("/api/history", params=params)
    assert r.status_code == 200, r.text
    return r.json()["forecast"]


def test_a_lab_is_forecast_from_its_scans(weekly):
    f = _forecast(weekly, "ada", lab="alpha")
    assert f["ok"] and f["scans"] == WEEKS and f["step"] is None
    assert f["per_month"] == pytest.approx(TB * 365.25 / 12 / 7, rel=1e-9)
    assert f["points"][0]["value"] == pytest.approx(61 * TB + TB * 31 / 7, rel=1e-9)  # from 30 March to 30 April


def test_a_lab_that_cleaned_up_waits_for_scans_after_it(weekly):
    f = _forecast(weekly, "ivy", lab="beta")
    assert not f["ok"] and f["why"] == "step" and f["scans"] == 3
    assert f["step"]["at"].startswith((START + timedelta(weeks=9)).date().isoformat())


def test_a_new_folder_has_too_few_scans(weekly):
    f = _forecast(weekly, "ivy", lab="gamma")
    assert not f["ok"] and f["why"] == "scans" and f["scans"] == 4


def test_all_data_is_forecast_for_administrators(weekly):
    f = _forecast(weekly, "ivy")
    assert not f["ok"] and f["why"] == "step"  # the Beta Lab's cleanup is a step for the whole filesystem too
    assert f["step"]["change"] == pytest.approx(-60 * TB + TB + TB // 10 + 2 * TB, rel=1e-9)
