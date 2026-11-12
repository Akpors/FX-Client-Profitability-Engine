select
    client_id::text as client_id,
    client_name::text as client_name,
    segment::text as segment,
    home_country::text as home_country,
    onboarded_date::date as onboarded_date,
    legacy_deal::boolean as legacy_deal,
    trades_per_month::double precision as trades_per_month,
    ticket_mu::double precision as ticket_mu,
    ticket_sigma::double precision as ticket_sigma,
    expected_annual_volume_eur::double precision as expected_annual_volume_eur,
    volume_tier_discount_pct::double precision as volume_tier_discount_pct,
    negotiation_factor::double precision as negotiation_factor
from {{ source('raw', 'clients') }}
