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
