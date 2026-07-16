#!/usr/bin/env bash
# 16-budget-alert.sh — Runbook §12: budget alert on the DEDICATED billing account.
#
# Because both the billing account and the project are dedicated to this agent,
# the budget tracks exactly one workload — so a runaway loop or a hijacked box
# shows up immediately instead of being buried in other projects' spend.
#
# Thresholds fire email alerts to the billing account's admins (you) at 50%,
# 90%, and 100% of BUDGET_AMOUNT. A budget alert does NOT cap spend; it notifies.
#
# Idempotent: skips creation if a budget with this display name already exists.

source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
load_env
require_gcloud
require_isolated_config
require PROJECT_ID
require BILLING_ACCOUNT_ID
show_active_config

# Scope the budget to THIS project only (filter uses the numeric project number).
PROJECT_NUM="$(project_number)"
if [[ -z "${PROJECT_NUM}" ]]; then
  echo "error: could not resolve project number for '${PROJECT_ID}'." >&2
  exit 1
fi

EXISTING="$(GCLOUD_NO_PROJECT=1 gc billing budgets list \
  --billing-account="${BILLING_ACCOUNT_ID}" \
  --format='value(displayName)' 2>/dev/null | grep -Fx "${BUDGET_NAME}" || true)"

if [[ -n "${EXISTING}" ]]; then
  echo "Budget '${BUDGET_NAME}' already exists on ${BILLING_ACCOUNT_ID} — skipping."
  exit 0
fi

echo "Creating budget '${BUDGET_NAME}' = ${BUDGET_AMOUNT}${BUDGET_CURRENCY}"
echo "  billing account : ${BILLING_ACCOUNT_ID}"
echo "  scoped to       : projects/${PROJECT_NUM} (${PROJECT_ID})"
GCLOUD_NO_PROJECT=1 gc billing budgets create \
  --billing-account="${BILLING_ACCOUNT_ID}" \
  --display-name="${BUDGET_NAME}" \
  --budget-amount="${BUDGET_AMOUNT}${BUDGET_CURRENCY}" \
  --filter-projects="projects/${PROJECT_NUM}" \
  --threshold-rule=percent=0.5 \
  --threshold-rule=percent=0.9 \
  --threshold-rule=percent=1.0

echo
echo "Budget alert set. Step 0 + hardened runbook complete."
