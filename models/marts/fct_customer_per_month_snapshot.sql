-- ============================================================================
-- fct_customer_per_month_snapshot  — THE analysis-ready fact.
-- ----------------------------------------------------------------------------
-- Grain: one row per ever-active customer per calendar month, from their cohort
-- month through the snapshot month. Inactive months are present too
-- (has_active_subscription = false), so churn and reactivation are observable.
--
-- `calendar_month` is the month each ROW describes (the observation month). It
-- ranges from the customer's cohort_month to the snapshot month; the snapshot
-- month is just the upper bound of that range, set by var('snapshot_date').
--
-- This single tall, drillable table answers all four brief requirements:
--   • retention over time        -> filter by `calendar_month`
--   • retention from cohort       -> group by cohort_month, months_since_acquisition
--   • churn / retention / acq     -> has_active_subscription, has_churned_this_month, is_new_customer
--   • activity over a period      -> has_active_subscription per calendar_month, any dimension
-- ============================================================================

{% set snapshot_month = "date_trunc(date '" ~ var('snapshot_date') ~ "', month)" %}

with customers as (
    select *
    from {{ ref('dim_customer') }}
    where is_ever_active
),

periods as (
    select * from {{ ref('int_customer_continuous_subscriptions') }}
),

activity as (
    select * from {{ ref('stg_voy__activity') }}
),

-- One row per customer per calendar month of their active lifetime (cohort -> snapshot).
month_spine as (
    select
        c.customer_id,
        c.cohort_month,
        c.country,
        c.acq_taxonomy,
        c.first_period_end,
        calendar_month
    from customers c,
    unnest(generate_date_array(
        c.cohort_month,
        {{ snapshot_month }},
        interval 1 month
    )) as calendar_month
),

-- Calendar months in which the customer held a live subscription (any continuous
-- period overlaps the month).
active_subscription_months as (
    select distinct
        p.customer_id,
        calendar_month
    from periods p,
    unnest(generate_date_array(
        date_trunc(p.subscription_period_start, month),
        date_trunc(p.subscription_period_end,   month),
        interval 1 month
    )) as calendar_month
),

-- Informational only: how many distinct subscriptions were active that month.
-- Carried to demonstrate it does NOT influence has_active_subscription.
subs_per_month as (
    select
        a.customer_id,
        calendar_month,
        count(distinct a.subscription_id) as active_subscription_count
    from activity a,
    unnest(generate_date_array(
        date_trunc(a.from_date, month),
        date_trunc(a.to_date,   month),
        interval 1 month
    )) as calendar_month
    group by a.customer_id, calendar_month
),

joined as (
    select
        s.customer_id,
        s.calendar_month,
        s.cohort_month,
        date_diff(s.calendar_month, s.cohort_month, month)   as months_since_acquisition,
        -- Holds a live subscription overlapping this calendar month.
        asm.calendar_month is not null                       as has_active_subscription,
        -- Still inside the first unbroken subscription period (survival: no churn yet).
        s.first_period_end >= s.calendar_month               as has_continuous_active_subscription,
        coalesce(spm.active_subscription_count, 0)           as active_subscription_count,
        s.country,
        s.acq_taxonomy
    from month_spine s
    left join active_subscription_months asm
        on asm.customer_id = s.customer_id and asm.calendar_month = s.calendar_month
    left join subs_per_month spm
        on spm.customer_id = s.customer_id and spm.calendar_month = s.calendar_month
)

select
    customer_id,
    calendar_month,
    cohort_month,
    months_since_acquisition,
    has_active_subscription,
    has_continuous_active_subscription,
    active_subscription_count,
    country,
    acq_taxonomy,
    -- Lifecycle flags
    has_active_subscription and months_since_acquisition = 0                     as is_new_customer,
    has_active_subscription
        and not coalesce(lag(has_active_subscription) over (
            partition by customer_id order by calendar_month), false)
        and months_since_acquisition > 0                                        as has_reactivated_subscription_this_month,
    not has_active_subscription
        and coalesce(lag(has_active_subscription) over (
            partition by customer_id order by calendar_month), false)           as has_churned_this_month
from joined
