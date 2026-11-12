"""Tests for the intraday market mid: trading grid, Brownian bridge and as-of lookup."""

from __future__ import annotations

from datetime import date, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fxengine.config import DEFAULT_CONFIG_PATH, load_config
from fxengine.ecb import parse_ecb_csv
from fxengine.market import fixes_for_pair, mid_at, simulate_mid_path, trading_grid

FIXTURE = Path(__file__).parent / "fixtures" / "ecb_sample.csv"
HOURS = (time(7, 0), time(16, 0))
FIX_TIME = time(13, 10)
MINUTES_PER_DAY = 540  # 07:00 to 16:00, half-open, at one-minute resolution


# --- helpers --------------------------------------------------------------------------------


def _ecb() -> pd.DataFrame:
    return parse_ecb_csv(FIXTURE.read_text())


def _eurusd_fixes() -> pd.Series:
    return fixes_for_pair(_ecb(), "EURUSD", FIX_TIME)


def _fix_days(fixes: pd.Series) -> list[date]:
    return [ts.date() for ts in fixes.index]


def _bracketed_grid(fixes: pd.Series, step_minutes: int = 1) -> pd.DatetimeIndex:
    """The full trading grid over the fix dates, trimmed to the first-fix/last-fix bracket."""
    grid = trading_grid(_fix_days(fixes), HOURS, FIX_TIME, step_minutes)
    return grid[(grid >= fixes.index.min()) & (grid <= fixes.index.max())]


def _weekdays(n: int, start: date = date(2024, 1, 1)) -> list[date]:
    days: list[date] = []
    day = start
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _flat_fixes(days: list[date], level: float = 1.0) -> pd.Series:
    index = pd.DatetimeIndex([pd.Timestamp(d) for d in days]).tz_localize("UTC")
    index = index + pd.Timedelta(hours=FIX_TIME.hour, minutes=FIX_TIME.minute)
    return pd.Series(level, index=index, name="EURFLAT")


def _rng(seed: int = 42) -> np.random.Generator:
    return np.random.default_rng(seed)


def _at(path: pd.Series, stamp: str) -> float:
    return float(path.loc[pd.Timestamp(stamp, tz="UTC")])


# --- fixes_for_pair -------------------------------------------------------------------------


def test_fixes_for_pair_is_utc_fix_time_indexed():
    fixes = _eurusd_fixes()
    assert fixes.index.tz is not None and str(fixes.index.tz) == "UTC"
    assert len(fixes) == 10
    assert fixes.index[0] == pd.Timestamp("2025-03-03 13:10", tz="UTC")
    assert fixes.iloc[0] == pytest.approx(1.0465)
    assert fixes.index.is_monotonic_increasing
    assert fixes.name == "EURUSD"


def test_fixes_for_pair_rejects_unknown_pair():
    with pytest.raises(ValueError, match="EURGBP"):
        fixes_for_pair(_ecb(), "EURGBP", FIX_TIME)
    with pytest.raises(ValueError, match="USD"):
        fixes_for_pair(_ecb(), "USD", FIX_TIME)


# --- trading_grid ---------------------------------------------------------------------------


def test_grid_excludes_weekends_and_contains_fix_minutes():
    fixes = _eurusd_fixes()
    days = _fix_days(fixes)
    grid = trading_grid(days, HOURS, FIX_TIME)

    assert str(grid.tz) == "UTC"
    assert len(grid) == len(days) * MINUTES_PER_DAY
    assert grid.is_monotonic_increasing and grid.is_unique
    assert (grid.day_of_week < 5).all()
    assert set(grid.date) == set(days)  # nothing on 2025-03-08 or 2025-03-09
    # Every ECB fix timestamp is a grid point.
    assert fixes.index.isin(grid).all()
    # And every day's grid point at 13:10 exists, one per day.
    at_fix = grid[(grid.hour == 13) & (grid.minute == 10)]
    assert len(at_fix) == len(days)


def test_grid_is_a_half_open_trading_day():
    grid = trading_grid([date(2025, 3, 7)], HOURS, FIX_TIME)
    assert len(grid) == MINUTES_PER_DAY
    assert grid[0] == pd.Timestamp("2025-03-07 07:00", tz="UTC")
    assert grid[-1] == pd.Timestamp("2025-03-07 15:59", tz="UTC")
    assert pd.Timestamp("2025-03-07 16:00", tz="UTC") not in grid


def test_grid_step_minutes_coarsens_the_resolution():
    grid = trading_grid([date(2025, 3, 7)], HOURS, FIX_TIME, step_minutes=5)
    assert len(grid) == MINUTES_PER_DAY // 5
    assert (grid[1:] - grid[:-1] == pd.Timedelta(minutes=5)).all()
    assert pd.Timestamp("2025-03-07 13:10", tz="UTC") in grid


def test_grid_is_empty_without_business_days():
    grid = trading_grid([], HOURS, FIX_TIME)
    assert len(grid) == 0 and str(grid.tz) == "UTC"


def test_grid_sorts_the_days_it_is_given():
    days = [date(2025, 3, 10), date(2025, 3, 7)]
    assert trading_grid(days, HOURS, FIX_TIME).is_monotonic_increasing


@pytest.mark.parametrize(
    ("hours", "fix_time", "step", "message"),
    [
        (HOURS, time(13, 10), 7, "step"),  # 13:10 is 370 minutes after open, not a multiple of 7
        (HOURS, time(16, 0), 1, "fix_time"),  # at the close, so outside the half-open day
        (HOURS, time(6, 59), 1, "fix_time"),  # before the open
        ((time(16, 0), time(7, 0)), time(13, 10), 1, "open"),
        ((time(7, 0), time(7, 0)), time(7, 0), 1, "open"),
        (HOURS, time(13, 10), 0, "step_minutes"),
        (HOURS, time(13, 10), -5, "step_minutes"),
        (HOURS, time(13, 10, 30), 1, "whole minute"),
    ],
)
def test_grid_rejects_bad_hours_fix_time_or_step(hours, fix_time, step, message):
    with pytest.raises(ValueError, match=message):
        trading_grid([date(2025, 3, 7)], hours, fix_time, step)


def test_grid_rejects_duplicate_days():
    with pytest.raises(ValueError, match="duplicate"):
        trading_grid([date(2025, 3, 7), date(2025, 3, 7)], HOURS, FIX_TIME)


def test_grid_matches_the_default_config_hours():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    grid = trading_grid([date(2025, 3, 7)], cfg.trading_hours_utc, cfg.fix_time_utc)
    assert len(grid) == MINUTES_PER_DAY


# --- simulate_mid_path: what the path looks like --------------------------------------------


def test_path_hits_fixes_exactly():
    fixes = _eurusd_fixes()
    grid = _bracketed_grid(fixes)
    path = simulate_mid_path(fixes, grid, 0.08, _rng())

    assert path.index.equals(grid)
    np.testing.assert_allclose(path.loc[fixes.index].to_numpy(), fixes.to_numpy(), atol=1e-12)


def test_mid_path_spans_weekend_without_nan():
    full = _eurusd_fixes()
    fixes = full.loc[
        [pd.Timestamp("2025-03-07 13:10", tz="UTC"), pd.Timestamp("2025-03-10 13:10", tz="UTC")]
    ]
    grid = _bracketed_grid(fixes)
    assert set(grid.date) == {date(2025, 3, 7), date(2025, 3, 10)}  # the weekend has no points

    path = simulate_mid_path(fixes, grid, 0.08, _rng())

    assert not path.isna().any()
    assert (path > 0).all()
    assert np.abs(np.diff(np.log(path.to_numpy()))).max() < 0.005
    # The Friday-close to Monday-open step is a single ordinary step, not a weekend-sized jump.
    friday_close = _at(path, "2025-03-07 15:59")
    monday_open = _at(path, "2025-03-10 07:00")
    assert abs(np.log(monday_open / friday_close)) < 0.005


def test_path_stays_near_benchmark():
    fixes = _eurusd_fixes()
    grid = _bracketed_grid(fixes)
    path = simulate_mid_path(fixes, grid, 0.08, _rng())

    fix_by_date = pd.Series(fixes.to_numpy(), index=[ts.date() for ts in fixes.index])
    same_day_fix = fix_by_date.loc[list(grid.date)].to_numpy()
    distance = np.abs(path.to_numpy() / same_day_fix - 1.0)
    assert distance.max() < 0.02


def test_zero_vol_path_is_log_linear_between_fixes():
    # With no diffusion the bridge collapses to X(t) = log f_k + (t/T)(log f_k+1 - log f_k).
    fixes = _eurusd_fixes()
    grid = _bracketed_grid(fixes)
    path = simulate_mid_path(fixes, grid, 0.0, _rng())

    first, second = fixes.iloc[0], fixes.iloc[1]
    segment = path.iloc[: MINUTES_PER_DAY + 1]  # first fix to second fix, both included
    t = np.arange(MINUTES_PER_DAY + 1) / MINUTES_PER_DAY
    expected = np.exp(np.log(first) + t * (np.log(second) - np.log(first)))
    np.testing.assert_allclose(segment.to_numpy(), expected, rtol=1e-12)


def test_vectorised_bridge_matches_a_per_segment_reference():
    # An independent loop over segments, built straight from the brief's formula, fed the same
    # normals. It guards the index arithmetic of the vectorised cumulative sum with resets.
    fixes = _eurusd_fixes()
    grid = _bracketed_grid(fixes)
    annual_vol = 0.08
    path = simulate_mid_path(fixes, grid, annual_vol, _rng(7))

    z = _rng(7).standard_normal(len(grid) - 1)
    sigma_step = annual_vol / np.sqrt(252 * MINUTES_PER_DAY)
    increments = z * sigma_step
    positions = grid.get_indexer(fixes.index)
    log_fix = np.log(fixes.to_numpy())
    expected = np.empty(len(grid))
    for k in range(len(fixes) - 1):
        lo, hi = positions[k], positions[k + 1]
        steps = hi - lo
        brownian = np.concatenate(([0.0], np.cumsum(increments[lo:hi])))
        t = np.arange(steps + 1) / steps
        expected[lo : hi + 1] = (
            log_fix[k] + t * (log_fix[k + 1] - log_fix[k]) + brownian - t * brownian[-1]
        )
    np.testing.assert_allclose(np.log(path.to_numpy()), expected, atol=1e-12)


@pytest.mark.parametrize("step_minutes", [1, 5])
def test_daily_variance_matches_annual_vol_whatever_the_step(step_minutes):
    # With flat fixes the path is pure bridge noise. Realised variance over one fix-to-fix day
    # should be annual_vol^2 / 252, however coarse the grid is.
    annual_vol = 0.10
    days = _weekdays(300)
    fixes = _flat_fixes(days)
    grid = trading_grid(days, HOURS, FIX_TIME, step_minutes)
    grid = grid[(grid >= fixes.index.min()) & (grid <= fixes.index.max())]
    path = simulate_mid_path(fixes, grid, annual_vol, _rng())

    log_returns = np.diff(np.log(path.to_numpy()))
    steps_per_day = MINUTES_PER_DAY // step_minutes
    per_day = (log_returns**2)[: steps_per_day * (len(days) - 1)].reshape(-1, steps_per_day)
    realised = per_day.sum(axis=1).mean()
    assert realised == pytest.approx(annual_vol**2 / 252, rel=0.05)


def test_weekends_add_no_variance():
    # Every fix-to-fix segment spans the same number of grid points, Friday to Monday included,
    # so a weekend carries no more variance than any other overnight.
    days = _weekdays(300)
    fixes = _flat_fixes(days)
    grid = _bracketed_grid(fixes)
    positions = grid.get_indexer(fixes.index)
    assert set(np.diff(positions)) == {MINUTES_PER_DAY}

    path = simulate_mid_path(fixes, grid, 0.10, _rng())
    log_returns = np.diff(np.log(path.to_numpy()))
    per_day = (log_returns**2).reshape(-1, MINUTES_PER_DAY).sum(axis=1)
    # Segment k runs from fix k to fix k+1; it crosses a weekend when fix k is on a Friday.
    crosses_weekend = np.array([d.weekday() == 4 for d in days[:-1]])
    assert crosses_weekend.sum() > 50
    weekend_mean, weekday_mean = per_day[crosses_weekend].mean(), per_day[~crosses_weekend].mean()
    assert weekend_mean == pytest.approx(weekday_mean, rel=0.15)


def test_reproducible_path():
    fixes = _eurusd_fixes()
    grid = _bracketed_grid(fixes)
    first = simulate_mid_path(fixes, grid, 0.08, _rng(42))
    second = simulate_mid_path(fixes, grid, 0.08, _rng(42))
    other = simulate_mid_path(fixes, grid, 0.08, _rng(43))

    pd.testing.assert_series_equal(first, second)
    assert not np.allclose(first.to_numpy(), other.to_numpy(), rtol=0, atol=1e-9)


def test_rng_consumption_does_not_depend_on_vol_or_fixes():
    # One normal draw per grid step, always, so a pair's volatility never shifts the random
    # stream seen by the next pair.
    fixes = _eurusd_fixes()
    grid = _bracketed_grid(fixes)
    states = []
    for vol in (0.0, 0.003, 0.16):
        rng = _rng()
        simulate_mid_path(fixes, grid, vol, rng)
        states.append(rng.bit_generator.state["state"]["state"])
    assert len(set(states)) == 1


def test_single_fix_on_a_single_point_grid_returns_that_fix():
    fixes = _eurusd_fixes().iloc[:1]
    grid = pd.DatetimeIndex(fixes.index)
    path = simulate_mid_path(fixes, grid, 0.08, _rng())
    assert path.iloc[0] == fixes.iloc[0] and len(path) == 1


# --- simulate_mid_path: refusing to extrapolate (controller ruling R1) -----------------------


def test_grid_before_the_first_fix_raises():
    fixes = _eurusd_fixes()
    grid = trading_grid(_fix_days(fixes), HOURS, FIX_TIME)  # untrimmed: starts at 07:00 day one
    assert grid[0] < fixes.index[0]
    with pytest.raises(ValueError, match="outside"):
        simulate_mid_path(fixes, grid, 0.08, _rng())


def test_grid_after_the_last_fix_raises():
    fixes = _eurusd_fixes()
    grid = _bracketed_grid(fixes)
    longer = grid.append(pd.DatetimeIndex([grid[-1] + pd.Timedelta(minutes=1)]))
    with pytest.raises(ValueError, match="outside"):
        simulate_mid_path(fixes, longer, 0.08, _rng())


def test_fix_not_on_the_grid_raises():
    fixes = _eurusd_fixes()
    grid = _bracketed_grid(fixes)
    stamps = list(fixes.index)
    stamps[3] += pd.Timedelta(seconds=30)  # 13:10:30, between two grid points
    shifted = pd.Series(fixes.to_numpy(), index=pd.DatetimeIndex(stamps))
    with pytest.raises(ValueError, match="not on the grid"):
        simulate_mid_path(shifted, grid, 0.08, _rng())


def test_fix_on_a_date_missing_from_the_grid_raises():
    fixes = _eurusd_fixes()
    days = [d for d in _fix_days(fixes) if d != date(2025, 3, 5)]
    grid = trading_grid(days, HOURS, FIX_TIME)
    grid = grid[(grid >= fixes.index.min()) & (grid <= fixes.index.max())]
    with pytest.raises(ValueError, match="not on the grid"):
        simulate_mid_path(fixes, grid, 0.08, _rng())


def test_bad_inputs_raise_value_error():
    fixes = _eurusd_fixes()
    grid = _bracketed_grid(fixes)
    with pytest.raises(ValueError, match="empty"):
        simulate_mid_path(fixes.iloc[:0], grid, 0.08, _rng())
    with pytest.raises(ValueError, match="positive"):
        simulate_mid_path(fixes.where(fixes.index != fixes.index[2], -1.0), grid, 0.08, _rng())
    with pytest.raises(ValueError, match="NaN|finite"):
        simulate_mid_path(fixes.where(fixes.index != fixes.index[2]), grid, 0.08, _rng())
    with pytest.raises(ValueError, match="annual_vol"):
        simulate_mid_path(fixes, grid, -0.01, _rng())
    with pytest.raises(ValueError, match="annual_vol"):
        simulate_mid_path(fixes, grid, float("nan"), _rng())
    with pytest.raises(ValueError, match="duplicate"):
        simulate_mid_path(pd.concat([fixes, fixes.iloc[:1]]), grid, 0.08, _rng())
    with pytest.raises(ValueError, match="time zone"):
        simulate_mid_path(fixes, grid.tz_localize(None), 0.08, _rng())
    naive_fixes = fixes.copy()
    naive_fixes.index = naive_fixes.index.tz_localize(None)
    with pytest.raises(ValueError, match="time zone"):
        simulate_mid_path(naive_fixes, grid, 0.08, _rng())
    with pytest.raises(ValueError, match="increasing"):
        simulate_mid_path(fixes, grid[::-1], 0.08, _rng())


# --- mid_at ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def two_day_path() -> pd.Series:
    fixes = _eurusd_fixes().loc[
        [pd.Timestamp("2025-03-07 13:10", tz="UTC"), pd.Timestamp("2025-03-10 13:10", tz="UTC")]
    ]
    return simulate_mid_path(fixes, _bracketed_grid(fixes), 0.08, _rng())


def _ts(*stamps: str) -> pd.Series:
    return pd.Series(pd.to_datetime(list(stamps), utc=True))


def test_mid_at_is_an_as_of_lookup(two_day_path):
    path = two_day_path
    ts = _ts(
        "2025-03-07 13:10:00",  # exactly on a grid point
        "2025-03-07 13:10:45",  # between two grid points: the earlier one
        "2025-03-07 20:00:00",  # after the close: the last grid point of the day
        "2025-03-09 12:00:00",  # on the weekend: Friday's last grid point
        "2025-03-10 07:00:30",
    )
    got = mid_at(path, ts)
    expected = [
        _at(path, "2025-03-07 13:10"),
        _at(path, "2025-03-07 13:10"),
        _at(path, "2025-03-07 15:59"),
        _at(path, "2025-03-07 15:59"),
        _at(path, "2025-03-10 07:00"),
    ]
    np.testing.assert_array_equal(got, expected)


def test_mid_at_returns_a_float_array_in_the_order_given(two_day_path):
    ts = _ts("2025-03-10 09:00", "2025-03-07 14:00", "2025-03-10 09:00")
    got = mid_at(two_day_path, ts)
    assert isinstance(got, np.ndarray) and got.dtype == np.float64 and got.shape == (3,)
    assert got[0] == got[2] != got[1]
    assert mid_at(two_day_path, _ts()).shape == (0,)


def test_mid_at_accepts_timestamps_of_any_resolution(two_day_path):
    stamp = np.array(["2025-03-10T09:00:00"], dtype="datetime64[s]")
    for unit in ("s", "ms", "us", "ns"):
        ts = pd.Series(pd.DatetimeIndex(stamp.astype(f"datetime64[{unit}]")).tz_localize("UTC"))
        assert mid_at(two_day_path, ts)[0] == _at(two_day_path, "2025-03-10 09:00")


def test_mid_at_before_the_first_grid_point_raises(two_day_path):
    with pytest.raises(ValueError, match="before"):
        mid_at(two_day_path, _ts("2025-03-07 13:10", "2025-03-07 13:09"))


def test_mid_at_rejects_a_path_that_is_not_in_time_order(two_day_path):
    with pytest.raises(ValueError, match="increasing"):
        mid_at(two_day_path.iloc[::-1], _ts("2025-03-10 09:00"))


def test_mid_at_rejects_naive_or_missing_timestamps(two_day_path):
    with pytest.raises(ValueError, match="time zone"):
        mid_at(two_day_path, pd.Series(pd.to_datetime(["2025-03-10 09:00"])))
    with pytest.raises(ValueError, match="NaT|missing"):
        mid_at(two_day_path, pd.Series(pd.to_datetime(["2025-03-10 09:00", None], utc=True)))
