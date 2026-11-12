select
    name::text as name,
    value::numeric as value,
    description::text as description
from {{ source('raw', 'parameters') }}
