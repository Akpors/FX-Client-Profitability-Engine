{{ config(materialized='view') }}

{#-
  The trade table in the original brief's layout:
  client | trade | currency_pair | buy_rate | sell_rate | benchmark_rate | volume | timestamp | pnl

  buy_rate and sell_rate are the dealer's two legs in EUR terms. On a client BUY the dealer buys
  EUR in the market (cover_rate) and sells it to the client (client_rate). On a client SELL it
  buys EUR from the client (client_rate) and sells it in the market (cover_rate). Either way
  (sell_rate − buy_rate) × volume is the dealer's quote-currency gross P&L, and pnl is that
  amount converted to EUR (gross_pnl_eur). client is the client name, which reads better in the
  brief's table than the ID; fct_trades carries client_id.
-#}
select
    clients.client_name as client,
    trades.trade_id as trade,
    trades.currency_pair,
    case trades.client_side
        when 'BUY' then trades.cover_rate
        when 'SELL' then trades.client_rate
    end as buy_rate,
    case trades.client_side
        when 'BUY' then trades.client_rate
        when 'SELL' then trades.cover_rate
    end as sell_rate,
    trades.benchmark_rate,
    trades.notional_eur as volume,
    trades.trade_ts as "timestamp",
    trades.gross_pnl_eur as pnl
from {{ ref('fct_trades') }} as trades
left join {{ ref('dim_clients') }} as clients
    on trades.client_id = clients.client_id
