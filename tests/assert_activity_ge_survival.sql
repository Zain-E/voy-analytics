-- Activity-based retention must never be below survival retention at any
-- cohort/tenure/dimension (activity includes win-backs). Any row returned = failure.
select
    cohort_month,
    country,
    acq_taxonomy,
    tenure,
    retained_never_churned,
    retained_total
from {{ ref('viz_cohort_retention') }}
where retained_total < retained_never_churned
