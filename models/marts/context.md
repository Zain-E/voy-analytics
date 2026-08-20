# Voy analytics — semantic context

Canonical, AI-ready context for the Voy retention marts. It exists so that a
person **or an LLM/agent** can answer questions against these tables correctly
without re-deriving definitions. If you are writing SQL against this schema,
read this first: it is the contract for what the tables mean, how they join, and
which column answers which question.

> **One idea underpins everything:** a customer is *active* independently of how
> many subscriptions they hold. All activity is measured by merging each
> customer's subscription spells into **continuous subscription periods**, then
> reading retention, churn and active-user counts off that customer-level
> primitive. "How many subscriptions" is informational only — it never changes
> whether a customer is active.

---

## Where the data lives

| Layer | Location | Notes |
|---|---|---|
| Raw sources | `zains-gcp.voy` | `customers`, `acq_orders`, `activity` (subscription spells). Read-only. |
| Models (marts) | dbt target dataset, e.g. `zains-gcp.voy_analytics` | The tables described below. Query these, not the raw sources. |

Everything is a **snapshot** as of `snapshot_date` (a dbt var, currently
`2024-08-16` — the max date present in `voy.activity`). There is no live feed;
treat the snapshot month as partial (see Caveats).

Grains in one line: `dim_customer` = one row per customer · `dim_subscription`
= one row per subscription · `fct_customer_per_month_snapshot` = one row per
active customer per month · `viz_cohort_retention` = cohort × tenure × dims ·
`viz_active_users_daily` = day × dims.

---

## Tables

### `dim_customer` — the customer entity (one row per registered customer)
Primary key `customer_id`. Includes never-active customers (for conversion /
activation analysis). Key columns: `country`, `acq_taxonomy` (acquisition group;
`'Unknown'` where unattributed), `cohort_month` (month of first activation),
`is_ever_active`. Use it for population counts, activation, and as the join
target for customer attributes.

### `dim_subscription` — the subscription entity (one row per subscription)
Primary key `subscription_id`; foreign key `customer_id` → `dim_customer`. A
subscription belongs to exactly one customer and can appear as several spells in
the raw activity table; this collapses them to one summarised row
(`subscription_start_date`, `subscription_end_date`, `lifespan_days`,
`spell_count`, `is_first_subscription`, `is_live_at_snapshot`). It also carries
`country` / `acq_taxonomy` denormalised from the owning customer. Use it to
count or describe subscriptions and to resolve a spell's dimensions by joining on
`subscription_id`. **Do not use it for month/day active-user maths** — those need
the spell intervals in `stg_voy__activity`, because a subscription's min→max span
can hide internal gaps.

### `fct_customer_per_month_snapshot` — the analysis-ready fact (query this first)
Grain: one row per **ever-active customer per calendar month**, from the
customer's `cohort_month` through the snapshot month — inactive months included,
so churn and reactivation are observable. This one tall, drillable table answers
most questions. Key columns: `has_active_subscription` (MAU basis),
`has_continuous_active_subscription` (survival / never-churned basis),
`months_since_acquisition` (tenure, 0 = acquisition month), `is_new_customer`,
`has_churned_this_month`, `has_reactivated_subscription_this_month`,
`active_subscription_count` (informational), plus `country` / `acq_taxonomy`.

### `viz_cohort_retention` — cohort × tenure retention (reporting rollup)
Grain: `cohort_month` × `country` × `acq_taxonomy` × `tenure`. Carries
`cohort_size`, the two retained counts, and the two ready rates
`never_churned_retention` and `total_retention` (both 0–1). Derived from the
fact; use it for cohort heatmaps and retention curves without re-aggregating.

### `viz_active_users_daily` — daily active users (reporting rollup)
Grain: `day` × `country` × `acq_taxonomy`. Carries `daily_active_customers`,
`active_customers_32d` (trailing `active_window_days`), and `live_subscriptions`
(subscription grain, informational). Bounded to `daily_report_start_date` to keep
the daily grain cheap; cohort retention uses full history separately.

---

## Star schema & join keys

```
dim_customer (customer_id)  ─┬─<  fct_customer_per_month_snapshot.customer_id
                             └─<  dim_subscription.customer_id
dim_subscription (subscription_id)  ─<  stg_voy__activity.subscription_id  (spell grain)
```

- Customer attributes: join anything to `dim_customer` on `customer_id`.
- Subscription attributes: join to `dim_subscription` on `subscription_id`.
- `country` and `acq_taxonomy` are already denormalised onto the fact and both
  viz models, so most dashboard queries need **no join** — filter/group directly.

---

## Canonical metric definitions

Use these exactly; do not invent variants.

- **Active user / MAU** — a customer with `has_active_subscription = true` in the
  month. Count `distinct customer_id`. Independent of subscription count.
- **Never-churned (survival) retention** — of a cohort, the share still in their
  **first unbroken** subscription at tenure *n*: `retained_never_churned /
  cohort_size` (= `has_continuous_active_subscription`). Monotonically decreasing.
  The conservative view.
- **Total retention** — of a cohort, the share **active** at tenure *n*
  **including win-backs**: `retained_total / cohort_size` (= any
  `has_active_subscription`). Always ≥ never-churned; the gap is reactivation.
  It is a per-tenure-month membership check, so the line can rise again when
  customers reactivate. Reactivations only appear if they land within the
  observed tenure window.
- **Monthly (logo) churn** — of customers active in month *M-1*, the share **not**
  active in month *M*: `1 − (active in M-1 and M) / (active in M-1)`. "Logo" =
  customer count, **not** revenue. `retention = 1 − churn`.
- **Reactivation / win-back** — active this month after being inactive last
  month, with prior history (`has_reactivated_subscription_this_month`).
- **New customer / acquisition** — first active month for the customer
  (`is_new_customer`, i.e. `months_since_acquisition = 0`).
- **Activation rate** — of all registered customers, the share that *ever*
  subscribed: `countif(is_ever_active) / count(*)` on `dim_customer`.
- **DAU** — `daily_active_customers` (distinct customers live that day).
- **Active · 32-day** — `active_customers_32d`: distinct customers with a live
  subscription in the trailing `active_window_days` (32). A robust "active now".
- **Live subscriptions** — `live_subscriptions`: distinct subscriptions live on
  the day. Subscription grain, informational — does **not** define "active".

Dimensions everywhere: `country`, `acq_taxonomy`. Cohort = the customer's first
activation month. Tenure = months since acquisition (M0 = joining month).

---

## Caveats (read before quoting a number)

- **Partial final month.** The snapshot month is incomplete; exclude it from
  month-over-month trends (`calendar_month < date_trunc(snapshot_date, month)`).
- **32-day window soft tail.** `active_customers_32d` at the very last few days
  of the extract is understated (the window runs past the data). Treat the tail
  as soft.
- **Total retention includes reactivations at any tenure** within the observed
  window, so it is not monotonic — do not read its slope as one fixed group aging.
- **Pooled retention curves mix cohorts** at higher tenures (only cohorts old
  enough contribute to later months). The composition changes across the x-axis.
- **Churn is logo (customer) churn, not revenue.** There is no price/plan data.
- **Revenue retention (NRR/GRR) is out of scope** — no monetary data exists.
- **Dates are inclusive** (`from_date`/`to_date` cover the customer on those days).
- **Gap tolerance** for merging spells into a continuous period is
  `island_gap_tolerance_days` (0) — any gap ≥ 1 day starts a new period (a
  churn/reactivation boundary).

---

## Worked examples (question → where to look)

- *"MAU in July 2024, by country"* → `fct_customer_per_month_snapshot`, filter
  `calendar_month`, `countif(has_active_subscription)` distinct `customer_id`,
  group by `country`.
- *"Never-churned retention curve for the 2023-Q1 hair-loss cohort"* →
  `viz_cohort_retention`, filter `cohort_month`/`acq_taxonomy`, plot
  `never_churned_retention` by `tenure`.
- *"How many subscriptions is each customer holding on average?"* →
  `dim_subscription` grouped by `customer_id` (count), or the informational
  `active_subscription_count` on the fact. Remember it does not affect "active".
- *"Which acquisition group retains best at M12?"* → `viz_cohort_retention` at
  `tenure = 12`, aggregate `sum(retained_*)/sum(cohort_size)` by `acq_taxonomy`.
- *"Daily active customers over the last 90 days"* → `viz_active_users_daily`,
  `daily_active_customers` by `day`.
- *"Activation rate by acquisition group"* → `dim_customer`,
  `countif(is_ever_active)/count(*)` grouped by `acq_taxonomy`.

When a question needs a metric not listed above, prefer building it from
`fct_customer_per_month_snapshot` (the tall, governed grain) rather than the raw
sources, and keep to the definitions in this file.
