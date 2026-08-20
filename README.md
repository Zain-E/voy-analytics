# Voy — Customer Retention Models

A dbt + BigQuery model of Voy's subscription retention, with a Streamlit dashboard
for stakeholders. Built for the Lead Data Engineer exercise.

> **The one idea:** a customer is active independently of how many subscriptions
> they hold. Everything is built on merging each customer's subscription spells into
> continuous **subscription periods**, then measuring retention, churn and active users
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
│   │   └── int_customer_continuous_subscriptions.sql   # ← gaps-and-islands merge (core)
│   └── marts/
│       ├── dim_customer.sql
│       ├── fct_customer_per_month_snapshot.sql            # ← analysis-ready fact
│       ├── viz_cohort_retention.sql
│       └── viz_active_users_daily.sql
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
| intermediate | `int_customer_continuous_subscriptions` | customer × continuous period | **merge spells → continuous subscription periods** (subscription-count-independent) |
| marts | `dim_customer` | customer | conformed dimension (+ activation flag) |
| marts | `fct_customer_per_month_snapshot` | customer × month | **analysis-ready**: has_active_subscription, has_continuous_active_subscription, tenure, new/reactivated/churned |
| marts | `viz_cohort_retention` | cohort × tenure × dims | never-churned + total retention |
| marts | `viz_active_users_daily` | day × dims | DAU + 32-day active window |

`fct_customer_per_month_snapshot` is the table to analyse — tall, drillable, not pre-aggregated.
The `viz_*` models are thin reporting layers that feed the dashboard.

## Metric definitions

Full formulas and SQL are in **[`docs/metric_definitions.md`](docs/metric_definitions.md)**. In brief:

- **Active user** — holds a live subscription **and** active in the last **32 days**
  (`active_window_days`, configurable).
- **Never-churned retention** (`never_churned_retention`, primary) — share of a cohort still in
  their first unbroken subscription at tenure *n* (never churned).
- **Total retention** (`total_retention`, secondary) — share active at tenure *n*, win-backs
  included (never-churned + reactivated).
- **Churn** — monthly logo churn = 1 − (active in both M-1 and M ÷ active in M-1).
- **Acquisition / activation** — new customers per cohort; never-subscribed tracked
  as a conversion metric, not churn.

Revenue retention (NRR/GRR) is intentionally out of scope — no price/plan data exists.

## Run it

Two convenience scripts at the repo root:

```bash
gcloud auth application-default login   # one-time — authenticate as yourself

# dbt: activate a venv with dbt-bigquery, then build
python3 -m venv .venv && source .venv/bin/activate && pip install dbt-bigquery
./run_dbt.sh          # dbt deps + dbt build (models + tests)

# dashboard (installs into your active venv)
./run_streamlit.sh
```

`./run_dbt.sh` passes extra args straight to dbt, e.g. `./run_dbt.sh test` or
`./run_dbt.sh docs generate`. The connection profile lives in
**`dbt_profiles/profiles.yml`** (the script points dbt there via `--profiles-dir`);
it has a `dev` target (local oauth) and a `ci` target (service account). Local dev
just needs `gcloud auth application-default login`. `./run_streamlit.sh` reads
`streamlit/.streamlit/secrets.toml` (copy from `streamlit/secrets.example.toml`).

<details><summary>Manual steps (if you'd rather not use the scripts)</summary>

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
</details>

## Time granularity

Monthly is the reporting default (subscription business); the interval-based
intermediate makes **daily/weekly** active-user views available without rebuilding.
Cohorts are bucketed monthly.

## How AI interacts with the data

With no separate semantic tool in scope, the **dbt layer is the contract**: one tall,
described, tested fact (`fct_customer_per_month_snapshot`) plus the documented metric SQL. A single
consistent grain and governed definitions let an LLM/agent answer questions like
*"hair-loss survival retention in Brazil for the 2023-Q1 cohort"* against one
definition instead of re-deriving ad-hoc SQL.

## Validation

Model logic was reconciled read-only against the raw data (see `docs/data_quality.md`):
island merge reproduces raw MAU exactly (Jul-2024 = 191,784), zero overlapping
periods, and the 2023-01 cohort curve behaves correctly (activity ≥ survival at every
tenure, total ≥ never-churned). Integrity is enforced continuously by the tests in `tests/` and the schema
`dbt_utils` tests.
