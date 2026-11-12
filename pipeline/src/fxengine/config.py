"""Generator configuration: one validated source of truth for every synthetic-data assumption.

The YAML file (``pipeline/config/generator.yaml``) is parsed into ``GeneratorConfig``. Nothing
else in the pipeline hard-codes a price, a spread, a cost or a client mix, so the owner can
calibrate the whole book from one file and ``docs/methodology/data-generation.md`` can describe
it. Unknown keys are rejected everywhere so a typo never silently falls back to a default.

Conventions used throughout the file:

* ``*_bps`` values are basis points (1 bp = 0.01%). Markups are one-sided, measured from the
  market mid to the client's rate. ``cover_half_spread_bps`` is one-sided too: half the interbank
  bid/offer the dealer crosses when it covers, by the pair's liquidity tier.
* ``discount_pct`` values are fractions in [0, 1), so 0.25 means a 25% discount.
* ``trades_per_month_lognorm`` and ``ticket_eur_lognorm`` are ``(mu, sigma)`` of the underlying
  normal distribution, not the mean and standard deviation of the lognormal itself.
"""

from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Annotated, Literal, Self, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from fxengine.ecb import CURRENCIES

SegmentName = Literal[
    "large_corporate",
    "mid_market",
    "sme",
    "asset_manager",
    "hedge_fund",
    "payment_institution",
]
LiquidityTier = Literal["g10_major", "g10_minor", "cee", "em"]

SEGMENT_NAMES: tuple[str, ...] = get_args(SegmentName)
LIQUIDITY_TIERS: tuple[str, ...] = get_args(LiquidityTier)

# <repo>/pipeline/config/generator.yaml; /app/config/generator.yaml inside the container.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "generator.yaml"

NonNegative = Annotated[float, Field(ge=0)]
Positive = Annotated[float, Field(gt=0)]
Fraction = Annotated[float, Field(ge=0, lt=1)]
Probability = Annotated[float, Field(ge=0, le=1)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PairConfig(_Model):
    """One EUR-based currency pair."""

    liquidity_tier: LiquidityTier
    annual_vol: Positive  # annualised volatility of the log rate, as a fraction (0.07 = 7%)
    price_decimals: Annotated[int, Field(ge=0, le=8)]  # decimals the pair is quoted to
    cls_settled: bool  # True when the quote currency settles through CLS (payment-vs-payment)


class SegmentConfig(_Model):
    """Behaviour and economics of one client segment."""

    client_share: Annotated[float, Field(gt=0, le=1)]
    trades_per_month_lognorm: tuple[float, Positive]  # (mu, sigma) of the underlying normal
    ticket_eur_lognorm: tuple[float, Positive]  # (mu, sigma) of the underlying normal
    pair_popularity: dict[str, Positive]  # relative weights; the generator normalises them
    buy_probability: Annotated[float, Field(gt=0, lt=1)]  # P(client buys EUR)
    base_markup_bps: dict[LiquidityTier, NonNegative]  # at the reference ticket band
    markup_noise_sigma: NonNegative  # sigma of the lognormal noise on each trade's markup
    # sigma of each client's lognormal negotiation factor, drawn once per client (median 1.0)
    client_markup_sigma: NonNegative
    compression_per_year: NonNegative  # markup shrinks by exp(-rate x years)
    adverse_selection_bps: tuple[float, NonNegative]  # (mean, sd) of cover slippage vs mid
    ops_cost_per_ticket_eur: NonNegative
    coverage_cost_eur_per_month: NonNegative
    credit_weight: Positive

    @model_validator(mode="after")
    def _all_liquidity_tiers_priced(self) -> Self:
        missing = [tier for tier in LIQUIDITY_TIERS if tier not in self.base_markup_bps]
        if missing:
            raise ValueError(f"base_markup_bps is missing liquidity tiers: {', '.join(missing)}")
        return self


class TicketBand(_Model):
    """A ticket-size band covering ``[previous band's max, max_ticket_eur)`` in EUR.

    ``max_ticket_eur`` is the exclusive upper edge, so a EUR 25,000 ticket sits in the band that
    starts at 25,000. None means no upper limit (the last band only).
    """

    label: str
    max_ticket_eur: Positive | None
    markup_multiplier: Positive


class VolumeTier(_Model):
    """A volume-discount tier: clients at or above the minimum annual EUR volume earn it."""

    min_annual_volume_eur: NonNegative
    discount_pct: Fraction


class LegacyDeal(_Model):
    """Old discounts that were never repriced."""

    probability: Probability  # chance a client onboarded before the window holds one
    discount_pct: Fraction


class GlobalCost(_Model):
    """Cost-to-serve assumptions that do not vary by segment."""

    # Internal settlement-risk and credit-line charge (not regulatory capital), in bps of
    # notional per unit of credit weight x settlement weight.
    capital_charge_bps: NonNegative
    settlement_weight_cls: NonNegative
    settlement_weight_non_cls: NonNegative
    hurdle_rate_bps: NonNegative  # minimum acceptable economic margin

    @model_validator(mode="after")
    def _non_cls_is_riskier(self) -> Self:
        if self.settlement_weight_non_cls < self.settlement_weight_cls:
            raise ValueError("settlement_weight_non_cls must be at least settlement_weight_cls")
        return self


class ClientBookConfig(_Model):
    """How the client book is drawn. Defaults are documented in ``data-generation.md``."""

    # Share of each segment onboarded before the window starts. The rest join uniformly inside it.
    pre_window_share: Probability = 0.8
    # Pre-window clients were onboarded between 1 day and this many years before the window.
    max_tenure_years: Positive = 6.0
    # Dirichlet concentration for a client's pair weights: alpha = concentration x popularity,
    # popularity normalised to sum to 1. Higher means clients hug the segment average.
    pair_concentration: Positive = 30.0
    # Between-client standard deviation of ln(median ticket) around the segment's ticket mu.
    ticket_mu_sd: NonNegative = 0.7


class GeneratorConfig(_Model):
    """Everything the generator needs. Build it with ``load_config``."""

    seed: int = 42
    n_clients: Annotated[int, Field(gt=0)]
    target_trades: Annotated[int, Field(gt=0)]
    trading_hours_utc: tuple[time, time] = (time(7, 0), time(16, 0))
    fix_time_utc: time = time(13, 10)
    client_book: ClientBookConfig = Field(default_factory=ClientBookConfig)
    pairs: dict[str, PairConfig]
    # Half the interbank bid/offer the dealer pays to cover, by liquidity tier (one-sided bps).
    cover_half_spread_bps: dict[LiquidityTier, NonNegative]
    segments: dict[SegmentName, SegmentConfig]
    ticket_bands: list[TicketBand]
    volume_tiers: list[VolumeTier]
    legacy_deal: LegacyDeal
    cost: GlobalCost

    @model_validator(mode="after")
    def _check_trading_day(self) -> Self:
        opens, closes = self.trading_hours_utc
        if not opens < closes:
            raise ValueError("trading_hours_utc must open before it closes")
        # The trading day is half-open [open, close), as in market.trading_grid, so a fix
        # exactly at the close is outside it.
        if not opens <= self.fix_time_utc < closes:
            raise ValueError(
                "fix_time_utc must fall inside the half-open trading day [open, close) "
                "of trading_hours_utc"
            )
        return self

    @model_validator(mode="after")
    def _check_pairs(self) -> Self:
        if not self.pairs:
            raise ValueError("pairs must not be empty")
        allowed = {f"EUR{ccy}" for ccy in CURRENCIES}
        for pair in self.pairs:
            if pair not in allowed:
                raise ValueError(
                    f"pair {pair!r} is not EUR plus one of the ECB currencies: "
                    f"{', '.join(CURRENCIES)}"
                )
        return self

    @model_validator(mode="after")
    def _check_cover_half_spread(self) -> Self:
        spread = self.cover_half_spread_bps
        missing = [tier for tier in LIQUIDITY_TIERS if tier not in spread]
        if missing:
            raise ValueError(
                f"cover_half_spread_bps is missing liquidity tiers: {', '.join(missing)}"
            )
        # The interbank touch widens as liquidity thins: g10_major <= g10_minor <= cee <= em.
        for tighter, wider in zip(LIQUIDITY_TIERS, LIQUIDITY_TIERS[1:], strict=False):
            if spread[tighter] > spread[wider]:
                raise ValueError(
                    f"cover_half_spread_bps must not narrow as liquidity thins: {tighter} "
                    f"({spread[tighter]:g}) is wider than {wider} ({spread[wider]:g})"
                )
        return self

    @model_validator(mode="after")
    def _check_segments(self) -> Self:
        missing = [name for name in SEGMENT_NAMES if name not in self.segments]
        if missing:
            raise ValueError(f"segments is missing: {', '.join(missing)}")
        total = sum(segment.client_share for segment in self.segments.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"segment client_share values must sum to 1 (got {total:.12g})")
        wanted = set(self.pairs)
        for name, segment in self.segments.items():
            have = set(segment.pair_popularity)
            if have != wanted:
                parts = []
                if wanted - have:
                    parts.append(f"missing {', '.join(sorted(wanted - have))}")
                if have - wanted:
                    parts.append(f"unknown {', '.join(sorted(have - wanted))}")
                raise ValueError(f"segment {name!r} pair_popularity: {'; '.join(parts)}")
        return self

    @model_validator(mode="after")
    def _check_ticket_bands(self) -> Self:
        bands = self.ticket_bands
        if not bands:
            raise ValueError("ticket_bands must not be empty")
        if bands[-1].max_ticket_eur is not None:
            raise ValueError("ticket_bands: the last band must have max_ticket_eur null (no limit)")
        edges = [band.max_ticket_eur for band in bands[:-1]]
        if any(edge is None for edge in edges):
            raise ValueError("ticket_bands: only the last band may be open-ended")
        if any(a >= b for a, b in zip(edges, edges[1:], strict=False)):
            raise ValueError("ticket_bands must be sorted ascending by max_ticket_eur")
        return self

    @model_validator(mode="after")
    def _check_volume_tiers(self) -> Self:
        tiers = self.volume_tiers
        if not tiers:
            raise ValueError("volume_tiers must not be empty")
        if tiers[0].min_annual_volume_eur != 0:
            raise ValueError("volume_tiers: the first tier must start at 0 so every client has one")
        pairs = list(zip(tiers, tiers[1:], strict=False))
        if any(a.min_annual_volume_eur >= b.min_annual_volume_eur for a, b in pairs):
            raise ValueError("volume_tiers must be sorted ascending by min_annual_volume_eur")
        if any(a.discount_pct > b.discount_pct for a, b in pairs):
            raise ValueError("volume_tiers: discount_pct must not fall as volume rises")
        return self


def load_config(path: Path) -> GeneratorConfig:
    """Read and validate a generator config. Raises ``pydantic.ValidationError`` if it is bad."""
    raw = yaml.safe_load(Path(path).read_text())
    return GeneratorConfig.model_validate(raw)
