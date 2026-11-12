select
    trade_id::text as trade_id,
    client_id::text as client_id,
    trade_ts::timestamptz as trade_ts,
    trade_date::date as trade_date,
    currency_pair::text as currency_pair,
    client_side::text as client_side,
    notional_base::numeric as notional_base,
    client_rate::numeric as client_rate,
    cover_rate::numeric as cover_rate,
    mid_rate::numeric as mid_rate,
    benchmark_rate::numeric as benchmark_rate,
    markup_bps_applied::double precision as markup_bps_applied
from {{ source('raw', 'trades') }}
