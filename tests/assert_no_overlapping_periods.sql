-- A customer's continuous subscription periods must never overlap (gaps-and-islands
-- integrity). Any row returned = failure.
select
    p.customer_id,
    p.period_index as period_a,
    q.period_index as period_b
from {{ ref('int_customer_continuous_subscriptions') }} p
join {{ ref('int_customer_continuous_subscriptions') }} q
    on p.customer_id = q.customer_id
   and p.period_index < q.period_index
   and p.subscription_period_start <= q.subscription_period_end
   and q.subscription_period_start <= p.subscription_period_end
