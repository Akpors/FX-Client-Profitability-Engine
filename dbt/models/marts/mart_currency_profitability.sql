{#-
  One row per currency pair in the pair reference (metrics 1-3, 5, 10 by pair). Formulas:
  docs/methodology/metrics.md section 4.

  Contribution here is before coverage cost. Coverage is the cost of the client relationship
  (sales time), incurred per client per month whether the client trades one pair or five; it has
  no currency, and any split by pair (by volume, by tickets) would be an allocation choice that
  moves profit between pairs without telling the desk anything about the pairs. Per-trade costs
  (ops, capital) do belong to the trade and so to its pair.

  A pair with no trades keeps its row with zero amounts and NULL margins.
-#}

with pair_totals as (

    select
        currency_pair,
        count(*) as trade_count,
        sum(notional_eur) as volume_eur,
        sum(gross_pnl_eur) as revenue_eur,
        -- Volume-weighted, as for clients.
        sum(benchmark_spread_bps * notional_eur) / nullif(sum(notional_eur), 0)
            as avg_benchmark_spread_bps,
        sum(cover_slippage_bps * notional_eur) / nullif(sum(notional_eur), 0)
            as avg_cover_slippage_bps,
        sum(ops_cost_eur) as ops_cost_eur,
        sum(capital_charge_eur) as capital_charge_eur
    from {{ ref('fct_trades') }}
    group by currency_pair

),

pairs as (

    select
        pairs.currency_pair,
        pairs.quote_ccy,
        pairs.liquidity_tier,
        coalesce(pair_totals.trade_count, 0) as trade_count,
        coalesce(pair_totals.volume_eur, 0) as volume_eur,
        coalesce(pair_totals.revenue_eur, 0) as revenue_eur,
        pair_totals.avg_benchmark_spread_bps,
        pair_totals.avg_cover_slippage_bps,
        coalesce(pair_totals.ops_cost_eur, 0) as ops_cost_eur,
        coalesce(pair_totals.capital_charge_eur, 0) as capital_charge_eur
    from {{ ref('stg_pairs') }} as pairs
    left join pair_totals
        on pairs.currency_pair = pair_totals.currency_pair

)

select
    currency_pair,
    quote_ccy,
    liquidity_tier,
    trade_count,
    volume_eur,
    revenue_eur,
    revenue_eur / nullif(volume_eur, 0) * 10000 as gross_margin_bps,
    avg_benchmark_spread_bps,
    avg_cover_slippage_bps,
    ops_cost_eur,
    capital_charge_eur,
    revenue_eur - ops_cost_eur - capital_charge_eur as contribution_ex_coverage_eur,
    (revenue_eur - ops_cost_eur - capital_charge_eur) / nullif(volume_eur, 0) * 10000
        as margin_ex_coverage_bps
from pairs
