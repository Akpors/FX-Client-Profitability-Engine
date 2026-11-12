select
    rate_date::date as rate_date,
    currency::text as quote_ccy,
    rate::numeric as ecb_rate
from {{ source('raw', 'ecb_rates') }}
