{#-
  Client economic contribution must add up to the book's revenue less every cost. Returns one
  row if the sum over mart_client_profitability is more than EUR 1 away from

      Σ gross_pnl_eur − Σ ops_cost_eur − Σ capital_charge_eur     (from fct_trades, per trade)
    − Σ coverage_cost_eur                                         (from int_client_month)

  0 rows is a pass. Revenue and per-trade costs come from the trade table, not from the
  client-month roll-up, so a trade dropped between the two would fail this test.
-#}
with mart as (

    select coalesce(sum(economic_contribution_eur), 0) as contribution_eur
    from {{ ref('mart_client_profitability') }}

),

trades as (

    select
        coalesce(sum(gross_pnl_eur), 0) as revenue_eur,
        coalesce(sum(ops_cost_eur), 0) as ops_cost_eur,
        coalesce(sum(capital_charge_eur), 0) as capital_charge_eur
    from {{ ref('fct_trades') }}

),

coverage as (

    select coalesce(sum(coverage_cost_eur), 0) as coverage_cost_eur
    from {{ ref('int_client_month') }}

),

expected as (

    select
        trades.revenue_eur - trades.ops_cost_eur - trades.capital_charge_eur
        - coverage.coverage_cost_eur as contribution_eur
    from trades
    cross join coverage

)

select
    mart.contribution_eur as mart_contribution_eur,
    expected.contribution_eur as expected_contribution_eur,
    mart.contribution_eur - expected.contribution_eur as difference_eur
from mart
cross join expected
where abs(mart.contribution_eur - expected.contribution_eur) > 1
