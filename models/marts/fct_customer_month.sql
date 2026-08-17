-- ============================================================================
-- fct_customer_month  — THE analysis-ready fact.
-- ----------------------------------------------------------------------------
-- Grain: one row per ever-active customer per calendar month, from their cohort
-- month through the snapshot month. Inactive months are present too (is_active =
-- false), so churn and reactivation are directly observable.
--
-- This single tall, drillable table answers all four brief requirements:
--   • retention over time        -> filter by `month`
--   • retention from cohort       -> group by cohort_month, months_since_acquisition
--   • churn / retention / acq     -> is_active, is_churned_this_month, is_new
--   • activity over a period      -> is_active per month, any dimension
-- ============================================================================

{% set snapshot_month = "date_trunc(date '" ~ var('snapshot_date') ~ "', month)" %}

with customers as (
    select *
    from {{ ref('dim_customer') }}
    where is_ever_active
),

periods as (
    select * from {{ ref('int_customer_active_periods') }}
),

activity as (
    select * from {{ ref('stg_voy__activity') }}
),

-- One row per customer per month of their active lifetime (cohort -> snapshot).
month_spine as (
    select
        c.customer_id,
        c.cohort_month,
        c.country,
        c.acq_taxonomy,
        c.first_period_end,
        month
    from customers c,
    unnest(generate_date_array(
        c.cohort_month,
        {{ snapshot_month }},
        interval 1 month
    )) as month
),

-- Months in which the customer was active (any continuous period overlaps the month).
active_months as (
    select distinct
        p.customer_id,
        month
    from periods p,
    unnest(generate_date_array(
        date_trunc(p.period_start, month),
        date_trunc(p.period_end,   month),
        interval 1 month
    )) as month
),

-- Informational only: how many distinct subscriptions were active that month.
-- Carried to demonstrate it does NOT influence is_active.
subs_per_month as (
    select
        a.customer_id,
        month,
        count(distinct a.subscription_id) as active_subscriptions
    from activity a,
    unnest(generate_date_array(
        date_trunc(a.from_date, month),
        date_trunc(a.to_date,   month),
        interval 1 month
    )) as month
    group by a.customer_id, month
),

joined as (
    select
        s.customer_id,
        s.month,
        s.cohort_month,
        date_diff(s.month, s.cohort_month, month)       as months_since_acquisition,
        am.month is not null                            as is_active,
        -- Survival: still inside the first unbroken active period this month.
        s.first_period_end >= s.month                   as is_active_survival,
        coalesce(spm.active_subscriptions, 0)           as active_subscriptions,
        s.country,
        s.acq_taxonomy
    from month_spine s
    left join active_months am
        on am.customer_id = s.customer_id and am.month = s.month
    left join subs_per_month spm
        on spm.customer_id = s.customer_id and spm.month = s.month
)

select
    customer_id,
    month,
    cohort_month,
    months_since_acquisition,
    is_active,
    is_active_survival,
    active_subscriptions,
    country,
    acq_taxonomy,
    -- Lifecycle flags
    is_active and months_since_acquisition = 0                                   as is_new,
    is_active
        and not coalesce(lag(is_active) over (
            partition by customer_id order by month), false)
        and months_since_acquisition > 0                                        as is_reactivated,
    not is_active
        and coalesce(lag(is_active) over (
            partition by customer_id order by month), false)                    as is_churned_this_month
from joined
