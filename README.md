# Voy — Customer Retention Models

A dbt + BigQuery model of Voy's subscription retention, with a Streamlit dashboard
for stakeholders. Built for the Lead Data Engineer exercise.

> **The one idea:** a customer is active independently of how many subscriptions
> they hold. Everything is built on merging each customer's subscription spells into
> continuous **active periods**, then measuring retention, churn and active users
> from that customer-level primitive.

---

## What's here

```
voy/
├── README.md
├── dbt_project.yml            # vars: active_window_days=32, snapshot_date, …
├── packages.yml               # dbt_utils
├── profiles.example.yml
├── models/
│   ├── staging/               # stg_voy__customers / acq_orders / activity
│   ├── intermediate/
│   │   └── int_customer_active_periods.sql   # ← gaps-and-islands merge (core)
│   └── marts/
│       ├── dim_customer.sql
│       ├── fct_customer_month.sql            # ← analysis-ready fact
│       ├── rpt_cohort_retention.sql
│       └── rpt_active_users_daily.sql
├── tests/                     # singular data-integrity tests
├── analyses/validation_checks.sql
├── streamlit/                 # dashboard (reads the marts)
└── docs/
    ├── metric_definitions.md  # exact formulas + SQL for every metric
    └── data_quality.md        # profiling findings, caveats, validation
```

## Model layers

| Layer | Model | Grain | Purpose |
|---|---|---|---|
| staging | `stg_voy__*` | source | type / rename / clean; `Unknown` taxonomy bucket |
| intermediate | `int_customer_active_periods` | customer × continuous period | **merge spells → active periods** (subscription-count-independent) |
| marts | `dim_customer` | customer | conformed dimension (+ activation flag) |
| marts | `fct_customer_month` | customer × month | **analysis-ready**: is_active, survival, tenure, new/reactivated/churned |
| marts | `rpt_cohort_retention` | cohort × tenure × dims | survival + activity retention |
| marts | `rpt_active_users_daily` | day × dims | DAU + 32-day active window |

`fct_customer_month` is the table to analyse — tall, drillable, not pre-aggregated.
The `rpt_*` models are thin reporting layers that feed the dashboard.

## Metric definitions

Full formulas and SQL are in **[`docs/metric_definitions.md`](docs/metric_definitions.md)**. In brief:

- **Active user** — holds a live subscription **and** active in the last **32 days**
  (`active_window_days`, configurable).
- **Survival retention** (primary) — share of a cohort still in their first unbroken
  subscription at tenure *n*.
- **Activity retention** (secondary) — share active at tenure *n*, win-backs included.
- **Churn** — monthly logo churn = 1 − (active in both M-1 and M ÷ active in M-1).
- **Acquisition / activation** — new customers per cohort; never-subscribed tracked
  as a conversion metric, not churn.

Revenue retention (NRR/GRR) is intentionally out of scope — no price/plan data exists.

## Run it

```bash
# 1. dbt models  (needs a profile — see profiles.example.yml; read-only source access)
dbt deps
dbt build            # runs models + tests

# 2. dashboard
cd streamlit
pip install -r requirements.txt
cp secrets.example.toml .streamlit/secrets.toml   # edit project/dataset
streamlit run app.py
```

## Time granularity

Monthly is the reporting default (subscription business); the interval-based
intermediate makes **daily/weekly** active-user views available without rebuilding.
Cohorts are bucketed monthly.

## How AI interacts with the data

With no separate semantic tool in scope, the **dbt layer is the contract**: one tall,
described, tested fact (`fct_customer_month`) plus the documented metric SQL. A single
consistent grain and governed definitions let an LLM/agent answer questions like
*"hair-loss survival retention in Brazil for the 2023-Q1 cohort"* against one
definition instead of re-deriving ad-hoc SQL.

## Validation

Model logic was reconciled read-only against the raw data (see `docs/data_quality.md`):
island merge reproduces raw MAU exactly (Jul-2024 = 191,784), zero overlapping
periods, and the 2023-01 cohort curve behaves correctly (activity ≥ survival at every
tenure). Integrity is enforced continuously by the tests in `tests/` and the schema
`dbt_utils` tests.
