# The ECB reference rate as the spot benchmark

Every trade in the synthetic book is measured against an independent benchmark: the ECB euro foreign exchange reference rate for the trade date. This page says exactly what that rate is, why it is a fair yardstick for client spot pricing, where it falls short, and how the repo stores it so a build is offline and repeatable.

## 1. What the ECB reference rate is

| Property | Detail |
| --- | --- |
| Source | ECB Data Portal, dataflow `EXR`, series key `D.{CCY}.EUR.SP00.A` (daily, spot). Public, no API key. |
| Setting | The rates come from a daily concertation procedure between central banks. It normally takes place around **14:10 CET**. The series metadata in the raw `csvdata` response labels it "2.15 pm (C.E.T.)", so read the time as mid-afternoon Frankfurt rather than an exact tick. |
| Publication | Around **16:00 CET** on each working day. The rate is therefore known only after the market has already traded through its time-stamp. |
| Quotation | **Units of quote currency per 1 EUR.** EUR is always the base: `USD 1.1214` is EURUSD 1.1214, `JPY 177.38` is EURJPY 177.38. Non-EUR crosses (USDJPY, GBPUSD) are not published. They would be derived by division, and this engine does not need them. |
| Type | One rate per currency. It is a reference **mid**: no bid, no offer, no spread. |
| Status | **For information only. Not a tradable price.** Nobody can deal at it, and the ECB discourages using it for transactions. |
| Calendar | One fix per TARGET working day. **No fix on weekends or TARGET holidays** (1 January, Good Friday, Easter Monday, 1 May, 25 and 26 December). The ECB does still fix on days when other centres are shut (for example 4 July and US Thanksgiving, and the UK summer bank holiday), when USD or GBP liquidity may be thin. |
| Precision | As published, not re-rounded by this repo. It varies by currency: 2 decimals for JPY and HUF, 3 for CZK, 4 for most others, 5 for GBP. |

For contrast, the WM/Refinitiv 4pm London fix is a calculated window benchmark that clients can reference in fixing orders. The ECB rate is a different kind of object: a published indicative level, not an executable fix.

## 2. Why it is still a fair, independent benchmark for spot

1. **Independent of both sides of the trade.** Neither the dealer's prices nor the client's flow feed it. A client's rate measured against the dealer's own cover rate asks how well the dealer hedged. Measured against the ECB rate it asks what the client paid away from a neutral level. The engine reports both, and the second does not depend on the book being analysed.
2. **A mid, so the signed distance is a cost.** With no spread baked in, `side_sign × (client_rate − benchmark_rate) / benchmark_rate × 10,000` reads directly as bps paid to the dealer, side-adjusted for a buy or a sell.
3. **Public and reproducible.** Anyone can download the same series for free and check the numbers. This is a public portfolio piece, so an auditable benchmark beats a licensed one nobody can re-run.
4. **One method across all 15 pairs.** Majors, Scandinavians, commodity currencies and CEE/EM are all measured on the same basis, so comparing margin by pair uses a common yardstick.
5. **Like-for-like with the product.** The book is spot only, and the ECB series is a spot series. There is no forward-point or value-date mismatch to explain away.
6. **Established.** The series is long, stable and widely used for valuation and reporting, so practitioners recognise it.

It is a fair benchmark for the question "how far from a neutral spot level was this client priced, on average?" It is not evidence of what could have been executed at a given moment.

## 3. The one-fix-per-day caveat

The ECB gives one level per day, set around 14:10 CET. Trades happen all day. A trade at 08:15 or 17:40 is compared with a rate set hours away from it, so the benchmark spread on a single ticket is:

```text
benchmark spread  =  genuine pricing cost  +  intraday market move between the trade and the fix
```

What follows from that:

- **Read it in aggregate.** The intraday move has no systematic sign across many tickets, so it averages down at client, segment, pair and month level. Do not read one ticket's benchmark spread as that ticket's margin.
- **Timing bias is possible.** If a client's flow clusters at a time of day with a systematic drift relative to the 14:10 mark (around the London close, say, or ahead of data releases), the benchmark spread picks up that timing effect as well as price.
- **Thin pairs are noisier.** For TRY, HUF, CZK, ZAR and MXN a single snapshot can sit well away from where size could actually trade, so their benchmark spread mixes cost with benchmark noise more than EURUSD does. Compare these pairs with that in mind.
- **The engine does not hide it.** The dealer-lens gross spread (client rate against the dealer's cover) and the ECB-lens benchmark spread are reported side by side. The gap between them is partly this intraday effect. The synthetic intraday mid is a Brownian bridge that passes through each ECB fix (see `data-generation.md`), so the generated book contains this intraday effect by design.

## 4. How the snapshot works

The pipeline never calls the ECB at build time. It reads a CSV committed to the repo, so `make pipeline` runs offline and the same config plus the same snapshot gives byte-identical output.

**Window.** The most recent 24 complete calendar months before the build date. For a build on 2026-11-12 that is 2024-11-01 to 2026-10-31.

**Buffer.** `fetch-ecb` requests the window plus **10 calendar days on each side**: 2024-10-22 to 2026-11-10 for the example above. The intraday mid is bridged between consecutive fixes, so the first and last day of the window each need a fix on both sides. A weekend plus a TARGET holiday spans at most four days with no fix (Good Friday to Easter Monday), so 10 days leaves ample margin.

**File name.** `pipeline/data/ecb_snapshot/ecb_rates_<window_start>_<window_end>.csv`, for example `ecb_rates_2024-11-01_2026-10-31.csv`. The name carries the **window**, while the contents cover the buffered range. `run-all` derives the window from the file name (`fxengine.ecb.window_from_snapshot`) and never from today's date, so rebuilding next month does not silently move the window. If several snapshots exist, `latest_snapshot` picks the one with the latest window end.

**Format.** `rate_date,currency,rate`: ISO dates, one row per currency per fixing date, sorted by date then currency, no blank rows.

**Refreshing.** Explicit only, never part of `make pipeline`:

```bash
make refresh-ecb
docker compose run --rm --no-deps pipeline python -m fxengine fetch-ecb --build-date 2026-11-12
```

`--build-date` (default: today) sets the window. `--snapshot-dir` overrides the output folder. A refresh for the same window overwrites the file. A refresh for a new month writes a new file next to the old one, which stays in place until you delete it.

**What is checked before anything is written.**

| Check | Failure |
| --- | --- |
| The API answers within 30 s with HTTP 200 and parseable CSV | `fetch-ecb: error: ...` on stderr (one line), exit code 1 |
| Every one of the 15 currencies has a rate on every date that any currency has one | The error names the ISO date and the missing currency or currencies |
| The data includes a fix on or before the window start and on or after the window end | The error names the date and the earliest or latest fix returned |

A date on which no currency has a rate is a weekend or TARGET holiday, so it is correctly absent and not treated as a gap. A date on which some currencies have a rate and others do not is a gap, and the pipeline fails loudly there instead of producing trades with no benchmark. The write itself is a temp file followed by a rename, so a failed or interrupted refresh leaves the committed snapshot exactly as it was.

## 5. The committed snapshot

| | |
| --- | --- |
| File | `ecb_rates_2024-11-01_2026-10-31.csv` |
| Window | 2024-11-01 to 2026-10-31 (509 fixing dates) |
| Contents | 2024-10-22 to 2026-11-10: 524 fixing dates x 15 currencies = 7,860 rows |
| Weekdays with no fix | 12, all TARGET holidays: 2024-12-25, 2024-12-26, 2025-01-01, 2025-04-18, 2025-04-21, 2025-05-01, 2025-12-25, 2025-12-26, 2026-01-01, 2026-04-03, 2026-04-06, 2026-05-01 |
| Weekend rows | None |
