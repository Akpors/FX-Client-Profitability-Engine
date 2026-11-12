{#-
  Each client's share of volume, revenue and economic contribution is its value divided by the
  book total, so each set of shares must sum to exactly 1. Returns the share columns whose sum is
  NULL or more than 1e-9 away from 1; 0 rows is a pass.

  Summed as numeric (exact decimal). The mart computes the shares as numeric too, so the sum is
  1 to far better than 1e-9; in float, rounding across 200 terms would make the check fragile.
  A NULL sum fails as well: it would mean a NULL or zero book total.

  Individual contribution shares can be negative (a client that costs more than it earns), and
  others can then exceed 1 between them; the sum is still 1.
-#}
with sums as (

    select
        sum(volume_share::numeric) as volume_share_sum,
        sum(revenue_share::numeric) as revenue_share_sum,
        sum(contribution_share::numeric) as contribution_share_sum
    from {{ ref('mart_client_profitability') }}

),

unpivoted as (

    select 'volume_share' as share, volume_share_sum as share_sum from sums
    union all
    select 'revenue_share' as share, revenue_share_sum as share_sum from sums
    union all
    select 'contribution_share' as share, contribution_share_sum as share_sum from sums

)

select share, share_sum
from unpivoted
where share_sum is null
    or abs(share_sum - 1) > 1e-9
