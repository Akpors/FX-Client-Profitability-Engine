# FX Client Profitability Engine — Plan 1: Data Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Dockerised pipeline that fetches ECB reference rates, generates a realistic synthetic spot-FX trade book, loads it into Postgres, and calculates all twelve metrics in tested dbt models. It ends with the owner's calibration gate.

**Architecture:** A Python package `fxengine` (in `pipeline/`) covers ECB ingestion, data generation and loading into a `raw` schema in Postgres 16. dbt turns `raw` into `staging → intermediate → marts`. Everything runs through Docker Compose and is driven by a Makefile. ECB data is saved as a committed CSV snapshot, so the build runs offline and gives the same result every time.

**Tech Stack:** Docker Compose, Python 3.12 (numpy, pandas, pydantic, PyYAML, httpx, psycopg 3, SQLAlchemy 2), PostgreSQL 16, dbt-core + dbt-postgres ≥ 1.8 (unit tests), pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-11-12-fx-client-profitability-engine-design.md`. Read it before starting any task.

**Follow-on:** `docs/superpowers/plans/2026-11-12-plan-2-insight-and-showcase.md` starts after Task 11 (the calibration gate).

## Global Constraints

- Nothing is installed on the host apart from Docker. Every command runs through `make` or `docker compose`.
- Host ports are bound to `127.0.0.1` only. Postgres `127.0.0.1:${FX_DB_PORT:-5433}`, Streamlit `127.0.0.1:${FX_APP_PORT:-8502}`, Jupyter `127.0.0.1:${FX_NOTEBOOK_PORT:-8889}`.
- Compose project name is `fxengine`. Volume names are prefixed `fxengine_`.
- All image tags and Python/dbt package versions are pinned exactly (no `latest`, no unpinned ranges in lock files).
- Products: **spot only.** No value date, tenor, forwards or swaps.
- Currency pairs (base EUR): USD, GBP, JPY, CHF, SEK, NOK, DKK, AUD, CAD, PLN, CZK, HUF, ZAR, TRY, MXN. Pair code format `EURUSD`.
- ECB rates are quoted as **units of quote currency per 1 EUR**. ECB series key `D.{CCY}.EUR.SP00.A`, dataflow `EXR`, base URL `https://data-api.ecb.europa.eu/service/data/EXR/`.
- Time window: the most recent 24 complete calendar months before the build date. Trades happen only on dates with an ECB fix.
- `side_sign` = `+1` when the client BUYs EUR and `−1` when the client SELLs EUR.
- Dealer gross P&L (quote ccy) = `side_sign × (client_rate − cover_rate) × notional_base`. Convert to EUR as `gross_pnl_quote / benchmark_rate`.
- bps = value × 10,000.
- Fixed default seed `42`. The same config and snapshot must give byte-identical raw tables.
- No real client data, no real company names. Client names are codes such as `Client LC-014`.
- Repo stays local (no remote) until the owner says go.

## Review Focus

1. **ECB data gaps.** If a currency is missing on a date that other currencies have, the pipeline should fail loudly, naming the date and currency, rather than produce trades without a benchmark. Pinned by `test_validate_complete_raises_on_gap` (Task 2).
2. **ECB API unreachable or returning an error.** `fetch-ecb` should fail with a clear message and leave the committed snapshot untouched, and the rest of the pipeline should keep working from the snapshot. Pinned by `test_fetch_failure_leaves_snapshot_untouched` (Task 2).
3. **Re-running `make pipeline`.** Running it twice should replace the raw tables, not double them. Pinned by `test_load_is_idempotent` (Task 7).
4. **Something already listening on 5432 or 8501 on the host.** The defaults avoid those ports and can be overridden in `.env`. Pinned by the `docker compose config` check in Task 1, Step 4.
5. **Weekends and holidays between fixes.** The intraday mid must not jump or produce NaN across a Friday-to-Monday gap or a TARGET holiday. Pinned by `test_mid_path_spans_weekend_without_nan` (Task 4).

---

## File Structure

```
.
├── README.md                         # placeholder showcase README (final version in Plan 2)
├── .gitignore  .env.example  Makefile  docker-compose.yml
├── docs/
│   ├── brief.md                      # moved from Readme.md
│   └── methodology/
│       ├── benchmark-ecb.md          # Task 2
│       ├── data-generation.md        # Tasks 3–6
│       ├── metrics.md                # Tasks 8–10
│       └── parameter-sheet.md        # Task 11 (generated)
├── pipeline/
│   ├── Dockerfile  pyproject.toml  requirements.lock
│   ├── config/generator.yaml  config/rationale.yaml
│   ├── data/ecb_snapshot/ecb_rates_<start>_<end>.csv
│   ├── src/fxengine/
│   │   ├── __init__.py  __main__.py  cli.py
│   │   ├── config.py      # GeneratorConfig (pydantic) + load_config
│   │   ├── ecb.py         # window, fetch, parse, validate, snapshot I/O
│   │   ├── clients.py     # client book
│   │   ├── market.py      # intraday mid path (Brownian bridge)
│   │   ├── trades.py      # trade generation + pricing + cover
│   │   ├── reference.py   # pairs / ticket bands / cost assumptions / parameters
│   │   ├── load.py        # write raw schema to Postgres
│   │   └── param_sheet.py # renders parameter-sheet.md
│   └── tests/ (test_<module>.py, fixtures/ecb_sample.csv)
└── dbt/
    ├── Dockerfile  dbt_project.yml  profiles.yml
    ├── models/staging/      stg_*.sql + _sources.yml + _staging.yml
    ├── models/intermediate/ int_trades_enriched.sql, int_client_month.sql + _intermediate.yml
    ├── models/marts/        fct_trades, fct_trades_brief_view, dim_clients,
    │                        mart_client_profitability, mart_currency_profitability,
    │                        mart_concentration, mart_client_monthly,
    │                        mart_spread_compression, mart_headline_kpis + _marts.yml
    └── tests/               assert_*.sql (singular tests)
```

---

### Task 1: Repository, Docker environment and skeleton

**Files:**
- Create: `.gitignore`, `.env.example`, `Makefile`, `docker-compose.yml`, `README.md` (placeholder), `pipeline/Dockerfile`, `pipeline/pyproject.toml`, `pipeline/requirements.lock`, `pipeline/src/fxengine/__init__.py`, `__main__.py`, `cli.py`, `pipeline/tests/test_smoke.py`, `dbt/Dockerfile`, `dbt/dbt_project.yml`, `dbt/profiles.yml`
- Move: `Readme.md` → `docs/brief.md`

**Interfaces:**
- Produces:
  - Compose services: `db` (postgres 16, exact tag pinned), `pipeline` (builds `pipeline/`), `dbt` (builds `dbt/`). `app` and `notebook` are added in Plan 2 under compose profiles.
  - Env vars: `FX_DB_HOST=db`, `FX_DB_NAME=fxengine`, `FX_DB_USER=fxengine`, `FX_DB_PASSWORD` (set in `.env`, never committed), `FX_DB_PORT`, `FX_APP_PORT`, `FX_NOTEBOOK_PORT`.
  - Make targets: `up`, `down`, `clean` (removes `fxengine_` volumes after a y/N prompt), `build`, `pipeline`, `dbt`, `dbt-debug`, `dbt-docs`, `test`, `test-slow`, `test-integration`, `lint`, `refresh-ecb`, `param-sheet`, `psql`.
  - `fxengine.cli.main(argv: list[str] | None = None) -> int` with argparse subcommands `fetch-ecb`, `generate`, `load`, `run-all`, `param-sheet`. They are stubs that return 0 until later tasks wire them up. `run-all` takes `--dry-run`.

- [ ] **Step 1:** `git init -b main`. `.gitignore` must include `.claude/settings.local.json`, `.env`, `__pycache__/`, `.pytest_cache/`, `dbt/target/`, `dbt/logs/` and `.ipynb_checkpoints/`. Create `docs/brief.md` with the exact content of `Readme.md`, delete `Readme.md`, and create a placeholder `README.md` (title, one-line description, "Work in progress", link to `docs/brief.md`). Commit: `git commit -m "chore: initialise repo with brief and approved design"`.
- [ ] **Step 2: Write the failing smoke test.** `pipeline/tests/test_smoke.py::test_cli_help_exits_zero`. It asserts that `main(["--help"])` raises `SystemExit` with code 0, and that `main(["run-all", "--dry-run"]) == 0`.
- [ ] **Step 3:** Write the Dockerfiles. `pipeline/`: python:3.12-slim with the exact tag pinned, a non-root user, installs `requirements.lock`, then `pip install -e .`. `dbt/`: the official dbt-postgres image, exact tag pinned. Then write `docker-compose.yml`:
  - `db` has a `pg_isready` healthcheck, and `pipeline` and `dbt` wait on `service_healthy`.
  - Bind-mount `./pipeline`, `./dbt` and `./docs`.
  - Use the named volume `fxengine_pgdata`.
- [ ] **Step 4: Verify isolation config.**
  - Run `FX_DB_PORT=6543 docker compose config | grep -c '127.0.0.1'`. Expected: ≥ 1, with the port `6543` shown.
  - Run `docker compose config | grep -c '0.0.0.0'`. Expected: `0`.
- [ ] **Step 5:** `make test`. Expected: FAIL. Implement the argparse stubs, then run `make test` again and expect PASS.
- [ ] **Step 6:** `make up && docker compose exec db pg_isready -U fxengine`. Expected: `accepting connections`. Then `make dbt-debug`, expected `All checks passed!`, then `make down`.
- [ ] **Step 7: Commit.** `git commit -m "chore: dockerised skeleton with pipeline and dbt services"`

---

### Task 2: ECB benchmark ingestion and snapshot

**Files:**
- Create: `pipeline/src/fxengine/ecb.py`, `pipeline/tests/test_ecb.py`, `pipeline/tests/fixtures/ecb_sample.csv` (a real ECB `csvdata` response trimmed to USD, JPY and TRY for 2025-03-03 → 2025-03-14), `docs/methodology/benchmark-ecb.md`, `pipeline/data/ecb_snapshot/ecb_rates_<start>_<end>.csv` (Step 7)
- Modify: `pipeline/src/fxengine/cli.py` (wire `fetch-ecb`)

**Interfaces:**
- Produces (in `fxengine.ecb`):
  - `CURRENCIES: tuple[str, ...]`: the 15 codes from Global Constraints
  - `window_for(build_date: date) -> tuple[date, date]`: the first and last day of the 24 complete months before `build_date`
  - `build_url(currencies: Sequence[str], start: date, end: date) -> str`
  - `parse_ecb_csv(text: str) -> pd.DataFrame`: columns `rate_date: date`, `currency: str`, `rate: float`, sorted by date then currency; drops empty `OBS_VALUE`
  - `validate_complete(df, currencies: Sequence[str]) -> None`: raises `EcbDataGapError` whose message names the ISO date and the currency
  - `fetch_rates(currencies, start, end, client: httpx.Client | None = None) -> pd.DataFrame`: 30 s timeout, raises `EcbFetchError` on non-200 or network errors
  - `write_snapshot(df, directory: Path, start: date, end: date) -> Path`: atomic write (temp file + rename) to `ecb_rates_{start}_{end}.csv`
  - `read_snapshot(path: Path) -> pd.DataFrame` and `latest_snapshot(directory: Path) -> Path`
  - Fetch buffer: `fetch-ecb` requests `start − 10` to `end + 10` calendar days, so the mid model always has a fix on each side of the window.

- [ ] **Step 1: Write failing tests** in `test_ecb.py`:
  - `test_window_for_mid_month`: `window_for(date(2026,11,12)) == (date(2024,11,1), date(2026,10,31))`
  - `test_window_for_first_of_month`: `window_for(date(2026,11,1)) == (date(2024,11,1), date(2026,10,31))`
  - `test_build_url`: `build_url(["USD","JPY"], date(2025,3,3), date(2025,3,14)) == "https://data-api.ecb.europa.eu/service/data/EXR/D.USD+JPY.EUR.SP00.A?startPeriod=2025-03-03&endPeriod=2025-03-14&format=csvdata"`
  - `test_parse_fixture`: 3 currencies, all rates > 0, and the JPY rate > the USD rate on every date
  - `test_validate_complete_raises_on_gap`: drop the TRY row for one date. Expect `EcbDataGapError`, with the message containing that ISO date and `"TRY"`.
  - `test_fetch_failure_leaves_snapshot_untouched`: `httpx.MockTransport` returns 503 and a snapshot file already exists. Running `main(["fetch-ecb", ...])` returns a non-zero code, and the file's bytes are unchanged.
  - `test_snapshot_roundtrip`: `read_snapshot(write_snapshot(df, tmp, s, e))` equals `df`
- [ ] **Step 2:** `make test`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement `fxengine/ecb.py`. Parse the `TIME_PERIOD`, `CURRENCY` and `OBS_VALUE` columns.
- [ ] **Step 4:** `make test`. Expected: PASS.
- [ ] **Step 5:** Wire the CLI `fetch-ecb [--build-date YYYY-MM-DD]`: window → fetch with buffer → `validate_complete` → `write_snapshot`. Make target `refresh-ecb` runs it.
- [ ] **Step 6:** Write `docs/methodology/benchmark-ecb.md`. Cover:
  - what the reference rate is: set around 14:10 CET, published around 16:00 CET, quote ccy per EUR, information only and not tradable, no fix on weekends or TARGET holidays
  - why it is still a fair independent benchmark for spot
  - the caveat: one fix per day against intraday trades
  - how the snapshot works
- [ ] **Step 7:** `make refresh-ecb`. Expected: one snapshot file of about 520 business days × 15 currencies, and no gap error.
- [ ] **Step 8: Commit.** `git commit -m "feat(ecb): ECB reference-rate client, validation and committed snapshot"`

---

### Task 3: Generator configuration and client book

**Files:**
- Create: `pipeline/config/generator.yaml`, `pipeline/src/fxengine/config.py`, `pipeline/src/fxengine/clients.py`, `pipeline/tests/test_config.py`, `pipeline/tests/test_clients.py`, `docs/methodology/data-generation.md` (client-book section)

**Interfaces:**
- Consumes: `fxengine.ecb.CURRENCIES`
- Produces:
  - `fxengine.config.GeneratorConfig` (pydantic v2, `extra="forbid"`). Fields:
    - `seed: int = 42`, `n_clients: int`, `target_trades: int`
    - `trading_hours_utc: tuple[time, time]` (default 07:00–16:00), `fix_time_utc: time` (default 13:10)
    - `pairs: dict[str, PairConfig]`. `PairConfig` has `liquidity_tier: Literal["g10_major","g10_minor","cee","em"]`, `annual_vol: float`, `price_decimals: int`, `cls_settled: bool`.
    - `segments: dict[SegmentName, SegmentConfig]`
    - `ticket_bands: list[TicketBand]`. `TicketBand` has `label: str`, `max_ticket_eur: float | None`, `markup_multiplier: float`.
    - `volume_tiers: list[VolumeTier]`. `VolumeTier` has `min_annual_volume_eur` and `discount_pct`.
    - `legacy_deal: LegacyDeal`. `LegacyDeal` has `probability` and `discount_pct`.
    - `cost: GlobalCost`. `GlobalCost` has `capital_charge_bps`, `settlement_weight_cls`, `settlement_weight_non_cls`, `hurdle_rate_bps`.
  - `SegmentName = Literal["large_corporate","mid_market","sme","asset_manager","hedge_fund","payment_institution"]`
  - `SegmentConfig` fields:
    - `client_share`, `trades_per_month_lognorm: tuple[float,float]`, `ticket_eur_lognorm: tuple[float,float]`, `pair_popularity: dict[str,float]`, `buy_probability`
    - `base_markup_bps: dict[tier,float]`, `markup_noise_sigma`, `compression_per_year`
    - `adverse_selection_bps: tuple[float,float]` (mean, sd), `ops_cost_per_ticket_eur`, `coverage_cost_eur_per_month`, `credit_weight`
  - `load_config(path: Path) -> GeneratorConfig`. It validates that:
    - segment `client_share` values sum to 1 (±1e-9)
    - every pair key is `EUR` + a code in `CURRENCIES`
    - `ticket_bands` and `volume_tiers` are sorted ascending
    - all bps values are ≥ 0, except adverse-selection means, which can be any sign
  - `fxengine.clients.generate_clients(cfg, window: tuple[date, date], rng: np.random.Generator) -> pd.DataFrame`. Columns:
    - `client_id`: `LC-001` style, prefix per segment LC/MM/SM/AM/HF/PI
    - `client_name`: `Client <client_id>`
    - `segment`; `home_country`: ISO2 from a fixed EU+GB+CH+NO list
    - `onboarded_date`: 80% before window start, 20% uniform inside it
    - `legacy_deal: bool`, `trades_per_month: float`, `ticket_mu: float`, `ticket_sigma: float`
    - `pair_weights: dict[str,float]`: Dirichlet around segment `pair_popularity`
    - `expected_annual_volume_eur: float`, `volume_tier_discount_pct: float`
- Default values in `generator.yaml` are the builder's illustrative defaults (spec §13). They must satisfy:
  - base markup ordering SME > mid_market > large_corporate > hedge_fund for every tier
  - tier ordering em > cee > g10_minor > g10_major for every segment
  - hedge_fund adverse-selection mean > every other segment's mean
  - `n_clients: 200`, `target_trades: 300000`

- [ ] **Step 1: Write failing tests.**
  - `test_config.py::test_default_config_loads`
  - `test_config.py::test_shares_must_sum_to_one`: alter one share, expect `ValidationError`
  - `test_config.py::test_unknown_pair_rejected`: add `EURXYZ`, expect `ValidationError`
  - `test_config.py::test_markup_orderings_hold`: assert the two orderings above on the default config
  - `test_clients.py::test_client_count_and_ids_unique`
  - `test_clients.py::test_segment_mix_close_to_shares`: each segment's count is within ±5 of `share × n_clients`
  - `test_clients.py::test_reproducible`: two runs with `default_rng(42)` give equal frames under `pd.testing.assert_frame_equal`
  - `test_clients.py::test_volume_tier_discount_monotonic`: sorted by `expected_annual_volume_eur`, the discount is non-decreasing
- [ ] **Step 2:** `make test`. Expected: FAIL.
- [ ] **Step 3:** Implement `config.py`, `generator.yaml` and `clients.py`. Use `expected_annual_volume_eur = trades_per_month × 12 × exp(mu + sigma²/2)`; the tier is the highest `volume_tiers` entry whose minimum is ≤ that value.
- [ ] **Step 4:** `make test`. Expected: PASS.
- [ ] **Step 5:** Write the "Client book" section of `data-generation.md`: the segments, why each behaves as it does, and how tiers are assigned.
- [ ] **Step 6: Commit.** `git commit -m "feat(generator): validated config and synthetic client book"`

---

### Task 4: Intraday market mid (Brownian bridge between ECB fixes)

**Files:**
- Create: `pipeline/src/fxengine/market.py`, `pipeline/tests/test_market.py`
- Modify: `docs/methodology/data-generation.md` (market-mid section)

**Interfaces:**
- Consumes: the `ecb.read_snapshot` frame, `GeneratorConfig.pairs`, `trading_hours_utc`, `fix_time_utc`
- Produces:
  - `trading_grid(business_days: Sequence[date], hours: tuple[time, time], fix_time: time, step_minutes: int = 1) -> pd.DatetimeIndex` (UTC). Trading minutes only, and each day's fix time is on the grid.
  - `simulate_mid_path(fixes: pd.Series, grid: pd.DatetimeIndex, annual_vol: float, rng) -> pd.Series`. `fixes` is indexed by UTC fix timestamp. The result is indexed by `grid`, equals the fix exactly at each fix timestamp, and runs a log-space Brownian bridge between consecutive fixes. Step variance is `annual_vol² / (252 × trading_minutes_per_day)`, so weekends and holidays add no variance because they have no grid points.
  - `mid_at(path: pd.Series, ts: pd.Series) -> np.ndarray`: as-of lookup (last grid point ≤ ts)

- [ ] **Step 1: Write failing tests.**
  - `test_path_hits_fixes_exactly`: atol 1e-12
  - `test_mid_path_spans_weekend_without_nan`: fixes Fri 2025-03-07 and Mon 2025-03-10. No NaN, and the maximum |log-return| between consecutive grid points is < 0.005.
  - `test_path_stays_near_benchmark`: EURUSD fixture with `annual_vol=0.08`; every grid value is within 2% of that day's fix
  - `test_grid_excludes_weekends_and_contains_fix_minutes`
  - `test_reproducible_path`
- [ ] **Step 2:** `make test`. Expected: FAIL.
- [ ] **Step 3:** Implement. Bridge per segment k over grid points 0..T: `X(t) = log f_k + (t/T)(log f_{k+1} − log f_k) + B(t) − (t/T)·B(T)`, where `B` is the cumulative sum of N(0, σ_step) draws.
- [ ] **Step 4:** `make test`. Expected: PASS.
- [ ] **Step 5:** Document the market mid: the bridge anchored to real fixes, the simplification of a fixed 13:10 UTC fix time that ignores DST, and annual vols per pair.
- [ ] **Step 6: Commit.** `git commit -m "feat(generator): intraday mid path anchored to ECB fixes"`

---

### Task 5: Trade generation, client pricing and cover execution

**Files:**
- Create: `pipeline/src/fxengine/trades.py`, `pipeline/tests/test_trades.py`, `pipeline/tests/conftest.py` (small-config fixture: 20 clients, 2 months)
- Modify: `docs/methodology/data-generation.md` (pricing and execution section)

**Interfaces:**
- Consumes: the `generate_clients` frame, `trading_grid`, `simulate_mid_path` and `mid_at`, the ECB frame, `GeneratorConfig`
- Produces: `generate_trades(cfg, clients, ecb, window, rng) -> pd.DataFrame`. This is the `raw.trades` contract:
  - `trade_id` (`T0000001`, ordered by `trade_ts`), `client_id`, `trade_ts` (tz-aware UTC), `trade_date`
  - `currency_pair`, `client_side` (`BUY`/`SELL`), `notional_base` (EUR, rounded to 1,000, minimum 1,000)
  - `client_rate`, `cover_rate`, `mid_rate` (rounded to the pair's `price_decimals`), `benchmark_rate`
  - `markup_bps_applied`: a pricing audit column, kept for transparency
- Pricing algorithm (fixes spec §5.3):
  - `markup_bps = base_markup_bps[segment][tier(pair)] × band_multiplier(ticket) × (1 − volume_tier_discount_pct) × (1 − legacy_discount_pct if legacy_deal) × exp(−compression_per_year × years_since_window_start) × LogNormal(0, markup_noise_sigma)`
  - `client_rate = mid × (1 + side_sign × markup_bps/1e4)`
  - `cover_rate = mid × (1 + side_sign × slippage_bps/1e4)`, with `slippage_bps ~ Normal(adverse_selection_bps)` per segment
  - Daily trades per client: Poisson(`trades_per_month / mean ECB business days per month`), only on ECB business days inside the window and on or after `onboarded_date`. Timestamp is uniform within trading hours. Pair is drawn from `pair_weights`; side is Bernoulli(`buy_probability`); ticket is LogNormal(`ticket_mu`, `ticket_sigma`).
  - `benchmark_rate` is that date's ECB fix for the pair.

- [ ] **Step 1: Write failing tests.**
  - `test_columns_and_dtypes_match_raw_contract`
  - `test_client_rate_on_correct_side_of_mid`: BUY gives `client_rate ≥ mid_rate`, SELL gives `≤`, with a tolerance of one price increment
  - `test_every_trade_has_same_day_benchmark`
  - `test_trade_dates_inside_window_and_on_ecb_days`
  - `test_timestamps_within_trading_hours`
  - `test_no_trades_before_onboarding`
  - `test_markup_orderings`: median `markup_bps_applied` on EURUSD is SME > mid_market > large_corporate, and for large_corporate EURTRY > EURUSD
  - `test_volume_discount_visible` (`@pytest.mark.slow`, full config): Spearman correlation of client annual volume against client median G10-major markup is < −0.3
  - `test_trade_count_near_target` (slow): count within ±20% of `target_trades`
  - `test_reproducible_trades`: identical frame hash with the same seed
- [ ] **Step 2:** `make test`. Expected: FAIL.
- [ ] **Step 3:** Implement, vectorised with numpy (no per-trade Python loop).
- [ ] **Step 4:** `make test && make test-slow`. Expected: PASS. Note the full-generation runtime in the commit message; aim for under 2 minutes.
- [ ] **Step 5:** Document pricing and execution, including why the high-volume, low-margin pattern *emerges* (tier discounts, ticket bands, legacy deals, compression, adverse selection) rather than being assigned.
- [ ] **Step 6: Commit.** `git commit -m "feat(generator): spot trades with tiered pricing and cover execution"`

---

### Task 6: Reference and cost-assumption tables

**Files:**
- Create: `pipeline/src/fxengine/reference.py`, `pipeline/tests/test_reference.py`

**Interfaces:**
- Consumes: `GeneratorConfig`
- Produces:
  - `pairs_table(cfg)` with columns `currency_pair`, `base_ccy` (`EUR`), `quote_ccy`, `liquidity_tier`, `price_decimals`, `cls_settled`, `settlement_weight`
  - `ticket_bands_table(cfg)` with columns `label`, `min_ticket_eur`, `max_ticket_eur` (null for the top band), `markup_multiplier`
  - `cost_assumptions_table(cfg)` with columns `segment`, `ops_cost_per_ticket_eur`, `coverage_cost_eur_per_month`, `credit_weight`
  - `parameters_table(cfg)` with columns `name`, `value: float`, `description`. Rows must include `capital_charge_bps`, `hurdle_rate_bps` and `seed`.

- [ ] **Step 1: Write failing tests.**
  - `test_pairs_table_has_15_rows_and_cls_flags`: `EURPLN`, `EURCZK` and `EURTRY` have `cls_settled == False` and the non-CLS weight
  - `test_ticket_bands_contiguous`: each band's `min` equals the previous band's `max`
  - `test_cost_table_one_row_per_segment`
  - `test_parameters_contains_required_names`
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(generator): reference and cost-assumption tables"`

---

### Task 7: Load raw schema and the `run-all` pipeline

**Files:**
- Create: `pipeline/src/fxengine/load.py`, `pipeline/tests/test_load.py` (`@pytest.mark.integration`, needs `db`)
- Modify: `pipeline/src/fxengine/cli.py`, `Makefile`

**Interfaces:**
- Consumes: Tasks 2–6 outputs
- Produces:
  - `engine_from_env() -> sqlalchemy.Engine`
  - `write_raw(engine, tables: Mapping[str, pd.DataFrame]) -> dict[str, int]`. In one transaction it runs `CREATE SCHEMA IF NOT EXISTS raw`, `DROP TABLE IF EXISTS raw.<name>`, then `CREATE TABLE` with explicit types (`numeric(18,8)` for rates, `numeric(20,2)` for notional, `timestamptz` for `trade_ts`), then psycopg `COPY`. It returns row counts.
  - Raw tables: `raw.ecb_rates`, `raw.clients` (excluding `pair_weights`), `raw.trades`, `raw.pairs`, `raw.ticket_bands`, `raw.cost_assumptions`, `raw.parameters`
  - CLI `run-all [--config PATH] [--snapshot PATH] [--dry-run]`: config → snapshot → clients → trades → reference tables → `write_raw`, then prints counts. `make pipeline` = `run-all`, then `dbt build`.

- [ ] **Step 1: Write failing integration tests.**
  - `test_write_raw_creates_tables_with_counts`
  - `test_load_is_idempotent`: call `write_raw` twice; `count(*)` of `raw.trades` equals the frame length both times
  - `test_numeric_precision_preserved`: a 3-dp JPY rate and a 5-dp USD rate round-trip exactly
- [ ] **Step 2:** `make test-integration`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `make test-integration` should PASS. Then run `make pipeline` and expect `raw.trades` ≈ 300,000 and `raw.clients` = 200.
- [ ] **Step 5: Commit.** `git commit -m "feat(pipeline): idempotent raw load and run-all command"`

---

### Task 8: dbt staging and enriched trades

**Files:**
- Create: `dbt/macros/generate_schema_name.sql`, `dbt/models/staging/_sources.yml`, `_staging.yml`, `stg_ecb_rates.sql`, `stg_clients.sql`, `stg_trades.sql`, `stg_pairs.sql`, `stg_ticket_bands.sql`, `stg_cost_assumptions.sql`, `stg_parameters.sql`, `dbt/models/intermediate/int_trades_enriched.sql`, `_intermediate.yml`, `dbt/tests/assert_benchmark_join_complete.sql`, `dbt/tests/assert_mid_near_benchmark.sql`
- Modify: `dbt/dbt_project.yml` (`+schema: staging` / `intermediate` / `marts` per folder), `docs/methodology/metrics.md` (create; trade-level section)
- Schema rule: `generate_schema_name` uses the custom schema name verbatim, so models land in the Postgres schemas `staging`, `intermediate` and `marts` exactly. Plan 2 reads from `marts.<table>`.

**Interfaces:**
- Consumes: `raw.*`
- Produces: `int_trades_enriched`, one row per trade, all `raw.trades` columns plus:
  - `side_sign`, `notional_eur` (= `notional_base`), `trade_month` (first of month)
  - `segment`, `liquidity_tier`, `ticket_band`
  - `gross_pnl_quote`, `gross_pnl_eur`, `gross_margin_bps`
  - `benchmark_spread_bps` = `side_sign × (client_rate − benchmark_rate) / benchmark_rate × 1e4`
  - `cover_slippage_bps` = `side_sign × (cover_rate − mid_rate) / mid_rate × 1e4` (positive = worse for the dealer)
  - `ops_cost_eur` = the segment per-ticket cost
  - `capital_charge_eur` = `notional_eur × capital_charge_bps/1e4 × credit_weight × settlement_weight`

- [ ] **Step 1: Write failing dbt unit tests** in `_intermediate.yml`:
  - **BUY:** EURUSD BUY, notional 1,000,000, client 1.1010, cover 1.1000, mid 1.0999, benchmark 1.1000. Expect `gross_pnl_quote = 1000.0000`, `gross_pnl_eur = 909.0909`, `gross_margin_bps = 9.0909`, `benchmark_spread_bps = 9.0909` and `cover_slippage_bps = 0.9092` (rounded to 4 dp in the test).
  - **SELL:** EURUSD SELL, notional 1,000,000, client 1.0990, cover 1.1000, benchmark 1.1000. Expect `gross_pnl_quote = 1000.0000` and `benchmark_spread_bps = 9.0909`.
  - **Capital:** EURTRY, `capital_charge_bps = 0.05`, `credit_weight = 1.0`, `settlement_weight = 2.5`, notional 2,000,000. Expect `capital_charge_eur = 25.00`.
  - Generic tests: `unique` and `not_null` on `trade_id`; `accepted_values` on `client_side`.
- [ ] **Step 2:** `make dbt`. Expected: FAIL.
- [ ] **Step 3:** Implement the staging models (rename and cast only) and `int_trades_enriched`.
- [ ] **Step 4:** Add singular tests (each returns offending rows, so the expected result is 0 rows):
  - `assert_benchmark_join_complete`: null `benchmark_rate`
  - `assert_mid_near_benchmark`: `abs(mid_rate/benchmark_rate − 1) > 0.02`
- [ ] **Step 5:** `make pipeline`. Expected: `dbt build` completes with every test PASS.
- [ ] **Step 6:** Write the trade-level formulas in `metrics.md` with the worked BUY and SELL examples.
- [ ] **Step 7: Commit.** `git commit -m "feat(dbt): staging and enriched trade economics with unit tests"`

---

### Task 9: Client-month economics and client / currency marts (metrics 1–7, 9, 10)

**Files:**
- Create: `dbt/models/intermediate/int_client_month.sql`, `dbt/models/marts/fct_trades.sql`, `fct_trades_brief_view.sql`, `dim_clients.sql`, `mart_client_profitability.sql`, `mart_currency_profitability.sql`, `_marts.yml`, `dbt/tests/assert_volume_reconciles.sql`, `assert_contribution_reconciles.sql`, `assert_shares_sum_to_one.sql`
- Modify: `docs/methodology/metrics.md`

**Interfaces:**
- Produces:
  - `int_client_month`: grain client × month, for every month from `greatest(onboarded month, window start)` to the window end, including zero-trade months (`generate_series`). Columns:
    - `trade_count`, `volume_eur`, `revenue_eur`, `ops_cost_eur`, `capital_charge_eur`
    - `coverage_cost_eur` (the segment monthly amount)
    - `economic_contribution_eur` = revenue − ops − capital − coverage
  - `fct_trades` = `int_trades_enriched` (materialised as a table)
  - `fct_trades_brief_view` columns, in order: `client`, `trade`, `currency_pair`, `buy_rate`, `sell_rate`, `benchmark_rate`, `volume`, `timestamp`, `pnl`. Client BUY gives buy=`cover_rate`, sell=`client_rate`; client SELL gives buy=`client_rate`, sell=`cover_rate`. `pnl` = `gross_pnl_eur`.
  - `dim_clients`: client attributes plus `first_trade_date` and `last_trade_date`
  - `mart_client_profitability`, one row per client over the full window:
    - `client_id`, `client_name`, `segment`, `trade_count`, `volume_eur`, `revenue_eur`, `gross_margin_bps`
    - `avg_benchmark_spread_bps` (volume-weighted)
    - `ops_cost_eur`, `capital_charge_eur`, `coverage_cost_eur`, `economic_contribution_eur`, `economic_margin_bps`
    - `contribution_per_trade_eur`, plus `p10_`, `median_` and `p90_contribution_per_trade_eur`. Per-trade contribution = revenue − ops − capital − (client coverage ÷ client trade count).
    - `avg_ticket_eur`, `median_ticket_eur`, `modal_ticket_band`
    - `volume_share`, `revenue_share`, `contribution_share`, `share_gap` (= volume_share − contribution_share)
    - `avg_cover_slippage_bps` (volume-weighted)
    - `peer_median_gross_margin_bps`: median `gross_margin_bps` of clients with the same `segment` *and* the same `modal_ticket_band`
  - `mart_currency_profitability`, per pair: `trade_count`, `volume_eur`, `revenue_eur`, `gross_margin_bps`, `avg_benchmark_spread_bps`, `avg_cover_slippage_bps`, `ops_cost_eur`, `capital_charge_eur`, `contribution_ex_coverage_eur`, `margin_ex_coverage_bps`. Coverage is a client-level cost; `metrics.md` explains why it's excluded here.

- [ ] **Step 1: Write failing tests** (singular tests return offending rows):
  - `assert_volume_reconciles`: |Σ client `volume_eur` − Σ `fct_trades.notional_eur`| > 0.01
  - `assert_contribution_reconciles`: |Σ client contribution − (Σ revenue − Σ ops − Σ capital − Σ `int_client_month.coverage_cost_eur`)| > 1
  - `assert_shares_sum_to_one`: any of volume, revenue or contribution share sums ≠ 1 ± 1e-9
  - A unit test on `fct_trades_brief_view` mapping one BUY and one SELL row to the legs above
- [ ] **Step 2:** `make dbt`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `make pipeline`. Expected: all PASS.
- [ ] **Step 5:** Document metrics 1–7, 9 and 10 in `metrics.md`: formula, grain and a "why it matters on a desk" line each. Explain that a negative contribution share is possible.
- [ ] **Step 6: Commit.** `git commit -m "feat(dbt): client and currency profitability marts"`

---

### Task 10: Concentration, trends, compression and headline KPIs (metrics 8, 11, 12)

**Files:**
- Create: `dbt/models/marts/mart_concentration.sql`, `mart_client_monthly.sql`, `mart_spread_compression.sql`, `mart_headline_kpis.sql`, `dbt/tests/assert_hhi_bounds.sql`
- Modify: `dbt/models/marts/_marts.yml`, `docs/methodology/metrics.md`

**Interfaces:**
- Produces:
  - `mart_concentration`: one row per `measure` ∈ {`volume`, `revenue`, `contribution_positive`}, with `top5_share`, `top10_share`, `top20_share`, `hhi` (Σ share²; the contribution measure uses positive contributors only) and `n_clients`
  - `mart_client_monthly`: `int_client_month` (keyed `client_id`, `month`, carrying `client_name` and `segment`) plus:
    - `gross_margin_bps`, `economic_margin_bps`, `contribution_rolling_3m_eur`
    - `trend_slope_eur_per_month`: `regr_slope` of contribution on month index, per client, repeated on each row
    - `trend_flag` ∈ {`improving`, `stable`, `deteriorating`}: stable when |slope| < 0.05 × the client's mean monthly |contribution|
  - `mart_spread_compression`: grain month × segment × currency_pair, with `volume_eur`, `gross_margin_bps`, and `compression_slope_bps_per_year` (`regr_slope` per segment × pair × 12)
  - `mart_headline_kpis`: one row with total volume, revenue and contribution; gross and economic margin bps; client count; trade count; `window_start`; `window_end`; `hurdle_rate_bps`

- [ ] **Step 1: Write failing tests.**
  - `assert_hhi_bounds`: rows where `hhi < 1.0/n_clients − 1e-9` or `hhi > 1`
  - `accepted_values` on `trend_flag` and `measure`
  - A unit test on `mart_client_monthly`: contributions 100, 200, 300 over 3 months give slope 100 and `trend_flag = 'improving'`
  - `not_null` on `compression_slope_bps_per_year`
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** `make pipeline`, all PASS.
- [ ] **Step 5: Sanity read.** Record this in the commit message; it is not asserted. `select segment, avg(compression_slope_bps_per_year) from mart_spread_compression group by 1` should show hedge_fund and payment_institution more negative than sme.
- [ ] **Step 6:** Document metrics 8, 11 and 12, then run `make dbt-docs` (serves on `127.0.0.1:8081`) and check that the lineage graph renders.
- [ ] **Step 7: Commit.** `git commit -m "feat(dbt): concentration, trend and compression marts"`

---

### Task 11: Parameter sheet and 🔒 calibration gate

**Files:**
- Create: `pipeline/src/fxengine/param_sheet.py`, `pipeline/config/rationale.yaml`, `pipeline/tests/test_param_sheet.py`, `docs/methodology/parameter-sheet.md` (generated)
- Modify: `pipeline/src/fxengine/cli.py`

**Interfaces:**
- Consumes: `GeneratorConfig`
- Produces: `render_parameter_sheet(cfg: GeneratorConfig, rationale: Mapping[str, str]) -> str`, Markdown that fits one printed page. Its tables cover:
  - markup bps (segment × liquidity tier), ticket-band multipliers, volume tiers, compression per year
  - adverse selection, ops cost per ticket, coverage per month
  - credit and settlement weights, capital charge bps, hurdle bps

  Columns are **Default | Your value (blank) | Why**.

- [ ] **Step 1: Write failing tests.**
  - `test_sheet_contains_every_segment_and_tier`
  - `test_sheet_values_match_config`: parse one table cell and check it equals the config value
  - `test_every_parameter_has_rationale`: a missing key raises `KeyError` naming the key
- [ ] **Step 2:** FAIL. **Step 3:** Implement and add the `param-sheet` CLI and Make target. **Step 4:** PASS.
- [ ] **Step 5:** `make param-sheet`, then commit the generated sheet: `git commit -m "feat: calibration parameter sheet"`.
- [ ] **Step 6: 🔒 GATE: stop and hand to the owner.** Send the sheet plus a three-line summary of what the defaults currently produce:
  - the number of clients in the top 20% by volume whose economic margin is below the hurdle
  - overall gross margin in bps
  - HHI on volume

  The owner edits the values. Apply them to `generator.yaml`, run `make pipeline`, and repeat until the owner approves.
- [ ] **Step 7: Freeze.** `git commit -m "chore: freeze calibrated generator parameters"` and `git tag params-v1`. **Plan 2 tasks marked 🔒 (findings, case study, screenshots, demo bundle, launch kit) must not start until this tag exists.** Plan 2 Tasks 1–8 can proceed on provisional data.
