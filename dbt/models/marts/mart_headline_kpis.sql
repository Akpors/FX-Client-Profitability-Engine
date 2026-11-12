{#-
  The headline numbers of the book over the window: one row. Formulas and the desk reading:
  docs/methodology/metrics.md section 9.

  Totals are sums of mart_client_profitability, so they reconcile with the client mart and, through
  the reconciliation tests, with the trades. total_contribution_eur therefore includes the coverage
  cost of clients that did not trade. client_count counts only clients with at least one trade.
  The window comes from int_window, the single definition of it, and the hurdle rate is read
  from the parameters by name.
-#}

with client_totals as (

    select
        sum(volume_eur) as total_volume_eur,
        sum(revenue_eur) as total_revenue_eur,
        sum(economic_contribution_eur) as total_contribution_eur,
        count(*) filter (where trade_count > 0)::bigint as client_count,
        sum(trade_count)::bigint as trade_count
    from {{ ref('mart_client_profitability') }}

),

window_bounds as (

    select window_start, window_end from {{ ref('int_window') }}

),

-- An aggregate, so this is always exactly one row: a missing parameter gives NULL (caught by the
-- not_null test) instead of a cross join that returns no row at all.
hurdle as (

    select max(value) as hurdle_rate_bps
    from {{ ref('stg_parameters') }}
    where name = 'hurdle_rate_bps'

)

select
    client_totals.total_volume_eur,
    client_totals.total_revenue_eur,
    client_totals.total_contribution_eur,
    client_totals.total_revenue_eur / nullif(client_totals.total_volume_eur, 0) * 10000
        as gross_margin_bps,
    client_totals.total_contribution_eur / nullif(client_totals.total_volume_eur, 0) * 10000
        as economic_margin_bps,
    client_totals.client_count,
    client_totals.trade_count,
    window_bounds.window_start,
    window_bounds.window_end,
    hurdle.hurdle_rate_bps
from client_totals
cross join window_bounds
cross join hurdle
