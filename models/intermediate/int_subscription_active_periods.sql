-- ============================================================================
-- int_subscription_active_periods
-- ----------------------------------------------------------------------------
-- The SUBSCRIPTION-grain companion to int_customer_continuous_subscriptions:
-- each subscription's spells merged into continuous ACTIVE PERIODS ("islands"),
-- with the same `island_gap_tolerance_days` as the customer-grain merge.
--
-- Why it exists: dim_subscription is one row per subscription, so it can only
-- carry min(start) -> max(end) — a bounding box that silently spans the days
-- between two spells. Anything asking "was this subscription live on day D"
-- needs the periods, not the box, so this model gives the marts a gap-aware
-- source at subscription grain and keeps interval maths out of staging.
-- ============================================================================

-- De-dupe to distinct (customer, subscription, interval) rows, matching the
-- customer-grain model.
with spells as (
    select distinct
        customer_id,
        subscription_id,
        from_date,
        to_date
    from {{ ref('stg_voy__activity') }}
),

ordered as (
    select
        customer_id,
        subscription_id,
        from_date,
        to_date,
        max(to_date) over (
            partition by subscription_id
            order by from_date, to_date
            rows between unbounded preceding and 1 preceding
        ) as prev_max_to
    from spells
),

-- A new period starts when a spell begins after the prior coverage ends.
flagged as (
    select
        *,
        if(
            prev_max_to is null
            or from_date > date_add(prev_max_to, interval {{ var('island_gap_tolerance_days') }} day),
            1, 0
        ) as is_new_period
    from ordered
),

grouped as (
    select
        *,
        sum(is_new_period) over (
            partition by subscription_id
            order by from_date, to_date
            rows between unbounded preceding and current row
        ) as period_index
    from flagged
)

select
    subscription_id,
    any_value(customer_id)  as customer_id,     -- one customer per subscription
    period_index,
    min(from_date)          as active_period_start,
    max(to_date)            as active_period_end,
    count(*)                as spell_count,
    period_index = 1        as is_first_active_period
from grouped
group by subscription_id, period_index
