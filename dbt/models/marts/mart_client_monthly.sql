{#-
  Client profitability trends (metric 12): int_client_month with the client's name, the month
  index, margins in bps, a rolling 3-month contribution, the trend slope and a trend flag. One
  row per client per month on the book, zero-trade months included. Formulas and the desk
  reading: docs/methodology/metrics.md section 8.

  month_index counts whole months from the window start (int_window), so a client onboarded
  mid-window starts at its onboarding month rather than at 0, and the slope is in EUR per
  calendar month whatever month the client joined.

  Trend slope: the least-squares slope (regr_slope) of the client's monthly economic
  contribution on month_index, repeated on each of the client's rows. A client with a single
  month has no slope (NULL) and is flagged stable.

  trend_flag: stable when |slope| < 0.05 x the client's mean monthly |contribution|, otherwise
  improving (slope > 0) or deteriorating (slope < 0).

  contribution_rolling_3m_eur is a SUM over the current and the two prior calendar months (not a
  mean), and is NULL until the client has three months of history, so a figure covering fewer
  months is never shown next to full ones.
-#}

with window_bounds as (

    select window_start from {{ ref('int_window') }}

),

client_months as (

    select
        client_month.*,
        dim_clients.client_name,
        (
            (extract(year from client_month.month) - extract(year from window_bounds.window_start)) * 12
            + extract(month from client_month.month) - extract(month from window_bounds.window_start)
        )::integer as month_index
    from {{ ref('int_client_month') }} as client_month
    cross join window_bounds
    left join {{ ref('dim_clients') }} as dim_clients
        on client_month.client_id = dim_clients.client_id

),

windowed as (

    select
        *,
        revenue_eur / nullif(volume_eur, 0) * 10000 as gross_margin_bps,
        economic_contribution_eur / nullif(volume_eur, 0) * 10000 as economic_margin_bps,
        -- RANGE over the month date, not ROWS, so the three months are three calendar months.
        case
            when count(*) over rolling_3m = 3
                then sum(economic_contribution_eur) over rolling_3m
        end as contribution_rolling_3m_eur,
        regr_slope(economic_contribution_eur, month_index) over per_client::numeric
            as trend_slope_eur_per_month,
        avg(abs(economic_contribution_eur)) over per_client as mean_abs_contribution_eur
    from client_months
    window
        per_client as (partition by client_id),
        rolling_3m as (
            partition by client_id
            order by month
            range between interval '2 months' preceding and current row
        )

)

select
    client_id,
    client_name,
    segment,
    month,
    month_index,
    trade_count,
    volume_eur,
    revenue_eur,
    ops_cost_eur,
    capital_charge_eur,
    coverage_cost_eur,
    economic_contribution_eur,
    gross_margin_bps,
    economic_margin_bps,
    contribution_rolling_3m_eur,
    trend_slope_eur_per_month,
    case
        when trend_slope_eur_per_month is null then 'stable'
        when abs(trend_slope_eur_per_month) < 0.05 * mean_abs_contribution_eur then 'stable'
        when trend_slope_eur_per_month > 0 then 'improving'
        when trend_slope_eur_per_month < 0 then 'deteriorating'
        -- A zero slope on a zero mean: nothing is moving.
        else 'stable'
    end as trend_flag
from windowed
