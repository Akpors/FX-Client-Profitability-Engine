# FX Client Profitability Engine — Plan 2: Insight & Showcase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the marts from Plan 1 into the commercial answer and its showcase: the volume–margin map, pricing recommendations with break-even scenarios, a Streamlit dashboard (local Postgres and hosted Parquet modes), the analysis notebook, CI, the case study, the showcase README and the LinkedIn launch kit.

**Architecture:** Pure-Python decision logic (`fxengine.insights`, `fxengine.recommend`, `fxengine.findings`) sits on a small data-access layer that reads dbt marts from either Postgres or Parquet. The Streamlit app and the notebook both call the same functions, so the dashboard, notebook and case study can never disagree. Case-study numbers are injected from a computed `findings.json`, never typed by hand.

**Tech Stack:** Everything in Plan 1, plus Streamlit (with `streamlit.testing.v1.AppTest`), Plotly, kaleido (static figure export), pyarrow, JupyterLab and nbconvert, a pinned Playwright Python image for screenshots, and GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-12-fx-client-profitability-engine-design.md`. Read it before any task.
**Depends on:** `docs/superpowers/plans/2026-11-12-plan-1-data-engine.md`, all tasks complete. Tasks marked 🔒 need the git tag `params-v1` (the owner's calibration freeze). Tasks 1–8 can run against provisional data before the freeze.

## Global Constraints

- Everything in Plan 1's Global Constraints applies.
- Marts are read only from schema `marts` (Postgres) or `<FX_PARQUET_DIR>/<table>.parquet`. Table names come from `MART_TABLES`.
- `FX_DATA_MODE` ∈ {`postgres`, `parquet`}, default `postgres`. `FX_PARQUET_DIR` defaults to `app/demo_data`.
- Quadrant labels are exactly `Star`, `Volume trap`, `Niche earner`, `Tail`.
- Verdict labels are exactly `Reprice`, `Reprice with care`, `Hold – fix cost to serve`, `Review relationship`.
- Recommendations use **trailing 12 complete months** of `mart_client_monthly`.
- Break-even follows spec §6.2. Calculated on economic contribution; gross spread scales with retained notional; ops cost scales with retained tickets; capital charge scales with retained notional; coverage is fixed while retention > 0 and released at full exit (contribution 0).
- Money is formatted by `fxengine.formatting.fmt_eur`. bps are shown to 2 dp.
- Every dashboard page shows a "What this tells us" callout built from computed values.
- Owner-voice placeholders use the exact marker `<!-- OWNER: <prompt> -->`. Nothing ships publicly while any marker remains (checked in Task 12).
- No public action (repo creation, push, deploy, post) happens without the owner's explicit go-ahead at that moment.

## Review Focus

1. **Threshold sliders at extremes.** If every client lands in one quadrant, or a filter leaves zero clients, the chart and table should show an "No clients match" message, not crash. Pinned by `test_classify_empty_and_single_quadrant` (Task 1) and `test_map_page_extreme_thresholds` (Task 6).
2. **Clients whose contribution is already negative, or who have zero volume in the last 12 months.** Break-even should return `0.0` or `None` as specified, verdict text should never divide by zero, and zero-volume clients should be excluded with a note. Pinned by `test_break_even_negative_variable`, `test_break_even_unfixable` and `test_zero_volume_clients_excluded` (Task 2).
3. **Dashboard opened before `make pipeline` has run** (empty database). It should show "No data yet: run `make pipeline`" and no stack trace. Pinned by `test_home_without_marts_shows_guidance` (Task 4).
4. **Parquet demo mode with a missing file.** It should raise `MartsUnavailableError` naming the file and `make export-demo`. Pinned by `test_parquet_missing_file_raises` (Task 3).
5. **A table name from outside the allow-list reaching the reader.** It should be rejected before any SQL is built. Pinned by `test_unknown_table_rejected` (Task 3).

---

## File Structure

```
pipeline/src/fxengine/
  formatting.py      # fmt_eur, fmt_bps, fmt_pct              (Task 1)
  insights.py        # quadrants, ttm economics, volume traps (Task 1)
  recommend.py       # break-even, scenarios, verdicts        (Task 2)
  data_access.py     # MartReader, Postgres/Parquet, export   (Task 3)
  findings.py        # compute_findings, render_case_study    (Task 9)
app/
  Home.py            # Overview                               (Task 4)
  pages/1_Book_Composition.py  2_Pricing_and_Execution.py     (Task 5)
  pages/3_Volume_Margin_Map.py 4_Pricing_Recommendations.py   (Task 6)
  pages/5_Methodology.py                                       (Task 4)
  components.py  charts.py  narrative.py
  .streamlit/config.toml  requirements.txt (hosted mode, Task 11)
  demo_data/         # exported Parquet + manifest.json      (Task 11)
  tests/conftest.py (mart fixture factory), test_charts.py, test_narrative.py, test_pages.py
notebooks/01_volume_vs_margin.ipynb                            (Task 7)
scripts/screenshots.py  scripts/check_owner_markers.sh         (Tasks 10, 12)
.github/workflows/ci.yml                                       (Task 8)
docs/
  templates/case-study.md.tmpl → case-study.md                 (Task 9)
  assets/ figures/*.png, screenshots/*.png, findings.json, architecture.svg
  launch/ linkedin-post.md, carousel.md, walkthrough-script.md, launch-checklist.md (Task 12)
README.md (showcase), LICENSE                                  (Task 10)
```

---

### Task 1: Formatting, quadrants and trailing-12-month economics

**Files:**
- Create: `pipeline/src/fxengine/formatting.py`, `pipeline/src/fxengine/insights.py`, `pipeline/tests/test_formatting.py`, `pipeline/tests/test_insights.py`

**Interfaces:**
- Consumes: the column contracts of `mart_client_profitability` and `mart_client_monthly` (Plan 1, Tasks 9–10)
- Produces:
  - `fmt_eur(x: float) -> str`, `fmt_bps(x: float) -> str` (`"0.70 bps"`), `fmt_pct(x: float, dp: int = 0) -> str`
  - `QUADRANTS: tuple[str, ...] = ("Star", "Volume trap", "Niche earner", "Tail")`
  - `default_volume_cut(df, top_fraction: float = 0.2) -> float`: the `volume_share` value at the (1 − top_fraction) quantile
  - `classify_quadrants(df, volume_share_cut: float, margin_cut_bps: float, margin_col: str = "economic_margin_bps") -> pd.DataFrame`: a copy plus `quadrant`. High volume = `volume_share ≥ cut`; high margin = `margin ≥ cut`.
  - `ttm_economics(client_monthly, as_of_month: date | None = None) -> pd.DataFrame`: one row per client over the 12 months ending at `as_of_month` (default: the latest month). Columns: `client_id`, `client_name`, `segment`, `trade_count`, `volume_eur`, `revenue_eur`, `ops_cost_eur`, `capital_charge_eur`, `coverage_cost_eur`, `economic_contribution_eur`, `gross_margin_bps`, `economic_margin_bps`, `volume_share`.
  - `volume_traps(classified) -> pd.DataFrame`: rows where `quadrant == "Volume trap"`, sorted by `volume_eur` descending

- [ ] **Step 1: Write failing tests.**
  - `test_fmt_eur`: `fmt_eur(1_200_000_000) == "€1.20bn"`, `fmt_eur(84_000) == "€84.0k"`, `fmt_eur(3_450_000) == "€3.45m"`, `fmt_eur(-6_000) == "−€6.0k"`, `fmt_eur(512) == "€512"`, `fmt_eur(0) == "€0"`
  - `test_classify_four_quadrants`: (share, bps) = (0.30, 5.0), (0.30, 0.5), (0.01, 5.0), (0.01, 0.5) with cuts 0.10 / 2.0 map to Star, Volume trap, Niche earner, Tail
  - `test_classify_boundary_inclusive`: share == cut and bps == cut gives `Star`
  - `test_classify_empty_and_single_quadrant`: an empty frame returns an empty frame with a `quadrant` column; cuts 0 / −1e9 make every row `Star`
  - `test_ttm_uses_last_12_months_only`: in a 15-month fixture, months 1–3 have volume 999 and are excluded
  - `test_ttm_volume_share_sums_to_one`
- [ ] **Step 2:** `make test`. Expected: FAIL.
- [ ] **Step 3:** Implement both modules.
- [ ] **Step 4:** `make test`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(insights): formatting, quadrants and TTM client economics"`

---

### Task 2: Pricing recommendation engine

**Files:**
- Create: `pipeline/src/fxengine/recommend.py`, `pipeline/tests/test_recommend.py`
- Modify: `docs/methodology/metrics.md` (a "Pricing recommendation" section)

**Interfaces:**
- Consumes: `ttm_economics` rows; `peer_median_gross_margin_bps` from `mart_client_profitability`; `fmt_*`
- Produces:
  - `@dataclass(frozen=True) ClientEconomics`: `client_id`, `client_name`, `segment`, `volume_eur`, `trade_count`, `revenue_eur`, `ops_cost_eur`, `capital_charge_eur`, `coverage_cost_eur`. Properties: `variable_contribution_eur` (revenue − ops − capital), `contribution_eur` (variable − coverage), `gross_margin_bps`, `economic_margin_bps`.
  - `contribution_at(e, retention: float, uplift_bps: float) -> float`: `retention × (revenue + volume × uplift_bps/1e4 − ops − capital) − coverage` for `retention > 0`, and `0.0` for `retention == 0`. Raises `ValueError` outside [0, 1].
  - `break_even_retention(e, uplift_bps) -> float | None`: `r* = variable_now / variable_new`. Returns `None` if `variable_new ≤ 0`; clamps to [0.0, 1.0].
  - `gross_break_even_retention(margin_bps, uplift_bps) -> float`: `margin / (margin + uplift)`
  - `recommended_uplift_bps(client_gross_bps, peer_median_gross_bps) -> float`: `max(0, peer − client)`
  - `scenario_table(e, uplift_bps, retentions=(1.0, 0.8, 0.6, 0.4)) -> pd.DataFrame`. Columns `scenario`, `retention`, `volume_eur`, `revenue_eur`, `contribution_eur`, `delta_vs_today_eur`, plus a final row `retention=0.0`, `scenario="Client exits"`.
  - `@dataclass(frozen=True) Recommendation`: `client_id`, `client_name`, `segment`, `economics`, `peer_median_gross_margin_bps`, `uplift_bps`, `break_even_retention`, `break_even_volume_loss`, `gross_break_even_volume_loss` (each `float | None`), `post_uplift_economic_margin_bps`, `verdict`, `verdict_text`, `scenarios`
  - `recommend(e, peer_median_gross_bps, reprice_threshold: float = 0.40, care_threshold: float = 0.15) -> Recommendation`. Verdict rules, in order:
    1. `uplift_bps == 0` gives **Hold – fix cost to serve**
    2. `break_even_retention is None` gives **Review relationship**
    3. loss ≥ `reprice_threshold` gives **Reprice**
    4. loss ≥ `care_threshold` gives **Reprice with care**
    5. otherwise **Hold – fix cost to serve**
  - `verdict_text`, exact copy: `"{client_name} traded {fmt_eur(volume)} over the last 12 months but contributed only {fmt_eur(contribution)} ({fmt_bps(economic_margin)}). Moving pricing up {uplift:.2f} bps to the peer median still leaves us better off unless they move more than {fmt_pct(loss)} of their flow elsewhere. Recommendation: {verdict}."` When the loss is `None`, the second sentence becomes `"Repricing to the peer median does not cover the cost of serving this client."` When the uplift is 0, it becomes `"Pricing is already at or above the peer median; the margin problem is cost to serve, not price."`
  - `@dataclass RecommendationBatch(recommendations: list[Recommendation], skipped: list[str])`
  - `recommend_all(ttm: pd.DataFrame, peers: pd.Series, client_ids: Iterable[str]) -> RecommendationBatch`: clients with `volume_eur == 0` go to `skipped`

- [ ] **Step 1: Write failing tests.** Base fixture `e`: volume 1,000,000,000; trades 1,200; revenue 100,000; ops 6,000; capital 10,000; coverage 20,000.
  - `test_contribution_today`: `e.contribution_eur == 64_000`, `e.gross_margin_bps == approx(1.0)`
  - `test_contribution_at_80pct`: `contribution_at(e, 0.8, 1.5) == approx(167_200)`
  - `test_contribution_at_exit_is_zero`: `contribution_at(e, 0.0, 1.5) == 0.0`
  - `test_retention_out_of_range_raises`: 1.2 raises `ValueError`
  - `test_break_even`: `break_even_retention(e, 1.5) == approx(84_000/234_000)` (loss ≈ 0.641026)
  - `test_gross_break_even`: `gross_break_even_retention(1.0, 1.5) == approx(0.4)`
  - `test_break_even_negative_variable`: revenue 10,000 and uplift 1.5 give `0.0`
  - `test_break_even_unfixable`: revenue 10,000 and uplift 0 give `None`
  - `test_scenarios_rows_and_delta`: 5 rows; the 0.8 row has delta `approx(103_200)`; the exit row has `−64_000`
  - `test_verdict_reprice`: `recommend(e, 2.5).verdict == "Reprice"`; the text contains `"€1.00bn"`, `"€64.0k"` and `"64%"`
  - `test_verdict_hold_when_at_peer`: `recommend(e, 1.0).verdict == "Hold – fix cost to serve"`, and the text contains `"cost to serve, not price"`
  - `test_verdict_review_relationship`: revenue 10,000 and peer 0.05 bps above the client give `"Review relationship"`, and the text contains `"does not cover the cost"`
  - `test_zero_volume_clients_excluded`: one zero-volume client goes into `skipped`, with no error
- [ ] **Step 2:** `make test`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `make test`. Expected: PASS.
- [ ] **Step 5:** Add the "Pricing recommendation" section to `metrics.md`: formulas, cost behaviour under retention, verdict thresholds and their rationale, marked as illustrative.
- [ ] **Step 6: Commit.** `git commit -m "feat(recommend): break-even repricing engine with verdicts"`

---

### Task 3: Data-access layer and Parquet export

**Files:**
- Create: `pipeline/src/fxengine/data_access.py`, `pipeline/tests/test_data_access.py`
- Modify: `pipeline/src/fxengine/cli.py` (`export-parquet`), `Makefile` (`export-demo`)

**Interfaces:**
- Produces:
  - `MART_TABLES: frozenset[str]` = {`fct_trades`, `dim_clients`, `mart_client_profitability`, `mart_currency_profitability`, `mart_concentration`, `mart_client_monthly`, `mart_spread_compression`, `mart_headline_kpis`}
  - `class MartReader(Protocol): def read(self, table: str) -> pd.DataFrame`
  - `PostgresMartReader(engine)`: reads `marts.<table>` via `sqlalchemy.table(table, schema="marts")`. A missing relation raises `MartsUnavailableError` with the text "run `make pipeline`".
  - `ParquetMartReader(directory: Path)`: a missing file raises `MartsUnavailableError` naming the path and `make export-demo`
  - Both readers raise `UnknownTableError` for names outside `MART_TABLES`, before any I/O.
  - `get_reader(env: Mapping[str, str] = os.environ) -> MartReader`. Raises `ValueError` for an unknown mode.
  - `export_marts(reader, out_dir: Path, exclude: Iterable[str] = ("fct_trades",)) -> Path`. It writes `<table>.parquet` for each table and `manifest.json` (`exported_at` ISO UTC; `params_tag` from `git describe --tags --always`, or `"untagged"`; `row_counts`), and returns the manifest path.
  - CLI `export-parquet --out DIR`. `make export-demo` = `export-parquet --out app/demo_data`.

- [ ] **Step 1: Write failing tests.**
  - `test_unknown_table_rejected`: `ParquetMartReader(tmp).read("raw.trades; drop table x")` raises `UnknownTableError`
  - `test_parquet_missing_file_raises`: the message contains `"export-demo"`
  - `test_export_roundtrip`: frames are equal and the manifest's `row_counts` match
  - `test_get_reader_modes`: `parquet` gives `ParquetMartReader`; `"bogus"` raises `ValueError`
  - `test_postgres_missing_marts_message` (integration): an empty `marts` schema gives a message containing `"make pipeline"`
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** `make test && make test-integration` should PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(data): mart reader for Postgres/Parquet and demo export"`

---

### Task 4: Dashboard shell, Overview and Methodology pages

**Files:**
- Create: `app/Home.py`, `app/pages/5_Methodology.py`, `app/components.py`, `app/charts.py`, `app/narrative.py`, `app/.streamlit/config.toml`, `app/tests/conftest.py`, `app/tests/test_pages.py`, `app/tests/test_narrative.py`
- Modify: `docker-compose.yml` (`app` service: profile `app`, `127.0.0.1:${FX_APP_PORT:-8502}:8501`, same image as `pipeline`, command `streamlit run app/Home.py`), `Makefile` (`app`, `test-app`), `pipeline/pyproject.toml` (an `app` extra: streamlit, plotly, pyarrow, pinned)

**Interfaces:**
- Consumes: `get_reader`, `MartsUnavailableError`, `fmt_*`
- Produces:
  - `components.load(table: str) -> pd.DataFrame`: `st.cache_data` around `get_reader().read`
  - `components.guard_data() -> bool`: on `MartsUnavailableError`, renders `st.info` with "No data yet: run `make pipeline`" (or the export-demo message in Parquet mode) and returns False
  - `components.callout(body: str) -> None`: a "What this tells us" box
  - `components.kpi_row(items: Sequence[tuple[str, str]]) -> None`
  - `narrative.overview_text(kpis: pd.Series, concentration: pd.DataFrame) -> str`: contains the top-10 volume share and top-10 contribution share as percentages
  - `app/tests/conftest.py::mart_fixtures(tmp_path) -> Path`: writes a small, consistent mart Parquet set (12 clients, 15 months, 3 pairs, all four quadrants represented) and sets `FX_DATA_MODE=parquet`
- **Overview page:** the question (exact copy of the spec §1 quote), a KPI row (volume, revenue, economic contribution, gross bps, economic bps, clients), the callout, and "How to read this dashboard".
- **Methodology page:** tabs rendering `docs/methodology/benchmark-ecb.md`, `data-generation.md`, `metrics.md` and `parameter-sheet.md`.

- [ ] **Step 1: Write failing tests.**
  - `test_overview_text_contains_shares`: top-10 shares 0.62 and 0.35 give text containing `"62%"` and `"35%"`
  - `test_home_renders`: `AppTest.from_file("app/Home.py").run()` gives `not at.exception`, and some metric label equals `"Economic contribution"`
  - `test_home_without_marts_shows_guidance`: an empty Parquet dir gives `at.info[0].value` containing `"export-demo"`
  - `test_methodology_renders_tabs`: 4 tabs
- [ ] **Step 2:** `make test-app`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `make test-app` should PASS. Then run `make app`, open `http://127.0.0.1:8502` and check that the KPIs show from provisional data.
- [ ] **Step 5: Commit.** `git commit -m "feat(app): dashboard shell with overview and methodology"`

---

### Task 5: Book Composition and Pricing & Execution pages

**Files:**
- Create: `app/pages/1_Book_Composition.py`, `app/pages/2_Pricing_and_Execution.py`, `app/tests/test_charts.py`
- Modify: `app/charts.py`, `app/narrative.py`, `app/tests/test_pages.py`, `app/tests/test_narrative.py`

**Interfaces:**
- Produces (in `charts.py`, each returning `plotly.graph_objects.Figure`):
  - `pareto_chart(client_prof, measure: Literal["volume", "contribution"])`: sorted bars plus a cumulative-share line
  - `segment_mix_chart(client_prof)`: volume share vs contribution share by segment
  - `currency_chart(currency_prof, pairs_order: Sequence[str])`: gross bps and margin ex-coverage bps by pair
  - `spread_vs_benchmark_chart(client_prof)`: `gross_margin_bps` against `avg_benchmark_spread_bps`
  - `execution_quality_chart(client_prof)`: `avg_cover_slippage_bps` by segment (box)
  - `compression_chart(spread_compression, segment: str | None)`: monthly gross bps plus a trend line
  - `narrative.book_text(concentration, client_prof) -> str`: names the volume HHI and the segment with the largest (volume share − contribution share) gap
  - `narrative.pricing_text(spread_compression, client_prof) -> str`: names the most-compressed segment and its slope in bps/yr
- **Book Composition page:** concentration KPIs (top-5/10/20 and HHI for volume and revenue), both Pareto charts, segment mix, currency chart, callout.
- **Pricing & Execution page:** spread vs benchmark, execution quality, compression (segment selectbox), callout.

- [ ] **Step 1: Write failing tests.**
  - `test_pareto_cumulative_ends_at_one`
  - `test_currency_chart_respects_order`
  - `test_book_text_names_hhi_and_segment`
  - `test_pricing_text_names_compression`
  - `test_book_page_renders` and `test_pricing_page_renders`
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** `make test-app` should PASS, then do a visual check in `make app`.
- [ ] **Step 5: Commit.** `git commit -m "feat(app): book composition and pricing & execution pages"`

---

### Task 6: Volume–Margin Map and Pricing Recommendations pages

**Files:**
- Create: `app/pages/3_Volume_Margin_Map.py`, `app/pages/4_Pricing_Recommendations.py`
- Modify: `app/charts.py`, `app/narrative.py`, `app/tests/test_pages.py`, `app/tests/test_charts.py`

**Interfaces:**
- Consumes: `classify_quadrants`, `default_volume_cut`, `volume_traps`, `ttm_economics`, `recommend_all`, `contribution_at`, `break_even_retention`
- Produces:
  - `charts.quadrant_chart(classified, volume_cut, margin_cut) -> Figure`: log-x volume share, y economic margin bps, two threshold lines, colour by quadrant, hover shows client, segment, volume and contribution
  - `charts.scenario_chart(scenarios) -> Figure`: contribution by retention, with a horizontal line at today's value
  - `narrative.map_text(classified) -> str`: states the trap count, their combined volume share and their combined contribution share, for example "8 clients bring 31% of volume but 4% of contribution". With zero traps it says so plainly.
- **Map page:**
  - Slider "High-volume = top X% of clients" (5–50, default 20).
  - Slider "Margin hurdle (bps)" (default `hurdle_rate_bps` from `mart_headline_kpis`, range 0 to the 95th-percentile margin).
  - Quadrant chart, callout, and the volume-traps table. Choosing a client sets `st.session_state["selected_client"]` and links to Recommendations.
- **Recommendations page:**
  - Client selector (defaults to the selected client, else the largest volume trap) and a recommendation card (verdict badge, `verdict_text`, KPIs).
  - What-if sliders: uplift bps (0 to max(3 × recommended, 3), default recommended) and retention (0–100%, default 80%). They live-update `contribution_at` and break-even.
  - Scenario table, scenario chart, and the gross-vs-economic break-even line.
  - A "Download recommendations (CSV)" button covering all volume traps.

- [ ] **Step 1: Write failing tests.**
  - `test_quadrant_chart_has_threshold_lines`: 2 shapes
  - `test_map_text_counts_traps` and `test_map_text_zero_traps`
  - `test_map_page_extreme_thresholds`: sliders at (50, 0) and at (5, max). No exception either way, and an empty trap table shows `"No clients match"` in `at.info`.
  - `test_recommendations_page_defaults_to_largest_trap`
  - `test_whatif_retention_zero_shows_exit`: retention 0 shows the contribution metric as `"€0"`
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** `make test-app` should PASS, then do a visual check in `make app`.
- [ ] **Step 5: Commit.** `git commit -m "feat(app): volume–margin map and pricing recommendations"`

---

### Task 7: Analysis notebook and the emergence check

**Files:**
- Create: `notebooks/01_volume_vs_margin.ipynb`, `pipeline/tests/test_emergence.py` (`integration`)
- Modify: `docker-compose.yml` (`notebook` service: profile `notebook`, `127.0.0.1:${FX_NOTEBOOK_PORT:-8889}:8888`, token from `.env`), `Makefile` (`notebook`, `notebook-check`), `.env.example` (`FX_NOTEBOOK_TOKEN`)

**Interfaces:**
- Consumes: `get_reader`, `insights`, `recommend`, `app/charts.py` (the first cell inserts `app/` into `sys.path`)
- Produces: the notebook sections, in order:
  1. The question
  2. The data and its honesty statement
  3. Book overview
  4. Concentration
  5. Pricing vs benchmark
  6. The volume–margin map
  7. Deep dive on the top 3 volume traps (why each is low-margin: tier discount, legacy deal, adverse selection or compression, read from attributes and metrics)
  8. Recommendations
  9. Limitations

  Practitioner-note cells carry `<!-- OWNER: ... -->` markers.

- [ ] **Step 1: Write the failing emergence test.** `test_volume_traps_exist` runs on full pipeline output with the defaults (top 20% and the hurdle) and asserts:
  - there is at least 1 volume trap
  - at least 2 distinct causes appear across the traps: the set of {`legacy_deal`, top volume tier, `hedge_fund` segment, compression slope < 0} flags present among the traps has size ≥ 2

  This guards against a finding that looks rigged (the spec's anti-goal).
- [ ] **Step 2:** `make test-integration`. Expected: FAIL, then PASS once the pipeline data exists. If it still fails, adjust generator *mechanisms*, never hard-coded outcomes. After the freeze, any change goes to the owner first.
- [ ] **Step 3:** Write the notebook.
- [ ] **Step 4:** `make notebook-check` (`jupyter nbconvert --to notebook --execute --output /tmp/out.ipynb`). Expected: exit 0.
- [ ] **Step 5: Commit** with outputs cleared: `git commit -m "feat(analysis): volume vs margin notebook and emergence check"`

---

### Task 8: Continuous integration

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `Makefile` (`ci`)

**Interfaces:**
- Produces:
  - `make ci` runs, in order: `build`, `lint`, `test`, `up`, `pipeline`, `test-integration`, `export-parquet` into a temp dir, `test-app`, `notebook-check`, `down`. It exits non-zero on the first failure.
  - `ci.yml`: on push and pull_request, one `ubuntu-24.04` job running `make ci`. Actions are pinned to full version tags, with a 30-minute timeout.

- [ ] **Step 1:** `make clean && make ci` locally. Expected: exit 0 from clean volumes; note the runtime.
- [ ] **Step 2:** Validate with `docker run --rm -v "$PWD":/repo rhysd/actionlint:<pinned tag> -color /repo/.github/workflows/ci.yml`. Expected: no findings.
- [ ] **Step 3: Commit.** `git commit -m "ci: full pipeline, tests and notebook check"`

---

### Task 9: 🔒 Findings and case study

**Files:**
- Create: `pipeline/src/fxengine/findings.py`, `pipeline/tests/test_findings.py`, `docs/templates/case-study.md.tmpl`, `docs/case-study.md` (rendered), `docs/assets/findings.json`, `docs/assets/figures/{quadrant,pareto,compression,scenario}.png`
- Modify: `pipeline/src/fxengine/cli.py` (`findings`), `Makefile` (`findings`), `pipeline/pyproject.toml` (kaleido, pinned to a version that bundles Chromium on Linux)

**Interfaces:**
- Produces:
  - `compute_findings(reader) -> dict[str, str | float | int]`. Keys:
    - `total_volume`, `total_contribution`, `gross_bps`, `economic_bps`, `n_clients`
    - `top10_volume_share`, `top10_contribution_share`, `hhi_volume`
    - `n_volume_traps`, `traps_volume_share`, `traps_contribution_share`
    - `example_client_name`, `example_volume`, `example_contribution`, `example_uplift_bps`, `example_break_even_loss`, `example_verdict`
    - `most_compressed_segment`, `compression_bps_per_year`, `window_start`, `window_end`, `params_tag`

    Money is pre-formatted via `fmt_eur` and percentages via `fmt_pct`. The example client is the largest volume trap.
  - `render_case_study(template: str, findings: Mapping[str, object]) -> str`: `string.Template.substitute`, so a missing key raises `KeyError`
  - `make findings`: writes `findings.json`, renders `case-study.md`, and exports the 4 figures at 1600×1000
- Case-study template sections:
  - Title, then a TL;DR (3 bullets with `$`-placeholders)
  - The question
  - Why it matters on a desk `<!-- OWNER -->`
  - The data (synthetic and why; the ECB benchmark and caveats; spot-only)
  - Method (the economic-contribution model; link to the metrics)
  - Findings (book, concentration, map)
  - The recommendation (worked example with the scenario figure)
  - How I'd take this to a client `<!-- OWNER -->`
  - Limitations and what I'd do with real data: forwards, real cost allocation, elasticity from RFQ hit ratios, intraday benchmarks such as WM/R 4pm
  - Links (repo, live demo, methodology)

- [ ] **Step 1:** `git tag --list params-v1` must print `params-v1`. If it doesn't, STOP.
- [ ] **Step 2: Write failing tests.**
  - `test_render_missing_key_raises`
  - `test_findings_keys_complete`: a fixture reader gives every key above
  - `test_case_study_has_no_unrendered_placeholders`: the rendered text has no match for `\$\{?[A-Za-z_]`
- [ ] **Step 3:** FAIL. **Step 4:** Implement and write the template. **Step 5:** PASS.
- [ ] **Step 6:** `make findings`, then read `docs/case-study.md` end to end. Every number must match the dashboard.
- [ ] **Step 7: Commit.** `git commit -m "docs: case study rendered from computed findings"`

---

### Task 10: 🔒 Showcase README, architecture diagram and screenshots

**Files:**
- Create: `docs/assets/architecture.svg`, `scripts/screenshots.py`, `docs/assets/screenshots/{overview,map,recommendation}.png`, `LICENSE`
- Modify: `README.md` (replace the placeholder), `Makefile` (`screenshots`), `docker-compose.yml` (a `screenshots` service using the pinned Playwright Python image, profile `tools`)

**Interfaces:**
- Produces:
  - `scripts/screenshots.py`: viewport 1600×1000; waits for `[data-testid="stAppViewContainer"]` and for the spinner to disappear; captures the Overview, the Map (default thresholds) and Recommendations (the default client); asserts each PNG is > 50 KB with ≥ 3 distinct colours in a centre sample
  - README sections, in order:
    1. Title and one-line pitch
    2. Headline finding: 2 sentences with numbers from `findings.json`, plus the map screenshot
    3. Live demo link (placeholder until Task 11) and the case study link
    4. The question and why it matters
    5. What's inside (the 12 metrics, with links)
    6. Architecture diagram
    7. Run it yourself (Docker is the only prerequisite: `make pipeline && make app`)
    8. Project structure
    9. Methodology and limitations
    10. About the author `<!-- OWNER -->`
    11. Licence
  - `LICENSE`: MIT, copyright holder `<!-- OWNER: full name -->`

- [ ] **Step 1:** Draw `architecture.svg` to match spec §7, readable on GitHub's light and dark themes.
- [ ] **Step 2:** `make app` (detached) and then `make screenshots`. Expected: 3 PNGs pass the script's checks.
- [ ] **Step 3:** Write the README and LICENSE.
- [ ] **Step 4: Verify the quick-start from scratch.** `make clean`, then follow the README commands exactly. Expected: the dashboard loads at `127.0.0.1:8502`.
- [ ] **Step 5: Commit.** `git commit -m "docs: showcase README, architecture and screenshots"`

---

### Task 11: 🔒 Hosted demo bundle (Parquet mode)

**Files:**
- Create: `app/demo_data/*.parquet`, `app/demo_data/manifest.json`, `app/requirements.txt`
- Modify: `app/.streamlit/config.toml`, `README.md`

**Interfaces:**
- Produces: an app that runs with `FX_DATA_MODE=parquet` and no database. `app/requirements.txt` pins streamlit, plotly, pandas, pyarrow, numpy and pydantic, and installs `fxengine` from `./pipeline`. If the host platform cannot install a local path, vendor the needed modules instead and record which in the commit. Demo data totals under 25 MB.

- [ ] **Step 1:** `make export-demo`. `manifest.json` `params_tag` should be `params-v1`, and `du -sh app/demo_data` should be under 25 MB.
- [ ] **Step 2: Simulate the host.** `docker run --rm -p 127.0.0.1:8503:8501 -v "$PWD":/src -w /src python:3.12-slim sh -c "pip install -r app/requirements.txt && FX_DATA_MODE=parquet streamlit run app/Home.py --server.address 0.0.0.0"`. At `127.0.0.1:8503`, all six pages should render with no database.
- [ ] **Step 3: Commit.** `git commit -m "feat(demo): self-contained Parquet demo bundle"`
- [ ] **Step 4: Owner action, not automated.** With the owner's explicit go-ahead, create the public GitHub repo and push (the owner, or the builder via `gh` when asked), then the owner connects Streamlit Community Cloud to `app/Home.py`. Put the live URL into the README and commit.

---

### Task 12: 🔒 LinkedIn launch kit and launch checklist

**Files:**
- Create: `docs/launch/linkedin-post.md`, `docs/launch/carousel.md`, `docs/launch/walkthrough-script.md`, `docs/launch/launch-checklist.md`, `scripts/check_owner_markers.sh`

**Interfaces:**
- Consumes: `findings.json`, figures, screenshots, the case study
- Produces:
  - **linkedin-post.md:** a hook line plus about 150–250 words for FX and financial-services readers. Problem → what I built → the counter-intuitive finding with numbers → one line on method → links. 3–5 hashtags. Draft with the `social-media-skills:linkedin-post-writer` skill.
  - **carousel.md:** 7 slides, each with headline, body (≤ 25 words) and asset. Slides: hook question → why volume misleads → the map → a volume-trap example → recommendation and break-even → how it's built → CTA. Draft with `social-media-skills:carousel-writer`. Building the visual slides is a separate step when the owner chooses.
  - **walkthrough-script.md:** a 60–90 s screen-recording script, timed per page.
  - **launch-checklist.md:** owner steps in order: fill OWNER markers → review case study → make the repo public → demo live → record the walkthrough → post → add to the Featured section → plan replies to comments.
  - `scripts/check_owner_markers.sh`: exits 1 and lists files while any `<!-- OWNER:` marker remains in `README.md`, `LICENSE`, `docs/case-study.md`, `docs/templates/`, `docs/launch/` or `notebooks/`; exits 0 otherwise.

- [ ] **Step 1:** Write `check_owner_markers.sh` and run it. Expected now: exit 1 listing the placeholder files.
- [ ] **Step 2:** Draft the four launch documents with the skills named above. Every number must come from `findings.json`.
- [ ] **Step 3:** Hand over to the owner: the list of markers to fill, and the checklist.
- [ ] **Step 4:** After the owner fills the markers, `scripts/check_owner_markers.sh` should exit 0. Re-run `make findings` if the template changed.
- [ ] **Step 5: Commit.** `git commit -m "docs: LinkedIn launch kit and checklist"`
