"""Tests for the generator configuration schema, its validation and the shipped defaults."""

from __future__ import annotations

from collections.abc import Callable
from datetime import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from fxengine.config import DEFAULT_CONFIG_PATH, GeneratorConfig, load_config
from fxengine.ecb import CURRENCIES

TIERS = ("g10_major", "g10_minor", "cee", "em")


@pytest.fixture(scope="module")
def cfg() -> GeneratorConfig:
    return load_config(DEFAULT_CONFIG_PATH)


@pytest.fixture
def variant(tmp_path: Path) -> Callable[[Callable[[dict[str, Any]], None]], Path]:
    """Write a mutated copy of the default YAML into ``tmp_path`` and return its path."""

    def make(mutate: Callable[[dict[str, Any]], None]) -> Path:
        raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text())
        mutate(raw)
        path = tmp_path / "generator.yaml"
        path.write_text(yaml.safe_dump(raw, sort_keys=False))
        return path

    return make


# --- the shipped defaults ---------------------------------------------------------------------


def test_default_config_loads(cfg: GeneratorConfig):
    assert cfg.seed == 42
    assert cfg.n_clients == 200
    assert cfg.target_trades == 300_000
    assert cfg.trading_hours_utc == (time(7, 0), time(16, 0))
    assert cfg.fix_time_utc == time(13, 10)
    assert set(cfg.pairs) == {f"EUR{ccy}" for ccy in CURRENCIES}
    assert set(cfg.segments) == {
        "large_corporate",
        "mid_market",
        "sme",
        "asset_manager",
        "hedge_fund",
        "payment_institution",
    }


def test_markup_orderings_hold(cfg: GeneratorConfig):
    # Pricing ladder by client type: the smaller and less competitive the client, the wider.
    for tier in TIERS:
        sme, mid, large, fund = (
            cfg.segments[name].base_markup_bps[tier]
            for name in ("sme", "mid_market", "large_corporate", "hedge_fund")
        )
        assert sme > mid > large > fund, tier
    # Liquidity ladder by pair: EM is wider than CEE, then the rest of G10, then the majors.
    for name, segment in cfg.segments.items():
        m = segment.base_markup_bps
        assert m["em"] > m["cee"] > m["g10_minor"] > m["g10_major"], name


def test_hedge_fund_has_the_worst_adverse_selection(cfg: GeneratorConfig):
    means = {name: seg.adverse_selection_bps[0] for name, seg in cfg.segments.items()}
    others = {name: mean for name, mean in means.items() if name != "hedge_fund"}
    assert means["hedge_fund"] > max(others.values())
    assert means["hedge_fund"] > 0  # positive means worse for the dealer


def test_fast_money_compresses_faster_than_sme(cfg: GeneratorConfig):
    sme = cfg.segments["sme"].compression_per_year
    assert cfg.segments["hedge_fund"].compression_per_year > sme
    assert cfg.segments["payment_institution"].compression_per_year > sme


def test_cls_flags_and_liquidity_tiers(cfg: GeneratorConfig):
    non_cls = {pair for pair, p in cfg.pairs.items() if not p.cls_settled}
    assert non_cls == {"EURPLN", "EURCZK", "EURTRY"}
    tiers: dict[str, set[str]] = {}
    for pair, p in cfg.pairs.items():
        tiers.setdefault(p.liquidity_tier, set()).add(pair[3:])
    assert tiers == {
        "g10_major": {"USD", "GBP", "JPY", "CHF"},
        "g10_minor": {"SEK", "NOK", "DKK", "AUD", "CAD"},
        "cee": {"PLN", "CZK", "HUF"},
        "em": {"ZAR", "TRY", "MXN"},
    }


def test_market_quoting_precision_and_vol(cfg: GeneratorConfig):
    decimals = {pair: p.price_decimals for pair, p in cfg.pairs.items()}
    assert decimals["EURUSD"] == 5
    assert decimals["EURJPY"] == 3
    assert decimals["EURHUF"] == 2
    assert decimals["EURCZK"] == 3
    assert decimals["EURTRY"] == 4
    assert cfg.pairs["EURTRY"].annual_vol > 1.5 * cfg.pairs["EURUSD"].annual_vol
    assert 0.05 < cfg.pairs["EURUSD"].annual_vol < 0.10


def test_every_segment_prices_every_pair_with_positive_popularity(cfg: GeneratorConfig):
    for name, segment in cfg.segments.items():
        assert set(segment.pair_popularity) == set(cfg.pairs), name
        assert all(w > 0 for w in segment.pair_popularity.values()), name


def test_settlement_weight_is_higher_outside_cls(cfg: GeneratorConfig):
    assert cfg.cost.settlement_weight_non_cls > cfg.cost.settlement_weight_cls > 0


# --- validation -------------------------------------------------------------------------------


def test_shares_must_sum_to_one(variant):
    def bump(raw):
        raw["segments"]["sme"]["client_share"] += 0.01

    with pytest.raises(ValidationError, match="sum to 1"):
        load_config(variant(bump))


def test_unknown_pair_rejected(variant):
    def add(raw):
        raw["pairs"]["EURXYZ"] = dict(raw["pairs"]["EURUSD"])
        for segment in raw["segments"].values():
            segment["pair_popularity"]["EURXYZ"] = 1.0

    with pytest.raises(ValidationError, match="EURXYZ"):
        load_config(variant(add))


def test_pair_must_be_eur_based(variant):
    def rename(raw):
        raw["pairs"]["USDJPY"] = raw["pairs"].pop("EURJPY")
        for segment in raw["segments"].values():
            segment["pair_popularity"]["USDJPY"] = segment["pair_popularity"].pop("EURJPY")

    with pytest.raises(ValidationError, match="USDJPY"):
        load_config(variant(rename))


def test_unknown_field_rejected(variant):
    def add(raw):
        raw["segments"]["sme"]["surprise"] = 1

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        load_config(variant(add))


def test_popularity_must_cover_exactly_the_configured_pairs(variant):
    def drop(raw):
        del raw["segments"]["hedge_fund"]["pair_popularity"]["EURMXN"]

    with pytest.raises(ValidationError, match="EURMXN"):
        load_config(variant(drop))


def test_popularity_must_be_strictly_positive(variant):
    def zero(raw):
        raw["segments"]["sme"]["pair_popularity"]["EURTRY"] = 0.0

    with pytest.raises(ValidationError, match="greater than 0"):
        load_config(variant(zero))


def test_missing_segment_rejected(variant):
    def drop(raw):
        del raw["segments"]["sme"]
        raw["segments"]["mid_market"]["client_share"] += 0.32

    with pytest.raises(ValidationError, match="sme"):
        load_config(variant(drop))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw["segments"]["sme"]["base_markup_bps"].update(g10_major=-1.0),
        lambda raw: raw["cost"].update(capital_charge_bps=-0.1),
        lambda raw: raw["cost"].update(hurdle_rate_bps=-0.1),
        lambda raw: raw["segments"]["hedge_fund"].update(adverse_selection_bps=[0.3, -0.5]),
    ],
    ids=["markup", "capital_charge", "hurdle", "adverse_selection_sd"],
)
def test_negative_bps_rejected(variant, mutate):
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        load_config(variant(mutate))


def test_adverse_selection_mean_may_be_negative(variant):
    def favourable(raw):
        raw["segments"]["large_corporate"]["adverse_selection_bps"] = [-0.2, 0.4]

    loaded = load_config(variant(favourable))
    assert loaded.segments["large_corporate"].adverse_selection_bps == (-0.2, 0.4)


def test_unsorted_ticket_bands_rejected(variant):
    def swap(raw):
        bands = raw["ticket_bands"]
        bands[0], bands[1] = bands[1], bands[0]

    with pytest.raises(ValidationError, match="ticket_bands"):
        load_config(variant(swap))


def test_open_ended_band_must_be_last(variant):
    def open_first(raw):
        raw["ticket_bands"][0]["max_ticket_eur"] = None

    with pytest.raises(ValidationError, match="ticket_bands"):
        load_config(variant(open_first))


def test_unsorted_volume_tiers_rejected(variant):
    def swap(raw):
        tiers = raw["volume_tiers"]
        tiers[1], tiers[2] = tiers[2], tiers[1]

    with pytest.raises(ValidationError, match="volume_tiers"):
        load_config(variant(swap))


def test_volume_tiers_must_start_at_zero(variant):
    def shift(raw):
        raw["volume_tiers"][0]["min_annual_volume_eur"] = 1_000_000

    with pytest.raises(ValidationError, match="volume_tiers"):
        load_config(variant(shift))


def test_discounts_are_fractions(variant):
    def too_big(raw):
        raw["legacy_deal"]["discount_pct"] = 25  # a percent, not a fraction

    with pytest.raises(ValidationError, match="less than 1"):
        load_config(variant(too_big))


def test_fix_time_must_fall_inside_trading_hours(variant):
    def late(raw):
        raw["fix_time_utc"] = "17:30"

    with pytest.raises(ValidationError, match="fix_time_utc"):
        load_config(variant(late))
