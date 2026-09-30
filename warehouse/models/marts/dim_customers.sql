with customers as (
    select * from {{ ref('stg_customers') }}
),

orders as (
    select * from {{ ref('stg_orders') }}
),

customer_orders as (
    select
        customer_id,
        min(ordered_at) as first_ordered_at,
        max(ordered_at) as last_ordered_at,
        count(*) as lifetime_orders
    from orders
    group by customer_id
),

final as (
    select
        customers.customer_id,
        customers.customer_name,
        customer_orders.first_ordered_at,
        customer_orders.last_ordered_at,
        coalesce(customer_orders.lifetime_orders, 0) as lifetime_orders
    from customers
    left join customer_orders
        on customers.customer_id = customer_orders.customer_id
)

select * from final
