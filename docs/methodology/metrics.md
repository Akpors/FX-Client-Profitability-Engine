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
| `assert_mid_near_benchmark` | `tests/` | The synthetic mid stays within 2% of the same-day fix |

`assert_mid_near_benchmark` raises a warning, not a failure, on a handful of trades. On days when the ECB fix itself jumped by more than about 2.5% from the previous day (for example TRY on 19 March 2025 and ZAR on 3 April 2025), a trade early in the day sits partway between two very different fixes and is legitimately up to about 3% from that day's benchmark. At seed 42 this affects 15 of about 298,000 trades. The test fails the build only if more than 0.1% of trades are that far out, which would mean the mid path is broken.
