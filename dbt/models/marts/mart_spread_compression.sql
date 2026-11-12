{#-
  Spread compression (metric 11): realised gross margin in bps by month, segment and currency
  pair, with the trend slope per segment x pair. Formulas, the choice of an unweighted
  regression and the desk reading: docs/methodology/metrics.md section 7.

  Grain: month x segment x currency_pair, for months in which that segment traded that pair.

      gross_margin_bps = Σ gross_pnl_eur / Σ notional_eur x 10,000      (ratio of sums)
      compression_slope_bps_per_year = regr_slope(gross_margin_bps, month_index) x 12

  month_index counts whole months from the window start (int_window), so a month with no trades
  leaves a gap in the index instead of closing it up.

  Minimum sample: a segment x pair with trades in fewer than 6 distinct months is dropped, so the
  slope is always defined and never rests on a handful of points. The threshold is
  min_months below.
-#}

{% set min_months = 6 %}

with window_bounds as (

    select window_start from {{ ref('int_window') }}

),

monthly as (

    select
        trades.trade_month as month,
        trades.segment,
        trades.currency_pair,
        (
            (extract(year from trades.trade_month) - extract(year from window_bounds.window_start)) * 12
            + extract(month from trades.trade_month) - extract(month from window_bounds.window_start)
        )::integer as month_index,
        count(*)::bigint as trade_count,
        sum(trades.notional_eur) as volume_eur,
        sum(trades.gross_pnl_eur) as revenue_eur
    from {{ ref('int_trades_enriched') }} as trades
    cross join window_bounds
    group by trades.trade_month, trades.segment, trades.currency_pair, window_bounds.window_start

),

with_margin as (

    select
        *,
        revenue_eur / nullif(volume_eur, 0) * 10000 as gross_margin_bps
    from monthly

),

with_slope as (

    select
        *,
        count(*) over per_cell as n_months,
        regr_slope(gross_margin_bps, month_index) over per_cell::numeric * 12
            as compression_slope_bps_per_year
    from with_margin
    window per_cell as (partition by segment, currency_pair)

)

select
    month,
    segment,
    currency_pair,
    month_index,
    n_months,
    trade_count,
    volume_eur,
    revenue_eur,
    gross_margin_bps,
    compression_slope_bps_per_year
from with_slope
where n_months >= {{ min_months }}
