-- ============================================================================
-- dim_subscription — conformed SUBSCRIPTION dimension.
-- One row per subscription_id. The subscription-grain companion to dim_customer:
-- downstream models resolve a spell's subscription (and, through it, the owning
-- customer's country / acquisition group) by joining this table on subscription_id.
-- ============================================================================

{% set snapshot = "date '" ~ var('snapshot_date') ~ "'" %}

with activity as (
    select * from {{ ref('stg_voy__activity') }}
),

customer as (
    select customer_id, country, acq_taxonomy
    from {{ ref('dim_customer') }}
),

-- Collapse a subscription's spells to a single row.
subscription as (
    select
        subscription_id,
        any_value(customer_id)          as customer_id,   -- one customer per subscription
        min(from_date)                  as subscription_start_date,
        max(to_date)                    as subscription_end_date,
        count(*)                        as spell_count
    from activity
    group by subscription_id
),

flagged as (
    select
        s.*,
        date_diff(s.subscription_end_date, s.subscription_start_date, day) + 1  as lifespan_days,
        s.subscription_end_date >= {{ snapshot }}                              as is_live_at_snapshot,
        -- The customer's earliest-starting subscription (subscription_id breaks ties).
        row_number() over (
            partition by s.customer_id
            order by s.subscription_start_date, s.subscription_id
        ) = 1                                                                  as is_first_subscription
    from subscription s
)

select
    f.subscription_id,
    f.customer_id,
    c.country,
    c.acq_taxonomy,
    f.subscription_start_date,
    f.subscription_end_date,
    f.lifespan_days,
    f.spell_count,
    f.is_first_subscription,
    f.is_live_at_snapshot
from flagged f
left join customer c using (customer_id)
