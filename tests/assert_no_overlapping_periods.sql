-- A customer's continuous active periods must never overlap (gaps-and-islands
-- integrity). Any row returned = failure.
select
    p.customer_id,
    p.period_index as period_a,
    q.period_index as period_b
from {{ ref('int_customer_active_periods') }} p
join {{ ref('int_customer_active_periods') }} q
    on p.customer_id = q.customer_id
   and p.period_index < q.period_index
   and p.period_start <= q.period_end
   and q.period_start <= p.period_end
