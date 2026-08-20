# Voy — Retention, Churn & Active-User Metric Definitions

**Purpose:** the single source of truth for how retention, churn, and "active user" are
defined for Voy's subscription base. Every number in the Streamlit dashboard and every dbt
model traces back to a definition in this document.

**Stack:** BigQuery (warehouse) · dbt (modelling) · Streamlit (reporting/dashboard).

---

## 1. Scope, grain & conventions

| Item | Decision |
|---|---|
| Dataset | `zains-gcp.voy` — `customers`, `acq_orders`, `activity` |
| Snapshot / as-of date | **2024-08-16** (the max date in `activity`). "As of today" means as of this date unless a date is given. |
| Metric grain | **Customer**. A customer's activity never depends on how many subscriptions they hold. |
| Time granularity | Daily-capable base; **monthly** is the default reporting grain for cohorts. DAU / WAU / MAU all derivable. |
| Retention universe | Customers who **ever activated** (512,366). |
| Excluded from retention | Registered-but-never-active customers (20,482) — measured separately as **activation/conversion**, not churn. |
| Partial-period rule | The latest **incomplete** period (here, Aug-2024) is **excluded** from trend and retention reporting. |

**Drill dimensions:** `country` (Brazil, United Kingdom), `acq_taxonomy` (7 groups + `Unknown`),
`cohort_month`, `tenure` (months since acquisition), and derived flags (new / reactivated /
churned).

---

## 2. Foundational concept — customer continuous subscription periods

A customer holds many subscriptions, whose active spells overlap and repeat. To measure the
customer (not the subscription), we **merge all of a customer's spells into continuous subscription
periods** using a gaps-and-islands pass. This is the primitive every metric below is built on.

- **Input:** `activity` rows, each a `[from_date, to_date]` spell for one `subscription_id`.
- **Output grain:** one row per **customer × continuous subscription period** —
  `customer_id, subscription_period_start, subscription_period_end, period_index, is_first_subscription_period`.
- **Why:** (a) makes activity subscription-count-independent, exactly as the brief requires;
  (b) the **gaps between periods are churn-and-reactivation events**, which powers reactivation
  and survival metrics.

```sql
-- int_customer_continuous_subscriptions  (gap tolerance is a dbt var; default 0 days = spells
-- must overlap to merge; a spell starting the day after the previous ends opens a new period)
with spells as (
  select customer_id, from_date, to_date
  from {{ ref('stg_voy__activity') }}
),
ordered as (
  select *,
    max(to_date) over (
      partition by customer_id order by from_date, to_date
      rows between unbounded preceding and 1 preceding
    ) as prev_max_to
  from spells
),
flagged as (
  select *,
    if(prev_max_to is null
       or from_date > date_add(prev_max_to, interval {{ var('island_gap_tolerance_days', 0) }} day),
       1, 0) as is_new_period
  from ordered
),
grouped as (
  select *,
    sum(is_new_period) over (
      partition by customer_id order by from_date, to_date
      rows between unbounded preceding and current row
    ) as period_index
  from flagged
)
select
  customer_id,
  period_index,
  min(from_date) as subscription_period_start,
  max(to_date)   as subscription_period_end,
  period_index = 1 as is_first_subscription_period
from grouped
group by customer_id, period_index
```

---

## 3. Active-user definitions

All at customer grain. `D` = the as-of date. `N` = the active window in days
(**dbt var `active_window_days`, default 32**).

### 3.1 Headline "Active User" — live subscription **and** active in the last 32 days

This is the agreed business definition: a customer is **Active** as of `D` if they **hold a live
subscription that covers `D`** *and* have been **active within the last 32 days**.

```sql
-- Active as of D (customer grain)
select distinct a.customer_id
from {{ ref('stg_voy__activity') }} a
where a.from_date <= D
  and a.to_date   >= date_sub(D, interval ({{ var('active_window_days', 32) }} - 1) day)  -- active in last 32d
  and exists (                                                                             -- AND live subscription covers D
    select 1 from {{ ref('stg_voy__activity') }} a2
    where a2.customer_id = a.customer_id
      and a2.from_date <= D and a2.to_date >= D
  )
```

> **Important nuance (interval-only data).** This dataset has **only subscription active-intervals**
> — there is no separate usage/engagement stream. So "active usage in the last 32 days" and
> "a live subscription exists" both resolve to the *same* spell data. Because a spell that covers
> `D` also lies within the last 32 days, the **live condition mathematically subsumes the 32-day
> condition**, and the strict definition reduces to *"holds a subscription covering `D`."*
>
> **Tail caveat.** At the very end of the extract (`D = 2024-08-16`), "covers `D`" collapses to
> only **9,637** customers, because the extract does not materialise the currently-in-flight
> billing period (closed spells only). The same window measured as *overlap of the last 32 days*
> yields **~195k**. We therefore (a) exclude the partial tail from trends, and (b) recommend the
> **robust variant** below as the headline the dashboard shows, with the strict version available
> as a secondary operational metric.

### 3.2 Robust variant (recommended headline on the dashboard) — active in the last 32 days

Drops the redundant "covers `D`" clause; equals "has a subscription interval overlapping the last
32 days." Robust to the extract tail and stable month-to-month.

```sql
select distinct a.customer_id
from {{ ref('stg_voy__activity') }} a
where a.from_date <= D
  and a.to_date   >= date_sub(D, interval ({{ var('active_window_days', 32) }} - 1) day)
```

### 3.3 Supporting active measures (all customer grain, all derivable from §2)

| Metric | Definition | SQL condition (spell overlaps window) |
|---|---|---|
| **Live / currently subscribed** | Subscription covers `D` | `from_date <= D and to_date >= D` |
| **DAU** (active on day `D`) | Continuous subscription period covers `D` | `subscription_period_start <= D and subscription_period_end >= D` |
| **WAU** | Continuous subscription period overlaps the calendar week of `D` | overlap week |
| **MAU** | Continuous subscription period overlaps the calendar month of `D` | `subscription_period_start <= month_end and subscription_period_end >= month_start` |
| **Rolling 30 / 90-day active** | Continuous subscription period overlaps `[D-29, D]` / `[D-89, D]` | `from_date <= D and to_date >= D-k` |

### 3.4 Reference values (as of 2024-08-16)

| Window | Active customers |
|---|---:|
| On the exact last day (`covers D`) | 9,637 *(tail-under-captured — see caveat)* |
| Last 7 days | 45,725 |
| Last 30 days *(≈ the 32-day headline)* | **195,503** |
| Last 90 days | 229,593 |
| Ever active (all time) | 512,366 |

MAU trend for context: 120,492 (Jul-2023) → 191,784 (Jul-2024); Aug-2024 is partial and excluded.

---

## 4. Retention metrics

### 4.1 Cohort definition

A customer's **cohort** is the calendar month of their **first-ever activation**:
`cohort_month = date_trunc(min(subscription_period_start), month)`.
**Tenure** `n = date_diff(calendar_month, cohort_month, month)` = months since acquisition.

### 4.2 Never-churned retention (`never_churned_retention`) — **primary**

Share of a cohort that has **not yet churned** by tenure `n` — i.e. their **first continuous
subscription period still reaches** month `cohort_month + n`. This is the classic "never left" curve; it
is **monotonically non-increasing**.

```
survival_Rₙ = customers whose first continuous subscription period reaches month (cohort_month + n)
              ─────────────────────────────────────────────────────────────────────
                                       cohort_size
```

```sql
with first_period as (          -- end of each customer's first continuous period = their first churn point
  select customer_id,
         date_trunc(min(subscription_period_start), month) as cohort_month,
         max(if(is_first_subscription_period, subscription_period_end, null)) as first_period_end
  from {{ ref('int_customer_continuous_subscriptions') }}
  group by customer_id
),
months_survived as (
  select customer_id, cohort_month,
         date_diff(date_trunc(first_period_end, month), cohort_month, month) as n_survived
  from first_period
)
select cohort_month, n as tenure,
       count(*)                              as cohort_size,
       countif(n_survived >= n)              as retained_never_churned,
       safe_divide(countif(n_survived >= n), count(*)) as never_churned_retention
from months_survived, unnest(generate_array(0, 60)) as n
group by cohort_month, tenure
```

### 4.3 Total retention (`total_retention`) — **secondary (reactivation overlay)**

Share of a cohort **active in** month `cohort_month + n`, **win-backs included** (any continuous
subscription period overlaps that month, even after a gap). Always **≥ survival**; can rise again after a dip.

```
activity_Rₙ = customers of the cohort active in month (cohort_month + n)
              ──────────────────────────────────────────────────────────
                                    cohort_size
```

Computed directly from `fct_customer_per_month_snapshot` (`has_active_subscription` flag) grouped by `cohort_month, tenure`.

### 4.4 Reading the two together

> Customer acquired in January, active Jan–Mar, lapses Apr–Jun, returns in July.
> **Survival** counts them churned from April (tenure 3) onward — they broke the initial run.
> **Activity-based** counts them retained again in July (tenure 6) — they're active then.

Survival answers *"how sticky was the initial commitment?"*; activity-based answers *"how large is
the active base over time, including returns?"* The dashboard shows survival as the headline
cohort triangle with activity-based as an overlaid line so reactivation is visible.

### 4.5 Cohort sizes (reference, by acquisition month)

Range 2019-01 (157) → 2024-07 (19,884), growing steadily; full table in the profiling output.
2024-08 (6,103) is partial and excluded.

---

## 5. Churn metrics

All **logo (customer-count) churn** — see §7 for why revenue churn (NRR/GRR) is out of scope.

### 5.1 Monthly churn rate (period-over-period)

```
monthly_churn(M) = customers active in M-1 AND NOT active in M
                   ────────────────────────────────────────────
                          customers active in M-1
```

```sql
with mau as (   -- distinct active customers per calendar month, from fct_customer_per_month_snapshot
  select calendar_month, customer_id from {{ ref('fct_customer_per_month_snapshot') }} where has_active_subscription
)
select
  m.calendar_month,
  count(distinct p.customer_id)                                            as active_prev,
  count(distinct p.customer_id) - count(distinct m.customer_id)            as churned,
  safe_divide(count(distinct p.customer_id) - count(distinct m.customer_id),
              count(distinct p.customer_id))                              as churn_rate
from mau p
left join mau m
  on m.customer_id = p.customer_id
 and m.calendar_month = date_add(p.calendar_month, interval 1 month)
group by m.calendar_month
```

### 5.2 Retention rate (period-over-period)

`retention_rate(M) = 1 − monthly_churn(M)` = customers active in **both** M-1 and M ÷ active in M-1.
(Distinct from cohort retention in §4: this is a rolling month-over-month view, not tenure-based.)

### 5.3 Survival churn (first-churn event)

The first churn is the **end of a customer's first continuous subscription period** (`first_period_end`). The
survival curve in §4.2 is `1 − cumulative survival churn`. Useful for "median customer lifetime"
(tenure at which survival crosses 50%).

### 5.4 Reactivation / resurrection

A customer is **reactivated** in month `M` if they are **active in `M`, inactive in `M-1`, and have
prior active history** (a `period_index > 1` beginning in `M`). Reactivations are the difference
between activity-based and survival retention, and feed a "win-back" tile.

---

## 6. Acquisition & activation

- **New customers (acquisition)** — count of customers whose `cohort_month = M` (first activations
  in the month). This is the top of every cohort.
- **Activation / conversion** — of **registered** customers (`customers` table, 532,848), the share
  that **ever activated** (512,366 ≈ 96.2%). The **20,482 never-subscribed** live here, *not* in
  churn. Tracked as a separate funnel metric so retention denominators stay clean.

---

## 7. Data-quality caveats & limitations

1. **Snapshot tail under-capture.** "Currently subscribed" on the final day is ~9,637 vs ~195k
   active-in-last-32-days — the extract only holds closed spells, so the in-flight period is
   under-represented. Mitigation: exclude the partial tail; use the 32-day window headline.
   *(Flag to Voy: confirm whether open/in-flight subscriptions are exportable.)*
2. **Missing acquisition taxonomy.** 4,896 ever-active customers have no `acq_orders` row →
   bucketed as `Unknown` (never dropped).
3. **Registered-but-never-active.** 20,482 customers → excluded from retention, measured as
   activation/conversion (§6).
4. **No revenue / plan / MRR fields.** Therefore **no revenue retention (NRR/GRR), ARPU, or
   LTV**. To add them we'd need per-subscription price/plan and billing amounts joined to the
   activity spells.
5. **Only two countries** in the data (Brazil, United Kingdom) — country drill is binary today but
   the model generalises.

---

## 8. How these surface (dbt + Streamlit)

**dbt models**

- `stg_voy__customers`, `stg_voy__acq_orders`, `stg_voy__activity` — cleaned staging.
- `int_customer_continuous_subscriptions` — the gaps-and-islands primitive (§2).
- `dim_customer` — customer + country + taxonomy + cohort_month.
- `fct_customer_per_month_snapshot` — **analysis-ready** customer × month fact (has_active_subscription, has_continuous_active_subscription,
  tenure, new/reactivated/churned, dims). The table everything else reads.
- `viz_cohort_retention`, `viz_active_users_daily` — thin reporting marts feeding the dashboard.

**Streamlit dashboard** reads the marts from BigQuery and renders: cohort retention heatmap
(survival) with activity-based overlay, survival curves by country/taxonomy, DAU/MAU trend with
the 32-day active line, monthly churn, and acquisition intake — all filterable by country and
taxonomy, with the partial month excluded.

**AI interaction.** With LookML off, the governed contract is the **dbt layer itself**: one tall,
**described and tested** fact (`fct_customer_per_month_snapshot`), the metric SQL in this document, and dbt docs.
That single consistent grain + documented definitions is what lets an LLM/agent answer questions
like *"hair-loss survival retention in Brazil for the 2023-Q1 cohort"* against one definition
rather than re-deriving ad-hoc SQL.

---

## 9. Reference values (profiled as of 2024-08-16, read-only)

| Fact | Value |
|---|---:|
| Registered customers | 532,848 |
| — Brazil / United Kingdom | 296,693 / 236,155 |
| Ever-active customers | 512,366 |
| Registered but never active | 20,482 |
| Subscriptions | 1,482,002 |
| Activity spells | 2,176,168 |
| Median subscriptions per customer | 2 (max 65) |
| Acquisition groups | Hair Loss 383,273 · ED 73,086 · Weight Loss 41,736 · Other 7,038 · Sleep 2,275 · TRT 1,285 · Mental Health 1 |
| Missing taxonomy (ever-active) | 4,896 → `Unknown` |
| Active last 30d / 90d | 195,503 / 229,593 |
| Date range | 2019-01-04 → 2024-08-16 |
