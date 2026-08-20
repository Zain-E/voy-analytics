#!/usr/bin/env bash
#
# Build the Voy dbt models (and tests) in BigQuery.
#
#   ./run_dbt.sh              # dbt deps + dbt build   (the default)
#   ./run_dbt.sh test         # pass any dbt command through, e.g. `test`, `run`, `docs generate`
#
# Assumes `dbt` is already available — activate your virtualenv first, e.g.:
#   python3 -m venv .venv && source .venv/bin/activate && pip install dbt-bigquery
#
# Uses the profile in dbt_profiles/profiles.yml (via --profiles-dir); falls back
# to ~/.dbt/profiles.yml if that folder is absent.
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

if [ "$#" -gt 0 ]; then
  echo "› dbt $*"
  dbt "$@" "${PROFILES_ARG[@]}"
else
  echo "› dbt build"
  dbt build "${PROFILES_ARG[@]}"
fi
