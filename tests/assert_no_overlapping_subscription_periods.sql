-- A subscription's active periods must never overlap (gaps-and-islands integrity) —
-- the subscription-grain twin of assert_no_overlapping_periods. dim_subscription's
-- active_days sums these period lengths, so an overlap would double-count coverage.
-- Any row returned = failure.
select
    p.subscription_id,
    p.period_index as period_a,
    q.period_index as period_b
from {{ ref('int_subscription_active_periods') }} p
join {{ ref('int_subscription_active_periods') }} q
    on p.subscription_id = q.subscription_id
   and p.period_index < q.period_index
   and p.active_period_start <= q.active_period_end
   and q.active_period_start <= p.active_period_end
