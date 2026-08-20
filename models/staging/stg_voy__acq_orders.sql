with source as (
    select * from {{ source('voy', 'acq_orders') }}
),

-- Defensive dedupe to one row per customer, and bucket missing taxonomy as 'Unknown'
ranked as (
    select
        customer_id,
        coalesce(taxonomy_business_category_group, 'Unknown') as acq_taxonomy,
        row_number() over (
            partition by customer_id
            order by taxonomy_business_category_group
        ) as rn
    from source
)

select
    customer_id,
    acq_taxonomy
from ranked
where rn = 1
