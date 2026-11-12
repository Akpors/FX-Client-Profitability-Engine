"""Tests for the calibration parameter sheet (spec section 11.1).

The sheet is the owner's review document: every calibration parameter with its default, a blank
slot for the owner's value and a one-line reason. The tests read the rendered Markdown back as
tables, so they check what a reader sees (a cell in a row), not just that a string is present.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from fxengine import cli
from fxengine.config import DEFAULT_CONFIG_PATH, GeneratorConfig, load_config
from fxengine.param_sheet import (
    DEFAULT_RATIONALE_PATH,
    DEFAULT_SHEET_PATH,
    load_rationale,
    render_parameter_sheet,
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

# Rows of the segment grid: label, then the SegmentConfig attribute it shows (markup rows are
# per liquidity tier and checked separately; adverse selection is a "mean / sd" pair).
SEGMENT_ROWS = {
    "Compression per year": "compression_per_year",
    "Ops cost per ticket (EUR)": "ops_cost_per_ticket_eur",
    "Coverage cost per month (EUR)": "coverage_cost_eur_per_month",
    "Credit weight": "credit_weight",
    "Negotiation sigma": "client_markup_sigma",
}
GRID_ROWS = [
    *(f"Markup, {label} (bps)" for label in TIER_LABELS.values()),
    "Compression per year",
    "Adverse selection mean / sd (bps)",
    "Ops cost per ticket (EUR)",
    "Coverage cost per month (EUR)",
    "Credit weight",
    "Negotiation sigma",
]

_SEPARATOR = re.compile(r"^:?-{3,}:?$")


@pytest.fixture(scope="module")
def cfg() -> GeneratorConfig:
    return load_config(DEFAULT_CONFIG_PATH)


@pytest.fixture(scope="module")
def rationale() -> dict[str, str]:
    return load_rationale(DEFAULT_RATIONALE_PATH)


@pytest.fixture(scope="module")
def sheet(cfg: GeneratorConfig, rationale: dict[str, str]) -> str:
    return render_parameter_sheet(cfg, rationale)


# --- reading the Markdown back ----------------------------------------------------------------


def parse_tables(markdown: str) -> list[list[list[str]]]:
    """Every pipe table in the document: rows of stripped cells, separator rows dropped."""
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in markdown.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
            if not all(_SEPARATOR.match(c) for c in cells):
                current.append(cells)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def table(markdown: str, name: str) -> list[list[str]]:
    """The table whose first header cell is ``name``: header row first."""
    for rows in parse_tables(markdown):
        if rows[0][0] == name:
            return rows
    raise AssertionError(f"no table headed {name!r} in the sheet")


def cell(markdown: str, name: str, row: str, column: str) -> str:
    """The cell at (row label, column header) of the table headed ``name``."""
    rows = table(markdown, name)
    for r in rows[1:]:
        if r[0] == row:
            return r[rows[0].index(column)]
    raise AssertionError(f"table {name!r} has no row {row!r}")


def number(text: str) -> float:
    return float(text.replace(",", ""))


def percent(text: str) -> float:
    assert text.endswith("%"), text
    return number(text.removesuffix("%")) / 100


# --- coverage ---------------------------------------------------------------------------------


def test_sheet_contains_every_segment_and_tier(sheet: str, cfg: GeneratorConfig):
    # Rendering the shipped defaults at all proves rationale.yaml covers every key.
    for grid in ("Default", "Your value"):
        rows = table(sheet, grid)
        assert rows[0][1:] == list(SEGMENT_LABELS.values()), grid
        assert [r[0] for r in rows[1:]] == GRID_ROWS, grid

    assert [r[0] for r in table(sheet, "Ticket band")[1:]] == [b.label for b in cfg.ticket_bands]
    tiers = table(sheet, "Volume tier")
    assert [r[0] for r in tiers[1:]] == [f"Tier {i}" for i in range(1, len(cfg.volume_tiers) + 1)]

    pairs = table(sheet, "Pair")
    assert pairs[0][1:] == [pair.removeprefix("EUR") for pair in cfg.pairs]

    parameters = [r[0] for r in table(sheet, "Parameter")[1:]]
    for needle in ("Capital charge", "CLS-settled", "non-CLS", "Hurdle", "Legacy deal prob"):
        assert any(needle in name for name in parameters), needle


def test_every_liquidity_tier_lists_its_pairs(sheet: str, cfg: GeneratorConfig):
    for tier, label in TIER_LABELS.items():
        pairs = ", ".join(
            code.removeprefix("EUR")
            for code, pair in cfg.pairs.items()
            if pair.liquidity_tier == tier
        )
        assert f"{label} = {pairs}" in sheet, tier


def test_sheet_has_header_and_footer(sheet: str):
    assert "Illustrative defaults, pending owner calibration" in sheet
    assert "section 11.1" in sheet
    assert "Regenerate with `make param-sheet`; do not edit by hand" in sheet


# --- values -----------------------------------------------------------------------------------


def test_sheet_values_match_config(sheet: str, cfg: GeneratorConfig):
    for name, segment in cfg.segments.items():
        label = SEGMENT_LABELS[name]

        for tier, tier_label in TIER_LABELS.items():
            shown = number(cell(sheet, "Default", f"Markup, {tier_label} (bps)", label))
            assert shown == segment.base_markup_bps[tier], (name, tier)

        for row, attribute in SEGMENT_ROWS.items():
            assert number(cell(sheet, "Default", row, label)) == getattr(segment, attribute), (
                name,
                row,
            )

        shown = cell(sheet, "Default", "Adverse selection mean / sd (bps)", label)
        mean, sd = (number(part) for part in shown.split(" / "))
        assert (mean, sd) == tuple(segment.adverse_selection_bps), name

    for band in cfg.ticket_bands:
        shown = number(cell(sheet, "Ticket band", band.label, "Default multiplier"))
        assert shown == band.markup_multiplier, band.label

    for i, tier in enumerate(cfg.volume_tiers, start=1):
        shown = percent(cell(sheet, "Volume tier", f"Tier {i}", "Default discount"))
        assert shown == pytest.approx(tier.discount_pct), i

    cost = cfg.cost
    legacy = cfg.legacy_deal
    cost_rows = {
        "Capital charge (bps of notional)": cost.capital_charge_bps,
        "Settlement weight, CLS-settled pairs": cost.settlement_weight_cls,
        "Settlement weight, non-CLS pairs": cost.settlement_weight_non_cls,
        "Hurdle rate (bps of volume)": cost.hurdle_rate_bps,
    }
    for row, expected in cost_rows.items():
        assert number(cell(sheet, "Parameter", row, "Default")) == expected, row
    assert percent(cell(sheet, "Parameter", "Legacy deal probability", "Default")) == pytest.approx(
        legacy.probability
    )
    assert percent(cell(sheet, "Parameter", "Legacy deal discount", "Default")) == pytest.approx(
        legacy.discount_pct
    )

    for code, pair in cfg.pairs.items():
        shown = percent(cell(sheet, "Pair", "Annual vol, default", code.removeprefix("EUR")))
        assert shown == pytest.approx(pair.annual_vol), code


def test_sheet_reads_the_config_it_is_given(cfg: GeneratorConfig, rationale: dict[str, str]):
    hedge_fund = cfg.segments["hedge_fund"]
    changed = cfg.model_copy(
        update={
            "segments": {
                **cfg.segments,
                "hedge_fund": hedge_fund.model_copy(
                    update={
                        "base_markup_bps": {**hedge_fund.base_markup_bps, "em": 7.25},
                        "client_markup_sigma": 0.45,
                    }
                ),
            }
        }
    )
    sheet = render_parameter_sheet(changed, rationale)
    assert number(cell(sheet, "Default", "Markup, EM (bps)", "Hedge fund")) == 7.25
    assert number(cell(sheet, "Default", "Negotiation sigma", "Hedge fund")) == 0.45
    # Nothing else moved.
    assert number(cell(sheet, "Default", "Markup, CEE (bps)", "Hedge fund")) == 3.5


def test_every_your_value_cell_is_blank(sheet: str):
    slots = 0
    for rows in parse_tables(sheet):
        header = rows[0]
        if header[0] == "Your value":  # the blank grid: row labels down the side, slots after
            assert all(set(r[1:]) == {""} for r in rows[1:])
            slots += 1
        for column, heading in enumerate(header[1:], start=1):
            if heading == "Your value":
                assert all(r[column] == "" for r in rows[1:]), header[0]
                slots += 1
    # The blank grid, then the band, volume-tier and cost tables.
    assert slots == 4


def test_pair_table_has_a_blank_row_for_the_owner(sheet: str):
    rows = table(sheet, "Pair")
    blank = next(r for r in rows if r[0] == "Annual vol, your value")
    assert set(blank[1:]) == {""}


# --- the reason on every line -----------------------------------------------------------------


def test_every_parameter_has_rationale(cfg: GeneratorConfig, rationale: dict[str, str]):
    """Removing any one key from the shipped rationale raises KeyError naming that key."""
    assert len(rationale) >= 30  # guards against an accidentally emptied file
    for key in rationale:
        without = {k: v for k, v in rationale.items() if k != key}
        with pytest.raises(KeyError) as excinfo:
            render_parameter_sheet(cfg, without)
        assert key in excinfo.value.args[0], key


def test_missing_key_reported_is_the_first_in_sheet_order(
    cfg: GeneratorConfig, rationale: dict[str, str]
):
    # The segment grid comes before the ticket bands, whatever order the dict holds the keys in.
    gone = ("ticket_band.10m+", "compression", "markup.cee")
    without = {k: v for k, v in rationale.items() if k not in gone}
    with pytest.raises(KeyError) as excinfo:
        render_parameter_sheet(cfg, without)
    assert "markup.cee" in excinfo.value.args[0]


def test_blank_rationale_counts_as_missing(cfg: GeneratorConfig, rationale: dict[str, str]):
    with pytest.raises(KeyError) as excinfo:
        render_parameter_sheet(cfg, {**rationale, "hurdle_rate_bps": "   "})
    assert "hurdle_rate_bps" in excinfo.value.args[0]


def test_why_text_reaches_the_sheet(cfg: GeneratorConfig, rationale: dict[str, str]):
    sheet = render_parameter_sheet(
        cfg, {**rationale, "ticket_band.10m+": "Block | size", "credit_weight": "Credit text"}
    )
    # A pipe in the text must not split a table cell: it is escaped, so the row keeps its cells.
    assert cell(sheet, "Ticket band", "10m+", "Why") == r"Block \| size"
    # Segment rows carry their reason in the list under the grids, in row order.
    assert "- **Credit weight:** Credit text" in sheet.splitlines()
    assert f"- **Negotiation sigma:** {rationale['client_markup_sigma']}" in sheet.splitlines()


# --- determinism and size ---------------------------------------------------------------------


def test_render_is_deterministic(cfg: GeneratorConfig, rationale: dict[str, str]):
    first = render_parameter_sheet(cfg, rationale)
    second = render_parameter_sheet(cfg.model_copy(deep=True), dict(reversed(rationale.items())))
    assert first == second
    assert first.endswith("\n") and not first.endswith("\n\n")
    assert not re.search(r"\b20\d\d-\d\d-\d\d\b", first)  # no date stamped into the sheet


def test_sheet_stays_compact(sheet: str):
    lines = sheet.splitlines()
    assert len(lines) <= 110, len(lines)
    # Why text that runs on wraps a printed line; the table ones are the tightest.
    table_why = max(len(r[-1]) for rows in parse_tables(sheet) for r in rows[1:] if r[-1])
    assert table_why <= 100, table_why
    bullets = max(len(line) for line in lines if line.startswith("- **"))
    assert bullets <= 170, bullets


def test_committed_sheet_is_current(sheet: str):
    if not DEFAULT_SHEET_PATH.exists():
        pytest.skip("docs directory is not mounted")
    assert DEFAULT_SHEET_PATH.read_text(encoding="utf-8") == sheet, (
        "docs/methodology/parameter-sheet.md is stale: run `make param-sheet`"
    )


# --- rationale file ---------------------------------------------------------------------------


def test_load_rationale_rejects_a_non_mapping(tmp_path: Path):
    path = tmp_path / "rationale.yaml"
    path.write_text("- just\n- a list\n")
    with pytest.raises(ValueError, match="mapping"):
        load_rationale(path)


def test_load_rationale_rejects_non_string_values(tmp_path: Path):
    path = tmp_path / "rationale.yaml"
    path.write_text('hurdle_rate_bps: 0.75\n"capital_charge_bps": "fine"\n')
    with pytest.raises(ValueError, match="hurdle_rate_bps"):
        load_rationale(path)


def test_load_rationale_reports_bad_yaml(tmp_path: Path):
    path = tmp_path / "rationale.yaml"
    path.write_text("key: [unclosed\n")
    with pytest.raises(ValueError, match="not valid YAML"):
        load_rationale(path)


# --- the command ------------------------------------------------------------------------------


def test_cli_writes_the_sheet(tmp_path: Path, sheet: str, capsys: pytest.CaptureFixture[str]):
    out = tmp_path / "nested" / "parameter-sheet.md"
    args = [
        "param-sheet",
        "--config",
        str(DEFAULT_CONFIG_PATH),
        "--rationale",
        str(DEFAULT_RATIONALE_PATH),
        "--out",
        str(out),
    ]
    assert cli.main(args) == 0
    assert out.read_text(encoding="utf-8") == sheet
    assert str(out) in capsys.readouterr().out

    first = out.read_bytes()
    assert cli.main(args) == 0
    assert out.read_bytes() == first  # byte-identical on a second run


def test_cli_names_the_missing_key_and_writes_nothing(
    tmp_path: Path, rationale: dict[str, str], capsys: pytest.CaptureFixture[str]
):
    broken = tmp_path / "rationale.yaml"
    broken.write_text(yaml.safe_dump({k: v for k, v in rationale.items() if k != "volume_tier.3"}))
    out = tmp_path / "parameter-sheet.md"
    status = cli.main(
        ["param-sheet", "--rationale", str(broken), "--config", str(DEFAULT_CONFIG_PATH)]
        + ["--out", str(out)]
    )
    assert status == 1
    assert "volume_tier.3" in capsys.readouterr().err
    assert not out.exists()


def test_cli_leaves_an_existing_sheet_alone_when_rendering_fails(
    tmp_path: Path, rationale: dict[str, str]
):
    broken = tmp_path / "rationale.yaml"
    broken.write_text(
        yaml.safe_dump({k: v for k, v in rationale.items() if k != "hurdle_rate_bps"})
    )
    out = tmp_path / "parameter-sheet.md"
    out.write_text("previous sheet\n")
    status = cli.main(
        ["param-sheet", "--rationale", str(broken), "--config", str(DEFAULT_CONFIG_PATH)]
        + ["--out", str(out)]
    )
    assert status == 1
    assert out.read_text() == "previous sheet\n"
