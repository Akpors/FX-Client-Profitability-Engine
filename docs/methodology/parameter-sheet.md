# Calibration parameter sheet
> **Illustrative defaults, pending owner calibration** (spec section 11.1), not market data or any firm's.
> Fill the Your value cells on a copy; the builder applies them to `generator.yaml` until you approve.

## 1. Segment parameters
Markup is base bps from mid, before bands, tiers, legacy deals, compression and noise.
Compression: markup x e^(-rate x years). Adverse selection: cover vs mid, + hurts dealer.

| Segment parameter | Value | Large corporate | Mid-market | SME | Asset manager | Hedge fund | Payment institution |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Markup, major: USD GBP JPY CHF (bps) | Default | 3.5 | 6.0 | 12.0 | 2.0 | 1.0 | 3.0 |
|  | Your value |  |  |  |  |  |  |
| Markup, minor: SEK NOK DKK AUD CAD (bps) | Default | 6.0 | 10.0 | 19.0 | 3.5 | 1.8 | 5.5 |
|  | Your value |  |  |  |  |  |  |
| Markup, CEE: PLN CZK HUF (bps) | Default | 11.0 | 18.0 | 32.0 | 7.0 | 3.5 | 10.0 |
|  | Your value |  |  |  |  |  |  |
| Markup, EM: ZAR TRY MXN (bps) | Default | 18.0 | 30.0 | 48.0 | 12.0 | 6.0 | 17.0 |
|  | Your value |  |  |  |  |  |  |
| Compression per year | Default | 0.05 | 0.03 | 0.00 | 0.06 | 0.18 | 0.12 |
|  | Your value |  |  |  |  |  |  |
| Adverse selection mean/sd (bps) | Default | 0.10/0.40 | 0.05/0.30 | 0.02/0.20 | 0.20/0.50 | 0.30/0.80 | 0.05/0.30 |
|  | Your value |  |  |  |  |  |  |
| Ops cost per ticket (EUR) | Default | 9.0 | 12.0 | 15.0 | 10.0 | 2.5 | 1.8 |
|  | Your value |  |  |  |  |  |  |
| Coverage cost per client per month (EUR) | Default | 3,500 | 1,200 | 200 | 3,500 | 3,000 | 1,200 |
|  | Your value |  |  |  |  |  |  |
| Credit weight | Default | 0.80 | 1.00 | 1.50 | 0.60 | 1.25 | 1.25 |
|  | Your value |  |  |  |  |  |  |
| Negotiation sigma (client price scatter) | Default | 0.30 | 0.25 | 0.20 | 0.20 | 0.20 | 0.20 |
|  | Your value |  |  |  |  |  |  |

- **Markup:** Wider as liquidity thins (cover cost, inventory risk); hedge funds tightest, SMEs widest
- **Compression:** None for SME rate cards, slow for corporates and asset managers, fast for shopped flow
- **Adverse selection:** Informed fund flow costs most, then asset managers; the rest barely moves cover
- **Ops cost:** Highest for SMEs and mid-market (manual handling, queries); lowest for automated flow
- **Coverage:** Own banker for corporates, asset managers, funds; shared for mid-market; little for SMEs
- **Credit weight:** 1.0 is the average counterparty: real money and big corporates below, SMEs highest
- **Negotiation sigma:** Each client's own price level: widest for bilateral deals, narrow for rate cards
## 2. Ticket bands and volume tiers
| Ticket band | Value | Under 25k | 25k-100k | 100k-500k | 500k-2m | 2m-10m | 10m+ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Upper edge (EUR) | Default | 25k | 100k | 500k | 2m | 10m | none |
|  | Your value |  |  |  |  |  |  |
| Markup multiplier | Default | 1.80 | 1.35 | 1.00 | 0.80 | 0.65 | 0.55 |
|  | Your value |  |  |  |  |  |  |

| Volume tier | Value | Tier 1 | Tier 2 | Tier 3 | Tier 4 | Tier 5 | Tier 6 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Annual volume from (EUR) | Default | 0 | 25m | 100m | 500m | 2bn | 5bn |
|  | Your value |  |  |  |  |  |  |
| Discount off markup | Default | 0% | 5% | 12% | 20% | 30% | 40% |
|  | Your value |  |  |  |  |  |  |

- **Band edges:** Round sizes where handling cost and the way a client shops the price change
- **Band multipliers:** Bigger tickets are cheaper to handle and shopped harder, so bands price tighter
- **Tier thresholds:** Spaced so each step is a clearly bigger relationship, small client to top flow
- **Tier discounts:** Bigger relationships negotiate harder and rivals bid for them, so discounts rise
## 3. Cover, capital, hurdle and legacy deals
| Parameter | Default | Your value | Why |
| --- | --- | --- | --- |
| Cover half-spread, major/minor/CEE/EM (bps) | 0.1/0.4/1.0/2.5 |  | Crossing the interbank touch to cover; wider in thin pairs |
| Capital charge (bps) | 0.10 |  | Internal settlement-risk and credit-line charge, not regulatory capital |
| Settlement weight, CLS | 1.0 |  | Payment-versus-payment through CLS removes principal risk: the baseline |
| Settlement weight, non-CLS (PLN CZK TRY) | 2.5 |  | Bilateral settlement leaves principal at risk: a higher weight |
| Hurdle rate (bps of volume) | 0.75 |  | Illustrative builder default, chosen before any trades; owner sets it independently |
| Legacy deal probability | 20% |  | Old discounts are rarely repriced, so some clients onboarded earlier still hold one |
| Legacy deal discount | 25% |  | A concession from winning the mandate years ago, stacking on the volume tier |

Regenerate with `make param-sheet`; do not edit by hand. Source: `generator.yaml` and `rationale.yaml`.
Client mix, trade frequency, ticket sizes, pair mix and noise are not calibrated here.
