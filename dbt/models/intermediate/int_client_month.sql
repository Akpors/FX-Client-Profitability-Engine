{{ config(materialized='table') }}

{#-
  One row per client per calendar month in which the client was on the book: from
  greatest(onboarding month, window start) to the window end month. Months with no trades are
  kept, with zero counts and amounts and the full coverage cost, because the relationship costs
  the desk sales time whether or not the client trades that month. A client onboarded after the
  window end has no rows.

      economic_contribution_eur = revenue_eur − ops_cost_eur − capital_charge_eur − coverage_cost_eur

  Formulas: docs/methodology/metrics.md section 2.
-#}

with window_bounds as (

    select window_start, window_end from {{ ref('int_window') }}

),

clients as (

    select
        clients.client_id,
        clients.segment,
        clients.onboarded_date,
        cost_assumptions.coverage_cost_eur_per_month
    from {{ ref('stg_clients') }} as clients
    left join {{ ref('stg_cost_assumptions') }} as cost_assumptions
        on clients.segment = cost_assumptions.segment

),

client_months as (

    select
        clients.client_id,
        clients.segment,
        clients.coverage_cost_eur_per_month,
        series.month_ts::date as month
    from clients
    cross join window_bounds
    -- Explicit timestamp casts: generate_series has no date variant, and the timestamptz one
    -- would make month arithmetic depend on the session time zone.
    cross join lateral generate_series(
        greatest(date_trunc('month', clients.onboarded_date), window_bounds.window_start)::timestamp,
        date_trunc('month', window_bounds.window_end)::timestamp,
        interval '1 month'
    ) as series (month_ts)

),

trade_months as (

    select
        client_id,
        trade_month as month,
        count(*) as trade_count,
        sum(notional_eur) as volume_eur,
        sum(gross_pnl_eur) as revenue_eur,
        sum(ops_cost_eur) as ops_cost_eur,
        sum(capital_charge_eur) as capital_charge_eur
    from {{ ref('int_trades_enriched') }}
    group by client_id, trade_month

),

joined as (

    select
        client_months.client_id,
        client_months.month,
        client_months.segment,
        coalesce(trade_months.trade_count, 0) as trade_count,
        coalesce(trade_months.volume_eur, 0) as volume_eur,
        coalesce(trade_months.revenue_eur, 0) as revenue_eur,
        coalesce(trade_months.ops_cost_eur, 0) as ops_cost_eur,
        coalesce(trade_months.capital_charge_eur, 0) as capital_charge_eur,
        client_months.coverage_cost_eur_per_month as coverage_cost_eur
    from client_months
    -- A trade in a month the client was not on the book (before onboarding) would be dropped
    -- here; assert_volume_reconciles compares this model's volume with fct_trades to catch it.
    left join trade_months
        on client_months.client_id = trade_months.client_id
        and client_months.month = trade_months.month

)

select
    *,
    revenue_eur - ops_cost_eur - capital_charge_eur - coverage_cost_eur as economic_contribution_eur
from joined
