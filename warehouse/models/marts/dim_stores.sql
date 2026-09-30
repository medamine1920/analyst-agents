select
    store_id,
    store_name,
    tax_rate,
    opened_at
from {{ ref('stg_stores') }}
