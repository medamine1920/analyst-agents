with orders as (
    select * from {{ ref('stg_orders') }}
),

order_items as (
    select * from {{ ref('stg_order_items') }}
),

-- Collapse items to one row per order BEFORE joining,
-- so the join can never multiply order rows (no fan-out).
items_per_order as (
    select
        order_id,
        count(*) as item_count
    from order_items
    group by order_id
),

final as (
    select
        orders.order_id,
        orders.customer_id,
        orders.store_id,
        orders.ordered_at,
        orders.subtotal,
        orders.tax_paid,
        orders.order_total,
        coalesce(items_per_order.item_count, 0) as item_count,
        row_number() over (
            partition by orders.customer_id
            order by orders.ordered_at, orders.order_id
        ) = 1 as is_first_order
    from orders
    left join items_per_order
        on orders.order_id = items_per_order.order_id
)

select * from final
