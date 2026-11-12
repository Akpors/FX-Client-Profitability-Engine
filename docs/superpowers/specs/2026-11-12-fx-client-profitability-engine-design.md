# FX Client Profitability Engine — Design

**Date:** 2026-11-12
**Status:** Approved by owner 2026-11-12
**Source brief:** `Readme.md` (project root)

---

## 1. Intent and the why

This is a portfolio project meant to be a signature piece shown on LinkedIn. The owner works in an FX business. The project should do two things:

1. **Show how the problem was solved**, not just the answer: the reasoning, the metric definitions, the benchmark method and the path from data to a commercial decision.
2. **Position the owner as an industry expert.** A reader from an FX desk, a bank or a fintech should recognise the problem as real and the treatment of it as practitioner-grade.

The central commercial question is:

> **"Which clients look valuable based on volume but are actually low-margin?"**

The project closes by answering a decision question rather than describing data:

> **"Client A generates €X in volume but only €Y in economic contribution. Should pricing change, and by how much?"**

This is the shift the brief describes, from BI (reporting what happened) to **commercial decision intelligence** (recommending what to do and quantifying the trade-off).

## 2. The imagined business

*Builder's framing, agreed in conversation:* a **European FX dealer**, such as a bank FX sales desk or a non-bank broker. It quotes prices to a book of corporate and institutional clients across **EUR-based currency pairs**. The dealer earns a spread between the rate it gives the client and the rate at which it covers (hedges) that position in the market. **All money figures are reported in EUR.** The brief's ₦X / ₦Y becomes €X / €Y because the ECB does not publish NGN reference rates.

## 3. Goals, non-goals, anti-goals

### Goals
- A realistic, reproducible **synthetic FX trade dataset** anchored to **real ECB reference rates**.
- All **twelve metrics** from the brief, each with a written, defensible definition (§6).
- A clear, evidence-backed answer to the **volume-versus-margin question**.
- A **pricing recommendation** with quantified scenarios, including the break-even volume loss and the revenue impact.
- An **interactive dashboard** and a **written case study** that walk a reader through the reasoning.
- Everything runs in **Docker**. Nothing gets installed on the host machine apart from Docker Desktop.
- A **showcase-ready public repo** plus a **LinkedIn launch kit** (§10).

### Non-goals
- No real client data and no real trade data of any kind, including from the owner's employer.
- No live or real-time pricing engine, streaming market data or order execution.
- **No forwards, swaps or options: spot only** *(owner's decision)*. All trades are spot (T+2, or T+1 where market convention applies), so the ECB spot fix is a like-for-like benchmark. The case study names forwards as a deliberate simplification and the natural next extension.
- No machine learning for its own sake. Use statistics and transparent rules unless a model clearly earns its place.
- No production-grade auth, multi-user features or cloud infrastructure beyond what a public demo needs.

### Anti-goals (failure even if it "works")
- **A rigged-looking finding.** If the high-volume, low-margin clients are obviously hard-coded, the project loses credibility with FX professionals. The pattern must **emerge from realistic pricing behaviour** in the generator (§5.3), and the generator's assumptions must be documented openly.
- **Domain naivety.** Examples: treating the ECB rate as a tradable price, ignoring which side of the trade the client is on, mixing currencies without converting, or reporting spread in absolute terms only, without basis points. Any of these would undercut the expert positioning.
- **A dashboard with no story.** Charts without the "so what" and the recommendation make this just another BI project.
- **Polluting the host machine.** Global Python installs, host-installed Postgres or port clashes with other local services.
- **Anything resembling real employer data or branding.**

## 4. Constraints

- **Isolation:** everything runs through Docker Compose. Images use pinned versions. Volumes are named and project-scoped. Ports are bound to `127.0.0.1` on non-default numbers to avoid clashing with other local services (for example Postgres on `5433` and Streamlit on `8502`).
- **Benchmark source:** the ECB Data Portal SDMX REST API, daily euro reference rates (dataflow `EXR`). Series key pattern `D.{CCY}.EUR.SP00.A`. Endpoint: `https://data-api.ecb.europa.eu/service/data/EXR/...` (CSV or JSON output). The API is public and needs no key.
- **ECB rate semantics (must be respected):** one reference rate per currency per TARGET working day, set around 14:10 CET and published around 16:00 CET. It is quoted as **units of foreign currency per 1 EUR**. It is **for information purposes only and is not a tradable price.** There are no rates on weekends or TARGET holidays.
- **Reproducibility:** fixed random seed. ECB data is fetched once and saved as a dated snapshot in the repo, so the whole pipeline rebuilds offline with identical results. A refresh is an explicit command.
- **Public by default:** the repo is intended to be public. No secrets are needed. Provide a `.env.example` anyway.

## 5. Data design

### 5.1 Currency pairs (EUR-based, all available from the ECB)
- **G10 majors:** EUR/USD, EUR/GBP, EUR/JPY, EUR/CHF
- **Scandies:** EUR/SEK, EUR/NOK, EUR/DKK
- **Commodity bloc:** EUR/AUD, EUR/CAD
- **CEE / EM:** EUR/PLN, EUR/CZK, EUR/HUF, EUR/ZAR, EUR/TRY, EUR/MXN

The list is a builder's call and can be trimmed. The mix matters because liquidity differs: majors have tight spreads and EM pairs have wide ones, which drives realistic per-currency profitability.

### 5.2 Time window
The **most recent 24 complete calendar months** of ECB data available at build time. Trades occur only on ECB working days, so every trade has a same-day benchmark.

### 5.3 Synthetic generator: how the pattern emerges
The generator models how a dealer actually prices clients. The volume-versus-margin tension comes out of these mechanics; it is not stamped on directly.

- **Client book:** about 200 clients across segments, each with a realistic share of the book:
  - Large corporates (treasury spot conversions for payables, receivables and repatriation; big tickets; negotiated tight pricing)
  - Mid-market corporates
  - SMEs (small tickets, wide spreads, high cost-to-serve per euro)
  - Asset managers / real money
  - Hedge funds / fast money (high volume, very tight spreads, flow that may be "toxic", i.e. adverse selection that drags on cover quality)
  - Payment institutions / fintechs (very high ticket count, small to medium tickets)
- **Per-client attributes:** segment, home country, preferred pairs, trading frequency, ticket-size distribution (log-normal), negotiated spread tier, price sensitivity, and an optional "legacy deal" flag for old discounts that were never repriced.
- **Pricing mechanics:**
  - Base markup in basis points by **pair liquidity × segment × ticket size band**.
  - **Volume tier discounts.** Big clients get tighter pricing, which is the main driver of the finding.
  - **Spread compression over time** for selected clients or segments (competitive pressure, electronic platforms).
  - **Adverse selection.** Fast-money flow gives worse cover execution, so the dealer's realised margin is lower than the quoted markup.
- **Intraday mid (synthetic):** a Brownian bridge between consecutive ECB daily fixes, with volatility scaled by pair, gives a plausible market mid at each trade timestamp.
- **Scale:** on the order of 200k–500k trades over 24 months. The builder can tune this for dashboard performance.
- **Documented assumptions:** every parameter lives in one config file and is explained in `docs/methodology/data-generation.md`.

### 5.4 Trade schema
The brief lists *Client → Trade → Currency Pair → Buy Rate → Sell Rate → Benchmark Rate → Volume → Timestamp → P&L*. To be precise about who is buying and selling, it is modelled as:

| Field | Meaning |
|---|---|
| `trade_id` | Unique ID |
| `client_id` | FK to client dimension |
| `trade_ts` | Execution timestamp (UTC) |
| `trade_date` | Business date (joins to the ECB fix) |
| `currency_pair` | e.g. `EURUSD` (base EUR, quote CCY) |
| `client_side` | `BUY` or `SELL`, i.e. whether the client buys or sells the **base** currency (EUR) |
| `notional_base` | Volume in EUR |
| `client_rate` | Rate given to the client |
| `cover_rate` | Rate the dealer achieved when hedging in the market |
| `mid_rate` | Synthetic market mid at `trade_ts` |
| `benchmark_rate` | ECB reference rate for `trade_date` |
| `gross_pnl_quote` | P&L in quote currency |
| `gross_pnl_eur` | P&L converted to EUR |

**Mapping to the brief:** "Buy Rate" and "Sell Rate" are the dealer's buy and sell legs. When the client buys EUR, the dealer *sells* to the client at `client_rate` and *buys* back at `cover_rate`. The reverse applies when the client sells. A view called `fct_trades_brief_view` exposes the brief's original column names for readers who expect them.

**Sign convention:** dealer P&L is positive when the dealer earns spread:
- Client BUY: `(client_rate − cover_rate) × notional_base`
- Client SELL: `(cover_rate − client_rate) × notional_base`

The result is in quote currency and is converted to EUR at the same-day ECB rate.

### 5.5 Cost-to-serve model (for economic contribution)
Gross spread is not profit. To get **economic contribution**, the model subtracts transparent, documented costs:
- **Per-ticket operational cost** (confirmation, settlement, payments): a fixed € per trade, higher for some segments.
- **Coverage cost:** sales and relationship time allocated per client by segment.
- **Settlement-risk / capital charge:** spot-only, so there is no tenor. The charge is proportional to notional × a segment credit weight × a settlement weight. The settlement weight is higher for currencies not settled through CLS, such as PLN, CZK and TRY.
- **Adverse-selection cost:** already captured as cover slippage against mid.

These are illustrative assumptions, labelled as such. The owner's practitioner input should calibrate them (§11).

## 6. Metric definitions

Every metric has an exact definition in `docs/methodology/metrics.md` and a corresponding dbt model and tests. Summary:

| # | Metric | Definition |
|---|---|---|
| 1 | **Trading volume** | Σ `notional_base` (EUR), cut by client, pair, segment and period |
| 2 | **Gross spread** | Dealer revenue from markup: Σ `gross_pnl_eur`, also in **bps** of volume (`revenue / volume × 10,000`) |
| 3 | **Benchmark spread** | Distance of the client's rate from the ECB fix, in bps, signed so that positive means the client paid away from the benchmark: `side_sign × (client_rate − benchmark_rate) / benchmark_rate × 10,000`. An *independent* view of pricing that does not rely on the dealer's own cover. |
| 4 | **Client profitability** | Economic contribution per client: gross spread − ops cost − coverage cost − capital charge, in € and in bps of volume |
| 5 | **Profitability per currency** | Metrics 2 and 4 grouped by currency pair |
| 6 | **Profitability per trade** | Economic contribution ÷ trade count, plus the distribution (median, P10, P90), not just the mean |
| 7 | **Average ticket size** | Volume ÷ trade count, plus the median, because the mean is distorted by large tickets |
| 8 | **Client concentration** | Top-5/10/20 share of volume and of revenue; **Herfindahl-Hirschman Index** for both |
| 9 | **Revenue contribution** | Each client's share of total economic contribution; Pareto curve; **volume share − contribution share** gap |
| 10 | **Execution quality** | Dealer cover slippage against mid, `side_sign × (cover_rate − mid_rate) / mid_rate × 10,000` bps, by client and segment. This exposes adverse-selection flow. |
| 11 | **Spread compression** | Change in realised margin (bps) over time per client, pair and segment: monthly series plus trend slope |
| 12 | **Client profitability trends** | Monthly economic contribution per client; rolling 3-month; trend direction flag |

### 6.1 The headline analysis: the volume–margin map
A quadrant chart with **volume share** on one axis and **economic margin (bps)** on the other. It sorts clients into:
- **Stars:** high volume, high margin
- **Volume traps:** high volume, low margin ← *the answer to the brief's question*
- **Niche earners:** low volume, high margin
- **Tail:** low volume, low margin, often with a cost-to-serve problem

Thresholds are explicit (for example the median or a business-set hurdle rate in bps) and adjustable in the dashboard.

### 6.2 Pricing recommendation
For each "volume trap" client, the engine produces a recommendation card:
- Current volume, revenue, economic contribution, margin in bps and peer benchmark (same segment and ticket band)
- **Recommended repricing:** a target spread in bps, for example moving to the segment median or to the hurdle rate
- **Scenario table:** revenue impact under several **volume-retention assumptions** (100%, 80%, 60%…)
- **Break-even volume loss:** the share of volume the client could take elsewhere before repricing loses money
- A plain-English verdict, for example: *"Client A generates €1.2bn in volume but only €84k in economic contribution (0.7 bps). Widening by 1.5 bps still leaves us better off unless they move more than X% of their flow elsewhere. Recommend repricing."* (X comes from the calculation below. The example figures are illustrative only.)

Elasticity is **assumption-driven and explicitly shown**, not a fitted model presented as truth.

#### Break-even basis and cost behaviour *(builder's call)*
- Break-even is calculated on **economic contribution**, not gross spread, because the project's whole argument is that gross revenue misleads. Gross-basis break-even is shown next to it for comparison.
- When volume leaves, each cost behaves according to what drives it:
  - **Gross spread** scales with retained notional.
  - **Ops cost** scales with retained ticket count.
  - **Capital charge** scales with retained notional.
  - **Coverage cost** is **fixed** in the short term. The relationship still has to be covered unless the client leaves entirely.
- **Retention** applies to notional and ticket count proportionally. Average ticket size is assumed unchanged.
- Break-even retention *r* solves: `new_contribution(r) = current_contribution`, where `new_contribution(r) = r × (volume × new_margin_bps − ops − capital) − coverage`. It is solved exactly, not read off a lookup table.
- The scenario table also shows the **full-loss case**: the client leaves and the coverage cost is released. This makes "would we be better off without them?" an explicit, visible answer.

## 7. Architecture and tools

*Builder's choices, agreed by the owner in conversation.*

```
            ┌──────────────┐     ┌──────────────────┐
 ECB SDMX ─►│ ingest (Py)  │────►│                  │
  API       │ snapshot CSV │     │   PostgreSQL     │
            └──────────────┘     │  raw schema      │
            ┌──────────────┐     │                  │
 config ───►│ generator(Py)│────►│                  │
            └──────────────┘     └────────┬─────────┘
                                          │
                                 ┌────────▼─────────┐
                                 │ dbt: staging →   │
                                 │ intermediate →   │
                                 │ marts + tests    │
                                 └────────┬─────────┘
                                          │
                         ┌────────────────┼───────────────┐
                         ▼                ▼               ▼
                   Streamlit app    Jupyter (analysis   Parquet export
                   (dashboard)      notebooks)          (public demo)
```

| Layer | Tool | Why |
|---|---|---|
| Runtime / isolation | Docker + Docker Compose | Owner requirement; one-command setup |
| Ingestion and generation | Python 3.12, `requests`/`httpx`, `pandas` or `polars`, `numpy` | Standard, readable |
| Storage | PostgreSQL 16 | Industry standard; real SQL warehouse feel |
| Transformation and metrics | dbt-core + dbt-postgres | Shows analytics-engineering maturity: layered models, tests, lineage docs |
| Dashboard | Streamlit + Plotly | Fast, interactive, Python-native |
| Exploration / narrative | JupyterLab (optional service) | For the analysis notebook that backs the case study |
| Quality | pytest, dbt tests, ruff | Credibility signal |
| Automation | Makefile; GitHub Actions CI running the pipeline and tests | "It actually runs" proof for visitors |

### 7.1 Compose services (outline)
- `db`: Postgres, named volume, port `127.0.0.1:5433`
- `pipeline`: one-shot Python container: fetch or load the ECB snapshot → generate synthetic data → load raw tables
- `dbt`: one-shot: `dbt build` (run + test); also serves `dbt docs` on demand
- `app`: Streamlit, port `127.0.0.1:8502`
- `notebook`: JupyterLab, optional profile

The **Makefile** gives the visitor's entry points: `make up`, `make pipeline`, `make test`, `make app`, `make refresh-ecb`, `make down`, `make clean`.

### 7.2 Public demo (builder's call)
Postgres can't run on free dashboard hosting, so the dashboard reads through a small data-access layer that can use **either** Postgres (local Docker) **or** an exported Parquet snapshot of the mart tables. The Parquet mode lets the dashboard be hosted free (for example on Streamlit Community Cloud) as a **live link for LinkedIn**.

## 8. Repository layout (outline)

```
.
├── README.md                  # showcase README (replaces the brief)
├── docs/
│   ├── brief.md               # original brief, moved from Readme.md
│   ├── case-study.md          # the narrative: problem → method → findings → recommendation
│   ├── methodology/
│   │   ├── data-generation.md
│   │   ├── metrics.md
│   │   └── benchmark-ecb.md
│   ├── superpowers/specs/     # this design
│   └── assets/                # screenshots, diagrams, GIFs
├── docker-compose.yml
├── Makefile
├── .env.example
├── pipeline/                  # Python: ECB client, generator, loaders, tests
│   ├── config/generator.yaml
│   └── data/ecb_snapshot/     # committed snapshot
├── dbt/                       # dbt project: staging, intermediate, marts, tests
├── app/                       # Streamlit
├── notebooks/                 # analysis notebook(s)
└── .github/workflows/ci.yml
```

## 9. Dashboard (outline)

Pages, in narrative order:
1. **Overview:** the question, headline KPIs (volume, revenue, contribution, margin bps), what this project is
2. **Book composition:** volume and revenue by segment, pair and client; concentration (HHI, Pareto)
3. **Pricing & execution:** gross spread vs benchmark spread; execution quality; spread compression over time
4. **The volume–margin map:** the quadrant, adjustable thresholds, drill-down to a client
5. **Pricing recommendations:** recommendation cards and the scenario and break-even calculator
6. **Methodology:** how the data was generated, metric definitions, ECB benchmark caveats

Every page carries a short **"what this tells us"** callout, so the reasoning is on the screen and not only in the charts.

## 10. Showcase deliverables (LinkedIn)

- **Public GitHub repo** with a showcase README: problem, headline finding, architecture diagram, screenshots/GIF, one-command setup, links.
- **Live dashboard link** (Parquet demo mode).
- **Case study** (`docs/case-study.md`), also suitable as a LinkedIn article: problem → approach → findings → recommendation → limitations → what I'd do with real data.
- **LinkedIn launch kit:** a launch post, a 6–8 slide carousel (headline finding, the quadrant, a recommendation card, architecture), and optionally a 60–90 second screen-recorded walkthrough.

### 10.1 Who does the public steps *(builder's call)*
The builder prepares everything so it is ready to publish: repo contents, CI workflow, Parquet demo build, deployment config, post and carousel drafts. Anything that publishes under the owner's accounts needs the owner's go-ahead at that moment: creating and pushing the public GitHub repo, connecting Streamlit Community Cloud, posting on LinkedIn. The builder can run the GitHub steps (via `gh`) when asked. Milestone 9 therefore ends with **ready-to-publish artifacts plus a short launch checklist**. "Live" happens when the owner says go.

- The repo stays local (git, no remote) until then, so CI is written and tested locally first and switches on when the repo is pushed.
- **Licence:** MIT *(builder's call)*.
- **Byline:** the owner's name goes in the README, case study and licence. The builder leaves a clearly marked placeholder for it.

## 11. Owner's voice (to make the expertise land)

The owner works in an FX business, so the case study and README should carry **practitioner notes** in their own voice: why this question matters on a real desk, which costs are usually missed, how a repricing conversation with a client actually goes, and what the synthetic data simplifies. The builder drafts placeholders; the owner writes or edits them.

### 11.1 Calibration gate *(owner's decision)*
Once the generator works, the builder produces a **one-page parameter sheet**: spreads in bps by pair × segment × ticket band, volume-tier discounts, compression rates, ops cost per ticket, coverage cost per client, settlement and credit weights, and adverse-selection slippage. Each line shows the default value and a one-line rationale. The owner reviews and adjusts these from experience, **without using any real employer data**. The parameters are then **frozen** (tagged in git). No findings, case-study figures, screenshots or carousel content are produced until the freeze. Before the freeze, the analysis notebook and dashboard can be built against provisional data.

## 12. Milestones (indicative order)

1. **Environment and skeleton:** git repo, Compose, Makefile, CI stub, empty services start cleanly
2. **ECB benchmark ingestion:** API client, snapshot, raw load, tests
3. **Synthetic generator:** config-driven client book and trades, tests for realism and reproducibility
4. **dbt models:** staging → intermediate → marts for all 12 metrics, with tests
4a. **🔒 Calibration gate:** parameter sheet → owner review → parameters frozen (§11.1)
5. **Headline analysis:** volume–margin map, notebook, sense-check that the finding emerges naturally from the frozen parameters
6. **Pricing recommendation engine:** scenarios, break-even, recommendation cards
7. **Dashboard:** all pages, both data modes
8. **Narrative:** case study, methodology docs, showcase README, diagrams, screenshots
9. **Launch kit:** LinkedIn post, carousel, optional walkthrough video; public demo deployment

## 13. Left to the builder

- Exact pair list, client count, trade count and all generator parameter values (within the realism constraints above)
- pandas vs polars; Plotly chart styling; dashboard visual design
- dbt model naming and internal layering details
- Specific threshold defaults for the quadrant and the hurdle rate
- CI details and the hosting provider for the public demo
