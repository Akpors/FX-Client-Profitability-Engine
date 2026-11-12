{#-
  One row per client in the book over the full window (metrics 1-7, 9, 10). Formulas, and the
  desk reading of each column: docs/methodology/metrics.md section 3.

  Amounts (volume, revenue, ops, capital, coverage, contribution) are sums of int_client_month,
  so they reconcile with the monthly view and include coverage for zero-trade months. Figures
  that need individual trades (volume-weighted spreads, ticket distribution, per-trade
  contribution, modal band) come from fct_trades.

  Clients with no trades in the window keep their row: zero counts and volume, NULL margins and
  per-trade figures (every per-trade or per-volume ratio divides by nullif(..., 0)). A client
  onboarded after the window end has no client-months, so zero coverage and zero contribution.
-#}

with clients as (

    select client_id, client_name, segment from {{ ref('dim_clients') }}

),

client_totals as (

    select
        client_id,
        sum(trade_count)::bigint as trade_count,
        sum(volume_eur) as volume_eur,
        sum(revenue_eur) as revenue_eur,
        sum(ops_cost_eur) as ops_cost_eur,
        sum(capital_charge_eur) as capital_charge_eur,
        sum(coverage_cost_eur) as coverage_cost_eur,
        sum(economic_contribution_eur) as economic_contribution_eur
    from {{ ref('int_client_month') }}
    group by client_id

),

trades as (

    select
        trades.client_id,
        trades.notional_eur,
        trades.ticket_band,
        trades.benchmark_spread_bps,
        trades.cover_slippage_bps,
        -- Per-trade contribution: the trade's own revenue less its own ops and capital, less an
        -- equal slice of the client's coverage cost over the window. The slices add back to the
        -- client total, so the mean of this column is contribution_per_trade_eur.
        trades.gross_pnl_eur - trades.ops_cost_eur - trades.capital_charge_eur
        - client_totals.coverage_cost_eur / nullif(client_totals.trade_count, 0)
            as contribution_eur
    from {{ ref('fct_trades') }} as trades
    left join client_totals
        on trades.client_id = client_totals.client_id

),

trade_stats as (

    select
        client_id,
        -- Volume-weighted: a EUR 10m ticket counts 1,000 times a EUR 10k ticket.
        sum(benchmark_spread_bps * notional_eur) / nullif(sum(notional_eur), 0)
            as avg_benchmark_spread_bps,
        sum(cover_slippage_bps * notional_eur) / nullif(sum(notional_eur), 0)
            as avg_cover_slippage_bps,
        percentile_cont(0.5) within group (order by notional_eur)::numeric as median_ticket_eur,
        percentile_cont(0.1) within group (order by contribution_eur)::numeric
            as p10_contribution_per_trade_eur,
        percentile_cont(0.5) within group (order by contribution_eur)::numeric
            as median_contribution_per_trade_eur,
        percentile_cont(0.9) within group (order by contribution_eur)::numeric
            as p90_contribution_per_trade_eur
    from trades
    group by client_id

),

-- The band the client trades in most often. Ties go to the band with more volume, then to the
-- larger band (higher min_ticket_eur), so the choice is deterministic.
band_counts as (

    select
        trades.client_id,
        trades.ticket_band,
        count(*) as band_trade_count,
        sum(trades.notional_eur) as band_volume_eur,
        bands.min_ticket_eur
    from trades
    left join {{ ref('stg_ticket_bands') }} as bands
        on trades.ticket_band = bands.label
    group by trades.client_id, trades.ticket_band, bands.min_ticket_eur

),

modal_band as (

    select client_id, ticket_band as modal_ticket_band
    from (
        select
            client_id,
            ticket_band,
            row_number() over (
                partition by client_id
                order by band_trade_count desc, band_volume_eur desc, min_ticket_eur desc
            ) as band_rank
        from band_counts
    ) as ranked
    where band_rank = 1

),

client_metrics as (

    select
        clients.client_id,
        clients.client_name,
        clients.segment,
        coalesce(client_totals.trade_count, 0) as trade_count,
        coalesce(client_totals.volume_eur, 0) as volume_eur,
        coalesce(client_totals.revenue_eur, 0) as revenue_eur,
        coalesce(client_totals.ops_cost_eur, 0) as ops_cost_eur,
        coalesce(client_totals.capital_charge_eur, 0) as capital_charge_eur,
        coalesce(client_totals.coverage_cost_eur, 0) as coverage_cost_eur,
        coalesce(client_totals.economic_contribution_eur, 0) as economic_contribution_eur,
        trade_stats.avg_benchmark_spread_bps,
        trade_stats.avg_cover_slippage_bps,
        trade_stats.median_ticket_eur,
        trade_stats.p10_contribution_per_trade_eur,
        trade_stats.median_contribution_per_trade_eur,
        trade_stats.p90_contribution_per_trade_eur,
        modal_band.modal_ticket_band
    from clients
    left join client_totals
        on clients.client_id = client_totals.client_id
    left join trade_stats
        on clients.client_id = trade_stats.client_id
    left join modal_band
        on clients.client_id = modal_band.client_id

),

with_ratios as (

    select
        *,
        revenue_eur / nullif(volume_eur, 0) * 10000 as gross_margin_bps,
        economic_contribution_eur / nullif(volume_eur, 0) * 10000 as economic_margin_bps,
        economic_contribution_eur / nullif(trade_count, 0) as contribution_per_trade_eur,
        volume_eur / nullif(trade_count, 0) as avg_ticket_eur,
        -- Shares of the book total. Numeric division, so each set sums to 1 exactly enough for
        -- assert_shares_sum_to_one (1e-9). A contribution share is negative for a client that
        -- costs more than it earns.
        volume_eur / nullif(sum(volume_eur) over (), 0) as volume_share,
        revenue_eur / nullif(sum(revenue_eur) over (), 0) as revenue_share,
        economic_contribution_eur / nullif(sum(economic_contribution_eur) over (), 0)
            as contribution_share
    from client_metrics

),

-- Peer group: same segment and same modal ticket band, the client itself included.
-- percentile_cont is an ordered-set aggregate, not a window function, so it is computed per
-- group and joined back.
peers as (

    select
        segment,
        modal_ticket_band,
        percentile_cont(0.5) within group (order by gross_margin_bps)::numeric
            as peer_median_gross_margin_bps,
        -- A group of one benchmarks the client against itself; consumers check this first.
        count(*) as peer_group_size
    from with_ratios
    where modal_ticket_band is not null
    group by segment, modal_ticket_band

)

select
    with_ratios.client_id,
    with_ratios.client_name,
    with_ratios.segment,
    with_ratios.trade_count,
    with_ratios.volume_eur,
    with_ratios.revenue_eur,
    with_ratios.gross_margin_bps,
    with_ratios.avg_benchmark_spread_bps,
    with_ratios.ops_cost_eur,
    with_ratios.capital_charge_eur,
    with_ratios.coverage_cost_eur,
    with_ratios.economic_contribution_eur,
    with_ratios.economic_margin_bps,
    with_ratios.contribution_per_trade_eur,
    with_ratios.p10_contribution_per_trade_eur,
    with_ratios.median_contribution_per_trade_eur,
    with_ratios.p90_contribution_per_trade_eur,
    with_ratios.avg_ticket_eur,
    with_ratios.median_ticket_eur,
    with_ratios.modal_ticket_band,
    with_ratios.volume_share,
    with_ratios.revenue_share,
    with_ratios.contribution_share,
    with_ratios.volume_share - with_ratios.contribution_share as share_gap,
    with_ratios.avg_cover_slippage_bps,
    peers.peer_median_gross_margin_bps,
    peers.peer_group_size
from with_ratios
left join peers
    on with_ratios.segment = peers.segment
    and with_ratios.modal_ticket_band = peers.modal_ticket_band
