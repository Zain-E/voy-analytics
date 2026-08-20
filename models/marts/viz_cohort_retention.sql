-- ============================================================================
-- viz_cohort_retention — cohort × tenure retention, drillable by dimension.
-- One row per cohort_month × country × acq_taxonomy × tenure. Two measures:
--   • never_churned_retention : share still CONTINUOUSLY subscribed (never churned) —
--                               the classic "survival" curve, monotonically decreasing
--   • total_retention       : share ACTIVE now, INCLUDING win-backs (never-churned +
--                               reactivated); always >= never_churned_retention
-- ============================================================================

with fct as (
    select * from {{ ref('fct_customer_per_month_snapshot') }}
),

cohort_sizes as (
    select
        cohort_month,
        country,
        acq_taxonomy,
        count(distinct customer_id) as cohort_size
    from fct
    where months_since_acquisition = 0
    group by cohort_month, country, acq_taxonomy
),

retained as (
    select
        cohort_month,
        country,
        acq_taxonomy,
        months_since_acquisition                                            as tenure,
        count(distinct if(has_continuous_active_subscription,   customer_id, null))       as retained_never_churned,
        count(distinct if(has_active_subscription, customer_id, null))       as retained_total
    from fct
    group by cohort_month, country, acq_taxonomy, months_since_acquisition
)

select
    r.cohort_month,
    r.country,
    r.acq_taxonomy,
    r.tenure,
    cs.cohort_size,
    r.retained_never_churned,
    r.retained_total,
    safe_divide(r.retained_never_churned, cs.cohort_size)    as never_churned_retention,
    safe_divide(r.retained_total, cs.cohort_size)    as total_retention
from retained r
join cohort_sizes cs
    using (cohort_month, country, acq_taxonomy)
