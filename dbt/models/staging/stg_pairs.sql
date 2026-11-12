select
    currency_pair::text as currency_pair,
    base_ccy::text as base_ccy,
    quote_ccy::text as quote_ccy,
    liquidity_tier::text as liquidity_tier,
    price_decimals::integer as price_decimals,
    cls_settled::boolean as cls_settled,
    settlement_weight::numeric as settlement_weight
from {{ source('raw', 'pairs') }}
