{#-
  One row per client in the book. Business attributes only: the generator's latent behaviour
  parameters (trades_per_month, ticket_mu, ticket_sigma, expected_annual_volume_eur,
  negotiation_factor) stay in staging, because a desk would not have them as data. Adds the
  first and last trade dates in the window, NULL for a client that has not traded.
-#}
with trade_dates as (

    select
        client_id,
        min(trade_date) as first_trade_date,
        max(trade_date) as last_trade_date
    from {{ ref('fct_trades') }}
    group by client_id

)

select
    clients.client_id,
    clients.client_name,
    clients.segment,
    clients.home_country,
    clients.onboarded_date,
    clients.legacy_deal,
    clients.volume_tier_discount_pct,
    trade_dates.first_trade_date,
    trade_dates.last_trade_date
from {{ ref('stg_clients') }} as clients
left join trade_dates
    on clients.client_id = trade_dates.client_id
