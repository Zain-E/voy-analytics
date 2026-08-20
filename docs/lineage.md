# Model lineage (DAG)

Flow: **raw spells → merge to continuous subscription periods → stamp customer-months → aggregate for reporting.**

```mermaid
graph LR
  subgraph SRC["Raw sources · voy"]
    s1[("customers")]
    s2[("acq_orders")]
    s3[("activity<br/>subscription spells")]
  end

  subgraph STG["Staging · views"]
    st1["stg_voy__customers"]
    st2["stg_voy__acq_orders"]
    st3["stg_voy__activity"]
  end

  subgraph INT["Intermediate"]
    i1["int_customer_continuous_subscriptions<br/><i>merge spells → continuous subscription periods</i>"]
  end

  subgraph MART["Marts · tables"]
    m1["dim_customer<br/><i>conformed dimension + cohort</i>"]
    m2["fct_customer_per_month_snapshot<br/><b>analysis-ready fact</b><br/>has_active_subscription · continuous · tenure · flags"]
    m3["viz_cohort_retention<br/><i>never-churned + total retention</i>"]
    m4["viz_active_users_daily<br/><i>DAU + 32-day active window</i>"]
  end

  s1 --> st1
  s2 --> st2
  s3 --> st3
  st3 --> i1
  st1 --> m1
  st2 --> m1
  i1  --> m1
  i1  --> m2
  m1  --> m2
  st3 --> m2
  m2  --> m3
  i1  --> m4
  m1  --> m4

  classDef src  fill:#e1e0d9,stroke:#898781,color:#0b0b0b;
  classDef stg  fill:#cde2fb,stroke:#2a78d6,color:#0b0b0b;
  classDef int  fill:#f8ddca,stroke:#eb6834,color:#0b0b0b;
  classDef mart fill:#c9ece0,stroke:#1baf7a,color:#0b0b0b;
  class s1,s2,s3 src;
  class st1,st2,st3 stg;
  class i1 int;
  class m1,m2,m3,m4 mart;
```

| Layer | Model | Purpose |
|---|---|---|
| Staging | `stg_voy__*` | clean/type/rename the three sources; `Unknown` taxonomy bucket |
| Intermediate | `int_customer_continuous_subscriptions` | **core** — merge each customer's spells into continuous subscription periods (gaps-and-islands) |
| Mart | `dim_customer` | conformed customer dimension + cohort assignment |
| Mart | `fct_customer_per_month_snapshot` | **analysis-ready** customer × month fact — the table to query |
| Mart | `viz_cohort_retention` | cohort × tenure never-churned + total retention |
| Mart | `viz_active_users_daily` | daily active users (DAU + 32-day window) |

_Metrics: never-churned & total retention come from `viz_cohort_retention`; churn and reactivation derive from `fct_customer_per_month_snapshot`; active-user counts from `viz_active_users_daily`._
