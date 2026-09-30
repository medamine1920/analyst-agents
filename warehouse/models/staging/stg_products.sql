with source as (
    select * from {{ ref('raw_products') }}
),

renamed as (
    select
        sku as product_id,
        name as product_name,
        type as product_type,
        description as product_description,
        cast(price / 100.0 as decimal(16, 2)) as product_price
    from source
)

select * from renamed
