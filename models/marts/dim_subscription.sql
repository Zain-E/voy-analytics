-- ============================================================================
-- dim_subscription — conformed SUBSCRIPTION dimension.
-- One row per subscription_id. The subscription-grain companion to dim_customer:
-- downstream models resolve a spell's subscription (and, through it, the owning
-- customer's country / acquisition group) by joining this table on subscription_id.
--
-- Built from int_subscription_active_periods (the gap-aware merge) rather than the
-- raw spells, so the row can describe the gaps as well as the outer bounds:
-- lifespan_days is the SPAN, active_days is the coverage inside it, and the
-- difference — gap_days — is time the subscription was lapsed.
-- ============================================================================

{% set snapshot = "date '" ~ var('snapshot_date') ~ "'" %}

with periods as (
    select * from {{ ref('int_subscription_active_periods') }}
),

customer as (
    select customer_id, country, acq_taxonomy
    from {{ ref('dim_customer') }}
),

-- Collapse a subscription's active periods to a single row. The periods are
-- merged and non-overlapping, so summing their lengths is true coverage.
subscription as (
    select
        subscription_id,
        any_value(customer_id)                                              as customer_id,
        min(active_period_start)                                            as subscription_start_date,
        max(active_period_end)                                              as subscription_end_date,
        sum(spell_count)                                                    as spell_count,
        count(*)                                                            as active_period_count,
        sum(date_diff(active_period_end, active_period_start, day) + 1)     as active_days
    from periods
    group by subscription_id
),

-- The longest single lapse between two consecutive active periods.
consecutive as (
    select
        subscription_id,
        active_period_start,
        lag(active_period_end) over (
            partition by subscription_id
            order by period_index
        ) as prev_period_end
    from periods
),

gaps as (
    select
        subscription_id,
        max(date_diff(active_period_start, prev_period_end, day) - 1) as longest_gap_days
    from consecutive
    where prev_period_end is not null
    group by subscription_id
),

flagged as (
    select
        s.*,
        date_diff(s.subscription_end_date, s.subscription_start_date, day) + 1  as lifespan_days,
        s.subscription_end_date >= {{ snapshot }}                               as is_live_at_snapshot,
        -- The customer's earliest-starting subscription (subscription_id breaks ties).
        row_number() over (
            partition by s.customer_id
            order by s.subscription_start_date, s.subscription_id
        ) = 1                                                                   as is_first_subscription
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
    f.active_days,
    f.lifespan_days - f.active_days     as gap_days,
    coalesce(g.longest_gap_days, 0)     as longest_gap_days,
    f.active_period_count,
    f.spell_count,
    f.is_first_subscription,
    f.is_live_at_snapshot
from flagged f
left join customer c using (customer_id)
left join gaps     g using (subscription_id)
