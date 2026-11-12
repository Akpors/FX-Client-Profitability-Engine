"""Intraday market mid: a Brownian bridge between the real ECB fixes.

The ECB publishes one reference rate per currency per business day. A trade book needs a price at
the minute each trade happens, so this module fills in the day: a synthetic mid, built in log
space, that passes through every real fix and wiggles realistically between them.

* ``trading_grid`` lists the minutes a spot desk trades (a half-open ``[open, close)`` day on
  business days only, with the 13:10 UTC fix on the grid).
* ``simulate_mid_path`` runs a Brownian bridge over that grid, pinned to the fixes.
* ``mid_at`` looks up the mid at arbitrary timestamps (last grid point at or before each one).

Weekends and holidays add no variance because they have no grid points: Friday's last minute and
Monday's first minute are neighbouring grid points, and the move between fixes is spread evenly
over the same number of steps whether or not a weekend sits in between. The mid is synthetic;
only the fixes are real. See ``docs/methodology/data-generation.md`` (section 2).

All timestamps are tz-aware UTC. All randomness comes from the ``numpy.random.Generator`` passed in.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, time

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252
_UNIT = "ns"  # every index this module builds or accepts is normalised to this resolution


def _minute_of_day(value: time, name: str) -> int:
    if value.second or value.microsecond:
        raise ValueError(f"{name} must be a whole minute, got {value.isoformat()}")
    return value.hour * 60 + value.minute


def fixes_for_pair(ecb: pd.DataFrame, pair: str, fix_time: time) -> pd.Series:
    """One pair's ECB fixes as a Series indexed by UTC fix timestamp.

    ``ecb`` is the ``ecb.read_snapshot`` frame (``rate_date``, ``currency``, ``rate``, with
    ``rate`` in units of quote currency per 1 EUR). ``pair`` is a code such as ``EURUSD``. Each
    ``rate_date`` is combined with ``fix_time`` as a UTC wall time (the spec simplification: one
    fixed fix time all year, no DST handling). The result is named after the pair.
    """
    if len(pair) != 6 or not pair.startswith("EUR"):
        raise ValueError(f"pair must look like 'EURUSD', got {pair!r}")
    rows = ecb.loc[ecb["currency"] == pair[3:]]
    if rows.empty:
        raise ValueError(f"no ECB rates for {pair} (currency {pair[3:]})")
    fix_minute = _minute_of_day(fix_time, "fix_time")
    index = pd.DatetimeIndex(pd.to_datetime(rows["rate_date"].to_numpy()))
    index = (index + pd.Timedelta(minutes=fix_minute)).tz_localize("UTC").as_unit(_UNIT)
    return pd.Series(rows["rate"].to_numpy(dtype=float), index=index, name=pair).sort_index()


def trading_grid(
    business_days: Sequence[date],
    hours: tuple[time, time],
    fix_time: time,
    step_minutes: int = 1,
) -> pd.DatetimeIndex:
    """The UTC timestamps at which the mid is simulated: trading minutes on business days only.

    Each day covers the half-open interval ``[open, close)`` at ``step_minutes`` resolution, so
    07:00 to 16:00 at one minute is 540 points (07:00 first, 15:59 last, 16:00 not included).
    ``business_days`` is whatever the caller says is a trading day; weekends and holidays are
    simply absent, which is what stops them carrying any variance. Days are sorted; a repeated
    day is an error.

    The fix time must land on the step lattice: ``open + n * step_minutes`` for a whole ``n``,
    inside ``[open, close)``. It is required, not patched in, because an extra off-lattice point
    would make one step shorter than the rest and break the equal-variance steps the bridge
    assumes. With the default one-minute step any whole-minute fix time qualifies. A fix time
    exactly at the close is rejected too, since the close is outside the half-open day.
    """
    if not isinstance(step_minutes, int) or step_minutes < 1:
        raise ValueError(f"step_minutes must be a positive whole number, got {step_minutes!r}")
    opens, closes = hours
    open_minute = _minute_of_day(opens, "hours open")
    close_minute = _minute_of_day(closes, "hours close")
    fix_minute = _minute_of_day(fix_time, "fix_time")
    if not open_minute < close_minute:
        raise ValueError(f"trading hours must open before they close, got {opens} to {closes}")
    if not open_minute <= fix_minute < close_minute:
        raise ValueError(
            f"fix_time {fix_time.isoformat()} must be inside the half-open trading day "
            f"[{opens.isoformat()}, {closes.isoformat()})"
        )
    if (fix_minute - open_minute) % step_minutes:
        raise ValueError(
            f"fix_time {fix_time.isoformat()} is not on the {step_minutes}-minute step lattice "
            f"that starts at {opens.isoformat()}; choose a step that divides "
            f"{fix_minute - open_minute} minutes"
        )

    days = np.sort(np.array(list(business_days), dtype="datetime64[D]"))
    if len(np.unique(days)) != len(days):
        raise ValueError("business_days contains a duplicate day")
    offsets = np.arange(open_minute, close_minute, step_minutes, dtype=np.int64)
    minutes = days.astype("datetime64[m]").astype(np.int64)[:, None] + offsets[None, :]
    stamps = minutes.ravel().astype("datetime64[m]").astype(f"datetime64[{_UNIT}]")
    return pd.DatetimeIndex(stamps).tz_localize("UTC")


def _as_utc_index(index: pd.Index, what: str) -> pd.DatetimeIndex:
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise ValueError(f"{what} must be a DatetimeIndex with a time zone (UTC)")
    return index.tz_convert("UTC").as_unit(_UNIT)


def _steps_per_day(grid: pd.DatetimeIndex) -> int:
    """How many grid steps make one trading day, read off the grid itself.

    The grid's resolution is its smallest gap (overnight and weekend gaps are larger), and its
    trading day runs from the earliest to the latest time of day on the grid. Both survive
    trimming to the first and last fix, which leaves the first and last days partial: the
    first day's late hours and the last day's early hours together cover the whole day.
    """
    seconds = grid.hour.to_numpy() * 3600 + grid.minute.to_numpy() * 60 + grid.second.to_numpy()
    step = int(np.diff(grid.asi8).min() // 1_000_000_000)
    return int((seconds.max() - seconds.min()) // step) + 1


def simulate_mid_path(
    fixes: pd.Series,
    grid: pd.DatetimeIndex,
    annual_vol: float,
    rng: np.random.Generator,
) -> pd.Series:
    """A synthetic intraday mid on ``grid`` that passes through every fix.

    ``fixes`` is indexed by UTC fix timestamp (rate in quote currency per EUR). Between
    consecutive fixes ``f_k`` and ``f_k+1``, ``T`` grid steps apart, the log mid is a Brownian
    bridge:

        X(t) = log f_k + (t / T) (log f_k+1 - log f_k) + B(t) - (t / T) B(T),   t = 0..T

    where ``B`` is the running sum of independent ``N(0, sigma_step^2)`` draws. The linear term
    carries the real fix-to-fix move; the ``B(t) - (t / T) B(T)`` term is mean-zero noise that
    vanishes at both fixes. The result is ``exp(X)``, exactly equal to the fix at each fix.

    ``sigma_step^2 = annual_vol^2 / (252 x trading_minutes_per_day) x step_minutes``: a trading
    day carries variance ``annual_vol^2 / 252`` however finely the grid slices it. The day
    length is read from the grid (see ``_steps_per_day``). The step variance is the same for
    every step, so a Friday-to-Monday segment gets exactly as many steps, and as much variance,
    as any other overnight.

    The function never extrapolates. It raises ``ValueError`` if any grid point lies outside
    ``[fixes.index.min(), fixes.index.max()]`` or any fix timestamp is not a grid point, so a
    caller must supply fixes on both sides of the window and trim the grid to that bracket.

    One call to ``rng.standard_normal`` draws every increment, ``len(grid) - 1`` of them whatever
    the volatility, so the random stream consumed is fixed by the grid alone.
    """
    if not np.isfinite(annual_vol) or annual_vol < 0:
        raise ValueError(f"annual_vol must be a finite number >= 0, got {annual_vol!r}")
    if fixes.empty:
        raise ValueError("fixes is empty")
    grid = _as_utc_index(grid, "grid")
    if not (grid.is_monotonic_increasing and grid.is_unique):
        raise ValueError("grid must be strictly increasing")
    fix_index = _as_utc_index(fixes.index, "fixes")
    if fix_index.has_duplicates:
        raise ValueError("fixes has a duplicate timestamp")
    values = fixes.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("fixes must be finite (found NaN or infinity)")
    if not (values > 0).all():
        raise ValueError("fixes must be positive")
    order = np.argsort(fix_index.asi8)
    fix_index, values = fix_index[order], values[order]

    outside = (grid < fix_index[0]) | (grid > fix_index[-1])
    if outside.any():
        raise ValueError(
            f"{int(outside.sum())} grid point(s) lie outside the fixes' span "
            f"[{fix_index[0]}, {fix_index[-1]}] (grid runs {grid[0]} to {grid[-1]}); "
            "the path never extrapolates, so trim the grid to the first and last fix"
        )
    positions = grid.get_indexer(fix_index)
    if (positions < 0).any():
        missing = fix_index[positions < 0]
        raise ValueError(
            f"{len(missing)} fix timestamp(s) are not on the grid, first {missing[0]}; "
            "every fix must be a grid point"
        )

    name = fixes.name
    if len(fix_index) == 1:  # the grid is then exactly that one point
        return pd.Series(values, index=grid, name=name)

    # Grid[0] is the first fix and grid[-1] the last (they are on the grid and nothing lies
    # outside them), so the segments tile the grid with no gaps.
    n = len(grid)
    sigma_step = annual_vol / np.sqrt(TRADING_DAYS_PER_YEAR * _steps_per_day(grid))
    increments = rng.standard_normal(n - 1) * sigma_step
    walk = np.concatenate(([0.0], np.cumsum(increments)))  # B at each grid point, one long walk

    # For every grid point, the segment it opens (a fix point opens the next segment, t = 0; the
    # final point closes the last one, t = T). B restarts at zero in each segment, which is the
    # long walk minus its value at the segment's first fix.
    k = np.clip(np.searchsorted(positions, np.arange(n), side="right") - 1, 0, len(values) - 2)
    start, end = positions[k], positions[k + 1]
    weight = (np.arange(n) - start) / (end - start)  # t / T
    brownian = walk - walk[start]
    brownian_end = walk[end] - walk[start]
    log_lo, log_hi = np.log(values[k]), np.log(values[k + 1])
    log_mid = log_lo + weight * (log_hi - log_lo) + brownian - weight * brownian_end

    path = np.exp(log_mid)
    path[positions] = values  # exact at the fixes, free of last-bit rounding in exp(log(.))
    return pd.Series(path, index=grid, name=name)


def mid_at(path: pd.Series, ts: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    """The mid at each timestamp in ``ts``: the last grid point at or before it.

    This is an as-of lookup, so a timestamp between two grid points gets the earlier one, and one
    on a weekend or after the close gets the last point before it. A timestamp before the first
    grid point has no mid to look up and raises ``ValueError``, as do tz-naive or missing
    (``NaT``) timestamps. Returns a float ``numpy`` array in the order of ``ts``.
    """
    grid = _as_utc_index(path.index, "path")
    if not grid.is_monotonic_increasing:
        raise ValueError("path must be indexed by an increasing grid")
    when = _as_utc_index(pd.DatetimeIndex(ts), "ts")
    if when.isna().any():
        raise ValueError("ts contains a missing timestamp (NaT)")
    position = grid.searchsorted(when, side="right") - 1
    if (position < 0).any():
        raise ValueError(
            f"{int((position < 0).sum())} timestamp(s) fall before the first grid point "
            f"{grid[0]}, first {when[position < 0][0]}"
        )
    return path.to_numpy(dtype=float)[position]
