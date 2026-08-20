-- ============================================================================
-- int_customer_continuous_subscriptions
-- ----------------------------------------------------------------------------
-- THE core modelling concept. Collapse every one of a customer's subscription
-- spells into CONTINUOUS subscription periods ("islands"), ignoring subscription_id.
-- This makes activity subscription-count-independent (per the brief) and turns
-- the GAPS between periods into churn/reactivation signals.
--
-- Output grain: one row per customer per continuous subscription period.
-- ============================================================================

-- De-dupe to distinct (customer, subscription, interval) rows, and carry
-- subscription_id so it can serve as a deterministic TIEBREAKER in the window
-- ORDER BY below. Multiple subscriptions can share the same [from_date, to_date];
-- those rows tie on (from_date, to_date), and BigQuery may break the tie
-- inconsistently across the two window functions, corrupting the island
-- assignment (overlapping periods). Ordering by subscription_id makes the sort a
-- total order — hence deterministic, non-overlapping continuous periods.
with spells as (
    select distinct
        customer_id,
        subscription_id,
        from_date,
        to_date
    from {{ ref('stg_voy__activity') }}
),

-- Running max end date of all PRIOR spells for the customer.
ordered as (
    select
        customer_id,
        subscription_id,
        from_date,
        to_date,
        max(to_date) over (
            partition by customer_id
            order by from_date, to_date, subscription_id
            rows between unbounded preceding and 1 preceding
        ) as prev_max_to
    from spells
),

-- A new island starts when a spell begins after the prior coverage ends
-- (allowing a configurable gap tolerance).
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
            partition by customer_id
            order by from_date, to_date, subscription_id
            rows between unbounded preceding and current row
        ) as period_index
    from flagged
)

select
    customer_id,
    period_index,
    min(from_date)          as subscription_period_start,
    max(to_date)            as subscription_period_end,
    period_index = 1        as is_first_subscription_period
from grouped
group by customer_id, period_index
