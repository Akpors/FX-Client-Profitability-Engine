{#-
  The analysis window, as whole calendar months: one row.

      window_start = first day of the month of the first trade
      window_end   = last day of the month of the last trade

  raw holds no window rows, so the window is read off the trades. The generator books trades on
  every ECB fixing day of the 24 complete months before the build date, so this recovers that
  window exactly. This is the only place the window is defined: int_client_month and the
  headline KPIs read it from here. With no trades both columns are NULL and every client-month
  roll-up is empty.
-#}
select
    date_trunc('month', min(trade_date))::date as window_start,
    (date_trunc('month', max(trade_date)) + interval '1 month - 1 day')::date as window_end
from {{ ref('int_trades_enriched') }}
