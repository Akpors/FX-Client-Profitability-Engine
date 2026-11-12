{#-
  The synthetic intraday mid is a Brownian bridge between consecutive ECB fixes, so it should
  stay close to the same-day benchmark. Returns the trades whose mid is further from the fix than

      2%  +  |benchmark_rate / prev_fix − 1|

  where prev_fix is the previous ECB fix for the same currency before trade_date. 0 rows is a pass.

  Why the allowance grows with the day's fix-to-fix move. A trade early in the day sits partway
  along the bridge from the previous fix to today's, so on a day when the ECB fix itself jumps
  (TRY on 19 Mar 2025, ZAR and TRY on 3 Apr 2025, AUD on 4 Apr 2025, MXN on 9 Apr 2025: moves of
  2.7-4.2%) the mid is legitimately up to that move away from today's fix. A flat 2% flags those
  genuine trades; adding the day's own move does not, and on a quiet day the check is still
  about 2%. A broken mid path (wrong currency, wrong day, a scaling error) is still caught.
-#}
with fixes as (

    select
        rate_date,
        quote_ccy,
        ecb_rate,
        lag(ecb_rate) over (partition by quote_ccy order by rate_date) as prev_fix
    from {{ ref('stg_ecb_rates') }}

),

trades as (

    select
        trades.trade_id,
        trades.trade_date,
        trades.currency_pair,
        trades.mid_rate,
        trades.benchmark_rate,
        fixes.prev_fix,
        abs(trades.mid_rate / trades.benchmark_rate - 1) as relative_distance,
        -- The first fix in the snapshot has no predecessor: no extra allowance.
        0.02 + coalesce(abs(trades.benchmark_rate / fixes.prev_fix - 1), 0) as allowed_distance
    from {{ ref('int_trades_enriched') }} as trades
    left join {{ ref('stg_pairs') }} as pairs
        on trades.currency_pair = pairs.currency_pair
    left join fixes
        on trades.trade_date = fixes.rate_date
        and pairs.quote_ccy = fixes.quote_ccy

)

select *
from trades
where relative_distance > allowed_distance
