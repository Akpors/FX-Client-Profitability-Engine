select
    label::text as label,
    min_ticket_eur::numeric as min_ticket_eur,
    max_ticket_eur::numeric as max_ticket_eur,
    markup_multiplier::numeric as markup_multiplier
from {{ source('raw', 'ticket_bands') }}
