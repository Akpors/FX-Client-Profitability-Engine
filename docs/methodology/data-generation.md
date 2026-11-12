# How the synthetic book is generated

The trade book is synthetic: no real client, no real trade and no employer data went into it. The only real input is the ECB reference rate (see [the benchmark note](benchmark-ecb.md)). Everything else is drawn by a generator whose assumptions all live in one file, `pipeline/config/generator.yaml`, and are explained here.

The point of the generator is to behave the way a dealer's book behaves, so that the volume-versus-margin question has a believable answer. The pattern the analysis later reports (large, active clients paying very little per euro traded) is never assigned to named clients. It follows from ordinary pricing rules applied to a realistic mix of clients. This page describes the generator one stage at a time, in the order the pipeline runs them.

**Status of the numbers.** The values in `generator.yaml` are the builder's illustrative defaults. They are chosen to be plausible for a European spot-FX dealer, not measured from any real desk. The owner reviews and adjusts them at the calibration gate, after which they are frozen. Figures on this page describe the shipped defaults with seed 42; the generated parameter sheet is the authority once the numbers change.

## 1. Client book

Code: `fxengine.clients.generate_clients`. Configuration: `segments`, `volume_tiers`, `legacy_deal` and `client_book` in `generator.yaml`, validated by `fxengine.config`.

The dealer has 200 clients in six segments. The generator draws each client's character once: who they are, how often they trade, how big their tickets are, which pairs they favour and how much business they do in a year. Pricing a trade is a later stage. Fixing the client's expected annual volume first is what lets a trade be priced the way a dealer's grid prices it: from the client's volume tier, not from a label on the client.

### 1.1 The segments and why each behaves as it does

Segment sizes use the largest-remainder method, so the mix is exact (20, 50, 64, 20, 16 and 30 clients) rather than a lottery.

| Segment | Clients | Trades a month (mean) | Median ticket | Mean annual volume per client | Net direction |
|---|---|---|---|---|---|
| Large corporate | 20 | 25 | EUR 600k | EUR 345m | slight net buyers of EUR (0.52) |
| Mid-market | 50 | 20 | EUR 120k | EUR 55m | balanced (0.50) |
| SME | 64 | 8 | EUR 20k | EUR 3m | net sellers of EUR (0.48) |
| Asset manager | 20 | 30 | EUR 1.5m | EUR 1.0bn | balanced (0.50) |
| Hedge fund | 16 | 228 | EUR 1.0m | EUR 4.8bn | balanced (0.50) |
| Payment institution | 30 | 255 | EUR 40k | EUR 260m | net sellers of EUR (0.42) |

"Net direction" is the probability that the client buys EUR on a trade. All pairs are EUR-based, so a client buying EUR is selling the quote currency.

- **Large corporates** run treasury conversions for payables, receivables and repatriation. A few trades a day in large tickets, with pricing negotiated by a dedicated coverage banker. European exporters turn foreign receivables into euros, hence the slight lean to buying EUR.
- **Mid-market corporates** are the middle of a dealer's franchise: tickets in the low hundreds of thousands and sales-negotiated spreads. They trade the major pairs plus CEE currencies (PLN, CZK, HUF).
- **SMEs** are the long tail. Small tickets, a handful of trades a month, rate-card pricing that is rarely renegotiated, and the highest cost to serve per euro traded. They are net importers paying foreign suppliers, so they lean to selling EUR.
- **Asset managers** (real money) trade rebalancing and benchmark-driven flow in large tickets, with more weight on JPY and AUD than corporates. Volume is high but ticket count is modest.
- **Hedge funds** (fast money) trade very often in large clips, concentrated in the liquid majors (about 70% of their pair weight is USD, GBP, JPY and CHF). Their flow is informed and algorithmic, which later shows up as adverse selection when the dealer covers.
- **Payment institutions** (fintechs and payment firms) send very many small and medium tickets from automated payouts and collections. They pay out to foreign beneficiaries, so they lean to selling EUR, and their pair mix is tilted to CEE and EM corridors (about 30% of their weight, against about 20% for corporates). They have the highest ticket count in the book but, because the tickets are small, far less volume than the hedge funds.

The mix gives a skew that real desks recognise. At seed 42, hedge funds and payment institutions together send about 80% of all trades, hedge funds alone about 58% of volume, and the 64 SMEs well under 1% of volume.

### 1.2 What is drawn for each client

| Column | How it is drawn |
|---|---|
| `client_id`, `client_name` | `LC-001` style IDs (prefixes LC, MM, SM, AM, HF, PI), numbered within each segment. The name is `Client <id>`, never a real company name. |
| `segment` | Fixed by the segment counts above. |
| `home_country` | ISO2 code from a fixed list of 30: the EU27, GB, CH and NO. Drawn with weights that favour the large financial centres (DE, FR, GB, NL, CH, IT, ES), so the book is not as likely to be Maltese as German. |
| `onboarded_date` | See 1.5. |
| `legacy_deal` | See 1.5. |
| `trades_per_month` | `LogNormal(mu, sigma)` from the segment's `trades_per_month_lognorm`. A client's own frequency, fixed for the whole window. |
| `ticket_mu`, `ticket_sigma` | The client's ticket distribution: each ticket is later drawn from `LogNormal(ticket_mu, ticket_sigma)`. `ticket_mu` is the segment's `mu` plus `Normal(0, 0.7)`, so a mid-market client can have a median ticket of EUR 60k or EUR 250k. `ticket_sigma` is the segment's value. |
| `pair_weights` | See 1.6. |
| `expected_annual_volume_eur` | See 1.3. |
| `volume_tier_discount_pct` | See 1.3. |

All `*_lognorm` parameters are the `(mu, sigma)` of the underlying normal distribution, not the mean and standard deviation of the lognormal. A trade frequency of `(2.899, 0.8)` therefore has a median of `exp(2.899)` = 18 a month and a mean of `exp(2.899 + 0.8^2 / 2)` = 25. Clients within a segment differ a lot: the frequency and ticket sizes are skewed, so a few clients are much larger than the typical one. That skew is realistic and it matters later, because it makes the biggest clients a visible minority.

### 1.3 Expected annual volume and the volume tier

Each client's expected annual volume is

```
expected_annual_volume_eur = trades_per_month x 12 x exp(ticket_mu + ticket_sigma^2 / 2)
```

The last factor is the mean of the client's lognormal ticket distribution, so the product is the expected sum of a year of tickets.

A dealer prices volume with a tier grid: the more a client trades with the desk, the tighter its pricing. The generator does the same. A client gets the discount of the highest tier whose minimum is at or below its expected annual volume:

| Tier minimum (EUR a year) | Discount off the markup | Clients at seed 42 |
|---|---|---|
| 0 | 0% | 83 (all 64 SMEs, 19 mid-market) |
| 25m | 5% | 38 |
| 100m | 12% | 46 |
| 500m | 20% | 16 |
| 2bn | 30% | 12 |
| 5bn | 40% | 5 (all hedge funds or asset managers) |

Discounts are stored as fractions (0.12, not 12) in the field called `discount_pct`. The tier is a property of the client's size, not of its segment, so the same ladder applies to everyone. An SME never reaches a tier above zero because its volume is too small, and at seed 42 no hedge fund sits in the bottom three tiers because their volumes are too large. Within a segment the tier still varies: mid-market clients span the bottom three tiers, large corporates the middle three.

This grid is the main reason big clients pay less. Together with the ticket bands (large tickets are priced tighter), which apply to each trade's notional and are documented with pricing, it makes the relationship between volume and markup something the pricing rules produce rather than something written into the data.

### 1.4 Why the relationship is not simply built in

A check on the defaults shows the structure. Ranking all 200 clients by expected annual volume against their expected G10-major markup (base markup x ticket band x volume tier x legacy discount, as the pricing stage will apply them), the Spearman correlation is about -0.9. A client-level test in the repo pins that this holds before any trade exists. Most of it comes from the pricing ladder between segments (a hedge fund is priced tighter than an SME, as on any desk), which would be there whatever the tiers did. The part the tiers and ticket bands add shows up within each segment. In a prototype of the pricing formula run for this task, with the tiers and bands switched off, a segment's bigger clients are no cheaper than its smaller ones (correlation near zero); with them on, the within-segment correlation is about -0.4 to -0.8 depending on the segment. So bigger clients within the same segment do get tighter pricing, through the grid, and not because anyone decided they should.

### 1.5 Onboarding and legacy deals

- **Onboarding.** Within each segment, 80% of clients are onboarded before the window starts, between 1 day and 6 years earlier, spread uniformly. The other 20% join at a uniformly random date inside the window. The 20% is a fixed share of each segment (chosen at random), not an independent coin flip per client, so a lucky or unlucky draw cannot swing the trade count. New clients have fewer months to trade, which is why the trade count is calibrated on active months (1.7).
- **Legacy deals.** A legacy deal is an old discount agreed years ago and never repriced. Each client onboarded before the window has a 20% chance of holding one; the discount is 25% off the markup it would otherwise pay, on top of its volume tier. A client onboarded inside the window cannot have one, since there has been no time for it to go stale. At seed 42, 39 of the 160 existing clients hold one. This is the realistic source of margin that has drifted out of line with the client's current size: the client once negotiated a good price, grew or shrank, and nobody revisited it.

### 1.6 Pair preferences

Clients do not trade all 15 pairs equally. Each segment has a `pair_popularity` weight per pair (all strictly positive, for example EURUSD 30 and EURTRY 2 for large corporates). A client's own pair weights are drawn from a Dirichlet distribution centred on the segment's weights:

```
alpha = pair_concentration x pair_popularity / sum(pair_popularity)      (pair_concentration = 30)
pair_weights ~ Dirichlet(alpha)
```

With a concentration of 30 the segment average is preserved but individual clients differ visibly: one corporate is mostly EURUSD and EURGBP, another leans to EURPLN and EURCZK. A lower concentration would make clients more specialised, a higher one would make them look like the segment average. Every weight is positive, but a pair with low popularity can get an extremely small weight for a client, which means that client effectively never trades it.

### 1.7 Trade-volume calibration

The generator is not told to produce 300,000 trades. Trade counts follow from the clients' frequencies, and the defaults are set so the book lands near the `target_trades` value. A client trades on ECB business days only, with the daily count Poisson with mean `trades_per_month / 21.2` (the average number of ECB business days in a month), from its onboarding date to the end of the window.

The expected total, with the means from 1.1:

| Segment | Clients | Mean trades a month | Trades a month if all active |
|---|---|---|---|
| Large corporate | 20 | 25 | 500 |
| Mid-market | 50 | 20 | 1,000 |
| SME | 64 | 8 | 512 |
| Asset manager | 20 | 30 | 600 |
| Hedge fund | 16 | 228 | 3,648 |
| Payment institution | 30 | 255 | 7,650 |
| **Book** | **200** | | **13,910** |

Not every client is active for all 24 months. Of each segment, 80% are active for 24 months and 20% for 12 months on average, so a client is active for 0.8 x 24 + 0.2 x 12 = 21.6 months on average. Expected trades are therefore 13,910 x 21.6, about 300,000, which is 69.6 trades per active client-month, or 62.6 per client-month across all 24 months. At seed 42 the drawn clients give an expected 298,900 trades (99.6% of target) on the 509 business days in the window. The Poisson noise on top is a few hundred trades.

That total is sensitive to the draw of a few busy clients. Across 200 different client-book seeds the expected total averages about 302,000 with a spread of about 11%, and 97% of the seeds land within 20% of the target. Seed 42 lands very close. Changing any frequency in the config moves the total in proportion; the parameter sheet shows both the inputs and the resulting expected count.

### 1.8 What the client book simplifies

- A client's frequency, ticket distribution and pair mix are fixed for the whole window. Real clients grow, shrink and shift. Change over time enters only through spread compression and onboarding, in the pricing stage.
- Client-level pricing differences come from volume tier, ticket size and legacy deals. A real desk also prices by negotiation, relationship and mood. That scatter is absent, which makes the volume-markup relationship tidier than it would be in real data.
- Home country is decorative. It does not influence the pairs a client trades.
- No client leaves. A client who is repriced and walks away is what the pricing recommendation later reasons about with explicit assumptions.
- Clients are independent. Real flow is correlated (month-end hedging, central-bank days).

### 1.9 Reproducibility

`generate_clients` takes the configuration, the 24-month window and a single `numpy.random.Generator`, and uses nothing else for randomness (no global random state). The same configuration, window and generator state give an identical frame. The default seed is 42.

## 2. Market mid

Code: `fxengine.market` (`trading_grid`, `simulate_mid_path`, `mid_at`). Configuration: `trading_hours_utc`, `fix_time_utc` and each pair's `annual_vol` in `generator.yaml`, validated by `fxengine.config`.

Every trade needs a market price at the minute it happens. Markups, cover slippage and the dealer's gross P&L are all measured from the market mid, and the only real price data in the project is one ECB reference rate per currency per business day. This stage fills in the day between the fixes: it builds a synthetic mid for every trading minute that passes through each real fix and moves realistically in between.

### 2.1 What is real and what is synthetic

The fixes are real. The mid between them is synthetic, a random path drawn by the generator. At each ECB fix timestamp the mid equals the published fix exactly, to the last digit. Everywhere else it is an invention that is consistent with the fixes on either side of it. Nothing in the intraday path is a recorded price, and nothing about it should be read as a statement about how EURUSD (or any other pair) actually traded at 09:42 on a given day.

### 2.2 The trading grid

The mid is simulated on a grid of trading minutes, in UTC:

- Each business day covers the half-open interval `[open, close)`, so 07:00 to 16:00 at one-minute resolution is 540 points: 07:00 is the first, 15:59 the last, and 16:00 is not on the grid.
- Business days are whatever dates the caller passes; in the pipeline they are the dates the ECB published a fix. A weekend or a TARGET holiday has no points on the grid at all.
- The 13:10 UTC fix is a grid point on every day. The fix time must sit on the grid's step lattice (`open + n x step_minutes`) and inside `[open, close)`. The code rejects a fix time that does not, instead of squeezing in an extra off-grid point, because an extra point would shorten one step and break the equal-sized steps the bridge assumes. At the default one-minute step, any whole-minute time inside the day qualifies. The grid takes `step_minutes` as a parameter, but the pipeline uses one minute.

Over the committed two-year snapshot the grid holds about 283,000 points between the first and last fix.

### 2.3 The Brownian bridge

Between two consecutive fixes `f_k` and `f_k+1`, which are `T` grid steps apart, the log mid is a Brownian bridge:

```
X(t) = log f_k + (t / T) x (log f_k+1 - log f_k) + B(t) - (t / T) x B(T)        t = 0 ... T
mid(t) = exp(X(t))
```

`B` is the running sum of independent normal draws, one per grid step, each with the same variance. The three parts do different jobs:

- `log f_k + (t / T) x (log f_k+1 - log f_k)` carries the real move. It is a straight line in log space from one fix to the next, so the part of the path that comes from data is the ECB's own day-to-day change, spread evenly across the trading minutes.
- `B(t)` is the random wiggle, a random walk that starts at zero.
- `- (t / T) x B(T)` tilts the random walk so that it comes back to zero at the next fix. Without it the path would drift away from the next fix by whatever the walk happened to reach.

The result starts at one fix, ends at the next, and in between looks like an ordinary random price path whose noise is largest midway and zero at both ends.

**Step variance.** Each step has variance

```
sigma_step^2 = annual_vol^2 / (252 x trading_minutes_per_day) x step_minutes
```

where `trading_minutes_per_day` is the number of points per day times `step_minutes` (540 at the defaults). A trading day therefore carries variance `annual_vol^2 / 252`, the usual square-root-of-time scaling with 252 trading days a year, whatever resolution the grid uses. The code reads the length of the trading day off the grid itself.

On the committed snapshot the realised annualised volatility of the minute returns for EURUSD comes out at 7.0%, the configured value. A test in the repo pins the same property on flat fixes: realised variance over a fix-to-fix day matches `annual_vol^2 / 252` at one-minute and five-minute grids.

**Implementation.** The bridge is vectorised. One call to the random generator draws every increment for the whole path (one fewer than the number of grid points, whatever the volatility), a single cumulative sum builds the walk, and each segment's walk is that cumulative sum minus its value at the segment's first fix. There is no Python loop over grid points or segments, and the path for one pair takes about 15 milliseconds on the full snapshot. A test checks the vectorised path against a plain per-segment loop written from the formula above.

**It never extrapolates.** The function raises an error if any grid point lies before the first fix or after the last one, or if any fix timestamp is not on the grid, rather than guess a price where it has no fix to anchor to. A caller therefore builds the grid over the buffered business days (the snapshot reaches ten days past each end of the 24-month window, so there are fixes on both sides) and trims it to the span from the first fix to the last.

### 2.4 Why weekends and holidays add no variance

A weekend is not 48 hours of price movement in this model, because nothing trades and nothing is simulated. Friday's last grid point (15:59) and Monday's first (07:00) are adjacent points on the grid, and the step between them has the same variance as any other single step. The move from the Friday fix to the Monday fix is spread across the same 540 steps as any other pair of consecutive fixes. A Monday fix that has moved a lot since Friday simply shows up as a steeper slope in the bridge's linear term; it does not add noise. The same goes for a TARGET holiday: the missing day has no grid points, and the bridge runs from the fix before it to the fix after it in the usual 540 steps.

This is a deliberate choice about *where* volatility lives. Real weekends do carry gap risk, but that risk arrives with the real fixes, which are the data. The generator adds noise only for the minutes the desk is open and does not stack extra overnight or weekend noise on top.

A test pins the consequence: a path across a Friday-to-Monday gap has no missing values and no single step larger than 0.5%, and every fix-to-fix segment, weekend or not, spans exactly the same number of grid steps.

### 2.5 The fixed 13:10 UTC fix time

The ECB computes its reference rate in a concertation procedure at about 14:10 CET. In winter CET is UTC+1, so that is 13:10 UTC, which is the value in `fix_time_utc`. In summer the clocks are on CEST (UTC+2), and the same 14:10 local time is 12:10 UTC.

The generator ignores that shift and puts the fix at 13:10 UTC all year. This is a simplification, and it has two effects:

- In summer the synthetic fix sits an hour later in the day than the real one (13:10 UTC is 15:10 CEST). The *level* of every fix is the real ECB value, so the data is not wrong. Only the clock time at which the mid is pinned to it is off, by an hour, for the roughly seven summer months of each year.
- The trading day (07:00 to 16:00 UTC, a London and Frankfurt-centred window) is also fixed in UTC, so the fix sits at the same place in the trading day all year, and the share of trades that land near the fix does not change with the clocks.

The analysis compares each trade with its date's fix, not with a fix matched to the trade's time, so the clock time of the pin matters only for how the mid behaves around it (see 2.7).

### 2.6 Volatility by pair

Each pair has its own annual volatility in `generator.yaml`. It is the volatility of the log rate, as a fraction, and it sets the size of the intraday noise. The values are the builder's rough 1 to 2 year levels for each EUR cross, chosen to be plausible and sensibly ordered (the pegged DKK barely moves, TRY and ZAR move the most). They are not measured from data, and the owner reviews them at the calibration gate with the other defaults.

| Pair | Liquidity tier | Annual vol | Sd of one day's noise (bps) | Largest noise sd, mid-segment (bps) |
|---|---|---|---|---|
| EURUSD | g10_major | 7.0% | 44.1 | 22.0 |
| EURGBP | g10_major | 6.0% | 37.8 | 18.9 |
| EURJPY | g10_major | 9.0% | 56.7 | 28.3 |
| EURCHF | g10_major | 5.5% | 34.6 | 17.3 |
| EURSEK | g10_minor | 7.0% | 44.1 | 22.0 |
| EURNOK | g10_minor | 8.0% | 50.4 | 25.2 |
| EURDKK | g10_minor | 0.3% | 1.9 | 0.9 |
| EURAUD | g10_minor | 8.5% | 53.5 | 26.8 |
| EURCAD | g10_minor | 7.0% | 44.1 | 22.0 |
| EURPLN | cee | 5.0% | 31.5 | 15.7 |
| EURCZK | cee | 4.5% | 28.3 | 14.2 |
| EURHUF | cee | 9.0% | 56.7 | 28.3 |
| EURZAR | em | 14.0% | 88.2 | 44.1 |
| EURTRY | em | 16.0% | 100.8 | 50.4 |
| EURMXN | em | 12.0% | 75.6 | 37.8 |

"Sd of one day's noise" is `annual_vol / sqrt(252)` in basis points: the standard deviation of the random part of the mid over one full trading day. "Largest noise sd, mid-segment" is the standard deviation of the bridge noise at its widest point, halfway through the 540 steps (about 08:40 UTC on the morning after a fix). A Brownian bridge's spread at its midpoint is half of the one-day figure, and it is zero at both fixes. These numbers describe the random part only. The real move from one fix to the next is added on top by the linear term.

Two things to know about the extremes. EURDKK is pegged inside the ERM II band, so the intraday noise is tiny by design (under 1 bp at its widest). EURTRY is a managed currency with a steady depreciation trend and occasional jumps: 16% describes the diffusion only. The trend itself and any jump are already in the ECB fixes the mid is pinned to, so they appear in the data without being modelled here.

### 2.7 A caution for FX readers: the mid is pinned to the benchmark

The ECB fix is the benchmark the later analysis uses (see [the benchmark note](benchmark-ecb.md)), and this stage pins the mid to that same number at 13:10 UTC. That is a property of the construction, and it affects how to read one kind of comparison:

- **The mid-to-benchmark distance is small by construction around 13:10 UTC.** At the fix it is exactly zero. Thirty minutes either side the noise has a standard deviation of about 10 bps on EURUSD (a minute's worth is about 1.9 bps; the bridge scales with the square root of the minutes from the fix), and it grows towards the middle of each segment. In real markets the ECB fix comes from a procedure of its own, is rounded to a few significant figures and is not itself a traded price, so the live mid at 14:10 CET differs from it by a small amount that nothing pins. Here that difference is zero at 13:10 by construction.
- **A trade-versus-benchmark gap is therefore a blend of two things here.** One is the markup the dealer charged. The other is how far the synthetic mid happened to be from the fix at that minute. For a trade close to the fix the second is tiny, so the gap looks like the markup. For a trade early in the day it can be large, and the sign is random. Over many trades the random part averages out, but a reader comparing one small group of trades against the benchmark should expect noise that comes from this stage, not from the dealer's pricing.
- **The generator measures its own markups from the mid, not from the benchmark.** `markup_bps_applied` (see the pricing stage) is relative to the mid, and the benchmark rate is carried alongside it as a separate column. Where the analysis uses the benchmark, the mid-to-fix distance described above is part of what it is measuring.

None of this says the ECB fix is a bad benchmark. It is a published reference, and the analysis is built on it for that reason. It says that in this dataset the mid and the benchmark are linked by design, which real desks do not see.

### 2.8 What the market mid simplifies

- **Intraday price behaviour is stylised.** A Brownian bridge has constant volatility, no jumps and no gaps. There is no news-driven spike, no wider spread around the open or close, no volatility clustering, and no difference between a quiet hour and a busy one.
- **Variance is concentrated in the trading day.** All of a day's variance is spread across the 540 trading minutes and none is placed overnight, so the per-minute volatility is about 1.6 times what it would be if the same daily variance were spread over 24 hours. Intraday noise is a little rougher than a 24-hour spread would imply.
- **Currencies are independent.** Each pair's path is drawn without reference to the others, so EURUSD and EURGBP can move in opposite directions on a day when, in reality, both would follow the dollar.
- **One fixed fix time all year**, as covered in 2.5.
- **The mid is a single price.** There is no bid and no ask here, so the spread a client pays is the markup applied to the mid in the pricing stage, with nothing in the market price standing for the touch.

### 2.9 Reproducibility

`simulate_mid_path` uses only the `numpy.random.Generator` it is given. It draws all of a pair's increments in one call, and always draws one fewer than the number of grid points, so the random stream it consumes depends on the grid alone and never on the pair's volatility. The same fixes, grid, volatility and generator state give an identical path.
