with source as (
    select * from {{ source('voy', 'activity') }}
),

cleaned as (
    select
        customer_id,
        subscription_id,
        from_date,
        to_date
    from source
    -- Guard against malformed spells (none in current data, but keep the model safe).
    where from_date is not null
      and to_date   is not null
      and to_date >= from_date
)

select * from cleaned
