{#-
  Client concentration (metric 8): one row per measure. Formulas, populations and the desk
  reading: docs/methodology/metrics.md section 6.

      share           = client's value / Σ value over the measure's own population
      topN_share      = sum of the N largest shares (the whole population when it has N or fewer)
      hhi             = Σ share², on a 0-1 scale (the antitrust convention multiplies by 10,000)
      n_clients       = size of the population

  Populations:
      volume                 every client with at least one trade
      revenue                every client with positive revenue
      contribution_positive  every client with positive economic contribution

  Revenue and contribution are restricted to positive values because a negative value gives a
  negative share, and then shares no longer lie between 0 and 1: HHI could exceed 1 and a top-N
  share could exceed 100%. Volume cannot be negative, so it takes every client that traded.
  Clients with no value in a measure are simply outside that measure's population, which is why
  n_clients can differ by measure. A measure with an empty population has no row.
-#}

with clients as (

    select
        client_id,
        trade_count,
        volume_eur,
        revenue_eur,
        economic_contribution_eur
    from {{ ref('mart_client_profitability') }}

),

populations as (

    select 'volume'::text as measure, client_id, volume_eur as value
    from clients
    where trade_count > 0

    union all

    select 'revenue'::text as measure, client_id, revenue_eur as value
    from clients
    where revenue_eur > 0

    union all

    select 'contribution_positive'::text as measure, client_id, economic_contribution_eur as value
    from clients
    where economic_contribution_eur > 0

),

shares as (

    select
        measure,
        -- Numeric division: the shares of a measure add back to 1 exactly enough for the HHI bounds.
        value / sum(value) over (partition by measure) as share,
        -- client_id breaks ties, so the top-N set is deterministic.
        row_number() over (partition by measure order by value desc, client_id) as share_rank
    from populations

)

select
    measure,
    count(*)::bigint as n_clients,
    sum(share) filter (where share_rank <= 5) as top5_share,
    sum(share) filter (where share_rank <= 10) as top10_share,
    sum(share) filter (where share_rank <= 20) as top20_share,
    sum(share * share) as hhi
from shares
group by measure
