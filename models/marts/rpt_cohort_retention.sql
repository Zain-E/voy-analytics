-- ============================================================================
-- rpt_cohort_retention — cohort × tenure retention, drillable by dimension.
-- Survival (primary) + activity-based (secondary/reactivation overlay).
-- One row per cohort_month × country × acq_taxonomy × tenure.
-- ============================================================================

with fct as (
    select * from {{ ref('fct_customer_month') }}
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
        months_since_acquisition                                         as tenure,
        count(distinct if(is_active_survival, customer_id, null))        as retained_survival,
        count(distinct if(is_active,          customer_id, null))        as retained_activity
    from fct
    group by cohort_month, country, acq_taxonomy, months_since_acquisition
)

select
    r.cohort_month,
    r.country,
    r.acq_taxonomy,
    r.tenure,
    cs.cohort_size,
    r.retained_survival,
    r.retained_activity,
    safe_divide(r.retained_survival, cs.cohort_size)    as survival_retention,
    safe_divide(r.retained_activity, cs.cohort_size)    as activity_retention
from retained r
join cohort_sizes cs
    using (cohort_month, country, acq_taxonomy)
