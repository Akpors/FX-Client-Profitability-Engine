"""Reference and cost-assumption tables: the lookups dbt joins to the trades.

``load`` writes these frames to ``raw.pairs``, ``raw.ticket_bands``, ``raw.cost_assumptions``
and ``raw.parameters``. They carry every number the cost-to-serve and pricing metrics need, so
the SQL never hard-codes an assumption: change ``generator.yaml`` and the models follow.

Each function returns a plain pandas frame with a fixed column order, simple dtypes (``str``,
``int64``, ``float64``, ``bool``) and deterministic row order, so the same config always gives
byte-identical raw tables.

How dbt uses them:

* ``pairs``: ``settlement_weight`` is the CLS (or non-CLS) multiplier on the capital charge.
* ``ticket_bands``: a trade falls in the band where ``min_ticket_eur <= notional <
  max_ticket_eur``. The top band has a null ``max_ticket_eur`` (no upper limit).
* ``cost_assumptions``: ops cost per ticket, monthly coverage cost and ``credit_weight`` per
  segment.
* ``parameters``: global scalars, read by ``name``. Capital charge per trade (an internal
  settlement-risk and credit-line charge, not regulatory capital) is
  ``notional x capital_charge_bps / 10,000 x credit_weight x settlement_weight``.
"""

from __future__ import annotations

import pandas as pd

from fxengine.config import SEGMENT_NAMES, GeneratorConfig

PAIR_COLUMNS = [
    "currency_pair",
    "base_ccy",
    "quote_ccy",
    "liquidity_tier",
    "price_decimals",
    "cls_settled",
    "settlement_weight",
]
TICKET_BAND_COLUMNS = ["label", "min_ticket_eur", "max_ticket_eur", "markup_multiplier"]
COST_COLUMNS = [
    "segment",
    "ops_cost_per_ticket_eur",
    "coverage_cost_eur_per_month",
    "credit_weight",
]
PARAMETER_COLUMNS = ["name", "value", "description"]

BASE_CCY = "EUR"


def pairs_table(cfg: GeneratorConfig) -> pd.DataFrame:
    """One row per EUR pair, sorted by ``currency_pair``, with its settlement weight."""
    cost = cfg.cost
    rows = []
    for pair in sorted(cfg.pairs):
        pair_cfg = cfg.pairs[pair]
        rows.append(
            {
                "currency_pair": pair,
                "base_ccy": BASE_CCY,
                "quote_ccy": pair.removeprefix(BASE_CCY),
                "liquidity_tier": pair_cfg.liquidity_tier,
                "price_decimals": pair_cfg.price_decimals,
                "cls_settled": pair_cfg.cls_settled,
                "settlement_weight": (
                    cost.settlement_weight_cls
                    if pair_cfg.cls_settled
                    else cost.settlement_weight_non_cls
                ),
            }
        )
    frame = pd.DataFrame(rows, columns=PAIR_COLUMNS)
    return frame.astype(
        {
            "currency_pair": "str",
            "base_ccy": "str",
            "quote_ccy": "str",
            "liquidity_tier": "str",
            "price_decimals": "int64",
            "cls_settled": "bool",
            "settlement_weight": "float64",
        }
    )


def ticket_bands_table(cfg: GeneratorConfig) -> pd.DataFrame:
    """Ticket bands in ascending order, each covering ``[min_ticket_eur, max_ticket_eur)``.

    The first band starts at 0 and each band starts where the previous one ends. The top band's
    ``max_ticket_eur`` is NaN, which becomes SQL NULL when the frame is loaded.
    """
    rows = []
    lower = 0.0
    for band in cfg.ticket_bands:
        upper = float("nan") if band.max_ticket_eur is None else float(band.max_ticket_eur)
        rows.append(
            {
                "label": band.label,
                "min_ticket_eur": lower,
                "max_ticket_eur": upper,
                "markup_multiplier": float(band.markup_multiplier),
            }
        )
        lower = upper
    frame = pd.DataFrame(rows, columns=TICKET_BAND_COLUMNS)
    return frame.astype(
        {
            "label": "str",
            "min_ticket_eur": "float64",
            "max_ticket_eur": "float64",
            "markup_multiplier": "float64",
        }
    )


def cost_assumptions_table(cfg: GeneratorConfig) -> pd.DataFrame:
    """One row per client segment, sorted by segment name, with its cost-to-serve inputs."""
    rows = []
    for name in sorted(SEGMENT_NAMES):
        segment = cfg.segments[name]
        rows.append(
            {
                "segment": name,
                "ops_cost_per_ticket_eur": float(segment.ops_cost_per_ticket_eur),
                "coverage_cost_eur_per_month": float(segment.coverage_cost_eur_per_month),
                "credit_weight": float(segment.credit_weight),
            }
        )
    frame = pd.DataFrame(rows, columns=COST_COLUMNS)
    return frame.astype(
        {
            "segment": "str",
            "ops_cost_per_ticket_eur": "float64",
            "coverage_cost_eur_per_month": "float64",
            "credit_weight": "float64",
        }
    )


def parameters_table(cfg: GeneratorConfig) -> pd.DataFrame:
    """Global scalar parameters as ``(name, value, description)`` rows, in a fixed order.

    ``value`` is always a float, so the seed is 42.0. There is deliberately no window row: dbt
    derives the window from the trades themselves.
    """
    cost = cfg.cost
    rows = [
        (
            "capital_charge_bps",
            cost.capital_charge_bps,
            "Internal settlement-risk and credit-line charge (not regulatory capital), bps of "
            "notional at credit weight 1.0 and settlement weight 1.0",
        ),
        (
            "settlement_weight_cls",
            cost.settlement_weight_cls,
            "Capital multiplier for pairs that settle through CLS (payment versus payment)",
        ),
        (
            "settlement_weight_non_cls",
            cost.settlement_weight_non_cls,
            "Capital multiplier for pairs that settle bilaterally outside CLS",
        ),
        (
            "hurdle_rate_bps",
            cost.hurdle_rate_bps,
            "Minimum economic margin in bps of volume for a client to earn its place",
        ),
        (
            "seed",
            cfg.seed,
            "Random seed of the synthetic data generator",
        ),
    ]
    frame = pd.DataFrame(
        [{"name": name, "value": float(value), "description": text} for name, value, text in rows],
        columns=PARAMETER_COLUMNS,
    )
    return frame.astype({"name": "str", "value": "float64", "description": "str"})
