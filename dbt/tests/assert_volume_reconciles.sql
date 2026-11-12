{#-
  Volume must survive every roll-up. Returns a row for each mart whose total volume is more
  than EUR 0.01 away from the trade-level total in fct_trades; 0 rows is a pass.

  The client mart is the brief's check. The currency mart is checked too, because it groups the
  same trades by a different key and a lost join would show up as missing volume.
-#}
with trade_total as (

    select coalesce(sum(notional_eur), 0) as volume_eur
    from {{ ref('fct_trades') }}

),

mart_totals as (

    select 'mart_client_profitability' as mart, coalesce(sum(volume_eur), 0) as volume_eur
    from {{ ref('mart_client_profitability') }}

    union all

    select 'int_client_month' as mart, coalesce(sum(volume_eur), 0) as volume_eur
    from {{ ref('int_client_month') }}

    union all

    select 'mart_currency_profitability' as mart, coalesce(sum(volume_eur), 0) as volume_eur
    from {{ ref('mart_currency_profitability') }}

)

select
    mart_totals.mart,
    mart_totals.volume_eur as mart_volume_eur,
    trade_total.volume_eur as trade_volume_eur,
    mart_totals.volume_eur - trade_total.volume_eur as difference_eur
from mart_totals
cross join trade_total
where abs(mart_totals.volume_eur - trade_total.volume_eur) > 0.01
