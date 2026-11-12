"""Render the owner's calibration parameter sheet (spec section 11.1).

``render_parameter_sheet`` turns a ``GeneratorConfig`` and a ``rationale`` mapping into one
Markdown document: every calibration parameter with its default, a blank "Your value" slot and a
one-line reason. ``make param-sheet`` writes it to ``docs/methodology/parameter-sheet.md``.

The sheet is a pure function of the config and the rationale. There is no timestamp and no
random or ordering-dependent content, so the same inputs always give byte-identical output and a
test can tell when the committed copy has gone stale.

The rationale is a flat mapping of dotted key to text, documented at the top of
``pipeline/config/rationale.yaml``. Sections are built in the order they appear on the page and
each Why is looked up as its row is built, so the ``KeyError`` for a missing key names the first
one a reader would hit, and a missing key never yields a half-written sheet.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

import yaml

from fxengine.config import LIQUIDITY_TIERS, SEGMENT_NAMES, GeneratorConfig, SegmentConfig

# <repo>/pipeline/config/rationale.yaml; /app/config/rationale.yaml inside the container.
DEFAULT_RATIONALE_PATH = Path(__file__).resolve().parents[2] / "config" / "rationale.yaml"
# <repo>/docs/methodology/parameter-sheet.md; ./docs is mounted at /docs inside the container, so
# the same expression lands on the repo's docs folder in both places (editable install at /app).
DEFAULT_SHEET_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "methodology" / "parameter-sheet.md"
)

SEGMENT_LABELS = {
    "large_corporate": "Large corporate",
    "mid_market": "Mid-market",
    "sme": "SME",
    "asset_manager": "Asset manager",
    "hedge_fund": "Hedge fund",
    "payment_institution": "Payment institution",
}
TIER_LABELS = {"g10_major": "major", "g10_minor": "minor", "cee": "CEE", "em": "EM"}

Why = Callable[[str], str]


def load_rationale(path: Path) -> dict[str, str]:
    """Read the rationale file. Raises ``ValueError`` unless it is a mapping of text to text."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a mapping of parameter key to one-line rationale")
    bad = [
        str(key)
        for key, value in raw.items()
        if not (isinstance(key, str) and isinstance(value, str))
    ]
    if bad:
        raise ValueError(f"{path}: every key and rationale must be text; check {', '.join(bad)}")
    return raw


def render_parameter_sheet(cfg: GeneratorConfig, rationale: Mapping[str, str]) -> str:
    """The parameter sheet as Markdown, ending in a single newline.

    Raises ``KeyError`` naming the first parameter, in page order, that has no (or a blank)
    rationale.
    """

    def why(key: str) -> str:
        text = rationale.get(key)
        if text is None or not text.strip():
            raise KeyError(f"rationale is missing a line for {key!r}")
        # One line, and a pipe must not split the table cell.
        return " ".join(text.split()).replace("|", r"\|")

    lines: list[str] = []
    for section in (
        _header(cfg),
        _segment_parameters(cfg, why),
        _ticket_bands(cfg, why),
        _volume_tiers(cfg, why),
        _cost_and_hurdle(cfg, why),
        _pairs(cfg, why),
        _footer(),
    ):
        lines.extend(section)
    return "\n".join(lines) + "\n"


# --- formatting -------------------------------------------------------------------------------


def _num(value: float, min_decimals: int = 0) -> str:
    """Up to six decimals, trailing zeros trimmed (but at least ``min_decimals``), with commas."""
    whole, _, fraction = f"{value:,.6f}".partition(".")
    fraction = fraction.rstrip("0").ljust(min_decimals, "0")
    return f"{whole}.{fraction}" if fraction else whole


def _pct(fraction: float, min_decimals: int = 0) -> str:
    return _num(round(fraction * 100, 4), min_decimals) + "%"


def _eur(amount: float) -> str:
    for size, suffix in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if amount >= size:
            return f"EUR {_num(amount / size)}{suffix}"
    return f"EUR {_num(amount)}"


def _segment_label(name: str) -> str:
    return SEGMENT_LABELS.get(name, name.replace("_", " ").capitalize())


def _table(headers: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    def line(cells: Sequence[str]) -> str:
        return "| " + " | ".join(cells) + " |"

    return [line(headers), line(["---"] * len(headers)), *(line(row) for row in rows), ""]


# --- sections ---------------------------------------------------------------------------------


def _header(cfg: GeneratorConfig) -> list[str]:
    return [
        "# Calibration parameter sheet",
        "",
        "> **Illustrative defaults, pending owner calibration** (spec section 11.1): the "
        "builder's plausible starting values for a European spot-FX dealer, not market data and "
        "not from any real firm or client. Write your value in the blank column on a copy or "
        "printout. The builder applies it to `pipeline/config/generator.yaml` and regenerates "
        "this sheet until you approve, then the parameters are frozen.",
        "",
        f"Book: {cfg.n_clients} clients, about {cfg.target_trades:,} trades over 24 months, "
        f"seed {cfg.seed}. Money in EUR.",
        "",
    ]


def _ticket_bands(cfg: GeneratorConfig, why: Why) -> list[str]:
    rows = [
        [band.label, _num(band.markup_multiplier, 2), "", why(f"ticket_band.{band.label}")]
        for band in cfg.ticket_bands
    ]
    return [
        "## 2. Ticket-size bands",
        "",
        "A multiplier on the base markup. A band includes its lower edge and excludes its "
        "upper edge; the edges are set in `generator.yaml`.",
        "",
        *_table(["Ticket band", "Default multiplier", "Your value", "Why"], rows),
    ]


def _volume_tiers(cfg: GeneratorConfig, why: Why) -> list[str]:
    rows = [
        [
            f"Tier {i}",
            _eur(tier.min_annual_volume_eur),
            _pct(tier.discount_pct),
            "",
            why(f"volume_tier.{i}"),
        ]
        for i, tier in enumerate(cfg.volume_tiers, start=1)
    ]
    return [
        "## 3. Volume-discount tiers",
        "",
        "A client gets the discount of the highest tier its expected annual volume reaches: a "
        "share taken off the markup, stacking with a legacy deal.",
        "",
        *_table(
            ["Volume tier", "Annual volume from", "Default discount", "Your value", "Why"], rows
        ),
    ]


def _segment_parameters(cfg: GeneratorConfig, why: Why) -> list[str]:
    """One grid of defaults and one blank grid, parameters down the side, segments across."""

    def adverse(segment: SegmentConfig) -> str:
        mean, sd = segment.adverse_selection_bps
        return f"{_num(mean, 2)} / {_num(sd, 2)}"

    # (row label, rationale key, value of that row for one segment)
    parameters: list[tuple[str, str, Callable[[SegmentConfig], str]]] = [
        (
            f"Markup, {TIER_LABELS[tier]} (bps)",
            f"markup.{tier}",
            lambda s, tier=tier: _num(s.base_markup_bps[tier], 1),
        )
        for tier in LIQUIDITY_TIERS
    ] + [
        ("Compression per year", "compression", lambda s: _num(s.compression_per_year, 2)),
        ("Adverse selection mean / sd (bps)", "adverse_selection", adverse),
        ("Ops cost per ticket (EUR)", "ops_cost", lambda s: _num(s.ops_cost_per_ticket_eur, 1)),
        (
            "Coverage cost per month (EUR)",
            "coverage",
            lambda s: _num(s.coverage_cost_eur_per_month),
        ),
        ("Credit weight", "credit_weight", lambda s: _num(s.credit_weight, 2)),
        ("Negotiation sigma", "client_markup_sigma", lambda s: _num(s.client_markup_sigma, 2)),
    ]
    segments = [cfg.segments[name] for name in SEGMENT_NAMES]
    labels = [_segment_label(name) for name in SEGMENT_NAMES]

    defaults = [[label, *(show(segment) for segment in segments)] for label, _, show in parameters]
    blanks = [[label, *([""] * len(segments))] for label, _, _ in parameters]
    # The reasons sit under the grids, one per row in row order. The segment ladder explains the
    # markup rows across segments, so it follows the last of them.
    reasons: list[str] = []
    for i, (label, key, _) in enumerate(parameters):
        reasons.append(f"- **{label}:** {why(key)}")
        if i == len(LIQUIDITY_TIERS) - 1:
            reasons.append(f"- **Markup by segment:** {why('markup.segment_ladder')}")

    tier_pairs = {
        tier: ", ".join(
            code.removeprefix("EUR")
            for code, pair in cfg.pairs.items()
            if pair.liquidity_tier == tier
        )
        for tier in LIQUIDITY_TIERS
    }
    tiers = "; ".join(f"{TIER_LABELS[tier]} = {tier_pairs[tier]}" for tier in LIQUIDITY_TIERS)
    return [
        "## 1. Segment parameters",
        "",
        "Markup is one-sided, in bps from mid to the client's rate, before the ticket-band "
        "multiplier (section 2), volume discount (section 3), legacy deal, compression and "
        f"noise. Tiers (pairs against EUR): {tiers}.",
        "",
        "- **Compression:** markup x e^(-rate x years since the window opened); a rate r cuts "
        "it by about 1 - e^-r a year.",
        "- **Adverse selection:** mean / sd of the dealer's cover price against mid, in bps; "
        "positive is worse for the dealer.",
        "- **Negotiation sigma:** each client's price factor is LogNormal(0, sigma), median "
        "1.0, fixed for the window.",
        "",
        *_table(["Default", *labels], defaults),
        *_table(["Your value", *labels], blanks),
        *reasons,
        "",
    ]


def _cost_and_hurdle(cfg: GeneratorConfig, why: Why) -> list[str]:
    cost, legacy = cfg.cost, cfg.legacy_deal
    non_cls = ", ".join(code for code, pair in cfg.pairs.items() if not pair.cls_settled)
    rows = [
        [
            "Capital charge (bps of notional)",
            _num(cost.capital_charge_bps, 2),
            "",
            why("capital_charge_bps"),
        ],
        [
            "Settlement weight, CLS-settled pairs",
            _num(cost.settlement_weight_cls, 1),
            "",
            why("settlement_weight.cls"),
        ],
        [
            "Settlement weight, non-CLS pairs",
            _num(cost.settlement_weight_non_cls, 1),
            "",
            why("settlement_weight.non_cls"),
        ],
        ["Hurdle rate (bps of volume)", _num(cost.hurdle_rate_bps, 2), "", why("hurdle_rate_bps")],
        ["Legacy deal probability", _pct(legacy.probability), "", why("legacy_deal.probability")],
        ["Legacy deal discount", _pct(legacy.discount_pct), "", why("legacy_deal.discount_pct")],
    ]
    return [
        "## 4. Capital, hurdle and legacy deals",
        "",
        "Capital charge per trade = notional x capital charge bps / 10,000 x credit weight x "
        f"settlement weight. Non-CLS pairs: {non_cls or 'none'}. The hurdle is the minimum "
        "economic margin (bps of volume) for a client to earn its place. A legacy deal is an old "
        "discount never repriced; only a client onboarded before the window can hold one.",
        "",
        *_table(["Parameter", "Default", "Your value", "Why"], rows),
    ]


def _pairs(cfg: GeneratorConfig, why: Why) -> list[str]:
    codes = [code.removeprefix("EUR") for code in cfg.pairs]
    rows = [
        ["Liquidity tier", *(TIER_LABELS[pair.liquidity_tier] for pair in cfg.pairs.values())],
        ["Annual vol, default", *(_pct(pair.annual_vol, 1) for pair in cfg.pairs.values())],
        ["Annual vol, your value", *([""] * len(codes))],
    ]
    return [
        "## 5. Pair volatility",
        "",
        *_table(["Pair", *codes], rows),
        f"- **Annual vol:** {why('pair_annual_vol')}",
        "",
    ]


def _footer() -> list[str]:
    return [
        "---",
        "",
        "Regenerate with `make param-sheet`; do not edit by hand. Source: "
        "`pipeline/config/generator.yaml` and `pipeline/config/rationale.yaml`. Client mix, trade "
        "frequency, ticket-size distribution, pair mix and noise are not calibrated here; they "
        "stay in `generator.yaml` and are described in `docs/methodology/data-generation.md`.",
    ]
