"""Spot trades: when clients trade, how the dealer prices them and how the dealer covers.

``generate_trades`` turns the client book into the ``raw.trades`` table. Each trade is priced
from the market mid the way a dealer's pricing grid would price it, and covered in the market
with some slippage. Nothing here knows which clients will turn out to be profitable: the
volume-versus-margin pattern comes out of the grid (volume tiers, ticket bands, legacy deals,
compression), the client's own negotiated level and adverse selection on the cover.

Markup, in basis points of mid, one-sided::

    markup_bps = base_markup_bps[segment][liquidity_tier(pair)]
                 x band_multiplier(notional)                    ticket band, [min, max) edges
                 x (1 - volume_tier_discount_pct)               client's volume tier
                 x (1 - legacy_deal.discount_pct)   if the client holds a legacy deal
                 x exp(-compression_per_year x years_since_window_start)
                 x negotiation_factor                           per client, LogNormal(0, sigma)
                 x LogNormal(0, markup_noise_sigma)             per trade

Rates, with ``side_sign`` = +1 when the client BUYS EUR and -1 when it SELLS EUR::

    client_rate = mid x (1 + side_sign x markup_bps / 1e4)
    cover_rate  = mid x (1 + side_sign x slippage_bps / 1e4),  slippage ~ Normal(mean, sd)

Positive slippage is worse for the dealer on either side, so dealer gross P&L in bps of mid is
``markup_bps - slippage_bps`` before rounding. See ``docs/methodology/data-generation.md``
(section 3).

Random draws, in this fixed order, all from the one generator passed in:

1. One mid path per pair, in sorted pair-code order (``len(grid) - 1`` normals each).
2. Daily trade counts: one Poisson matrix, clients x window business days, row-major.
3. Per trade, one vectorised call each: second of the trading day, pair, side, ticket,
   markup noise, cover slippage.
"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd

from fxengine.config import GeneratorConfig
from fxengine.market import fixes_for_pair, mid_at, simulate_mid_path, trading_grid

COLUMNS = [
    "trade_id",
    "client_id",
    "trade_ts",
    "trade_date",
    "currency_pair",
    "client_side",
    "notional_base",
    "client_rate",
    "cover_rate",
    "mid_rate",
    "benchmark_rate",
    "markup_bps_applied",
]

TICKET_ROUNDING_EUR = 1_000
DAYS_PER_YEAR = 365.25


def ticket_band_multiplier(cfg: GeneratorConfig, notional: np.ndarray) -> np.ndarray:
    """Markup multiplier of the ticket band each EUR notional falls in.

    Bands cover ``[previous max, max)``: ``max_ticket_eur`` is exclusive and the last band is
    open-ended, so a EUR 25,000 ticket is in the band that starts at 25,000.
    """
    edges = np.array([band.max_ticket_eur for band in cfg.ticket_bands[:-1]], dtype=float)
    multipliers = np.array([band.markup_multiplier for band in cfg.ticket_bands])
    return multipliers[np.searchsorted(edges, np.asarray(notional, dtype=float), side="right")]


def _months_in(window: tuple[date, date]) -> int:
    start, end = window
    return (end.year - start.year) * 12 + end.month - start.month + 1


def _round_to_decimals(values: np.ndarray, decimals: np.ndarray) -> np.ndarray:
    """Round each value to its own number of decimals (a loop over the few distinct ones)."""
    out = np.empty_like(values)
    for d in np.unique(decimals):
        mask = decimals == d
        out[mask] = np.round(values[mask], int(d))
    return out


def _mid_paths(
    cfg: GeneratorConfig, ecb: pd.DataFrame, pairs: list[str], rng: np.random.Generator
) -> dict[str, pd.Series]:
    """One synthetic mid path per pair over every ECB business day in the snapshot.

    The grid covers all snapshot days, buffer days included, trimmed to the span from the first
    fix to the last, so every trading minute inside the window is bracketed by fixes.
    """
    days = sorted(set(ecb["rate_date"]))
    grid = trading_grid(days, cfg.trading_hours_utc, cfg.fix_time_utc)
    paths: dict[str, pd.Series] = {}
    for pair in pairs:  # sorted, so the random stream does not depend on YAML order
        fixes = fixes_for_pair(ecb, pair, cfg.fix_time_utc)
        bracket = grid[(grid >= fixes.index.min()) & (grid <= fixes.index.max())]
        paths[pair] = simulate_mid_path(fixes, bracket, cfg.pairs[pair].annual_vol, rng)
    return paths


def _window_days(ecb: pd.DataFrame, cfg: GeneratorConfig, window: tuple[date, date]) -> list[date]:
    """ECB business days inside the window, checked to be bracketed by fixes on both sides."""
    start, end = window
    if start > end:
        raise ValueError(f"window ends before it starts: {start} to {end}")
    all_days = sorted(set(ecb["rate_date"]))
    days = [d for d in all_days if start <= d <= end]
    if not days:
        raise ValueError(f"the ECB data has no business day inside the window {start} to {end}")
    opens, closes = cfg.trading_hours_utc
    first_fix = datetime.combine(all_days[0], cfg.fix_time_utc)
    last_fix = datetime.combine(all_days[-1], cfg.fix_time_utc)
    if not first_fix <= datetime.combine(days[0], opens):
        raise ValueError(
            f"no ECB fix before the window's first trading minute ({days[0]} {opens}); the "
            "snapshot must start before the window so the intraday mid is bracketed by fixes"
        )
    if not datetime.combine(days[-1], closes) <= last_fix:
        raise ValueError(
            f"no ECB fix after the window's last trading minute ({days[-1]} {closes}); the "
            "snapshot must run past the window so the intraday mid is bracketed by fixes"
        )
    return days


def generate_trades(
    cfg: GeneratorConfig,
    clients: pd.DataFrame,
    ecb: pd.DataFrame,
    window: tuple[date, date],
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Generate, price and cover every spot trade in ``window`` for the ``clients`` book.

    ``clients`` is the ``generate_clients`` frame, ``ecb`` the ``read_snapshot`` frame (rates
    in quote currency per EUR, covering the window plus buffer days), ``window`` the first and
    last day of the trading window. Returns the ``raw.trades`` table with the columns in
    ``COLUMNS``, sorted by ``trade_ts`` and numbered ``T0000001`` onwards in that order.

    The full markup formula and the draw order are in the module docstring. Trade timestamps
    are uniform at one-second resolution within the half-open trading day; ``mid_rate`` is the
    mid at the last grid minute at or before the trade. ``client_rate``, ``cover_rate`` and
    ``mid_rate`` are rounded to the pair's ``price_decimals``; ``benchmark_rate`` is the ECB
    rate as published and ``markup_bps_applied`` is the markup before rounding.
    """
    pairs = sorted(cfg.pairs)
    days = _window_days(ecb, cfg, window)
    start = window[0]

    # 1. Mid paths.
    paths = _mid_paths(cfg, ecb, pairs, rng)

    # 2. How many trades each client does each business day, from onboarding on.
    day_values = np.array(days, dtype="datetime64[D]")
    onboarded = np.array(list(clients["onboarded_date"]), dtype="datetime64[D]")
    active = day_values[None, :] >= onboarded[:, None]
    per_day = clients["trades_per_month"].to_numpy(dtype=float) / (len(days) / _months_in(window))
    counts = rng.poisson(per_day[:, None] * active)
    client_idx, day_idx = np.nonzero(counts)
    repeats = counts[client_idx, day_idx]
    client_idx, day_idx = np.repeat(client_idx, repeats), np.repeat(day_idx, repeats)
    n = len(client_idx)

    # Client attributes, broadcast to trades.
    segment_names = sorted(cfg.segments)
    seg_of_client = clients["segment"].map(segment_names.index).to_numpy()
    seg = seg_of_client[client_idx]

    def by_segment(get) -> np.ndarray:
        return np.array([get(cfg.segments[name]) for name in segment_names], dtype=float)[seg]

    weights = np.array(
        [[w[pair] for pair in pairs] for w in clients["pair_weights"]], dtype=float
    )
    cumulative = np.cumsum(weights / weights.sum(axis=1, keepdims=True), axis=1)
    cumulative[:, -1] = 1.0

    # 3. Per-trade draws, one call each, in this order.
    opens, closes = cfg.trading_hours_utc
    open_s = opens.hour * 3600 + opens.minute * 60 + opens.second
    close_s = closes.hour * 3600 + closes.minute * 60 + closes.second
    second_of_day = open_s + rng.integers(0, close_s - open_s, size=n)
    pair_u = rng.random(n)
    side_u = rng.random(n)
    tickets = rng.lognormal(
        clients["ticket_mu"].to_numpy(dtype=float)[client_idx],
        clients["ticket_sigma"].to_numpy(dtype=float)[client_idx],
    )
    noise_z = rng.standard_normal(n)
    slippage_z = rng.standard_normal(n)

    pair_idx = (pair_u[:, None] >= cumulative[client_idx]).sum(axis=1)
    side_sign = np.where(side_u < by_segment(lambda s: s.buy_probability), 1.0, -1.0)
    notional = np.maximum(
        np.rint(tickets / TICKET_ROUNDING_EUR) * TICKET_ROUNDING_EUR, TICKET_ROUNDING_EUR
    ).astype(np.int64)
    trade_ts = (
        day_values[day_idx].astype("datetime64[s]") + second_of_day.astype("timedelta64[s]")
    ).astype("datetime64[ns]")

    # Pricing.
    tiers = [cfg.pairs[pair].liquidity_tier for pair in pairs]
    base = np.array(
        [[cfg.segments[name].base_markup_bps[t] for t in tiers] for name in segment_names]
    )[seg, pair_idx]
    legacy = np.where(
        clients["legacy_deal"].to_numpy(dtype=bool)[client_idx],
        1.0 - cfg.legacy_deal.discount_pct,
        1.0,
    )
    years = np.array([(d - start).days / DAYS_PER_YEAR for d in days])[day_idx]
    markup_bps = (
        base
        * ticket_band_multiplier(cfg, notional)
        * (1.0 - clients["volume_tier_discount_pct"].to_numpy(dtype=float)[client_idx])
        * legacy
        * np.exp(-by_segment(lambda s: s.compression_per_year) * years)
        * clients["negotiation_factor"].to_numpy(dtype=float)[client_idx]
        * np.exp(by_segment(lambda s: s.markup_noise_sigma) * noise_z)
    )
    slippage_bps = (
        by_segment(lambda s: s.adverse_selection_bps[0])
        + by_segment(lambda s: s.adverse_selection_bps[1]) * slippage_z
    )

    # Market data: the mid at each trade and the day's ECB fix.
    ts_index = pd.DatetimeIndex(trade_ts).tz_localize("UTC")
    mid = np.empty(n)
    for k, pair in enumerate(pairs):
        mask = pair_idx == k
        if mask.any():
            mid[mask] = mid_at(paths[pair], ts_index[mask])
    fixes = ecb.pivot(index="rate_date", columns="currency", values="rate")
    fixes = fixes.reindex(index=days, columns=[pair[3:] for pair in pairs])
    benchmark = fixes.to_numpy(dtype=float)[day_idx, pair_idx]
    if np.isnan(benchmark).any():
        bad = int(np.flatnonzero(np.isnan(benchmark))[0])
        raise ValueError(
            f"no ECB rate for {pairs[pair_idx[bad]]} on {days[day_idx[bad]]}, a trading day"
        )

    decimals = np.array([cfg.pairs[pair].price_decimals for pair in pairs])[pair_idx]
    client_rate = _round_to_decimals(mid * (1.0 + side_sign * markup_bps / 1e4), decimals)
    cover_rate = _round_to_decimals(mid * (1.0 + side_sign * slippage_bps / 1e4), decimals)

    # Deterministic order: time, then client_id, then draw order.
    client_ids = clients["client_id"].to_numpy(dtype=object)
    client_rank = np.argsort(np.argsort(client_ids, kind="stable"), kind="stable")[client_idx]
    order = np.lexsort((np.arange(n), client_rank, trade_ts.astype(np.int64)))

    trade_dates = np.array(days, dtype=object)
    frame = pd.DataFrame(
        {
            "trade_id": pd.Series(np.arange(1, n + 1)).astype(str).str.zfill(7).radd("T"),
            "client_id": pd.Series(client_ids[client_idx][order], dtype="str"),
            "trade_ts": ts_index[order],
            "trade_date": trade_dates[day_idx][order],
            "currency_pair": pd.Series(np.array(pairs)[pair_idx][order], dtype="str"),
            "client_side": pd.Series(np.where(side_sign > 0, "BUY", "SELL")[order], dtype="str"),
            "notional_base": notional[order],
            "client_rate": client_rate[order],
            "cover_rate": cover_rate[order],
            "mid_rate": _round_to_decimals(mid, decimals)[order],
            "benchmark_rate": benchmark[order],
            "markup_bps_applied": markup_bps[order],
        }
    )
    return frame[COLUMNS]
