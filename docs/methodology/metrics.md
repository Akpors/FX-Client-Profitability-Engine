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
