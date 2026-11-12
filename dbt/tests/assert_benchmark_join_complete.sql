-- Every trade must carry a benchmark. Returns the offending trades, so 0 rows is a pass.
--
-- Two ways to fail: the benchmark_rate is NULL, or it is not the ECB fix for the trade's own
-- date and quote currency (which also catches a trade booked on a date with no fix).
select
    trades.trade_id,
    trades.trade_date,
    trades.currency_pair,
    trades.benchmark_rate,
    fixes.ecb_rate
from {{ ref('int_trades_enriched') }} as trades
left join {{ ref('stg_pairs') }} as pairs
    on trades.currency_pair = pairs.currency_pair
left join {{ ref('stg_ecb_rates') }} as fixes
    on trades.trade_date = fixes.rate_date
    and pairs.quote_ccy = fixes.quote_ccy
where trades.benchmark_rate is null
    or fixes.ecb_rate is null
    or trades.benchmark_rate <> fixes.ecb_rate
