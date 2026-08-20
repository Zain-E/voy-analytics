# Streamlit dashboard

Presentation layer over the dbt marts. No business logic lives here — every
number comes from `viz_*` / `fct_*` / `dim_*`.

Three tabs:

| Tab | What's on it | Source |
|---|---|---|
| Overview | Headline KPIs, this-month scorecards, active users, acquisition | `data.py` → marts |
| Retention | Cohort retention heatmap, retention curves, monthly churn | `data.py` → marts |
| Data model | Architecture DAG + ERD, zoomable and pannable | `data_model.py` → the dbt project files |

The Data model tab is generated from the dbt project rather than drawn by hand:
`models/**/*.sql` gives the lineage (every arrow is a real `ref()`), `models/**/*.yml`
gives grains, columns and tests, and `target/catalog.json` — when the project has been
built — gives real BigQuery column types.

Neither diagram is laid out in the browser — Mermaid can't lay one out in a tab that's
hidden on first paint, and the dashboard shouldn't need a CDN. The lineage DAG is Mermaid
pre-rendered to `assets/lineage.svg`; regenerate it after changing a model:

```bash
python streamlit/data_model.py     # rewrites assets/lineage.mmd + .svg (needs node)
```

The tab warns on itself if that render no longer matches the project, so a missed
regeneration shows up rather than passing silently. The ERD is drawn straight to SVG in
Python on every render (`erd_svg()`), so it needs no regeneration at all; its star layout
— `dim_customer` in the middle — is declared, because every arrow points away from the hub
and an auto-layout engine would push it to one edge. Zoom and pan are inline JS in both.

## Run

```bash
cd streamlit
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# auth as a read-only principal
gcloud auth application-default login

mkdir -p .streamlit
cp secrets.example.toml .streamlit/secrets.toml   # then edit project/dataset
streamlit run app.py
```

## Deploy (Streamlit Community Cloud)

| Setting | Value |
|---|---|
| Repository | `Zain-E/voy-analytics` |
| Branch | `main` |
| Main file path | `streamlit/app.py` |
| Python version | 3.14 (Cloud's default) or 3.13 to match local dev — both resolve |

Community Cloud looks for a dependency file next to the entrypoint before the repository
root, so it installs `streamlit/requirements.txt` and **not** the dbt pip-freeze in the
root `requirements.txt`. Streamlit also puts the entrypoint's directory on `sys.path`,
which is what makes `import data` work from a subdirectory app.

Paste `secrets.example.toml` into **Settings → Secrets**, this time *including* the
`[gcp_service_account]` block — the container has no gcloud, so Application Default
Credentials are unavailable and the key is the only way in. `data.py` uses the key when
that block is present and falls back to ADC when it isn't, so the same file works in both
places.

The Data model tab reads `models/**` and `dbt_project.yml` from the checkout, so it needs
nothing extra. `target/catalog.json` is gitignored and therefore absent on Cloud: the tab
falls back to inferred column types, which is the documented degraded path.

One caveat worth deciding on deliberately: the deployed app holds a live BigQuery client,
so anyone who can open the URL can run its queries against the project, billed to you. The
parquet cache makes repeat queries free and the marts are small, but apps from a public
repo are public by default — restrict viewers under **Share** if that is not what you
want (the free tier allows one private app).

## Prerequisite

The dbt models must be built first so the marts exist:

```bash
dbt build   # from the repo root, with a configured profile
```

Point `marts_dataset` in secrets at the dataset dbt wrote to (default `voy_analytics`).
