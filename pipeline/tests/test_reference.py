"""Tests for the reference and cost-assumption tables that dbt joins to the trades."""

from __future__ import annotations

import pandas as pd
import pytest

from fxengine.config import SEGMENT_NAMES, GeneratorConfig
from fxengine.reference import (
    cost_assumptions_table,
    pairs_table,
    parameters_table,
    ticket_bands_table,
)

NON_CLS = {"EURPLN", "EURCZK", "EURTRY"}


@pytest.fixture(scope="module")
def pairs(full_cfg: GeneratorConfig) -> pd.DataFrame:
    return pairs_table(full_cfg)


@pytest.fixture(scope="module")
def bands(full_cfg: GeneratorConfig) -> pd.DataFrame:
    return ticket_bands_table(full_cfg)


@pytest.fixture(scope="module")
def costs(full_cfg: GeneratorConfig) -> pd.DataFrame:
    return cost_assumptions_table(full_cfg)


@pytest.fixture(scope="module")
def params(full_cfg: GeneratorConfig) -> pd.DataFrame:
    return parameters_table(full_cfg)


def test_pairs_table_has_15_rows_and_cls_flags(full_cfg: GeneratorConfig, pairs: pd.DataFrame):
    assert list(pairs.columns) == [
        "currency_pair",
        "base_ccy",
        "quote_ccy",
        "liquidity_tier",
        "price_decimals",
        "cls_settled",
        "settlement_weight",
    ]
    assert len(pairs) == 15
    assert pairs["currency_pair"].is_unique
    assert pairs["currency_pair"].is_monotonic_increasing
    assert (pairs["base_ccy"] == "EUR").all()
    assert (pairs["currency_pair"] == "EUR" + pairs["quote_ccy"]).all()

    by_pair = pairs.set_index("currency_pair")
    assert set(by_pair.index[~by_pair["cls_settled"]]) == NON_CLS
    for pair in NON_CLS:
        assert by_pair.loc[pair, "settlement_weight"] == full_cfg.cost.settlement_weight_non_cls
    for pair in by_pair.index.difference(list(NON_CLS)):
        assert by_pair.loc[pair, "cls_settled"]
        assert by_pair.loc[pair, "settlement_weight"] == full_cfg.cost.settlement_weight_cls

    # Everything else is copied straight from the config.
    for pair, pair_cfg in full_cfg.pairs.items():
        assert by_pair.loc[pair, "liquidity_tier"] == pair_cfg.liquidity_tier
        assert by_pair.loc[pair, "price_decimals"] == pair_cfg.price_decimals


def test_pairs_table_dtypes_are_simple(pairs: pd.DataFrame):
    assert pairs["price_decimals"].dtype == "int64"
    assert pairs["cls_settled"].dtype == "bool"
    assert pairs["settlement_weight"].dtype == "float64"


def test_ticket_bands_contiguous(full_cfg: GeneratorConfig, bands: pd.DataFrame):
    assert list(bands.columns) == ["label", "min_ticket_eur", "max_ticket_eur", "markup_multiplier"]
    assert len(bands) == len(full_cfg.ticket_bands) == 6
    assert bands["label"].is_unique
    # [min, max): the first band starts at 0 and each band starts where the previous one ends.
    assert bands["min_ticket_eur"].iloc[0] == 0
    mins, maxes = bands["min_ticket_eur"], bands["max_ticket_eur"]
    assert (mins.iloc[1:].to_numpy() == maxes.iloc[:-1].to_numpy()).all()
    assert bands["min_ticket_eur"].is_monotonic_increasing
    # Only the top band is open-ended, and that is a null, not an infinity.
    assert bands["max_ticket_eur"].iloc[:-1].notna().all()
    assert pd.isna(bands["max_ticket_eur"].iloc[-1])
    assert (bands["max_ticket_eur"].iloc[:-1] > bands["min_ticket_eur"].iloc[:-1]).all()
    assert bands["min_ticket_eur"].dtype == "float64"
    assert bands["max_ticket_eur"].dtype == "float64"
    configured = full_cfg.ticket_bands
    assert bands["markup_multiplier"].tolist() == [b.markup_multiplier for b in configured]
    assert bands["label"].tolist() == [b.label for b in configured]


def test_cost_table_one_row_per_segment(full_cfg: GeneratorConfig, costs: pd.DataFrame):
    assert list(costs.columns) == [
        "segment",
        "ops_cost_per_ticket_eur",
        "coverage_cost_eur_per_month",
        "credit_weight",
    ]
    assert len(costs) == len(SEGMENT_NAMES) == 6
    assert costs["segment"].is_unique
    assert set(costs["segment"]) == set(SEGMENT_NAMES)
    assert costs["segment"].is_monotonic_increasing
    by_segment = costs.set_index("segment")
    for name, segment in full_cfg.segments.items():
        assert by_segment.loc[name, "ops_cost_per_ticket_eur"] == segment.ops_cost_per_ticket_eur
        assert by_segment.loc[name, "coverage_cost_eur_per_month"] == (
            segment.coverage_cost_eur_per_month
        )
        assert by_segment.loc[name, "credit_weight"] == segment.credit_weight
    for column in costs.columns[1:]:
        assert costs[column].dtype == "float64"


def test_parameters_contains_required_names(full_cfg: GeneratorConfig, params: pd.DataFrame):
    assert list(params.columns) == ["name", "value", "description"]
    assert params["name"].is_unique
    assert {"capital_charge_bps", "hurdle_rate_bps", "seed"} <= set(params["name"])
    assert params["value"].dtype == "float64"
    assert (params["description"].str.len() > 0).all()

    by_name = params.set_index("name")["value"]
    assert by_name["capital_charge_bps"] == full_cfg.cost.capital_charge_bps
    assert by_name["hurdle_rate_bps"] == full_cfg.cost.hurdle_rate_bps
    assert by_name["seed"] == 42.0
    # dbt derives the window from the trades, so the parameters must not carry one.
    assert not any("window" in name for name in params["name"])


def test_tables_are_deterministic(full_cfg: GeneratorConfig):
    for build in (pairs_table, ticket_bands_table, cost_assumptions_table, parameters_table):
        pd.testing.assert_frame_equal(build(full_cfg), build(full_cfg))
