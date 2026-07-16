#!/usr/bin/env bash
# deploy-all.sh — run the full hardened deployment in order (10 -> 16).
#
# Prompts once, then runs every step with ASSUME_YES=1 so the per-VM prompt in
# 15-create-vm.sh doesn't stop the batch. Each step is idempotent, so re-running
# this after a partial failure is safe.
#
# Prerequisites (Step 0): env.sh filled in, ./scripts/bootstrap/setup-config.sh run,
# project exists, billing linked.

# Directory of THIS script (scripts/deploy) — where the numbered steps live.
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "${DEPLOY_DIR}/../lib.sh"
load_env
require_gcloud
require_isolated_config
require PROJECT_ID
show_active_config

cat <<EOF

This will run the full hardened deployment against project '${PROJECT_ID}':
  10  enable APIs
  11  create least-privilege service account
  12  create custom VPC + subnet
  13  create IAP-only firewall rules
  14  grant your account IAP + OS Login access
  15  create the hardened VM            <-- starts incurring cost
  16  create the budget alert

EOF

if ! confirm "Run all steps now?"; then
  echo "Aborted."
  exit 1
fi

export ASSUME_YES=1
for step in \
  10-enable-apis.sh \
  11-service-account.sh \
  12-network.sh \
  13-firewall-iap.sh \
  14-iam-iap.sh \
  15-create-vm.sh \
  16-budget-alert.sh; do
  echo
  echo "==================== ${step} ===================="
  "${DEPLOY_DIR}/${step}"
done

echo
echo "All steps complete. Connect with: ./scripts/deploy/ssh-agent.sh"
