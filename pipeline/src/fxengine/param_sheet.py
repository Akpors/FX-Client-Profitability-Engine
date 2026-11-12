"""Render the owner's calibration parameter sheet (spec section 11.1).

``render_parameter_sheet`` turns a ``GeneratorConfig`` and a ``rationale`` mapping into one
Markdown document: every calibration parameter with its default, a blank "Your value" slot and a
one-line reason. ``make param-sheet`` writes it to ``docs/methodology/parameter-sheet.md``.

The sheet must fit one printed A4 portrait page (spec section 11.1), so the layout is dense:
the segment parameters, the ticket bands and the volume tiers are grids of default and blank
"Your value" rows, and each grid row has one reason in a bullet underneath. The limits are
pinned by ``test_sheet_fits_one_page_70_lines_8_columns_110_chars``.

The sheet is a pure function of the config and the rationale. There is no timestamp and no
random or ordering-dependent content, so the same inputs always give byte-identical output and a
test can tell when the committed copy has gone stale.

The rationale is a flat mapping of dotted key to text, documented at the top of
``pipeline/config/rationale.yaml``. Sections are built in the order they appear on the page and
each Why is looked up as its row is built, so ``MissingRationaleError`` (a ``KeyError``) names
the first missing key a reader would hit, and a missing key never yields a half-written sheet.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

import yaml

from fxengine.config import (
    LIQUIDITY_TIERS,
    SEGMENT_NAMES,
    GeneratorConfig,
    PairConfig,
    SegmentConfig,
)

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


class MissingRationaleError(KeyError):
    """A parameter on the sheet has no rationale line; ``key`` is the rationale key it needs."""

    def __init__(self, key: str) -> None:
        super().__init__(f"rationale is missing a line for {key!r}")
        self.key = key

    def __str__(self) -> str:
        return str(self.args[0])


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

    Raises ``MissingRationaleError`` naming the first parameter, in page order, that has no (or
    a blank) rationale.
    """

    def why(key: str) -> str:
        text = rationale.get(key)
        if text is None or not text.strip():
            raise MissingRationaleError(key)
        # One line, and a pipe must not split the table cell.
        return " ".join(text.split()).replace("|", r"\|")

    lines: list[str] = []
    for section in (
        _header(),
        _segment_parameters(cfg, why),
        _bands_and_tiers(cfg, why),
        _cover_cost_and_hurdle(cfg, why),
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


def _size(amount: float) -> str:
    """A EUR amount in the shorthand a desk uses: 25k, 100m, 2bn."""
    for size, suffix in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if amount >= size:
            return f"{_num(amount / size)}{suffix}"
    return _num(amount)


def _segment_label(name: str) -> str:
    return SEGMENT_LABELS.get(name, name.replace("_", " ").capitalize())


def _table(headers: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    def line(cells: Sequence[str]) -> str:
        return "| " + " | ".join(cells) + " |"

    return [line(headers), line(["---"] * len(headers)), *(line(row) for row in rows)]


def _grid(
    name: str, columns: Sequence[str], rows: Sequence[tuple[str, Sequence[str]]]
) -> list[str]:
    """Each parameter as a Default row and a blank Your value row, one column per entry."""
    body: list[Sequence[str]] = []
    for label, values in rows:
        body.append([label, "Default", *values])
        body.append(["", "Your value", *([""] * len(values))])
    return _table([name, "Value", *columns], body)


def _reasons(why: Why, bullets: Sequence[tuple[str, str]]) -> list[str]:
    return [f"- **{label}:** {why(key)}" for label, key in bullets]


def _pairs_where(cfg: GeneratorConfig, keep: Callable[[PairConfig], bool]) -> str:
    codes = [code.removeprefix("EUR") for code, pair in cfg.pairs.items() if keep(pair)]
    return " ".join(codes) or "none"


# --- sections ---------------------------------------------------------------------------------
# Sections return lines with no trailing blank; ``render_parameter_sheet`` joins them as they are.
# An ATX heading needs no blank line before it, which is how the sheet saves lines.


def _header() -> list[str]:
    return [
        "# Calibration parameter sheet",
        "> **Illustrative defaults, pending owner calibration** (spec section 11.1), not market "
        "data or any firm's.",
        "> Fill the Your value cells on a copy; the builder applies them to `generator.yaml` "
        "until you approve.",
        "",
    ]


def _segment_parameters(cfg: GeneratorConfig, why: Why) -> list[str]:
    """Parameters down the side, the six segments across; a Default and a blank row each."""

    def adverse(segment: SegmentConfig) -> str:
        mean, sd = segment.adverse_selection_bps
        return f"{_num(mean, 2)}/{_num(sd, 2)}"

    rows: list[tuple[str, Callable[[SegmentConfig], str]]] = []
    for tier in LIQUIDITY_TIERS:
        pairs = _pairs_where(cfg, lambda pair, tier=tier: pair.liquidity_tier == tier)
        rows.append(
            (
                f"Markup, {TIER_LABELS[tier]}: {pairs} (bps)",
                lambda s, tier=tier: _num(s.base_markup_bps[tier], 1),
            )
        )
    rows += [
        ("Compression per year", lambda s: _num(s.compression_per_year, 2)),
        ("Adverse selection mean/sd (bps)", adverse),
        ("Ops cost per ticket (EUR)", lambda s: _num(s.ops_cost_per_ticket_eur, 1)),
        (
            "Coverage cost per client per month (EUR)",
            lambda s: _num(s.coverage_cost_eur_per_month),
        ),
        ("Credit weight", lambda s: _num(s.credit_weight, 2)),
        ("Negotiation sigma (client price scatter)", lambda s: _num(s.client_markup_sigma, 2)),
    ]
    segments = [cfg.segments[name] for name in SEGMENT_NAMES]
    grid = _grid(
        "Segment parameter",
        [_segment_label(name) for name in SEGMENT_NAMES],
        [(label, [show(segment) for segment in segments]) for label, show in rows],
    )
    bullets = [
        ("Markup", "markup"),
        ("Compression", "compression"),
        ("Adverse selection", "adverse_selection"),
        ("Ops cost", "ops_cost"),
        ("Coverage", "coverage"),
        ("Credit weight", "credit_weight"),
        ("Negotiation sigma", "client_markup_sigma"),
    ]
    return [
        "## 1. Segment parameters",
        "Markup is base bps from mid, before bands, tiers, legacy deals, compression and noise.",
        "Compression: markup x e^(-rate x years). Adverse selection: cover vs mid, + hurts dealer.",
        "",
        *grid,
        "",
        *_reasons(why, bullets),
    ]


def _bands_and_tiers(cfg: GeneratorConfig, why: Why) -> list[str]:
    bands = cfg.ticket_bands
    tiers = cfg.volume_tiers
    band_grid = _grid(
        "Ticket band",
        [band.label for band in bands],
        [
            (
                "Upper edge (EUR)",
                [
                    _size(b.max_ticket_eur) if b.max_ticket_eur is not None else "none"
                    for b in bands
                ],
            ),
            ("Markup multiplier", [_num(b.markup_multiplier, 2) for b in bands]),
        ],
    )
    tier_grid = _grid(
        "Volume tier",
        [f"Tier {i}" for i in range(1, len(tiers) + 1)],
        [
            ("Annual volume from (EUR)", [_size(t.min_annual_volume_eur) for t in tiers]),
            ("Discount off markup", [_pct(t.discount_pct) for t in tiers]),
        ],
    )
    bullets = [
        ("Band edges", "ticket_band.edges"),
        ("Band multipliers", "ticket_band.multiplier"),
        ("Tier thresholds", "volume_tier.threshold"),
        ("Tier discounts", "volume_tier.discount"),
    ]
    return [
        "## 2. Ticket bands and volume tiers",
        *band_grid,
        "",
        *tier_grid,
        "",
        *_reasons(why, bullets),
    ]


def _cover_cost_and_hurdle(cfg: GeneratorConfig, why: Why) -> list[str]:
    cost, legacy = cfg.cost, cfg.legacy_deal
    non_cls = _pairs_where(cfg, lambda pair: not pair.cls_settled)
    # The four tier half-spreads share one row (and one Your value cell), in the order the label
    # names them, so the table stays within the one-page line budget.
    tiers = "/".join(TIER_LABELS[tier] for tier in LIQUIDITY_TIERS)
    half_spreads = "/".join(_num(cfg.cover_half_spread_bps[tier], 1) for tier in LIQUIDITY_TIERS)
    rows = [
        (f"Cover half-spread, {tiers} (bps)", half_spreads, "cover_half_spread"),
        ("Capital charge (bps)", _num(cost.capital_charge_bps, 2), "capital_charge_bps"),
        (
            "Settlement weight, CLS",
            _num(cost.settlement_weight_cls, 1),
            "settlement_weight.cls",
        ),
        (
            f"Settlement weight, non-CLS ({non_cls})",
            _num(cost.settlement_weight_non_cls, 1),
            "settlement_weight.non_cls",
        ),
        ("Hurdle rate (bps of volume)", _num(cost.hurdle_rate_bps, 2), "hurdle_rate_bps"),
        ("Legacy deal probability", _pct(legacy.probability), "legacy_deal.probability"),
        ("Legacy deal discount", _pct(legacy.discount_pct), "legacy_deal.discount_pct"),
    ]
    return [
        "## 3. Cover, capital, hurdle and legacy deals",
        *_table(
            ["Parameter", "Default", "Your value", "Why"],
            [[label, default, "", why(key)] for label, default, key in rows],
        ),
        "",
    ]


def _footer() -> list[str]:
    return [
        "Regenerate with `make param-sheet`; do not edit by hand. Source: `generator.yaml` and "
        "`rationale.yaml`.",
        "Client mix, trade frequency, ticket sizes, pair mix and noise are not calibrated here.",
    ]
