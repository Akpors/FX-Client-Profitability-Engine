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

- **Large corporates** run treasury conversions for payables, receivables and repatriation. About 25 trades a month in large tickets (one or two a business day), with pricing negotiated by a dedicated coverage banker. European exporters turn foreign receivables into euros, hence the slight lean to buying EUR.
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
| `negotiation_factor` | The client's own price level relative to the dealer's grid, `LogNormal(0, client_markup_sigma)` with the segment's sigma, drawn once per client. See 1.4. |

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

This grid is the main reason big clients pay less. Together with the ticket bands (large tickets are priced tighter), which apply to each trade's notional and are documented with pricing (section 3), it makes the relationship between volume and markup something the pricing rules produce rather than something written into the data.

### 1.4 The negotiation factor, and why the relationship is not simply built in

Two clients in the same segment, of the same size and in the same volume tier, rarely pay the same price. One treasurer negotiated hard when the relationship was set up, another never asked; one runs a competitive tender every year, another values the coverage banker's time. Spec section 5.3 calls this the client's negotiated spread level and price sensitivity. The generator gives each client a `negotiation_factor`, a multiplier on every markup that client pays:

```
negotiation_factor ~ LogNormal(0, client_markup_sigma)       one draw per client, fixed for the window
```

Its median is 1.0, so it moves clients around the grid without moving the grid. The sigma is set per segment and reflects how bilateral that segment's pricing is:

| Segment | `client_markup_sigma` | Why |
|---|---|---|
| Large corporate | 0.30 | Bilateral deals agreed with a coverage banker: similar treasuries can pay quite different prices. |
| Mid-market | 0.25 | Sales-negotiated deal by deal, but closer to a price schedule. |
| SME | 0.20 | Rate-card pricing, rarely renegotiated, so little room to differ. |
| Asset manager | 0.20 | Several banks on the panel keep prices close to each other. |
| Hedge fund | 0.20 | Electronic and multi-dealer, though each fund has its own deal. |
| Payment institution | 0.20 | Platform pricing schedules, negotiated contract by contract. |

A sigma of 0.30 means about two clients in three sit within a factor of 1.35 of the grid price (exp(0.30) = 1.35), and one in twenty pays more than 1.8 times or less than 0.55 times it. The factor is independent of the client's size, segment ladder, tier and legacy flag, so it cannot create the volume-versus-margin pattern; it can only blur it. It is drawn after every other client attribute, in one call over the whole book, so switching it off (sigma 0, factor exactly 1) leaves every other column unchanged. A test pins that.

**What the generated book shows.** For each client, take its realised annual EUR volume (traded notional over the window divided by the years it was active) and its median markup on the G10 majors (EURUSD, EURGBP, EURJPY, EURCHF). Over the 298,178 trades generated at seed 42:

| Spearman, volume against G10-major markup | All 200 clients | Pooled within segment | Large corp. | Mid-market | SME | Asset mgr | Hedge fund | Payment inst. |
|---|---|---|---|---|---|---|---|---|
| Shipped defaults | -0.91 | -0.29 | -0.20 | -0.20 | -0.10 | -0.73 | -0.44 | -0.49 |
| Negotiation factor off (sigma 0) | -0.93 | | -0.55 | -0.48 | -0.22 | -0.83 | -0.75 | -0.59 |
| Volume tiers and ticket bands off | -0.87 | -0.03 | +0.06 | +0.12 | -0.01 | -0.34 | +0.16 | -0.24 |

"Pooled within segment" takes the segment ladder out: each client's volume and markup are ranked as percentiles within its own segment, each segment's ranks are centred on zero, and the pooled ranks are correlated. It is in effect a weighted average of the six within-segment correlations, so it reads the grid's effect in one number.

Three things follow.

- **Across the whole book the correlation is about -0.9, and the segment ladder supplies most of it.** A hedge fund is priced tighter than an SME on any desk, whatever the tiers do: with tiers and bands switched off the book-wide figure is still -0.87. That part is market structure, not a finding, and the analysis should not present it as one.
- **Within a segment, the grid is what makes bigger clients cheaper.** With the tiers and ticket bands off, the within-segment correlations scatter around zero (from -0.34 to +0.16, the noise of 16 to 64 clients) and the pooled figure is -0.03. With them on, the pooled figure is -0.29, and at seed 42 every segment is negative. That last part is a property of this seed, not a rule: across seeds 42 to 61, six seeds have one segment at or slightly above zero (at most +0.18), while the pooled figure is negative at every seed (-0.26 to -0.47). So a segment's bigger clients pay less because the grid gives volume a discount, not because anyone labelled them.
- **The negotiation factor makes the within-segment pattern look like real data.** Without it, the within-segment correlations run to -0.83, too tidy for a book where every price was negotiated. With it they run from -0.10 to -0.73 at seed 42. Across 20 seeds (42 to 61) the averages are -0.28 for large corporates, -0.35 for mid-market, -0.23 for SMEs, -0.42 for asset managers, -0.55 for hedge funds and -0.42 for payment institutions. Seed 42 is on the weak side for the two corporate segments. SMEs show the least because they all sit in the bottom volume tier and only the ticket bands separate them.

The client-level test in the repo checks a proxy before any trade exists (expected volume against base x band at the median ticket x tier x legacy x negotiation factor) and requires a correlation below -0.5. On the generated book, one trade-level test requires the all-client correlation to be below -0.3. That one would pass on the segment ladder alone, so a second requires the pooled within-segment figure to be below -0.15, and a third checks that the same figure with the tiers and bands off stays above -0.15. Together they fail if the grid stops doing its job.

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

Not every client is active for all 24 months. Of each segment, 80% are active for 24 months and 20% for 12 months on average, so a client is active for 0.8 x 24 + 0.2 x 12 = 21.6 months on average. Expected trades are therefore 13,910 x 21.6, about 300,000, which is 69.6 trades per active client-month, or 62.6 per client-month across all 24 months. At seed 42 the drawn clients give an expected 298,900 trades (99.6% of target) on the 509 business days in the window. The Poisson noise on top is a few hundred trades: the generated book (section 3) has 298,178.

That total is sensitive to the draw of a few busy clients. Across 200 different client-book seeds the expected total averages about 302,000 with a spread of about 11%, and 97% of the seeds land within 20% of the target. Seed 42 lands very close. Changing any frequency in the config moves the total in proportion. Trade frequencies are not on the parameter sheet; they are set in `generator.yaml`, and the client-level test `test_expected_trade_count_is_close_to_target` checks the expected count against `target_trades`.

### 1.8 What the client book simplifies

- A client's frequency, ticket distribution and pair mix are fixed for the whole window. Real clients grow, shrink and shift. Change over time enters only through spread compression and onboarding (section 3).
- Client-level pricing differences come from volume tier, ticket size, legacy deals and one negotiation factor per client. A real client's price level also drifts with the relationship, a tender or a new banker; here the factor is fixed for the whole window, and it is independent of everything else about the client (a real price-sensitive client might also be the one that splits its flow across more banks).
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
- **The generator measures its own markups from the mid, not from the benchmark.** `markup_bps_applied` (section 3.5) is relative to the mid, and the benchmark rate is carried alongside it as a separate column. Where the analysis uses the benchmark, the mid-to-fix distance described above is part of what it is measuring.

None of this says the ECB fix is a bad benchmark. It is a published reference, and the analysis is built on it for that reason. It says that in this dataset the mid and the benchmark are linked by design, which real desks do not see.

### 2.8 What the market mid simplifies

- **Intraday price behaviour is stylised.** A Brownian bridge has constant volatility, no jumps and no gaps. There is no news-driven spike, no wider spread around the open or close, no volatility clustering, and no difference between a quiet hour and a busy one.
- **Variance is concentrated in the trading day.** All of a day's variance is spread across the 540 trading minutes and none is placed overnight, so the per-minute volatility is about 1.6 times what it would be if the same daily variance were spread over 24 hours. Intraday noise is a little rougher than a 24-hour spread would imply.
- **Currencies are independent.** Each pair's path is drawn without reference to the others, so EURUSD and EURGBP can move in opposite directions on a day when, in reality, both would follow the dollar.
- **One fixed fix time all year**, as covered in 2.5.
- **The mid is a single price.** There is no bid and no ask in the simulated market, so the spread a client pays is the markup applied to the mid in the pricing stage (section 3). The interbank bid/offer enters only where the dealer covers: it pays a fixed half-spread per liquidity tier on every hedge (section 3.3), the same at every minute of the day.

### 2.9 Reproducibility

`simulate_mid_path` uses only the `numpy.random.Generator` it is given. It draws all of a pair's increments in one call, and always draws one fewer than the number of grid points, so the random stream it consumes depends on the grid alone and never on the pair's volatility. The same fixes, grid, volatility and generator state give an identical path.

## 3. Pricing and execution

Code: `fxengine.trades.generate_trades`. Configuration: each segment's `base_markup_bps`, `markup_noise_sigma`, `compression_per_year`, `adverse_selection_bps` and `buy_probability`, plus `cover_half_spread_bps`, `ticket_bands`, `volume_tiers` and `legacy_deal` in `generator.yaml`; the client attributes come from section 1.

This stage turns the client book into trades. For each trade it decides when it happens, in which pair, in which direction and for how much, then prices it the way a dealer's pricing grid would and covers it in the market. The output is the `raw.trades` table. At seed 42 on the committed snapshot it holds 298,178 trades over the 509 ECB business days from 1 November 2024 to 30 October 2026.

### 3.1 When and what clients trade

- **Days.** A client trades only on ECB business days inside the window, and only on or after its onboarding date. Its number of trades on each such day is `Poisson(trades_per_month / mean business days per month)`, where the mean is the window's ECB business days divided by its months (509 / 24 = 21.2). Every trade therefore has a same-day ECB fix.
- **Time.** The timestamp is uniform over the half-open trading day `[07:00, 16:00)` UTC at one-second resolution, so 15:59:59 is possible and 16:00:00 is not. The mid at the trade is the mid at the last grid minute at or before it (section 2).
- **Pair.** Drawn from the client's own `pair_weights` (section 1.6).
- **Side.** `client_side` is `BUY` when the client buys EUR, the base currency, and `SELL` when it sells EUR. A client buys with its segment's `buy_probability`. Across the book 45.6% of trades are client BUYs, because SMEs and payment institutions lean to selling EUR.
- **Ticket.** `LogNormal(ticket_mu, ticket_sigma)` from the client's ticket distribution, rounded to the nearest EUR 1,000, with a floor of EUR 1,000. `notional_base` is that rounded EUR amount, and the ticket band uses it.

### 3.2 The markup

The markup is one-sided, in basis points of the mid, and every factor in it comes from the configuration or the client book:

```
markup_bps = base_markup_bps[segment][liquidity_tier(pair)]     the segment's price for the pair, at a 100k-500k ticket
           x markup_multiplier(ticket band of notional_base)     bands are [min, max): EUR 25,000 is in "25k-100k"
           x (1 - volume_tier_discount_pct)                      the client's volume tier (1.3)
           x (1 - legacy_deal.discount_pct)    if legacy_deal    an old discount never repriced (1.5)
           x exp(-compression_per_year x years_since_window_start)
           x negotiation_factor                                  the client's own level, fixed (1.4)
           x LogNormal(0, markup_noise_sigma)                    trade-by-trade noise
```

`years_since_window_start` is `(trade_date - window start) / 365.25` in days. Compression is competitive pressure: the same client is quoted a little tighter each month, faster for electronic flow (hedge funds 18% a year, payment institutions 12%) than for corporates (large 5%, mid-market 3%) and not at all for SMEs on a rate card. The per-trade noise stands for everything a pricing grid does not capture on the day: the salesperson, the size of the morning's queue, the client asking twice.

A worked example. A mid-market client in the 12% volume tier, with no legacy deal and a negotiation factor of 1.10, buys EUR 250,000 against USD one year into the window, at a mid of 1.08500, with neutral noise:

```
6.0 bps (mid-market, g10_major) x 1.00 (100k-500k) x 0.88 x 1.00 x exp(-0.03 x 1.0) x 1.10 x 1.00 = 5.64 bps
client_rate = 1.08500 x (1 + 5.64 / 10,000) = 1.0856115, quoted as 1.08561
```

Median `markup_bps_applied` in the generated book, by segment and liquidity tier:

| Segment | G10 major | G10 minor | CEE | EM |
|---|---|---|---|---|
| SME | 17.7 | 27.7 | 47.4 | 73.5 |
| Mid-market | 6.0 | 10.4 | 17.7 | 30.0 |
| Payment institution | 3.0 | 5.2 | 10.1 | 17.7 |
| Large corporate | 2.3 | 4.0 | 7.0 | 11.3 |
| Asset manager | 1.0 | 1.6 | 3.1 | 6.3 |
| Hedge fund | 0.4 | 0.7 | 1.5 | 2.4 |

On EURUSD alone the medians are SME 18.0, mid-market 6.0, payment institution 3.0, large corporate 2.3, asset manager 1.0 and hedge fund 0.4 bps.

### 3.3 Client rate, cover rate and the sign convention

With `side_sign = +1` when the client BUYs EUR and `-1` when it SELLs EUR:

```
client_rate = mid x (1 + side_sign x markup_bps / 10,000)
cover_rate  = mid x (1 + side_sign x (slippage_bps + half_spread_bps) / 10,000)
              slippage_bps    ~ Normal(mean, sd) by segment              (adverse selection)
              half_spread_bps = cover_half_spread_bps[liquidity_tier(pair)]   (fixed, no random draw)
```

All rates are quote currency per EUR, like the ECB fix.

- **Client BUY.** The dealer sells EUR to the client above the mid (`client_rate > mid`) and buys the EUR back in the market at `cover_rate`, at the interbank offer.
- **Client SELL.** The dealer buys EUR from the client below the mid (`client_rate < mid`) and sells it in the market at `cover_rate`, at the interbank bid.

**The cover pays the interbank half-spread.** A dealer that hedges in the market does not trade at mid: it lifts the offer to buy and hits the bid to sell, so every cover costs half the interbank bid/offer, by the pair's liquidity tier:

| Liquidity tier | Pairs | `cover_half_spread_bps` |
|---|---|---|
| g10_major | USD, GBP, JPY, CHF | 0.1 |
| g10_minor | SEK, NOK, DKK, AUD, CAD | 0.4 |
| cee | PLN, CZK, HUF | 1.0 |
| em | ZAR, TRY, MXN | 2.5 |

These are illustrative values for a European dealer's cover in normal market hours, not measured spreads, and the owner calibrates them at the gate. The configuration requires all four tiers, no negative value, and an order that does not narrow as liquidity thins (major, minor, CEE, EM). The half-spread is a lookup, not a random draw, so adding it leaves every other column of the book, and the random stream, unchanged.

**Slippage is signed so that positive is worse for the dealer**, on either side. On a client BUY a positive slippage puts the cover above the mid, so the dealer pays more for the EUR it has just sold; on a client SELL it puts the cover below the mid, so the dealer receives less for the EUR it has just bought. The half-spread is signed the same way. Dealer gross P&L is then

```
gross_pnl_quote = side_sign x (client_rate - cover_rate) x notional_base
                = mid x (markup_bps - slippage_bps - half_spread_bps) / 10,000 x notional_base   (before rounding)
gross_pnl_eur   = gross_pnl_quote / benchmark_rate
```

so the dealer keeps the markup minus the slippage and the half-spread. The slippage mean is the segment's adverse selection: how far the market has moved against the dealer by the time it hedges a client's trade. It is largest for hedge funds (mean 0.30 bps, sd 0.80), whose flow is informed and fast, then asset managers (0.20, sd 0.50) whose large rebalancing tickets move the market a little, and close to zero for corporates and SMEs (0.02 to 0.10). The mean may be negative in the configuration (a client whose flow the dealer can cover better than mid); the defaults do not use that.

In the generated book the volume-weighted hedge fund markup is 0.61 bps. Adverse selection takes 0.30 bps of it and the cover half-spread another 0.37 bps (72% of hedge fund volume is in the majors at 0.1 bps, but its minor, CEE and EM flow pays 0.4 to 2.5), which leaves the dealer **-0.07 bps**: at the shipped defaults, hedge fund flow loses money before any cost to serve. Nearly half of hedge fund trades (47%) lose money before costs, against 9% for asset managers and almost none elsewhere. By tier the hedge fund gross margin is -0.02 bps on the majors, -0.06 on the minors, +0.04 on CEE and -0.56 on EM, where 61% of trades lose. The segments that pay wider markups absorb the half-spread comfortably: on EM pairs, asset managers keep 2.68 bps, large corporates 6.65, payment institutions 9.28, mid-market corporates 25.3 and SMEs 66.5.

This is the reason a tight markup is not the same thing as a profitable client, and why the analysis measures realised margin against the cover rather than the quoted markup. It is also the result for a desk that covers every trade on its own. A desk that internalises would pay the half-spread on less of the hedge fund flow (section 3.6), so read the hedge fund figure as the cost of covering this flow trade by trade, not as a verdict on hedge fund business in general.

### 3.4 Why the high-volume, low-margin pattern emerges

No step in the generator looks at a client's volume and assigns it a margin. The pattern comes out of six ordinary pricing mechanisms, each one something a dealer would recognise:

| Mechanism | What it does | In the generated book |
|---|---|---|
| Segment price ladder | Fast, electronic and institutional flow is quoted tighter than corporate and SME flow. | EURUSD median from 18.0 bps (SME) to 0.4 bps (hedge funds). |
| Volume tiers | A client's expected annual volume earns 0% to 40% off its markup (1.3). | Median client G10-major markup falls tier by tier: 16.4, 5.0, 2.6, 0.93, 0.57 and 0.36 bps. |
| Ticket bands | Bigger tickets are priced tighter, from x1.80 under EUR 25k to x0.55 at EUR 10m and above. | Mid-market EURUSD median 8.6 bps under 25k, 6.8 at 25k-100k, 5.3 at 100k-500k, 4.5 at 500k-2m. |
| Legacy deals | 39 existing clients keep an old 25% discount whatever their size today. | Within every segment, the median legacy client's G10-major markup is 72% to 82% of the median non-legacy client's (each client's own median markup, then the ratio of the two group medians; legacy groups are 3 to 17 clients). |
| Compression | Markups drift down through the window, fastest for electronic flow. | Hedge fund EURUSD median 0.46 bps in the first three months (November 2024 to January 2025), 0.35 in the last three (August to October 2026), x0.77 against exp(-0.18 x 1.75) = 0.73 configured; SMEs, with no compression, x1.04 (noise and mix). |
| Adverse selection and cover cost | The busiest, tightest-priced flow is also the hardest to cover, and every cover pays the interbank half-spread. | Hedge funds' 0.61 bps markup loses 0.30 bps to adverse selection and 0.37 bps to the half-spread, leaving -0.07 bps. |

Under all six sits the negotiation factor (1.4), which scatters clients around the grid without favouring big or small. It is why two clients of the same size can pay noticeably different prices, and why the within-segment relationship between volume and markup is clearly negative but far from perfect.

Put together, the shares of the book look like this:

| Segment | Share of trades | Share of EUR volume | Markup (bps) | Slippage (bps) | Cover half-spread (bps) | Gross margin (bps of volume) | Gross P&L (EUR m, 24 months) |
|---|---|---|---|---|---|---|---|
| Hedge fund | 25.2% | 58.4% | 0.61 | 0.30 | 0.37 | -0.07 | -0.78 |
| Asset manager | 4.7% | 24.6% | 1.30 | 0.20 | 0.35 | 0.75 | 3.75 |
| Payment institution | 54.7% | 8.6% | 4.38 | 0.05 | 0.59 | 3.75 | 6.60 |
| Large corporate | 3.2% | 5.8% | 2.68 | 0.09 | 0.32 | 2.28 | 2.68 |
| Mid-market | 8.5% | 2.4% | 9.59 | 0.05 | 0.46 | 9.08 | 4.53 |
| SME | 3.7% | 0.1% | 24.25 | 0.02 | 0.40 | 23.83 | 0.66 |
| **Book** | | | | | **0.39** | **0.85** | **17.43** |

Markup, slippage and half-spread are volume-weighted, so markup minus slippage minus half-spread gives the gross margin to within rounding. Across the book the half-spread costs EUR 7.9m over the 24 months, and the gross margin falls from 1.24 bps without it to 0.85 bps.

Hedge funds bring 58% of the volume and lose EUR 0.8m at gross (-4.5% of the book's gross P&L); mid-market corporates bring 2% of the volume and 26% of the P&L. That is the volume-versus-margin tension the analysis investigates, and it arrives with no client named in advance. These are gross figures: the cost to serve (spec section 5.5), which weighs heavily on small tickets, comes later in the dbt models.

### 3.5 Rounding, the benchmark and the audit column

- **Rounding.** `client_rate`, `cover_rate` and `mid_rate` are rounded to the pair's `price_decimals`, the precision a client price is quoted in (five decimals for EURUSD, a tenth of a pip, about 0.09 bps; two for EURHUF, about 0.25 bps; three for EURCZK, about 0.4 bps). The client and cover rates are computed from the unrounded mid and then rounded, so a client rate is on the correct side of `mid_rate` to within one price increment, not always strictly. P&L computed from the rounded columns carries up to a tick of rounding noise per trade. The noise averages out, but on a single hedge fund trade it can be as large as the markup.
- **Benchmark.** `benchmark_rate` is the ECB reference rate for the trade's date and pair, exactly as published, not rounded to the quoting precision. Every trade has one; the generator fails, naming the pair and date, if a trading day lacks a fix. Section 2.7 explains why the mid sits close to the benchmark around 13:10 UTC by construction.
- **`markup_bps_applied`.** The markup the formula produced, before rounding, in bps of the unrounded mid. It is an audit column, kept so a reader can check the pricing rule trade by trade. The realised spread recomputed from the rounded rates agrees with it to within rounding.

### 3.6 What pricing and execution simplify

- **Arrival is uniform through the day.** Real flow bunches at the London open, the New York overlap and the WM/Reuters 4pm fix, and around month-end. Here every second of the trading day is equally likely.
- **Trades are independent.** No order is split, no client trades in bursts, and no two clients react to the same news.
- **Cover is immediate and complete.** Each trade is hedged on its own, at its own minute's mid plus slippage plus the tier half-spread. There is no internalisation (netting a client BUY against another client's SELL), no warehousing and no market risk carried overnight. A real desk nets a large part of its flow, so a smaller share of it ever goes to the market and pays the interbank half-spread. Netting does not make any one cover cheaper; it means fewer trades need one. Here every trade pays it, which overstates the cover cost of a desk that internalises, most of all for the high-turnover, two-way flow of hedge funds and payment institutions.
- **Cover cost does not depend on size or time of day.** A EUR 10m hedge fund clip costs the same in bps to cover as a EUR 100k one, and the half-spread is the same at 07:00 as at the London close and on a quiet day as in a sell-off. It varies only by the pair's liquidity tier (EURTRY pays more than EURUSD), and the slippage only by segment. Adverse selection lives only in the slippage; clients do not trade ahead of the mid's direction.
- **Prices do not react to the market.** The markup does not widen in volatile hours or for illiquid times of day.
- **Compression is smooth.** Real repricing comes in steps (an annual review, a lost tender, a new platform). Here it is a steady exponential from the start of the window.

### 3.7 Reproducibility and performance

`generate_trades` uses only the `numpy.random.Generator` it is given, after `generate_clients` has used it, and draws in a fixed order:

1. One mid path per pair, in sorted pair-code order (EURAUD first, EURZAR last), over every ECB business day in the snapshot including the buffer days, trimmed to the span from the first fix to the last.
2. The daily trade counts: one Poisson draw per active client-day (a business day in the window on or after the client's onboarding date), clients in book order. The draws are made as one matrix over every client and day, but a day before onboarding has a mean of zero, and numpy's `rng.poisson(0)` consumes nothing from the stream.
3. Per trade, one vectorised call each, in this order: second of the trading day, pair, side, ticket, markup noise, cover slippage.

Trades are then sorted by timestamp, then `client_id`, then the order they were drawn in, and numbered `T0000001` onwards in that order, so the same configuration, snapshot and seed give a byte-identical table on the same Docker image and CPU architecture (floating-point results can differ in the last bit across architectures or library builds). A test checks this by hashing two generated frames. The cover half-spread is a lookup, not a draw, so it is outside this list.

There is no Python loop over trades: every draw and every pricing step is a numpy operation over the whole book (the only loops are over the 15 pairs and the few distinct price precisions). The full 298,178-trade book takes about 0.3 seconds to generate in the pipeline container, mid paths included.
