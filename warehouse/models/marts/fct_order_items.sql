with order_items as (
    select * from {{ ref('stg_order_items') }}
),

orders as (
    select * from {{ ref('stg_orders') }}
),

products as (
    select * from {{ ref('stg_products') }}
),

final as (
    select
        order_items.order_item_id,
        order_items.order_id,
        order_items.product_id,
        products.product_type,
        products.product_price,
        orders.customer_id,
        orders.store_id,
        orders.ordered_at
    from order_items
    left join orders
        on order_items.order_id = orders.order_id
    left join products
        on order_items.product_id = products.product_id
)

select * from final
