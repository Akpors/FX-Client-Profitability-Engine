# Metric definitions

This page defines every number the engine reports: the exact formula, the grain it is calculated at, the dbt model that produces it and why a dealer cares about it. The definitions are the contract. The models implement them and the tests pin them, so if a figure in the dashboard disagrees with this page, the dashboard is wrong.

The page is organised by grain. It starts at the single trade, because every client, currency and portfolio figure is an aggregation of trade-level numbers, and adds sections as the models for each grain arrive. The numbers it quotes in worked examples are hand-checkable on purpose. The shipped parameters and the generated book are described in [the data-generation note](data-generation.md).

## Conventions

These hold throughout the engine and are not repeated in each definition.

- **Whose side we are on.** Every figure is from the **dealer's** point of view. The dealer is the bank's trading desk, the counterparty to every client trade, and it hedges each client trade in the market straight away (the *cover*). The dealer's P&L on a trade is the difference between the price it gave the client and the price it got in the market.
- **Pairs are EUR-based.** All fifteen pairs have EUR as the base currency and a quote currency such as USD or TRY. A rate is units of quote currency per 1 EUR, so `EURUSD 1.1010` is USD 1.1010 per EUR. `notional_base` is therefore already an amount in EUR, and `notional_eur` is the same number under the name the later models use.
- **`client_side` is the client's side of EUR.** `BUY` means the client buys EUR from the dealer, `SELL` means the client sells EUR to the dealer. The dealer is on the other side of each trade.
- **`side_sign`** is `+1` for a client BUY and `-1` for a client SELL. It turns the two directions into one formula.
- **Sign.** Positive means good for the dealer. A positive gross P&L is earned spread, a positive benchmark spread is the client paying away from the benchmark. The one exception is cover slippage, where positive is *bad* for the dealer, because it is a cost. The exception is stated wherever it matters.
- **Basis points.** `bps = value × 10,000`. A margin in bps is P&L divided by notional.
- **Precision.** Money and bps are stored as exact decimals (`numeric`) and are not rounded in the models. Figures on this page are rounded to four decimal places for reading, and the unit tests compare at four decimal places for the same reason.

## 1. Trade-level economics

**Model:** `intermediate.int_trades_enriched`, one row per trade (a table, because every later model reads all of it). It carries every column of `raw.trades` plus the derived columns below. Staging models (`staging.stg_*`) only rename and cast the raw tables. All the logic is in this one model.

| Metric (spec #) | Column | Meaning |
| --- | --- | --- |
| Gross spread (2) | `gross_pnl_quote`, `gross_pnl_eur`, `gross_margin_bps` | What the dealer earned on the trade, in quote currency and EUR, and as bps of notional |
| Benchmark spread (3) | `benchmark_spread_bps` | How far the client's rate was from the ECB fix, in bps, positive when the client paid away from it |
| Execution quality (10) | `cover_slippage_bps` | How far the dealer's hedge was from the market mid, in bps, positive when the hedge was worse for the dealer |
| Cost to serve | `ops_cost_eur`, `capital_charge_eur` | Per-ticket operations cost and settlement-risk capital charge, inputs to economic contribution |

Attributes joined on the way: `side_sign`, `notional_eur`, `trade_month` (first day of the month of `trade_date`), `segment` (from the client), `liquidity_tier` (from the pair) and `ticket_band`.

### 1.1 Dealer gross P&L

```text
gross_pnl_quote = side_sign × (client_rate − cover_rate) × notional_base
gross_pnl_eur   = gross_pnl_quote / benchmark_rate
```

On a client BUY the dealer sells EUR to the client at `client_rate` and buys it back in the market at `cover_rate`. It earns the spread when `client_rate` is above `cover_rate`. On a client SELL the dealer buys EUR from the client at `client_rate` and sells it in the market at `cover_rate`, and earns when `cover_rate` is above `client_rate`. `side_sign` turns both cases into `(client_rate − cover_rate)` with the right sign. The result is in the **quote currency**, because the rates are quote per EUR and the notional is in EUR.

**Why the conversion to EUR uses the same-day ECB fix.** P&L earned in USD, JPY and TRY cannot be added together until it is in one currency, and EUR is the base of every pair. The ECB reference rate for the trade date is the rate used because it is the same rate the benchmark spread is measured against, it is public and reproducible, and it is one consistent rule across all fifteen pairs. It is a mid, so the conversion carries no spread of its own. Using the fix rather than the rate at the minute of the trade is an approximation, but a small one: the P&L being converted is about one basis point of notional, so even a 1% difference between the fix and the intraday rate moves a EUR 909 P&L by about EUR 9. The dated fix is also the only EUR rate the engine has that is real data. See [the benchmark note](benchmark-ecb.md).

### 1.2 Gross margin (bps)

```text
gross_margin_bps = gross_pnl_eur / notional_eur × 10,000
```

Revenue per euro traded, in bps. It is the dealer's realised margin against its own hedge, before any cost. It is the figure in the spec's definition of gross spread (revenue ÷ volume × 10,000), at trade grain. The aggregated version is the volume-weighted figure, `Σ gross_pnl_eur / Σ notional_eur × 10,000`, never an average of trade-level bps, because a EUR 10m ticket and a EUR 10k ticket carry different weight.

### 1.3 Benchmark spread (bps)

```text
benchmark_spread_bps = side_sign × (client_rate − benchmark_rate) / benchmark_rate × 10,000
```

The client's price measured against the ECB fix, not against the dealer's hedge. A client BUY above the fix, or a client SELL below it, is positive: the client has paid away from the benchmark. It is an independent view of pricing, because it does not depend on how well the dealer covered. It also includes the intraday market move between the trade and the fix (set around 14:10 CET), which averages out over many trades but not on one. The caveat is set out in [the benchmark note](benchmark-ecb.md).

### 1.4 Cover slippage (bps)

```text
cover_slippage_bps = side_sign × (cover_rate − mid_rate) / mid_rate × 10,000
```

How far the dealer's hedge landed from the market mid at the time of the trade. **Positive means worse for the dealer**:

- on a client BUY the dealer has to buy EUR back in the market, so a `cover_rate` above `mid_rate` means it paid more than mid;
- on a client SELL the dealer has to sell EUR in the market, so a `cover_rate` below `mid_rate` means it received less than mid. The `side_sign` makes this positive too.

Zero is covering at mid. Negative means the dealer covered better than mid. Sustained positive slippage on a client's flow is the signature of adverse selection: the market moves against the dealer between the client's trade and the hedge. It is the cost that a tight quoted markup hides, and the reason a client can be tightly priced and still unprofitable. The spec's execution-quality metric is this number aggregated by client and segment.

### 1.5 Costs

```text
ops_cost_eur       = ops_cost_per_ticket_eur of the client's segment
capital_charge_eur = notional_eur × capital_charge_bps / 10,000 × credit_weight × settlement_weight
```

- `ops_cost_eur` is a flat amount per ticket for confirmation, settlement and payments. It varies by segment (it is cheapest per ticket for the automated segments).
- `capital_charge_eur` prices the settlement risk of a spot trade, which has no tenor, so the charge scales with notional. `capital_charge_bps` is the charge in bps at weights of 1.0, `credit_weight` is the client's segment credit weight, and `settlement_weight` is the pair's multiplier: 1.0 for pairs settled through CLS and higher for pairs settled bilaterally outside it (PLN, CZK and TRY). The settlement weight is read per pair from the pair reference, not from the parameter list.
- Coverage cost (sales time) is a per-client, per-month amount, not a per-trade one, and appears when trades are rolled up to client-months.

All of these are illustrative assumptions and are labelled as such. Gross spread is not profit: these costs are what turn it into economic contribution.

### 1.6 Ticket bands

`ticket_band` is the `label` of the band that holds `notional_eur`. Bands are **[min, max)**: a notional equal to a band's minimum belongs to that band, and a notional equal to its maximum belongs to the next one. EUR 25,000 is in `25k-100k`, not `Under 25k`. The top band has no maximum.

### 1.7 Worked examples

Both trades are EURUSD, EUR 1,000,000, with a benchmark (ECB fix) of 1.1000 and a cover rate of 1.1000. These are the rows of the unit tests, so the arithmetic below is re-checked on every build.

**Client BUY.** The client buys EUR at 1.1010. The dealer sells EUR to the client and buys it back in the market at 1.1000. The market mid is 1.0999, so the hedge was a pip above mid.

| Step | Working | Result |
| --- | --- | --- |
| `side_sign` | client BUYs EUR | `+1` |
| `gross_pnl_quote` | `+1 × (1.1010 − 1.1000) × 1,000,000 = 0.0010 × 1,000,000` | USD 1,000.00 |
| `gross_pnl_eur` | `1,000.00 / 1.1000` | EUR 909.0909 |
| `gross_margin_bps` | `909.0909 / 1,000,000 × 10,000` | 9.0909 bps |
| `benchmark_spread_bps` | `+1 × (1.1010 − 1.1000) / 1.1000 × 10,000 = 0.0010 / 1.1000 × 10,000` | 9.0909 bps |
| `cover_slippage_bps` | `+1 × (1.1000 − 1.0999) / 1.0999 × 10,000 = 0.0001 / 1.0999 × 10,000` | 0.9092 bps |

The dealer kept 9.09 bps. It covered a pip above mid, which is 0.91 bps worse than mid, so positive slippage, a small cost. Here the cover rate equals the benchmark, so the gross margin and the benchmark spread coincide. On real data they differ by how far the cover was from the fix.

**Client SELL.** The client sells EUR at 1.0990. The dealer buys EUR from the client and sells it in the market at 1.1000. The mid is 1.1001, so the hedge was a pip below mid. The brief gives no mid for this trade, so 1.1001 is chosen to show the sign of slippage on the other side.

| Step | Working | Result |
| --- | --- | --- |
| `side_sign` | client SELLs EUR | `−1` |
| `gross_pnl_quote` | `−1 × (1.0990 − 1.1000) × 1,000,000 = −1 × (−0.0010) × 1,000,000` | USD 1,000.00 |
| `gross_pnl_eur` | `1,000.00 / 1.1000` | EUR 909.0909 |
| `gross_margin_bps` | `909.0909 / 1,000,000 × 10,000` | 9.0909 bps |
| `benchmark_spread_bps` | `−1 × (1.0990 − 1.1000) / 1.1000 × 10,000 = −1 × (−0.0010) / 1.1000 × 10,000` | 9.0909 bps |
| `cover_slippage_bps` | `−1 × (1.1000 − 1.1001) / 1.1001 × 10,000 = 0.0001 / 1.1001 × 10,000` | 0.9090 bps |

The two directions give the same P&L from mirrored prices. Selling EUR at 1.0990 is 9.09 bps below the fix, so the client paid 9.09 bps away from the benchmark and the sign flip makes that positive. Selling at 1.1000 when the mid is 1.1001 leaves the dealer a pip short of mid, so the slippage is positive again.

**Capital charge.** EURTRY, EUR 2,000,000, with illustrative parameters `capital_charge_bps = 0.05`, `credit_weight = 1.0` and a non-CLS `settlement_weight = 2.5`:

```text
capital_charge_eur = 2,000,000 × 0.05 / 10,000 × 1.0 × 2.5 = 10 × 2.5 = EUR 25.00
```

The shipped `capital_charge_bps` is set in `pipeline/config/generator.yaml` and appears in `raw.parameters`.

### 1.8 What is checked

| Check | Where | What it pins |
| --- | --- | --- |
| Unit tests | `models/intermediate/_intermediate.yml` | The BUY, SELL and capital-charge arithmetic above, and the ticket-band boundaries, on fixed inputs |
| `unique`, `not_null`, `accepted_values` | `_intermediate.yml`, `_staging.yml` | One row per `trade_id`, `client_side` is BUY or SELL, every join found its match (segment, liquidity tier, ticket band, cost) |
| `assert_benchmark_join_complete` | `tests/` | Every trade has a benchmark rate, and it is the ECB fix for the trade's own date and quote currency |
| `assert_mid_near_benchmark` | `tests/` | The synthetic mid stays within 2% of the same-day fix, plus that day's fix-to-fix move |

`assert_mid_near_benchmark` allows the mid to be `2% + |benchmark_rate / prev_fix − 1|` away from the fix, where `prev_fix` is the previous ECB fix for the same currency. The intraday mid is a bridge from the previous fix to today's, so a trade early in the day sits partway between the two. On a quiet day that adds almost nothing and the check is about 2%. On a shock day, when the ECB fix itself moved 2.7–4.2% (TRY on 19 March 2025, ZAR and TRY on 3 April 2025, AUD on 4 April 2025, MXN on 9 April 2025), a flat 2% would flag 15 genuine trades at seed 42; the allowance grows with the day's own move, so it does not. The test is a hard failure: any trade outside the allowance means the mid path is broken (wrong currency, wrong day or a scaling error).

## 2. The window and client-month economics

### 2.1 The analysis window

**Model:** `intermediate.int_window`, one row.

```text
window_start = first day of the month of the first trade
window_end   = last day of the month of the last trade
```

The book covers the 24 complete calendar months before the build date, and the generator books trades on every ECB fixing day in them, so reading the window off the trades recovers it exactly (1 November 2024 to 31 October 2026 for the shipped build). `raw` carries no separate window table, and this model is the only place the window is defined. Every client-month roll-up and the headline KPIs read it from here, so "the window" means the same thing on every page.

### 2.2 Client-months

**Model:** `intermediate.int_client_month`, one row per client per calendar month on the book (a table).

A client's months run from `greatest(onboarding month, window_start)` to the `window_end` month. A client onboarded before the window gets all 24 months, one onboarded in month 10 gets 15, and one onboarded after the window end gets none. **Months with no trades are kept**, with zero counts and amounts.

| Column | Formula |
| --- | --- |
| `trade_count` | number of the client's trades in the month |
| `volume_eur` | Σ `notional_eur` |
| `revenue_eur` | Σ `gross_pnl_eur` |
| `ops_cost_eur` | Σ per-ticket `ops_cost_eur` |
| `capital_charge_eur` | Σ `capital_charge_eur` |
| `coverage_cost_eur` | the segment's `coverage_cost_eur_per_month`, in full, for every month on the book |
| `economic_contribution_eur` | `revenue_eur − ops_cost_eur − capital_charge_eur − coverage_cost_eur` |

**Why zero-trade months carry the full coverage cost.** Coverage is the sales and relationship time behind a client: the salesperson's calls, the credit line, onboarding refreshes, the quarterly review. The desk pays for it whether or not the client trades that month, so a quiet month is a loss-making month, not a missing one. Dropping zero-trade months would flatter exactly the clients a desk most needs to see: the ones that take coverage and trade rarely.

**Why it matters on a desk.** The month is the grain at which a P&L review happens and at which coverage is costed. It is also the grain for the trend metrics (spread compression and client trends), which read this model.

## 3. Client profitability

**Model:** `marts.mart_client_profitability`, one row per client in the book over the full window (metrics 1–4, 6, 7, 9 and 10).

Amounts (volume, revenue, the three costs, contribution) are sums of `int_client_month`, so they agree with the monthly view to the cent. Figures that need the individual trades (volume-weighted spreads, the ticket and per-trade distributions, the modal band) come from `marts.fct_trades`. All bps are volume-weighted: a ratio of sums, never an average of trade-level bps.

### 3.1 Trading volume (metric 1)

```text
volume_eur = Σ notional_eur        trade_count = number of trades
```

**Grain:** client × window (and client × month in `int_client_month`, pair in section 4). **On the desk:** volume is what sales are paid on and what league tables rank, which is exactly why it cannot be the measure of a client's value on its own. It is the denominator of every margin below.

### 3.2 Gross spread (metric 2)

```text
revenue_eur      = Σ gross_pnl_eur
gross_margin_bps = revenue_eur / volume_eur × 10,000
```

**Grain:** client × window. **On the desk:** this is the markup actually realised against the dealer's own hedges, after cover slippage, before any cost. It is the number the pricing grid sets out to earn, and the gap between the grid and this figure is where pricing leaks.

### 3.3 Benchmark spread (metric 3)

```text
avg_benchmark_spread_bps = Σ (benchmark_spread_bps × notional_eur) / Σ notional_eur
```

**Grain:** client × window. **On the desk:** the client's all-in price against the ECB fix, the number a client's treasury or a TCA provider would compute. It is independent of the dealer's hedging, so it is what the client sees; if it is wide while the gross margin is thin, the dealer is losing the difference in its cover.

### 3.4 Client profitability (metric 4)

```text
economic_contribution_eur = revenue_eur − ops_cost_eur − capital_charge_eur − coverage_cost_eur
economic_margin_bps       = economic_contribution_eur / volume_eur × 10,000
```

**Grain:** client × window. **On the desk:** gross spread is not profit. This is what the client leaves behind once its tickets are processed, its settlement risk is capitalised and its coverage is paid for. A client can show a healthy gross margin and a negative economic margin (many small tickets, heavy coverage), or a thin gross margin on enormous volume that still does not cover its costs.

### 3.5 Profitability per trade (metric 6)

```text
contribution_per_trade_eur = economic_contribution_eur / trade_count

per-trade contribution = gross_pnl_eur − ops_cost_eur − capital_charge_eur
                         − (client coverage_cost_eur over the window / client trade_count)

p10_ / median_ / p90_contribution_per_trade_eur = percentile_cont(0.1 / 0.5 / 0.9) of per-trade contribution
```

Each trade carries its own revenue, ops cost and capital charge, plus an equal slice of the client's coverage. The slices add back to the client's coverage, so the mean of the per-trade figures is exactly `contribution_per_trade_eur`. The percentiles interpolate (`percentile_cont`).

**Grain:** client × window. **On the desk:** the mean hides the shape. A client whose P10 trade loses money and whose P90 trade earns well is a different conversation (fix the small tickets: a minimum fee, a platform route) from one whose every trade is thin (fix the price).

### 3.6 Average ticket size (metric 7)

```text
avg_ticket_eur    = volume_eur / trade_count
median_ticket_eur = percentile_cont(0.5) of notional_eur
modal_ticket_band = the ticket band holding most of the client's trades
```

Ties for the modal band are broken by the band's volume, then by the larger band (higher `min_ticket_eur`), so the result is deterministic.

**Grain:** client × window. **On the desk:** ticket size drives cost (ops cost is per ticket) and pricing (the grid is banded by ticket). The mean is pulled up by a few large tickets; the median is the typical trade. The modal band is the band the client is priced in most of the time, and defines its peer group (section 3.9).

### 3.7 Revenue contribution and the share gap (metric 9)

```text
volume_share       = volume_eur / Σ volume_eur (all clients)
revenue_share      = revenue_eur / Σ revenue_eur
contribution_share = economic_contribution_eur / Σ economic_contribution_eur
share_gap          = volume_share − contribution_share
```

Each set of shares sums to 1 across the book (tested to 1e-9). Sorted by `contribution_share`, the cumulative shares give the Pareto curve.

**A contribution share can be negative.** A client whose costs exceed its revenue has negative contribution, so its share of a positive book total is negative: it destroys part of what the others earn. The positive shares then sum to more than 1, and a single client's share can exceed 1 in a small book. That is the point of the metric, not a defect: "our top client is −2% of contribution" is a sentence a desk head needs to hear.

**Grain:** client × window. **On the desk:** `share_gap` is the volume-trap detector. A large positive gap is a client that brings far more volume than it contributes; a negative gap is a small client punching above its weight.

### 3.8 Execution quality (metric 10)

```text
avg_cover_slippage_bps = Σ (cover_slippage_bps × notional_eur) / Σ notional_eur
```

Positive is a cost to the dealer (section 1.4). **Grain:** client × window (and pair, section 4). **On the desk:** persistent positive slippage on one client's flow means the market moves against the dealer straight after that client trades: informed or toxic flow. It is the cost a tight quoted markup hides, and the first number to check before agreeing to tighten a fast-money client further.

### 3.9 Peer benchmark

```text
peer_median_gross_margin_bps = percentile_cont(0.5) of gross_margin_bps
                               over clients with the same segment and modal_ticket_band (the client included)
peer_group_size              = number of clients in that group
```

**On the desk:** the recommendation card asks "what do comparable clients pay?". Comparable means the same segment and the same ticket band, because both drive the pricing grid. The median of client-level margins (each client one vote) is used rather than a volume-weighted figure, so one large client does not set its own benchmark. `peer_group_size` is the number of clients in the group. Where it is 1, the benchmark is the client's own margin and carries no information, so check it before quoting the benchmark to anyone.

### 3.10 Clients with no trades

Every client in the book has a row. A client with no trades in the window has zero counts, volume and revenue, NULL margins, NULL per-trade and per-ticket figures, no modal band and no peer benchmark: each ratio divides by `nullif(…, 0)`, so it is NULL rather than an error. Its coverage cost still counts (section 2.2), so its contribution is negative unless it was onboarded after the window end, in which case it has no months on the book and its contribution is zero.

### 3.11 Worked example

Three mid-market clients; these are the rows of the unit test `mart_client_profitability_core_metrics`.

| | Client A | Client B | Client C |
| --- | --- | --- | --- |
| Trades | EUR 200k (band 100k-500k), EUR 50k (band 25k-100k) | EUR 100k (band 100k-500k) | none (onboarded after the window) |
| Revenue / ops / capital / coverage | 500 / 20 / 2 / 100 | 100 / 10 / 0.5 / 100 | 0 / 0 / 0 / 0 |
| Economic contribution | 378 | −10.5 | 0 |
| Gross / economic margin | 20 / 15.12 bps | 10 / −1.05 bps | NULL / NULL |
| Per-trade contribution | 300 − 10 − 1 − 50 = 239 and 200 − 10 − 1 − 50 = 139: mean and median 189, P10 149, P90 229 | −10.5 | NULL |
| Modal band | 1–1 tie, larger volume wins: 100k-500k | 100k-500k | NULL |
| Peer median gross margin (group size) | median(20, 10) = 15 bps (2) | 15 bps (2) | NULL |
| Volume / contribution share | 0.7143 / 1.0286 | 0.2857 / −0.0286 | 0 / 0 |
| Share gap | −0.3143 | +0.3143 | 0 |

Client B is a small volume trap: 29% of the volume, a negative share of contribution.

### 3.12 What is checked

| Check | Where | What it pins |
| --- | --- | --- |
| Unit tests | `_intermediate.yml`, `_marts.yml` | Client-months start at onboarding and keep zero-trade months; the client metrics above |
| `assert_volume_reconciles` | `tests/` | Client mart, client-months and currency mart volumes each equal Σ `fct_trades.notional_eur` (to EUR 0.01) |
| `assert_contribution_reconciles` | `tests/` | Σ client contribution = Σ trade revenue − Σ ops − Σ capital − Σ client-month coverage (to EUR 1) |
| `assert_shares_sum_to_one` | `tests/` | Volume, revenue and contribution shares each sum to 1 (to 1e-9) |
| `unique`, `not_null` | `_marts.yml`, `_intermediate.yml` | One row per client, per client-month, per pair, per trade; amounts and shares are never NULL |

## 4. Profitability per currency (metric 5)

**Model:** `marts.mart_currency_profitability`, one row per currency pair in the pair reference, over the full window.

```text
volume_eur, revenue_eur, trade_count       as for clients, grouped by currency_pair
gross_margin_bps                           = revenue_eur / volume_eur × 10,000
avg_benchmark_spread_bps, avg_cover_slippage_bps   volume-weighted, as for clients
contribution_ex_coverage_eur = revenue_eur − ops_cost_eur − capital_charge_eur
margin_ex_coverage_bps       = contribution_ex_coverage_eur / volume_eur × 10,000
```

**Why coverage is excluded here.** Coverage pays for the client relationship, not for a currency. A treasurer who trades EURUSD and EURPLN costs the same sales time as one who trades only EURUSD, and the cost does not go away if one pair stops trading. Splitting it across pairs (by volume, by tickets) would be an allocation choice, and whichever key is used, the answer to "is EURPLN profitable?" would then move with that choice rather than with anything about EURPLN. Ops cost and capital charge are per trade, so they belong to the trade's pair. The client-level contribution in section 3 is the fully costed figure; this is the pair's contribution towards coverage and profit.

**Grain:** currency pair × window. **On the desk:** it shows where the spread is earned. The majors carry the volume at fractions of a bp; the emerging and CEE pairs earn several bps on far less. It also shows where execution is hardest (cover slippage by pair) and where non-CLS settlement makes the capital charge bite.

## 5. Trade tables in the marts schema

- **`marts.fct_trades`** is `int_trades_enriched` as a table in the marts schema, so the dashboard and notebooks read one schema. Columns and formulas as in section 1.
- **`marts.fct_trades_brief_view`** (a view) is the trade table in the original brief's layout, in this order: `client`, `trade`, `currency_pair`, `buy_rate`, `sell_rate`, `benchmark_rate`, `volume`, `timestamp`, `pnl`.
  - `client` is the client name (for example `Client LC-014`), which reads better in the brief's table; `client_id` is on `fct_trades`.
  - `buy_rate` and `sell_rate` are the dealer's two legs in EUR. On a client BUY the dealer buys EUR in the market and sells it to the client: `buy_rate = cover_rate`, `sell_rate = client_rate`. On a client SELL the dealer buys EUR from the client and sells it in the market: `buy_rate = client_rate`, `sell_rate = cover_rate`. Either way `(sell_rate − buy_rate) × volume` is the dealer's quote-currency gross P&L.
  - `volume` is `notional_eur`, `timestamp` is `trade_ts` and `pnl` is `gross_pnl_eur`. The unit test `fct_trades_brief_view_maps_legs_by_side` checks the leg mapping on the two trades of section 1.7.

## 6. Client concentration (metric 8)

**Model:** `marts.mart_concentration`, one row per `measure`, over the full window.

```text
share      = client's value / Σ value over the measure's own population
topN_share = sum of the N largest shares                (N = 5, 10, 20; the whole population when it has N or fewer)
hhi        = Σ share²                                   (0–1 scale)
n_clients  = size of the measure's population
```

### 6.1 The three measures and their populations

| `measure` | Value | Population |
| --- | --- | --- |
| `volume` | `volume_eur` | every client with at least one trade |
| `revenue` | `revenue_eur` | every client with **positive** revenue |
| `contribution_positive` | `economic_contribution_eur` | every client with **positive** economic contribution |

Shares are computed **within the measure's own population**, so each measure's shares add to 1. `n_clients` is therefore the size of that population and can differ by measure: clients that lost money, or earned nothing, are outside the revenue and contribution populations, and clients that never traded are outside all three.

**Why revenue and contribution use positive values only.** A concentration index describes how a positive total is split between holders. A client with negative revenue (a gross P&L below zero, which a bad run of covers can produce) would have a negative share. Then the shares are no longer between 0 and 1: another client's share can pass 100%, and HHI can pass 1 (shares of 1.5 and −0.5 give 2.5). Restricting to positive values keeps every share between 0 and 1 and the HHI bounds below true for any book. The cost is that the revenue measure describes the clients that earned revenue; `n_clients` shows how many that is, and a `revenue` population smaller than the `volume` one is itself the finding that some clients lost money on gross P&L. Volume cannot be negative, so it takes every client that traded. The `contribution_positive` name says the same thing: it is the concentration of the contribution earned by clients that earn one, not of the net total (which has negative contributors in it, section 3.7).

**Ties.** Clients with equal values are ranked by `client_id`, so the top-N set is the same on every run.

**A measure with an empty population has no row** (for example `contribution_positive` in a book where no client is profitable). The HHI bounds test then has nothing to divide by.

### 6.2 HHI scale

`hhi` is on a **0–1 scale**: the sum of squared shares as fractions. Antitrust practice (the US Department of Justice's merger guidelines, for example) multiplies by 10,000 to get a 0–10,000 index, so an HHI of `0.05` here is `500` there. This page and the model use 0–1 throughout so that the bounds are simple, and any display that wants the antitrust scale multiplies by 10,000.

- **Floor:** `1 / n_clients`, reached when every client in the population has an equal share.
- **Ceiling:** `1`, reached when one client holds everything.
- **Reading:** `1 / hhi` is the *effective number of equal-sized clients*. An HHI of 0.05 behaves like a book of 20 equal clients, however many names are actually on it. Compare `1 / hhi` with `n_clients` to see how far the real book is from an even split.

### 6.3 On the desk

Compare the three measures with each other, because the gap is the story. Volume concentrated in a few names that hold a much smaller share of revenue, and a smaller share still of contribution, is the volume-trap picture at book level: the desk's biggest flows are not where it makes its money. Top-5 and top-20 are the dependency numbers a head of desk quotes ("five clients are X% of our contribution"), and the HHI is the single number that tracks the same thing without choosing a cut-off.

### 6.4 What is checked

| Check | Where | What it pins |
| --- | --- | --- |
| `assert_hhi_bounds` | `tests/` | `1 / n_clients − 1e-9 ≤ hhi ≤ 1` for every measure |
| `accepted_values`, `unique`, `not_null` | `_marts.yml` | `measure` is one of the three and appears once; no NULL shares or HHI |
| Unit test `mart_concentration_populations_and_hhi` | `_marts.yml` | The populations and the arithmetic of section 6.5 |

### 6.5 Worked example

Seven clients; these are the rows of the unit test.

| Client | A | B | C | D | E | F | G |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Trades | 10 | 5 | 5 | 5 | 5 | 5 | 0 |
| Volume | 400 | 200 | 150 | 100 | 100 | 50 | 0 |
| Revenue | 50 | 30 | 20 | 10 | 0 | −10 | 0 |
| Contribution | 20 | 10 | −5 | 5 | −2 | −30 | −15 |

| Measure | Population | Shares | `top5_share` | `hhi` |
| --- | --- | --- | --- | --- |
| `volume` | A–F (G did not trade), 6 clients | 0.40, 0.20, 0.15, 0.10, 0.10, 0.05 | 0.95 | 0.16 + 0.04 + 0.0225 + 0.01 + 0.01 + 0.0025 = 0.245 |
| `revenue` | A–D (E earned 0, F lost money), 4 clients | 50, 30, 20, 10 of 110 | 1 (4 clients, so top 5 is everyone) | (2,500 + 900 + 400 + 100) / 110² = 0.3223 |
| `contribution_positive` | A, B, D, 3 clients | 20, 10, 5 of 35 | 1 | (400 + 100 + 25) / 35² = 0.4286 |

## 7. Spread compression (metric 11)

**Model:** `marts.mart_spread_compression`, grain **month × segment × currency pair**, for months in which that segment traded that pair.

```text
gross_margin_bps               = Σ gross_pnl_eur / Σ notional_eur × 10,000        per month × segment × pair
compression_slope_bps_per_year = regr_slope(gross_margin_bps, month_index) × 12    per segment × pair
```

`month_index` counts whole months since `window_start` (from `int_window`, section 2.1), 0 for the first month of the window. The slope is the ordinary least-squares slope of the monthly margin on the month index: bps per month, times 12 for bps per year. **Negative means the margin is compressing.** Each row carries its segment × pair's slope, repeated, and `n_months`, the number of months the regression used.

### 7.1 Why the monthly margin is a ratio of sums

Each monthly cell is the volume-weighted margin, `Σ P&L / Σ notional`, never the average of trade-level bps (section 1.2). One EUR 10m ticket and one EUR 10k ticket in the same month carry their volumes in the cell. Months with no trades have no row: the margin of nothing is undefined, and a zero would drag the slope. The month index keeps its calendar spacing, so a gap in the trading shows as a gap in the index (0, 1, 3, 4 …), not as adjacent months.

### 7.2 Why the regression is not volume-weighted

The slope is a plain (unweighted) least-squares fit across the monthly cells. Volume weighting is already done inside each cell, where it matters (the ratio of sums). Across months, each month is one observation of "what margin did this segment realise on this pair this month", and the question the slope answers is whether that margin is trending, not whether the euros are. A weighted fit would let the two or three biggest months decide the trend for a segment whose flow is lumpy, and the lumpiest flow (large tickets, few clients) is exactly where one trade swings a month. The price of the unweighted choice is that a thin month counts as much as a thick one. The slope of a pair with few trades a month is therefore noisy, and the average of slopes across the pairs of a segment is pulled around by its thinnest pairs. **Read a segment's compression from the median slope or from the volume-weighted average of its pair slopes, not from the plain mean of the pair slopes.**

The measure is also not like-for-like. It is the realised margin on whatever traded that month, so a change in the ticket-size or client mix inside a segment × pair moves it as well as a change in the price any client is given. It is the margin the desk actually earned, which is what a desk head asks about, and not an estimate of repricing.

### 7.3 Minimum sample

Only segment × pair combinations with trades in **at least 6 distinct months** are kept. A regression on fewer points is a line through noise, and the rule guarantees the slope is never NULL (a single month has no slope at all). The threshold is `min_months` at the top of `mart_spread_compression.sql`, and `n_months` on every row shows the sample size.

At the shipped configuration (seed 42) the rule **drops 0 of the 90 combinations** that have trades: every segment trades every pair in 23 or 24 of the 24 window months, so all 90 are kept (2,158 rows). The rule matters for a smaller book, a shorter window or a pair that a segment barely trades. If the book is regenerated with different parameters, the count changes: compare `count(distinct segment, currency_pair)` in the mart with the same count over `int_trades_enriched`.

### 7.4 On the desk

A negative slope means the same flow is paying the desk less than it used to, which is competitive pressure in numbers. Compression is the early warning that sits ahead of a client-level loss: a segment can still be profitable at the book level while its margin on a pair has fallen every month for a year. Because the grain is segment × pair, it also says where: whether it is in the liquid majors, where it is expected, or in the pairs that carry the margin.

### 7.5 What is checked

| Check | Where | What it pins |
| --- | --- | --- |
| `not_null` on `compression_slope_bps_per_year` | `_marts.yml` | The slope exists on every row, so the minimum-sample rule is working |
| `unique` on month × segment × pair | `_marts.yml` | One row per cell |
| Unit test `mart_spread_compression_slope_and_minimum_months` | `_marts.yml` | Section 7.6 |

### 7.6 Worked example

Window starting 1 January 2025. Segment `sme`, pair EURUSD, trades in January, February and April to July (March has none):

| Month | `month_index` | Trades | `gross_margin_bps` |
| --- | --- | --- | --- |
| Jan | 0 | EUR 3m earning 2,700 and EUR 1m earning 1,300 | 4,000 / 4,000,000 × 10,000 = **10** (the average of the two trades' 9 and 13 bps would be 11) |
| Feb | 1 | EUR 1m earning 900 | 9 |
| Apr | 3 | EUR 1m earning 700 | 7 |
| May | 4 | EUR 1m earning 600 | 6 |
| Jun | 5 | EUR 1m earning 500 | 5 |
| Jul | 6 | EUR 1m earning 400 | 4 |

The margin is 10 minus the month index every month, so the slope is exactly −1 bp a month, and `compression_slope_bps_per_year` is **−12**. Indexing the six rows 0 to 5 instead of by calendar month would give a different slope, which is why the index comes from the window. A `hedge_fund` EURUSD series at a flat 3 bps has slope 0 and is regressed separately from the `sme` one. An `sme` EURGBP series with trades in only five months has no rows at all.

## 8. Client profitability trends (metric 12)

**Model:** `marts.mart_client_monthly`, one row per client per month on the book (`int_client_month`, so zero-trade months are included), with the client's name and these additions:

| Column | Definition |
| --- | --- |
| `month_index` | Whole months since `window_start` (from `int_window`). A client onboarded mid-window starts at its onboarding month, not at 0 |
| `gross_margin_bps` | `revenue_eur / nullif(volume_eur, 0) × 10,000`, NULL for a month with no volume |
| `economic_margin_bps` | `economic_contribution_eur / nullif(volume_eur, 0) × 10,000`, NULL for a month with no volume |
| `contribution_rolling_3m_eur` | The **sum** of `economic_contribution_eur` over the current month and the two before it |
| `trend_slope_eur_per_month` | `regr_slope(economic_contribution_eur, month_index)` over the client's months, repeated on each of its rows |
| `trend_flag` | `improving`, `stable` or `deteriorating`, below |

### 8.1 Rolling 3-month contribution

A **sum, not a mean**: `contribution_rolling_3m_eur` for March is January + February + March. It is in the same units as a month's contribution multiplied by three, which keeps it directly comparable with a quarter's P&L. Divide by 3 for a monthly average. It is NULL until the client has three months of history (its first two months on the book), so a two-month figure is never plotted beside three-month ones and read as a drop. The window is three *calendar* months, so a missing month would show as NULL and not be silently skipped.

### 8.2 Trend slope and flag

```text
trend_slope_eur_per_month = regr_slope(economic_contribution_eur, month_index)       per client, over all its months

trend_flag = stable          when |slope| < 0.05 × mean(|economic_contribution_eur|) over the client's months
             improving       otherwise, when slope > 0
             deteriorating   otherwise, when slope < 0
```

The slope is in EUR per month: how much the client's monthly contribution is changing, month after month. The flag compares it with the scale of the client's own monthly contribution. A change of less than 5% of a typical month's absolute contribution, month after month, is treated as noise: a slope of EUR 2 a month on a client that earns or loses EUR 100 a month is not a trend, and the same EUR 2 on a client at EUR 10 a month is. The mean uses the absolute contribution so that a client whose months swing between profit and loss does not look small by averaging to zero.

- **A client with one month** (onboarded in the last month of the window) has no slope: one point has none, so `trend_slope_eur_per_month` is NULL. It is flagged **`stable`**, because there is no evidence of a direction, not because it is known to be steady.
- **A zero slope** is `stable`, including when the mean is zero.
- The slope uses the calendar month index, so a client onboarded in month 10 is regressed on 10 to 23 and not on 0 to 13. The slope's units are the same for every client whenever it joined.

### 8.3 On the desk

The window total in section 3 hides direction. A client that earned EUR 120k over two years can have earned it at EUR 5k a month, steadily, or at EUR 9k a month falling to EUR 1k, and the account plan differs. The flag turns 24 months of contribution into one word per client for the review list (`deteriorating` first), and the rolling quarter is the chart that supports it.

### 8.4 What is checked

| Check | Where | What it pins |
| --- | --- | --- |
| `accepted_values`, `not_null` on `trend_flag` | `_marts.yml` | Every row has one of the three flags |
| `unique` on client × month | `_marts.yml` | One row per client-month |
| Unit test `mart_client_monthly_improving_trend` | `_marts.yml` | The brief's example: contributions of 100, 200, 300 over three months give slope 100 and `improving`; margins; the rolling sum of 600 in March only |
| Unit test `mart_client_monthly_flags_and_edge_cases` | `_marts.yml` | `deteriorating`, a negligible drift staying `stable`, a single month (NULL slope, `stable`), a client onboarded in month 1 of the window starting at `month_index` 1, a zero-volume month with NULL margins |

### 8.5 Worked example

Window of January to March 2025, client volume EUR 1m a month.

| Client | Contribution (Jan, Feb, Mar) | Slope | Mean \|contribution\| | 5% band | Flag |
| --- | --- | --- | --- | --- | --- |
| A | 100, 200, 300 | +100 | 200 | 10 | `improving` |
| B | 300, 200, 100 | −100 | 200 | 10 | `deteriorating` |
| C | 100, 101, 99 | −0.5 | 100 | 5 | `stable` (drift inside the band) |
| D | Mar only: 50 | NULL | 50 | | `stable` (one month) |
| E | Feb: −100 (no trades), Mar: +200 | +300 (on `month_index` 1 and 2) | 150 | 7.5 | `improving` |

Client A's margins are 1.0, 2.0 and 3.0 bps economic (`100 / 1,000,000 × 10,000` and so on) and its rolling 3-month figure is NULL, NULL, then 100 + 200 + 300 = 600.

## 9. Headline KPIs

**Model:** `marts.mart_headline_kpis`, one row for the whole book over the window.

| Column | Definition |
| --- | --- |
| `total_volume_eur` | Σ client `volume_eur` |
| `total_revenue_eur` | Σ client `revenue_eur` |
| `total_contribution_eur` | Σ client `economic_contribution_eur` |
| `gross_margin_bps` | `total_revenue_eur / total_volume_eur × 10,000`, the book's volume-weighted gross margin |
| `economic_margin_bps` | `total_contribution_eur / total_volume_eur × 10,000` |
| `client_count` | Clients with **at least one trade** in the window |
| `trade_count` | Σ client `trade_count` |
| `window_start`, `window_end` | From `int_window` (section 2.1), the single definition of the window |
| `hurdle_rate_bps` | The `hurdle_rate_bps` parameter, looked up by name: the minimum economic margin, in bps of volume, for a client to earn its place |

The totals are sums of `mart_client_profitability`, so they reconcile with the client mart and, through the section 3.12 tests, with the trades. `total_contribution_eur` includes the coverage cost of clients that did not trade: the desk pays it whether or not they traded, so a book total that left it out would overstate what the desk kept. `client_count` is therefore the clients that traded, not every client on the book. Comparing `economic_margin_bps` with `hurdle_rate_bps` says whether the book as a whole earns its hurdle; the per-client version is the volume-margin map (spec section 6.1).

**Worked example** (the unit test `mart_headline_kpis_totals`). Three clients: A with 10 trades, EUR 1m volume, revenue 200 and contribution 150; B with 5 trades, EUR 500k, revenue 100 and contribution −20; C with no trades and contribution −30 (coverage). Volume 1.5m, revenue 300, contribution 150 − 20 − 30 = 100. Gross margin `300 / 1,500,000 × 10,000 = 2` bps, economic margin `100 / 1,500,000 × 10,000 = 0.6667` bps, `client_count` 2 (C did not trade), `trade_count` 15. The hurdle rate is picked out of the parameter table by name, not by position.
