with source as (
    select * from {{ ref('raw_stores') }}
),

renamed as (
    select
        id as store_id,
        name as store_name,
        tax_rate,
        cast(opened_at as timestamp) as opened_at
    from source
)

select * from renamed
