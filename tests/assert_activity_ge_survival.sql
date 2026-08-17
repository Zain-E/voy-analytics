-- Activity-based retention must never be below survival retention at any
-- cohort/tenure/dimension (activity includes win-backs). Any row returned = failure.
select
    cohort_month,
    country,
    acq_taxonomy,
    tenure,
    retained_survival,
    retained_activity
from {{ ref('rpt_cohort_retention') }}
where retained_activity < retained_survival
