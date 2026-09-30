select
    product_id,
    product_name,
    product_type,
    product_description,
    product_price
from {{ ref('stg_products') }}
