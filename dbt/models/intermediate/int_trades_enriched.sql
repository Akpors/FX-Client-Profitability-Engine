{{ config(materialized='table') }}

{#-
  One row per trade, with the economics every later model builds on. Formulas and worked
  examples: docs/methodology/metrics.md (trade-level section).

  Sign convention: side_sign is +1 when the client BUYs EUR and -1 when the client SELLs EUR.
  The dealer is always on the other side. Everything is numeric (exact decimal), so P&L is not
  subject to binary float error; bps are value x 10,000.
-#}

with trades as (

    select * from {{ ref('stg_trades') }}

),

clients as (

    select client_id, segment from {{ ref('stg_clients') }}

),

pairs as (

    select currency_pair, liquidity_tier, settlement_weight from {{ ref('stg_pairs') }}

),

ticket_bands as (

    select label, min_ticket_eur, max_ticket_eur from {{ ref('stg_ticket_bands') }}

),

cost_assumptions as (

    select segment, ops_cost_per_ticket_eur, credit_weight from {{ ref('stg_cost_assumptions') }}

),

-- An aggregate, so this is always exactly one row: a missing parameter gives NULL (caught by
-- the not_null test on capital_charge_eur) instead of a cross join that silently drops every trade.
capital as (

    select max(value) as capital_charge_bps
    from {{ ref('stg_parameters') }}
    where name = 'capital_charge_bps'

),

joined as (

    select
        trades.*,
        case trades.client_side when 'BUY' then 1 when 'SELL' then -1 end as side_sign,
        -- Every pair is EUR-based, so the base-currency notional is the EUR notional.
        trades.notional_base as notional_eur,
        date_trunc('month', trades.trade_date)::date as trade_month,
        clients.segment,
        pairs.liquidity_tier,
        ticket_bands.label as ticket_band,
        cost_assumptions.ops_cost_per_ticket_eur as ops_cost_eur,
        cost_assumptions.credit_weight,
        pairs.settlement_weight,
        capital.capital_charge_bps
    from trades
    left join clients
        on trades.client_id = clients.client_id
    left join pairs
        on trades.currency_pair = pairs.currency_pair
    -- Bands are [min, max): a notional on a boundary belongs to the band that starts there.
    left join ticket_bands
        on trades.notional_base >= ticket_bands.min_ticket_eur
        and (ticket_bands.max_ticket_eur is null or trades.notional_base < ticket_bands.max_ticket_eur)
    left join cost_assumptions
        on clients.segment = cost_assumptions.segment
    cross join capital

),

priced as (

    select
        *,
        -- Dealer P&L in quote currency: positive when the dealer earns the spread.
        side_sign * (client_rate - cover_rate) * notional_base as gross_pnl_quote
    from joined

)

select
    trade_id,
    client_id,
    trade_ts,
    trade_date,
    currency_pair,
    client_side,
    notional_base,
    client_rate,
    cover_rate,
    mid_rate,
    benchmark_rate,
    markup_bps_applied,

    side_sign,
    notional_eur,
    trade_month,
    segment,
    liquidity_tier,
    ticket_band,

    gross_pnl_quote,
    -- Converted at the same-day ECB fix (quote currency per 1 EUR).
    gross_pnl_quote / benchmark_rate as gross_pnl_eur,
    gross_pnl_quote / benchmark_rate / notional_eur * 10000 as gross_margin_bps,
    -- Positive: the client paid away from the ECB fix, in the dealer's favour.
    side_sign * (client_rate - benchmark_rate) / benchmark_rate * 10000 as benchmark_spread_bps,
    -- Positive: the dealer covered worse than mid.
    side_sign * (cover_rate - mid_rate) / mid_rate * 10000 as cover_slippage_bps,

    ops_cost_eur,
    notional_eur * capital_charge_bps / 10000 * credit_weight * settlement_weight as capital_charge_eur
from priced
