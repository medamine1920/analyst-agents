with source as (
    select * from {{ ref('raw_orders') }}
),

renamed as (
    select
        id as order_id,
        customer as customer_id,
        store_id,
        cast(ordered_at as timestamp) as ordered_at,
        cast(subtotal / 100.0 as decimal(16, 2)) as subtotal,
        cast(tax_paid / 100.0 as decimal(16, 2)) as tax_paid,
        cast(order_total / 100.0 as decimal(16, 2)) as order_total
    from source
)

select * from renamed
