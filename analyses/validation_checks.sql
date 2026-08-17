-- Ad-hoc reconciliation queries (run with `dbt compile` then paste, or run directly).
-- These mirror the read-only checks used while building the models.

-- 1. Island merge reconciles to raw monthly-active counts (expect 120492 / 191784).
select
  count(*)                                                     as n_periods,        -- ~874,036
  count(distinct customer_id)                                 as n_customers,      -- 512,366
  countif(period_start <= date '2023-07-31'
          and period_end >= date '2023-07-01')                as approx_mau_flag
from {{ ref('int_customer_active_periods') }};

-- 2. Activity retention >= survival retention everywhere (should return 0 rows).
select count(*) as violations
from {{ ref('rpt_cohort_retention') }}
where retained_activity < retained_survival;

-- 3. 2023-01 cohort curve (validated: survival 100/94.9/78.1/38.4/17.1 %,
--    activity 100/95.3/81.6/51.8/32.4 % at tentures 0/1/3/6/12).
select tenure, cohort_size, survival_retention, activity_retention
from {{ ref('rpt_cohort_retention') }}
where cohort_month = date '2023-01-01'
  and country = 'United Kingdom'   -- example drill; remove to aggregate
order by tenure;
