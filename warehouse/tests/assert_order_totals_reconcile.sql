with staged as (
    select sum(order_total) as total from {{ ref('stg_orders') }}
),

modeled as (
    select sum(order_total) as total from {{ ref('fct_orders') }}
)

select
    staged.total as staged_total,
    modeled.total as modeled_total
from staged
cross join modeled
where staged.total != modeled.total
