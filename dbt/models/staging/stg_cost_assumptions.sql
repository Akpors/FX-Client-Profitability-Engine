select
    segment::text as segment,
    ops_cost_per_ticket_eur::numeric as ops_cost_per_ticket_eur,
    coverage_cost_eur_per_month::numeric as coverage_cost_eur_per_month,
    credit_weight::numeric as credit_weight
from {{ source('raw', 'cost_assumptions') }}
