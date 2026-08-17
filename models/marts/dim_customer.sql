-- ============================================================================
-- dim_customer — conformed customer dimension.
-- One row per REGISTERED customer (includes never-active for conversion tracking).
-- ============================================================================

with customers as (
    select * from {{ ref('stg_voy__customers') }}
),

acq as (
    select * from {{ ref('stg_voy__acq_orders') }}
),

periods as (
    select * from {{ ref('int_customer_active_periods') }}
),

-- Customer-level activity summary derived from the merged active periods.
activity_summary as (
    select
        customer_id,
        min(period_start)                                   as first_activation_date,
        max(period_end)                                     as last_active_date,
        date_trunc(min(period_start), month)                as cohort_month,
        -- End of the FIRST continuous period = the customer's first churn point.
        -- Drives survival retention.
        max(if(is_first_period, period_end, null))          as first_period_end
    from periods
    group by customer_id
)

select
    c.customer_id,
    c.country,
    coalesce(a.acq_taxonomy, 'Unknown')                     as acq_taxonomy,
    s.first_activation_date,
    s.cohort_month,
    s.last_active_date,
    s.first_period_end,
    s.customer_id is not null                               as is_ever_active
from customers c
left join acq             a on a.customer_id = c.customer_id
left join activity_summary s on s.customer_id = c.customer_id
