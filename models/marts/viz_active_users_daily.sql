-- ============================================================================
-- viz_active_users_daily — daily active-user reporting, drillable by dimension.
-- One row per day × country × acq_taxonomy with three measures:
--   • daily_active_customers : distinct CUSTOMERS with a live subscription on the day
--   • active_customers_32d   : distinct CUSTOMERS active within the last `active_window_days`
--   • live_subscriptions     : distinct SUBSCRIPTIONS live on the day (subscription grain,
--                              informational — does not define "active")
--
-- Bounded to `daily_report_start_date` to keep the daily grain cheap; cohort
-- retention uses full history separately.
-- ============================================================================

{% set window_lookback = var('active_window_days') - 1 %}

with periods as (
    select
        cp.customer_id,
        cp.subscription_period_start,
        cp.subscription_period_end,
        d.country,
        d.acq_taxonomy
    from {{ ref('int_customer_continuous_subscriptions') }} cp
    join {{ ref('dim_customer') }} d using (customer_id)
),

-- Raw subscription spells for the subscription-grain count, joined to
-- dim_subscription so the dimensions are resolved through the subscription entity.
activity as (
    select
        a.subscription_id,
        a.from_date,
        a.to_date,
        s.country,
        s.acq_taxonomy
    from {{ ref('stg_voy__activity') }} a
    join {{ ref('dim_subscription') }} s using (subscription_id)
),

spine as (
    select day
    from unnest(generate_date_array(
        date '{{ var("daily_report_start_date") }}',
        date '{{ var("snapshot_date") }}',
        interval 1 day
    )) as day
),

-- Customers with a live subscription covering the day.
live as (
    select
        s.day,
        p.country,
        p.acq_taxonomy,
        count(distinct p.customer_id) as daily_active_customers
    from spine s
    join periods p
        on p.subscription_period_start <= s.day
       and p.subscription_period_end   >= s.day
    group by s.day, p.country, p.acq_taxonomy
),

-- Customers active within the last `active_window_days`.
windowed as (
    select
        s.day,
        p.country,
        p.acq_taxonomy,
        count(distinct p.customer_id) as active_customers_32d
    from spine s
    join periods p
        on p.subscription_period_start <= s.day
       and p.subscription_period_end   >= date_sub(s.day, interval {{ window_lookback }} day)
    group by s.day, p.country, p.acq_taxonomy
),

-- Subscriptions live on the day (subscription grain).
subs as (
    select
        s.day,
        act.country,
        act.acq_taxonomy,
        count(distinct act.subscription_id) as live_subscriptions
    from spine s
    join activity act
        on act.from_date <= s.day
       and act.to_date   >= s.day
    group by s.day, act.country, act.acq_taxonomy
)

select
    s.day,
    dims.country,
    dims.acq_taxonomy,
    coalesce(l.daily_active_customers, 0)   as daily_active_customers,
    coalesce(w.active_customers_32d,   0)   as active_customers_32d,
    coalesce(sub.live_subscriptions,   0)   as live_subscriptions
from spine s
cross join (select distinct country, acq_taxonomy from periods) dims
left join live     l   on l.day  = s.day and l.country   = dims.country and l.acq_taxonomy   = dims.acq_taxonomy
left join windowed w   on w.day  = s.day and w.country   = dims.country and w.acq_taxonomy   = dims.acq_taxonomy
left join subs     sub on sub.day = s.day and sub.country = dims.country and sub.acq_taxonomy = dims.acq_taxonomy
