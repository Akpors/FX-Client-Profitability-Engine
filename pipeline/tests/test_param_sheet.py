"""Tests for the calibration parameter sheet (spec section 11.1).

The sheet is the owner's review document: every calibration parameter with its default, a blank
slot for the owner's value and a one-line reason, on one printed A4 page. The tests read the
rendered Markdown back as tables, so they check what a reader sees (a cell in a row), not just
that a string is present.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from fxengine import cli, param_sheet
from fxengine.config import DEFAULT_CONFIG_PATH, GeneratorConfig, load_config
from fxengine.param_sheet import (
    DEFAULT_RATIONALE_PATH,
    DEFAULT_SHEET_PATH,
    MissingRationaleError,
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

# The segment grid's rows after the four markup rows: label and the SegmentConfig attribute.
SEGMENT_ROWS = {
    "Compression per year": "compression_per_year",
    "Ops cost per ticket (EUR)": "ops_cost_per_ticket_eur",
    "Coverage cost per client per month (EUR)": "coverage_cost_eur_per_month",
    "Credit weight": "credit_weight",
    "Negotiation sigma (client price scatter)": "client_markup_sigma",
}
ADVERSE_ROW = "Adverse selection mean/sd (bps)"
HALF_SPREAD_ROW = "Cover half-spread, major/minor/CEE/EM (bps)"
SEGMENT_GRID = "Segment parameter"

# Every rationale key and the start of the row (a bullet under a grid, or a table row) that must
# carry it. test_each_why_lands_on_its_own_row holds this to the shipped rationale.yaml.
ROW_ANCHORS = {
    "markup": "- **Markup:** ",
    "compression": "- **Compression:** ",
    "adverse_selection": "- **Adverse selection:** ",
    "ops_cost": "- **Ops cost:** ",
    "coverage": "- **Coverage:** ",
    "credit_weight": "- **Credit weight:** ",
    "client_markup_sigma": "- **Negotiation sigma:** ",
    "ticket_band.edges": "- **Band edges:** ",
    "ticket_band.multiplier": "- **Band multipliers:** ",
    "volume_tier.threshold": "- **Tier thresholds:** ",
    "volume_tier.discount": "- **Tier discounts:** ",
    "cover_half_spread": f"| {HALF_SPREAD_ROW} |",
    "settlement_weight.cls": "| Settlement weight, CLS |",
    "settlement_weight.non_cls": "| Settlement weight, non-CLS (",
    "capital_charge_bps": "| Capital charge (bps) |",
    "hurdle_rate_bps": "| Hurdle rate (bps of volume) |",
    "legacy_deal.probability": "| Legacy deal probability |",
    "legacy_deal.discount_pct": "| Legacy deal discount |",
}

# One printed A4 portrait page (spec section 11.1), as the controller ruled for this sheet.
MAX_LINES = 70
MAX_COLUMNS = 8
MAX_TEXT_LINE = 110
MAX_TABLE_ROW = 130  # a table row longer than this would not render within the page width

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
    """The cell at (row label, column header) of a plain Default | Your value | Why table."""
    rows = table(markdown, name)
    for r in rows[1:]:
        if r[0] == row:
            return r[rows[0].index(column)]
    raise AssertionError(f"table {name!r} has no row {row!r}")


def grid_row(markdown: str, name: str, label: str, which: str = "Default") -> dict[str, str]:
    """A grid row as {column header: cell}. A parameter is a Default row, then a Your value row."""
    rows = table(markdown, name)
    header = rows[0]
    for i, r in enumerate(rows[1:], start=1):
        if r[0] == label:
            chosen = r if which == "Default" else rows[i + 1]
            assert chosen[1] == which, (label, which, chosen[:2])
            return dict(zip(header[2:], chosen[2:], strict=True))
    raise AssertionError(f"table {name!r} has no row {label!r}")


def number(text: str) -> float:
    return float(text.replace(",", ""))


def percent(text: str) -> float:
    assert text.endswith("%"), text
    return number(text.removesuffix("%")) / 100


def size(text: str) -> float | None:
    """EUR shorthand back to a number: 25k, 100m, 2bn; ``none`` is no limit."""
    if text == "none":
        return None
    for suffix, factor in (("bn", 1e9), ("m", 1e6), ("k", 1e3)):
        if text.endswith(suffix):
            return number(text.removesuffix(suffix)) * factor
    return number(text)


def pairs_text(cfg: GeneratorConfig, keep) -> str:
    return " ".join(c.removeprefix("EUR") for c, p in cfg.pairs.items() if keep(p))


def markup_label(cfg: GeneratorConfig, tier: str) -> str:
    pairs = pairs_text(cfg, lambda p: p.liquidity_tier == tier)
    return f"Markup, {TIER_LABELS[tier]}: {pairs} (bps)"


def non_cls_label(cfg: GeneratorConfig) -> str:
    return f"Settlement weight, non-CLS ({pairs_text(cfg, lambda p: not p.cls_settled)})"


# --- coverage ---------------------------------------------------------------------------------


def test_sheet_contains_every_segment_and_tier(sheet: str, cfg: GeneratorConfig):
    # Rendering the shipped defaults at all proves rationale.yaml covers every key.
    rows = table(sheet, SEGMENT_GRID)
    assert rows[0][2:] == list(SEGMENT_LABELS.values())
    assert [r[0] for r in rows[1:] if r[0]] == [
        *(markup_label(cfg, tier) for tier in TIER_LABELS),
        "Compression per year",
        ADVERSE_ROW,
        *(label for label in SEGMENT_ROWS if label != "Compression per year"),
    ]

    # Every band edge and multiplier, every tier threshold and discount, in its own column.
    bands = table(sheet, "Ticket band")
    assert bands[0][2:] == [band.label for band in cfg.ticket_bands]
    assert [r[0] for r in bands[1:] if r[0]] == ["Upper edge (EUR)", "Markup multiplier"]
    tiers = table(sheet, "Volume tier")
    assert tiers[0][2:] == [f"Tier {i}" for i in range(1, len(cfg.volume_tiers) + 1)]
    assert [r[0] for r in tiers[1:] if r[0]] == ["Annual volume from (EUR)", "Discount off markup"]

    assert [r[0] for r in table(sheet, "Parameter")[1:]] == [
        HALF_SPREAD_ROW,
        "Capital charge (bps)",
        "Settlement weight, CLS",
        non_cls_label(cfg),
        "Hurdle rate (bps of volume)",
        "Legacy deal probability",
        "Legacy deal discount",
    ]


def test_markup_rows_list_the_pairs_of_each_tier(sheet: str, cfg: GeneratorConfig):
    # The tier-to-pair mapping rides in the row labels; check it names every pair exactly once.
    labels = " ".join(markup_label(cfg, tier) for tier in TIER_LABELS)
    for code in cfg.pairs:
        assert len(re.findall(rf"\b{code.removeprefix('EUR')}\b", labels)) == 1, code
        assert markup_label(cfg, cfg.pairs[code].liquidity_tier) in sheet


def test_sheet_has_header_and_footer(sheet: str):
    assert "Illustrative defaults, pending owner calibration" in sheet
    assert "section 11.1" in sheet
    assert "Regenerate with `make param-sheet`; do not edit by hand" in sheet


def test_no_per_pair_volatility_table(sheet: str):
    # Optional extra, cut to fit the page; nothing about it may linger.
    assert "volatility" not in sheet.lower()
    assert all(rows[0][0] != "Pair" for rows in parse_tables(sheet))


# --- values -----------------------------------------------------------------------------------


def test_sheet_values_match_config(sheet: str, cfg: GeneratorConfig):
    for name, segment in cfg.segments.items():
        label = SEGMENT_LABELS[name]

        for tier in TIER_LABELS:
            row = grid_row(sheet, SEGMENT_GRID, markup_label(cfg, tier))
            assert number(row[label]) == segment.base_markup_bps[tier], (name, tier)

        for row_label, attribute in SEGMENT_ROWS.items():
            shown = grid_row(sheet, SEGMENT_GRID, row_label)[label]
            assert number(shown) == getattr(segment, attribute), (name, row_label)

        shown = grid_row(sheet, SEGMENT_GRID, ADVERSE_ROW)[label]
        mean, sd = (number(part) for part in shown.split("/"))
        assert (mean, sd) == tuple(segment.adverse_selection_bps), name

    for band in cfg.ticket_bands:
        edge = grid_row(sheet, "Ticket band", "Upper edge (EUR)")[band.label]
        assert size(edge) == band.max_ticket_eur, band.label
        multiplier = grid_row(sheet, "Ticket band", "Markup multiplier")[band.label]
        assert number(multiplier) == band.markup_multiplier, band.label

    for i, tier in enumerate(cfg.volume_tiers, start=1):
        threshold = grid_row(sheet, "Volume tier", "Annual volume from (EUR)")[f"Tier {i}"]
        assert size(threshold) == tier.min_annual_volume_eur, i
        discount = grid_row(sheet, "Volume tier", "Discount off markup")[f"Tier {i}"]
        assert percent(discount) == pytest.approx(tier.discount_pct), i

    # One cell, four tiers in the order the label names them.
    shown = cell(sheet, "Parameter", HALF_SPREAD_ROW, "Default").split("/")
    assert [number(part) for part in shown] == [
        cfg.cover_half_spread_bps[tier] for tier in TIER_LABELS
    ]

    cost, legacy = cfg.cost, cfg.legacy_deal
    plain = {
        "Capital charge (bps)": cost.capital_charge_bps,
        "Settlement weight, CLS": cost.settlement_weight_cls,
        non_cls_label(cfg): cost.settlement_weight_non_cls,
        "Hurdle rate (bps of volume)": cost.hurdle_rate_bps,
    }
    for row, expected in plain.items():
        assert number(cell(sheet, "Parameter", row, "Default")) == expected, row
    shown = percent(cell(sheet, "Parameter", "Legacy deal probability", "Default"))
    assert shown == pytest.approx(legacy.probability)
    shown = percent(cell(sheet, "Parameter", "Legacy deal discount", "Default"))
    assert shown == pytest.approx(legacy.discount_pct)


def test_sheet_reads_the_config_it_is_given(cfg: GeneratorConfig, rationale: dict[str, str]):
    hedge_fund = cfg.segments["hedge_fund"]
    changed = cfg.model_copy(
        update={
            "cover_half_spread_bps": {**cfg.cover_half_spread_bps, "em": 3.75},
            "segments": {
                **cfg.segments,
                "hedge_fund": hedge_fund.model_copy(
                    update={
                        "base_markup_bps": {**hedge_fund.base_markup_bps, "em": 7.25},
                        "client_markup_sigma": 0.45,
                    }
                ),
            },
            "ticket_bands": [
                band.model_copy(update={"max_ticket_eur": 30_000})
                if band.label == "Under 25k"
                else band
                for band in cfg.ticket_bands
            ],
        }
    )
    sheet = render_parameter_sheet(changed, rationale)
    em = grid_row(sheet, SEGMENT_GRID, markup_label(changed, "em"))
    assert number(em["Hedge fund"]) == 7.25
    sigma = grid_row(sheet, SEGMENT_GRID, "Negotiation sigma (client price scatter)")
    assert number(sigma["Hedge fund"]) == 0.45
    assert size(grid_row(sheet, "Ticket band", "Upper edge (EUR)")["Under 25k"]) == 30_000
    half_spread = cell(sheet, "Parameter", HALF_SPREAD_ROW, "Default").split("/")
    assert number(half_spread[-1]) == 3.75
    # Nothing else moved.
    cee = grid_row(sheet, SEGMENT_GRID, markup_label(changed, "cee"))
    assert number(cee["Hedge fund"]) == hedge_fund.base_markup_bps["cee"]


def test_every_your_value_cell_is_blank(sheet: str):
    grids = slots = 0
    for rows in parse_tables(sheet):
        header = rows[0]
        if header[1] == "Value":
            # A Default row (label, "Default", values) then a blank Your value row, in pairs.
            body = rows[1:]
            assert len(body) % 2 == 0, header[0]
            for default, yours in zip(body[::2], body[1::2], strict=True):
                assert default[0] and default[1] == "Default" and all(default[2:]), default
                assert yours[0] == "" and yours[1] == "Your value", yours
                assert set(yours[2:]) == {""}, yours
                slots += len(yours[2:])
            grids += 1
        elif "Your value" in header:
            column = header.index("Your value")
            assert all(r[column] == "" for r in rows[1:]), header[0]
            slots += len(rows) - 1
    # Segment, band and tier grids, plus the cover and capital table's Your value column.
    assert grids == 3
    assert slots == 10 * 6 + 2 * 6 + 2 * 6 + 7


# --- the reason on every line -----------------------------------------------------------------


def test_every_parameter_has_rationale(cfg: GeneratorConfig, rationale: dict[str, str]):
    """Removing any one key from the shipped rationale raises KeyError naming that key."""
    assert len(rationale) >= 17  # guards against an accidentally emptied file
    for key in rationale:
        without = {k: v for k, v in rationale.items() if k != key}
        with pytest.raises(KeyError) as excinfo:
            render_parameter_sheet(cfg, without)
        assert key in str(excinfo.value), key
        assert isinstance(excinfo.value, MissingRationaleError)
        assert excinfo.value.key == key


def test_missing_key_reported_is_the_first_in_sheet_order(
    cfg: GeneratorConfig, rationale: dict[str, str]
):
    # The segment grid comes before the bands and the capital table, whatever the dict order.
    gone = ("hurdle_rate_bps", "ticket_band.edges", "compression")
    without = {k: v for k, v in rationale.items() if k not in gone}
    with pytest.raises(MissingRationaleError) as excinfo:
        render_parameter_sheet(cfg, without)
    assert excinfo.value.key == "compression"


def test_blank_rationale_counts_as_missing(cfg: GeneratorConfig, rationale: dict[str, str]):
    with pytest.raises(MissingRationaleError) as excinfo:
        render_parameter_sheet(cfg, {**rationale, "hurdle_rate_bps": "   "})
    assert excinfo.value.key == "hurdle_rate_bps"


def test_each_why_lands_on_its_own_row(cfg: GeneratorConfig, rationale: dict[str, str]):
    """Render a sentinel for every key: each must appear once, on the row built for that key."""
    assert set(ROW_ANCHORS) == set(rationale), "ROW_ANCHORS and rationale.yaml are out of step"
    sentinels = {key: f"WHY[{key}]" for key in rationale}
    lines = render_parameter_sheet(cfg, sentinels).splitlines()
    for key, anchor in ROW_ANCHORS.items():
        hits = [line for line in lines if sentinels[key] in line]
        assert len(hits) == 1, (key, hits)
        assert hits[0].startswith(anchor), (key, hits[0])


def test_why_text_is_one_line_and_pipes_are_escaped(
    cfg: GeneratorConfig, rationale: dict[str, str]
):
    sheet = render_parameter_sheet(
        cfg, {**rationale, "hurdle_rate_bps": "Break | even\n  with  gaps"}
    )
    # A pipe must not split a table cell: it is escaped, so the row keeps its four cells.
    assert (
        cell(sheet, "Parameter", "Hurdle rate (bps of volume)", "Why") == r"Break \| even with gaps"
    )


def test_rationale_holds_no_figures_that_could_go_stale(rationale: dict[str, str]):
    """Lines name roles, not numbers; the one exception is the credit-weight convention."""
    allowed = {"credit_weight"}
    for key, text in rationale.items():
        if key not in allowed:
            assert not re.search(r"\d", text), f"{key}: {text!r}"


# --- determinism and size ---------------------------------------------------------------------


def test_render_is_deterministic(cfg: GeneratorConfig, rationale: dict[str, str]):
    first = render_parameter_sheet(cfg, rationale)
    second = render_parameter_sheet(cfg.model_copy(deep=True), dict(reversed(rationale.items())))
    assert first == second
    assert first.endswith("\n") and not first.endswith("\n\n")
    assert not re.search(r"\b20\d\d-\d\d-\d\d\b", first)  # no date stamped into the sheet


def test_sheet_fits_one_page_70_lines_8_columns_110_chars(sheet: str):
    lines = sheet.splitlines()
    assert len(lines) <= MAX_LINES, f"{len(lines)} lines"
    assert max(len(rows[0]) for rows in parse_tables(sheet)) <= MAX_COLUMNS
    for line in lines:
        limit = MAX_TABLE_ROW if line.startswith("|") else MAX_TEXT_LINE
        assert len(line) <= limit, f"{len(line)} > {limit}: {line}"


def test_committed_sheet_is_current(sheet: str):
    if not DEFAULT_SHEET_PATH.parent.is_dir():
        pytest.skip("docs directory is not mounted")
    assert DEFAULT_SHEET_PATH.is_file(), (
        "docs/methodology/parameter-sheet.md is missing: run `make param-sheet`"
    )
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


def _args(rationale_path: Path, out: Path) -> list[str]:
    return [
        "param-sheet",
        "--config",
        str(DEFAULT_CONFIG_PATH),
        "--rationale",
        str(rationale_path),
        "--out",
        str(out),
    ]


def test_cli_writes_the_sheet(tmp_path: Path, sheet: str, capsys: pytest.CaptureFixture[str]):
    out = tmp_path / "nested" / "parameter-sheet.md"
    args = _args(DEFAULT_RATIONALE_PATH, out)
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
    broken.write_text(
        yaml.safe_dump({k: v for k, v in rationale.items() if k != "volume_tier.discount"})
    )
    out = tmp_path / "parameter-sheet.md"
    assert cli.main(_args(broken, out)) == 1
    assert "volume_tier.discount" in capsys.readouterr().err
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
    assert cli.main(_args(broken, out)) == 1
    assert out.read_text() == "previous sheet\n"


def test_cli_does_not_report_an_unrelated_key_error_as_a_missing_rationale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def broken_renderer(cfg: GeneratorConfig, rationale: dict[str, str]) -> str:
        raise KeyError("a bug, not a missing rationale")

    monkeypatch.setattr(param_sheet, "render_parameter_sheet", broken_renderer)
    with pytest.raises(KeyError, match="a bug"):
        cli.main(_args(DEFAULT_RATIONALE_PATH, tmp_path / "parameter-sheet.md"))
