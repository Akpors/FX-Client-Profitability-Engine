"""Tests for trade generation, client pricing and cover execution (the ``raw.trades`` contract)."""

from __future__ import annotations

import time as timer
from datetime import date

import numpy as np
import pandas as pd
import pytest

from fxengine.clients import generate_clients
from fxengine.config import GeneratorConfig
from fxengine.trades import COLUMNS, generate_trades, ticket_band_multiplier

RAW_TRADES_DTYPES = {
    "trade_id": "str",
    "client_id": "str",
    "trade_ts": "datetime64[ns, UTC]",
    "trade_date": "object",  # python datetime.date, like the ECB frame's rate_date
    "currency_pair": "str",
    "client_side": "str",
    "notional_base": "int64",
    "client_rate": "float64",
    "cover_rate": "float64",
    "mid_rate": "float64",
    "benchmark_rate": "float64",
    "markup_bps_applied": "float64",
}


def _book(
    cfg: GeneratorConfig, window: tuple[date, date], ecb: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Clients then trades from one generator, in the order ``run-all`` uses."""
    rng = np.random.default_rng(cfg.seed)
    clients = generate_clients(cfg, window, rng)
    return clients, generate_trades(cfg, clients, ecb, window, rng)


@pytest.fixture(scope="module")
def small_book(small_cfg, small_window, ecb) -> tuple[pd.DataFrame, pd.DataFrame]:
    return _book(small_cfg, small_window, ecb)


@pytest.fixture(scope="module")
def trades(small_book) -> pd.DataFrame:
    return small_book[1]


@pytest.fixture(scope="module")
def full_book(full_cfg, full_window, ecb) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    started = timer.perf_counter()
    clients, trades = _book(full_cfg, full_window, ecb)
    return clients, trades, timer.perf_counter() - started


def _with_segment(trades: pd.DataFrame, clients: pd.DataFrame) -> pd.DataFrame:
    return trades.merge(clients[["client_id", "segment"]], on="client_id", how="left")


def _tick(cfg: GeneratorConfig, pairs: pd.Series) -> np.ndarray:
    return 10.0 ** -pairs.map(lambda p: cfg.pairs[p].price_decimals).to_numpy(dtype=float)


# --- contract ---------------------------------------------------------------------------------


def test_columns_and_dtypes_match_raw_contract(trades: pd.DataFrame):
    assert list(trades.columns) == COLUMNS == list(RAW_TRADES_DTYPES)
    assert {col: str(dtype) for col, dtype in trades.dtypes.items()} == RAW_TRADES_DTYPES
    assert len(trades) > 500
    assert trades["trade_id"].is_unique
    assert trades["trade_id"].str.fullmatch(r"T\d{7}").all()
    assert trades["trade_id"].iloc[0] == "T0000001"
    assert trades["trade_ts"].is_monotonic_increasing  # ids follow time
    assert set(trades["client_side"]) == {"BUY", "SELL"}
    assert trades["trade_date"].map(type).eq(date).all()
    assert not trades.isna().any().any()


def test_notional_rounded_to_thousand_with_minimum(trades: pd.DataFrame):
    assert (trades["notional_base"] % 1000 == 0).all()
    assert trades["notional_base"].min() >= 1000


def test_rates_rounded_to_pair_precision(small_cfg: GeneratorConfig, trades: pd.DataFrame):
    tick = _tick(small_cfg, trades["currency_pair"])
    for col in ("client_rate", "cover_rate", "mid_rate"):
        steps = trades[col].to_numpy() / tick
        np.testing.assert_allclose(steps, np.round(steps), atol=1e-6, err_msg=col)


def test_client_rate_on_correct_side_of_mid(small_cfg: GeneratorConfig, trades: pd.DataFrame):
    tick = _tick(small_cfg, trades["currency_pair"])
    gap = (trades["client_rate"] - trades["mid_rate"]).to_numpy()
    buy = (trades["client_side"] == "BUY").to_numpy()
    assert (gap[buy] >= -tick[buy] - 1e-12).all()
    assert (gap[~buy] <= tick[~buy] + 1e-12).all()
    # The unrounded markup is always positive, so the bulk of trades sit strictly off mid.
    assert (trades["markup_bps_applied"] > 0).all()


def test_every_trade_has_same_day_benchmark(ecb: pd.DataFrame, trades: pd.DataFrame):
    published = ecb.assign(currency_pair="EUR" + ecb["currency"]).rename(
        columns={"rate_date": "trade_date", "rate": "published"}
    )
    joined = trades.merge(
        published[["trade_date", "currency_pair", "published"]],
        on=["trade_date", "currency_pair"],
        how="left",
        validate="many_to_one",
    )
    assert joined["published"].notna().all()
    # The ECB value as published, not rounded to the pair's quoting precision.
    assert (joined["benchmark_rate"] == joined["published"]).all()


def test_trade_dates_inside_window_and_on_ecb_days(
    ecb: pd.DataFrame, small_window: tuple[date, date], trades: pd.DataFrame
):
    start, end = small_window
    assert trades["trade_date"].between(start, end).all()
    assert set(trades["trade_date"]) <= set(ecb["rate_date"])
    assert (trades["trade_ts"].dt.date == trades["trade_date"]).all()
    assert trades["trade_ts"].map(lambda ts: ts.weekday() < 5).all()


def test_timestamps_within_trading_hours(small_cfg: GeneratorConfig, trades: pd.DataFrame):
    opens, closes = small_cfg.trading_hours_utc
    assert str(trades["trade_ts"].dt.tz) == "UTC"
    clock = trades["trade_ts"].dt.time
    assert (clock >= opens).all()
    assert (clock < closes).all()  # the trading day is half-open


def test_no_trades_before_onboarding(small_book):
    clients, trades = small_book
    onboarded = trades["client_id"].map(clients.set_index("client_id")["onboarded_date"])
    assert (trades["trade_date"] >= onboarded).all()
    assert set(trades["client_id"]) <= set(clients["client_id"])


def test_markup_orderings(full_cfg: GeneratorConfig, small_window, ecb):
    """All 200 clients over the 2-month window: the 20-client book has only two large
    corporates, too few to trade every pair, while 200 clients still take well under a second."""
    clients, trades = _book(full_cfg, small_window, ecb)
    book = _with_segment(trades, clients)
    eurusd = book[book["currency_pair"] == "EURUSD"].groupby("segment")["markup_bps_applied"]
    median = eurusd.median()
    assert median["sme"] > median["mid_market"] > median["large_corporate"], median
    corporate = book[book["segment"] == "large_corporate"]
    by_pair = corporate.groupby("currency_pair")["markup_bps_applied"].median()
    assert by_pair["EURTRY"] > by_pair["EURUSD"], by_pair


def test_cover_slippage_sign_convention(small_cfg: GeneratorConfig, small_window, ecb):
    """Positive slippage is worse for the dealer on either side: with a fixed 2 bps of adverse
    selection, a BUY is covered 2 bps above mid and a SELL 2 bps below, and dealer gross P&L in
    bps is the markup minus those 2 bps."""
    segments = {
        name: seg.model_copy(update={"adverse_selection_bps": (2.0, 0.0)})
        for name, seg in small_cfg.segments.items()
    }
    cfg = small_cfg.model_copy(update={"segments": segments})
    _, trades = _book(cfg, small_window, ecb)
    sign = np.where(trades["client_side"] == "BUY", 1.0, -1.0)
    mid = trades["mid_rate"].to_numpy()
    # Rounding moves each rate by at most half a tick, so allow one tick (in bps) plus a hair.
    tick_bps = _tick(cfg, trades["currency_pair"]) / mid * 1e4 * 1.01
    slippage = sign * (trades["cover_rate"].to_numpy() / mid - 1) * 1e4
    assert (np.abs(slippage - 2.0) <= tick_bps).all()
    gross_bps = sign * (trades["client_rate"] - trades["cover_rate"]).to_numpy() / mid * 1e4
    expected = trades["markup_bps_applied"].to_numpy() - 2.0
    assert (np.abs(gross_bps - expected) <= 2 * tick_bps).all()


def test_markup_formula_without_noise(small_cfg: GeneratorConfig, small_window, ecb):
    """With the per-trade noise switched off, every markup is the documented formula exactly."""
    segments = {
        name: seg.model_copy(update={"markup_noise_sigma": 0.0})
        for name, seg in small_cfg.segments.items()
    }
    cfg = small_cfg.model_copy(update={"segments": segments})
    clients, trades = _book(cfg, small_window, ecb)
    c = clients.set_index("client_id").loc[trades["client_id"]].reset_index()
    base = [
        cfg.segments[seg].base_markup_bps[cfg.pairs[pair].liquidity_tier]
        for seg, pair in zip(c["segment"], trades["currency_pair"], strict=True)
    ]
    years = np.array([(d - small_window[0]).days / 365.25 for d in trades["trade_date"]])
    compression = np.exp(-c["segment"].map(
        lambda s: cfg.segments[s].compression_per_year).to_numpy() * years)
    legacy = np.where(c["legacy_deal"], 1 - cfg.legacy_deal.discount_pct, 1.0)
    expected = (
        np.array(base)
        * ticket_band_multiplier(cfg, trades["notional_base"].to_numpy())
        * (1 - c["volume_tier_discount_pct"].to_numpy())
        * legacy
        * compression
        * c["negotiation_factor"].to_numpy()
    )
    np.testing.assert_allclose(trades["markup_bps_applied"].to_numpy(), expected, rtol=1e-12)


def test_ticket_band_edges_are_half_open(full_cfg: GeneratorConfig):
    notional = np.array([1_000, 24_000, 25_000, 99_000, 100_000, 500_000, 9_999_000, 10_000_000])
    np.testing.assert_allclose(
        ticket_band_multiplier(full_cfg, notional),
        [1.80, 1.80, 1.35, 1.35, 1.00, 0.80, 0.65, 0.55],
    )


def test_reproducible_trades(small_cfg: GeneratorConfig, small_window, ecb, trades):
    _, again = _book(small_cfg, small_window, ecb)
    first = pd.util.hash_pandas_object(trades, index=True).to_numpy()
    second = pd.util.hash_pandas_object(again, index=True).to_numpy()
    np.testing.assert_array_equal(first, second)
    other_cfg = small_cfg.model_copy(update={"seed": small_cfg.seed + 1})
    _, other = _book(other_cfg, small_window, ecb)
    assert not trades["notional_base"].equals(other["notional_base"])


def test_window_must_be_bracketed_by_fixes(small_cfg, small_window, ecb):
    clients = generate_clients(small_cfg, small_window, np.random.default_rng(0))
    cut = ecb[ecb["rate_date"] >= small_window[0]]  # no fix before the window opens
    with pytest.raises(ValueError, match="fix"):
        generate_trades(small_cfg, clients, cut, small_window, np.random.default_rng(0))


# --- full configuration -----------------------------------------------------------------------


def _realised_annual_volume(trades: pd.DataFrame, clients: pd.DataFrame, window) -> pd.Series:
    start, end = window
    active_from = clients.set_index("client_id")["onboarded_date"].map(lambda d: max(d, start))
    active_years = active_from.map(lambda d: ((end - d).days + 1) / 365.25)
    return trades.groupby("client_id")["notional_base"].sum() / active_years


def _client_g10_major_markup(cfg, trades: pd.DataFrame) -> pd.Series:
    majors = [p for p, pc in cfg.pairs.items() if pc.liquidity_tier == "g10_major"]
    g10 = trades[trades["currency_pair"].isin(majors)]
    return g10.groupby("client_id")["markup_bps_applied"].median()


@pytest.mark.slow
def test_volume_discount_visible(full_cfg, full_window, full_book):
    clients, trades, _ = full_book
    frame = pd.DataFrame(
        {
            "volume": _realised_annual_volume(trades, clients, full_window),
            "markup": _client_g10_major_markup(full_cfg, trades),
        }
    ).dropna()
    frame["segment"] = clients.set_index("client_id").loc[frame.index, "segment"]
    rho = frame["volume"].rank().corr(frame["markup"].rank())  # Spearman, without scipy
    within = frame.groupby("segment").apply(
        lambda g: g["volume"].rank().corr(g["markup"].rank()), include_groups=False
    )
    print(f"\nSpearman overall {rho:.3f}; within segment {within.round(3).to_dict()}")
    assert rho < -0.3, rho


@pytest.mark.slow
def test_trade_count_near_target(full_cfg, full_book):
    _, trades, seconds = full_book
    print(f"\nfull generation: {len(trades):,} trades in {seconds:.1f} s")
    assert 0.8 * full_cfg.target_trades <= len(trades) <= 1.2 * full_cfg.target_trades
    assert seconds < 120
