# Calibration parameter sheet

> **Illustrative defaults, pending owner calibration** (spec section 11.1): the builder's plausible starting values for a European spot-FX dealer, not market data and not from any real firm or client. Write your value in the blank column on a copy or printout. The builder applies it to `pipeline/config/generator.yaml` and regenerates this sheet until you approve, then the parameters are frozen.

Book: 200 clients, about 300,000 trades over 24 months, seed 42. Money in EUR.

## 1. Segment parameters

Markup is one-sided, in bps from mid to the client's rate, before the ticket-band multiplier (section 2), volume discount (section 3), legacy deal, compression and noise. Tiers (pairs against EUR): major = USD, GBP, JPY, CHF; minor = SEK, NOK, DKK, AUD, CAD; CEE = PLN, CZK, HUF; EM = ZAR, TRY, MXN.

- **Compression:** markup x e^(-rate x years since the window opened); a rate r cuts it by about 1 - e^-r a year.
- **Adverse selection:** mean / sd of the dealer's cover price against mid, in bps; positive is worse for the dealer.
- **Negotiation sigma:** each client's price factor is LogNormal(0, sigma), median 1.0, fixed for the window.

| Default | Large corporate | Mid-market | SME | Asset manager | Hedge fund | Payment institution |
| --- | --- | --- | --- | --- | --- | --- |
| Markup, major (bps) | 3.5 | 6.0 | 12.0 | 2.0 | 1.0 | 3.0 |
| Markup, minor (bps) | 6.0 | 10.0 | 19.0 | 3.5 | 1.8 | 5.5 |
| Markup, CEE (bps) | 11.0 | 18.0 | 32.0 | 7.0 | 3.5 | 10.0 |
| Markup, EM (bps) | 18.0 | 30.0 | 48.0 | 12.0 | 6.0 | 17.0 |
| Compression per year | 0.05 | 0.03 | 0.00 | 0.06 | 0.18 | 0.12 |
| Adverse selection mean / sd (bps) | 0.10 / 0.40 | 0.05 / 0.30 | 0.02 / 0.20 | 0.20 / 0.50 | 0.30 / 0.80 | 0.05 / 0.30 |
| Ops cost per ticket (EUR) | 9.0 | 12.0 | 15.0 | 10.0 | 2.5 | 1.8 |
| Coverage cost per month (EUR) | 3,500 | 1,200 | 200 | 3,500 | 3,000 | 1,200 |
| Credit weight | 0.80 | 1.00 | 1.50 | 0.60 | 1.25 | 1.25 |
| Negotiation sigma | 0.30 | 0.25 | 0.20 | 0.20 | 0.20 | 0.20 |

| Your value | Large corporate | Mid-market | SME | Asset manager | Hedge fund | Payment institution |
| --- | --- | --- | --- | --- | --- | --- |
| Markup, major (bps) |  |  |  |  |  |  |
| Markup, minor (bps) |  |  |  |  |  |  |
| Markup, CEE (bps) |  |  |  |  |  |  |
| Markup, EM (bps) |  |  |  |  |  |  |
| Compression per year |  |  |  |  |  |  |
| Adverse selection mean / sd (bps) |  |  |  |  |  |  |
| Ops cost per ticket (EUR) |  |  |  |  |  |  |
| Coverage cost per month (EUR) |  |  |  |  |  |  |
| Credit weight |  |  |  |  |  |  |
| Negotiation sigma |  |  |  |  |  |  |

- **Markup, major (bps):** Deepest liquidity and the cheapest cover for the dealer, so the tightest markup in every segment
- **Markup, minor (bps):** Good liquidity but wider cover spreads and less natural flow to net against than the majors
- **Markup, CEE (bps):** Thin interbank liquidity and wide cover spreads, so well above the G10 pairs
- **Markup, EM (bps):** The thinnest liquidity, wide and jumpy cover spreads and the most inventory risk: the widest tier
- **Markup by segment:** Tightest for hedge funds and asset managers (competitive panels), then platforms and large corporates, then mid-market; SMEs pay the rate card
- **Compression per year:** None for rate-card SMEs; slow for corporates and asset managers; fastest for hedge funds and platforms, whose flow is shopped across banks
- **Adverse selection mean / sd (bps):** Highest for informed hedge fund flow, then asset managers' large clips; the rest hedge real needs, so cover lands near mid
- **Ops cost per ticket (EUR):** Highest for SMEs and corporates (manual confirmations, queries); lowest for automated, straight-through hedge funds and platforms
- **Coverage cost per month (EUR):** Dedicated bankers for large corporates, asset managers and hedge funds; shared for mid-market and platforms; almost none for SMEs
- **Credit weight:** 1.0 is the average counterparty: real money and large corporates below it, leveraged funds and platforms above, SMEs highest
- **Negotiation sigma:** Widest for bilateral large-corporate deals; narrower where a rate card or panel competition keeps similar clients' prices close together

## 2. Ticket-size bands

A multiplier on the base markup. A band includes its lower edge and excludes its upper edge; the edges are set in `generator.yaml`.

| Ticket band | Default multiplier | Your value | Why |
| --- | --- | --- | --- |
| Under 25k | 1.80 |  | The smallest tickets cost the most to handle, so they carry the widest markup |
| 25k-100k | 1.35 |  | Still small enough that the price is a rate card, not a negotiation |
| 100k-500k | 1.00 |  | Reference band: the base markups in section 1 are stated for this size |
| 500k-2m | 0.80 |  | Size starts to buy a better price: a real concession for the flow |
| 2m-10m | 0.65 |  | Large clips are shopped across banks and priced near the cost of cover |
| 10m+ | 0.55 |  | Block size: the tightest price on the grid, though still above zero |

## 3. Volume-discount tiers

A client gets the discount of the highest tier its expected annual volume reaches: a share taken off the markup, stacking with a legacy deal.

| Volume tier | Annual volume from | Default discount | Your value | Why |
| --- | --- | --- | --- | --- |
| Tier 1 | EUR 0 | 0% |  | Everyone starts here: no volume discount below the first threshold |
| Tier 2 | EUR 25m | 5% |  | A first relationship discount once the client is worth a coverage conversation |
| Tier 3 | EUR 100m | 12% |  | Mid-sized flow is worth competing for, so the discount steps up |
| Tier 4 | EUR 500m | 20% |  | Large active clients get a real concession for keeping the flow |
| Tier 5 | EUR 2bn | 30% |  | Strategic relationships: heavy discounting to hold flow on a competitive panel |
| Tier 6 | EUR 5bn | 40% |  | The biggest flow in the book: rival banks always bid for it, so the deepest discount |

## 4. Capital, hurdle and legacy deals

Capital charge per trade = notional x capital charge bps / 10,000 x credit weight x settlement weight. Non-CLS pairs: EURPLN, EURCZK, EURTRY. The hurdle is the minimum economic margin (bps of volume) for a client to earn its place. A legacy deal is an old discount never repriced; only a client onboarded before the window can hold one.

| Parameter | Default | Your value | Why |
| --- | --- | --- | --- |
| Capital charge (bps of notional) | 0.10 |  | About 2 days' exposure x 8% capital x 12% cost of equity, cut back for CLS flow |
| Settlement weight, CLS-settled pairs | 1.0 |  | Payment-versus-payment through CLS removes principal risk: the baseline |
| Settlement weight, non-CLS pairs | 2.5 |  | Bilateral settlement leaves principal at risk, so it carries a higher weight |
| Hurdle rate (bps of volume) | 0.75 |  | Judgement call: below it, volume does not pay for its own costs and capital |
| Legacy deal probability | 20% |  | Old discounts are rarely repriced: a fifth of pre-window clients still hold one |
| Legacy deal discount | 25% |  | A quarter off: a plausible past concession to win the mandate, on top of tier |

## 5. Pair volatility

| Pair | USD | GBP | JPY | CHF | SEK | NOK | DKK | AUD | CAD | PLN | CZK | HUF | ZAR | TRY | MXN |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Liquidity tier | major | major | major | major | minor | minor | minor | minor | minor | CEE | CEE | CEE | EM | EM | EM |
| Annual vol, default | 7.0% | 6.0% | 9.0% | 5.5% | 7.0% | 8.0% | 0.3% | 8.5% | 7.0% | 5.0% | 4.5% | 9.0% | 14.0% | 16.0% | 12.0% |
| Annual vol, your value |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |

- **Annual vol:** Rough 1-2 year realised level of each EUR cross; DKK is pegged, TRY is diffusion only

---

Regenerate with `make param-sheet`; do not edit by hand. Source: `pipeline/config/generator.yaml` and `pipeline/config/rationale.yaml`. Client mix, trade frequency, ticket-size distribution, pair mix and noise are not calibrated here; they stay in `generator.yaml` and are described in `docs/methodology/data-generation.md`.
