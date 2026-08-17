with source as (
    select * from {{ source('voy', 'customers') }}
)

select
    customer_id,
    customer_country as country
from source
