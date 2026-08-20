#!/usr/bin/env bash
#
# Generate and serve the dbt documentation site — including the auto-generated
# LINEAGE GRAPH, which is the single source of truth for model lineage
# (no hand-maintained diagram to drift out of sync with the code).
#
#   ./run_docs.sh              # dbt deps + docs generate + docs serve  → http://localhost:8080
#   ./run_docs.sh --port 9000  # extra args pass straight through to `dbt docs serve`
#
# In the browser, click the green "View Lineage Graph" button (bottom-right) for
# the full DAG; click any node for its docs, columns and tests.
#
# Assumes `dbt` is available — activate your virtualenv first, e.g.:
#   python3 -m venv .venv && source .venv/bin/activate && pip install dbt-bigquery
#
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v dbt >/dev/null 2>&1; then
  echo "✗ dbt not found on PATH. Activate your virtualenv and install dbt-bigquery:" >&2
  echo "    python3 -m venv .venv && source .venv/bin/activate && pip install dbt-bigquery" >&2
  exit 1
fi

# Use the repo's dbt_profiles/ folder if present, otherwise fall back to ~/.dbt.
PROFILES_ARG=()
if [ -f "dbt_profiles/profiles.yml" ]; then
  PROFILES_ARG=(--profiles-dir dbt_profiles)
fi

echo "› dbt deps"
dbt deps "${PROFILES_ARG[@]}"
echo "› dbt docs generate"
dbt docs generate "${PROFILES_ARG[@]}"
echo "› dbt docs serve  (Ctrl-C to stop)"
dbt docs serve "${PROFILES_ARG[@]}" "$@"
