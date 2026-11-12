"""Tests for the synthetic client book."""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from fxengine.clients import HOME_COUNTRIES, SEGMENT_PREFIX, generate_clients
from fxengine.config import DEFAULT_CONFIG_PATH, GeneratorConfig, load_config

WINDOW = (date(2024, 11, 1), date(2026, 10, 31))
COLUMNS = [
    "client_id",
    "client_name",
    "segment",
    "home_country",
    "onboarded_date",
    "legacy_deal",
    "trades_per_month",
    "ticket_mu",
    "ticket_sigma",
    "pair_weights",
    "expected_annual_volume_eur",
    "volume_tier_discount_pct",
]


@pytest.fixture(scope="module")
def cfg() -> GeneratorConfig:
    return load_config(DEFAULT_CONFIG_PATH)


@pytest.fixture(scope="module")
def clients(cfg: GeneratorConfig) -> pd.DataFrame:
    return generate_clients(cfg, WINDOW, np.random.default_rng(42))


def test_columns(clients: pd.DataFrame):
    assert list(clients.columns) == COLUMNS


def test_client_count_and_ids_unique(cfg: GeneratorConfig, clients: pd.DataFrame):
    assert len(clients) == cfg.n_clients == 200
    assert clients["client_id"].is_unique
    assert clients["client_id"].str.fullmatch(r"(LC|MM|SM|AM|HF|PI)-\d{3}").all()
    for row in clients.itertuples():
        assert row.client_id.startswith(SEGMENT_PREFIX[row.segment] + "-")
        assert row.client_name == f"Client {row.client_id}"
    # Numbering restarts at 001 within each segment and has no gaps.
    for segment, group in clients.groupby("segment"):
        numbers = sorted(int(cid.split("-")[1]) for cid in group["client_id"])
        assert numbers == list(range(1, len(group) + 1)), segment


def test_segment_mix_close_to_shares(cfg: GeneratorConfig, clients: pd.DataFrame):
    counts = clients["segment"].value_counts()
    assert set(counts.index) == set(cfg.segments)
    for name, segment in cfg.segments.items():
        assert abs(counts[name] - segment.client_share * cfg.n_clients) <= 5, name


def test_reproducible(cfg: GeneratorConfig):
    first = generate_clients(cfg, WINDOW, np.random.default_rng(42))
    second = generate_clients(cfg, WINDOW, np.random.default_rng(42))
    pd.testing.assert_frame_equal(first, second)
    other = generate_clients(cfg, WINDOW, np.random.default_rng(43))
    assert not first["trades_per_month"].equals(other["trades_per_month"])


def test_does_not_use_global_numpy_state(cfg: GeneratorConfig):
    np.random.seed(0)
    before = np.random.get_state()[1].copy()
    generate_clients(cfg, WINDOW, np.random.default_rng(1))
    np.testing.assert_array_equal(np.random.get_state()[1], before)


def test_volume_tier_discount_monotonic(clients: pd.DataFrame):
    ordered = clients.sort_values("expected_annual_volume_eur")
    assert ordered["volume_tier_discount_pct"].is_monotonic_increasing
    assert clients["volume_tier_discount_pct"].nunique() >= 4  # the tiers actually bite


def test_expected_volume_and_tier_follow_the_brief(cfg: GeneratorConfig, clients: pd.DataFrame):
    mean_ticket = np.exp(clients["ticket_mu"] + clients["ticket_sigma"] ** 2 / 2)
    expected = clients["trades_per_month"] * 12 * mean_ticket
    np.testing.assert_allclose(clients["expected_annual_volume_eur"], expected)
    for row in clients.itertuples():
        eligible = [
            t.discount_pct
            for t in cfg.volume_tiers
            if t.min_annual_volume_eur <= row.expected_annual_volume_eur
        ]
        assert row.volume_tier_discount_pct == eligible[-1]


def test_onboarding_split(clients: pd.DataFrame):
    start, end = WINDOW
    dates = clients["onboarded_date"]
    inside = (dates >= start) & (dates <= end)
    assert 0.16 <= inside.mean() <= 0.24  # 20% onboarded inside the window
    assert (dates <= end).all()
    assert (dates >= start - timedelta(days=6 * 365)).all()
    assert (dates[~inside] < start).all()


def test_legacy_deals_belong_to_clients_who_predate_the_window(clients: pd.DataFrame):
    start, _ = WINDOW
    legacy = clients[clients["legacy_deal"]]
    assert 15 <= len(legacy) <= 50
    assert (legacy["onboarded_date"] < start).all()
    assert clients["legacy_deal"].dtype == bool


def test_home_country_comes_from_the_fixed_list(clients: pd.DataFrame):
    assert set(HOME_COUNTRIES) >= {"DE", "FR", "GB", "CH", "NO", "SE", "PL", "IE"}
    assert len(HOME_COUNTRIES) == 30  # EU27 + GB + CH + NO
    assert set(clients["home_country"]) <= set(HOME_COUNTRIES)
    assert clients["home_country"].nunique() >= 8


def test_pair_weights_are_a_distribution_over_the_configured_pairs(
    cfg: GeneratorConfig, clients: pd.DataFrame
):
    for weights in clients["pair_weights"]:
        assert set(weights) == set(cfg.pairs)
        assert all(w >= 0 for w in weights.values())
        assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9)


def test_pair_weights_centre_on_the_segment_popularity(cfg: GeneratorConfig, clients: pd.DataFrame):
    # Dirichlet draws are noisy per client but unbiased: the segment average tracks popularity.
    for name, segment in cfg.segments.items():
        popularity = pd.Series(segment.pair_popularity)
        popularity /= popularity.sum()
        mean = pd.DataFrame(list(clients.loc[clients["segment"] == name, "pair_weights"])).mean()
        assert abs(mean["EURUSD"] - popularity["EURUSD"]) < 0.08, name
    # ... while individual clients genuinely differ.
    usd = pd.Series([w["EURUSD"] for w in clients["pair_weights"]])
    assert usd.std() > 0.03


def test_ticket_and_frequency_parameters_are_per_client(clients: pd.DataFrame):
    assert clients["trades_per_month"].gt(0).all()
    assert clients["ticket_sigma"].gt(0).all()
    assert clients["ticket_mu"].nunique() == len(clients)
    # Median ticket by segment runs from SME up to asset managers.
    median_ticket = clients.groupby("segment")["ticket_mu"].median().map(math.exp)
    assert median_ticket["sme"] < median_ticket["mid_market"] < median_ticket["large_corporate"]
    assert median_ticket["large_corporate"] < median_ticket["asset_manager"]


def test_fast_flow_segments_trade_far_more_often(clients: pd.DataFrame):
    mean_tpm = clients.groupby("segment")["trades_per_month"].mean()
    slow = mean_tpm[["sme", "mid_market", "large_corporate", "asset_manager"]].max()
    assert mean_tpm["hedge_fund"] > 4 * slow
    assert mean_tpm["payment_institution"] > 4 * slow


def test_expected_trade_count_is_close_to_target(cfg: GeneratorConfig, clients: pd.DataFrame):
    """Pins the volume calibration that Task 5's full-book test depends on."""
    start, end = WINDOW
    window_days = (end - start).days + 1
    active_from = clients["onboarded_date"].map(lambda d: max(d, start))
    active_share = active_from.map(lambda d: ((end - d).days + 1) / window_days)
    expected = float((clients["trades_per_month"] * 24 * active_share).sum())
    assert 0.9 * cfg.target_trades <= expected <= 1.1 * cfg.target_trades


def test_defaults_let_the_volume_discount_emerge(cfg: GeneratorConfig, clients: pd.DataFrame):
    """A client-level proxy for Task 5's Spearman check, before any trade is generated.

    Expected G10-major markup = base x ticket-band multiplier (at the client's median ticket)
    x (1 - volume-tier discount) x (1 - legacy discount if flagged). Noise and compression are
    left out: they add scatter, not direction.
    """
    maxima = [
        math.inf if b.max_ticket_eur is None else b.max_ticket_eur for b in cfg.ticket_bands
    ]
    multipliers = [b.markup_multiplier for b in cfg.ticket_bands]

    def band_multiplier(ticket: float) -> float:
        return multipliers[next(i for i, m in enumerate(maxima) if ticket < m)]

    base = clients["segment"].map(lambda s: cfg.segments[s].base_markup_bps["g10_major"])
    band = clients["ticket_mu"].map(lambda mu: band_multiplier(math.exp(mu)))
    legacy = np.where(clients["legacy_deal"], 1 - cfg.legacy_deal.discount_pct, 1.0)
    markup = base * band * (1 - clients["volume_tier_discount_pct"]) * legacy

    # Spearman = Pearson on ranks. (pandas' method="spearman" needs scipy, which is not installed.)
    rho = clients["expected_annual_volume_eur"].rank().corr(markup.rank())
    assert rho < -0.5, rho
