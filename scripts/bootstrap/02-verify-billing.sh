#!/usr/bin/env bash
# 02-verify-billing.sh — Step 0.5: verify the link points at the right account.
#
# Confirms billingEnabled: true and that billingAccountName matches the NEW
# account ID from env.sh.

source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
load_env
require_gcloud
require_isolated_config
require PROJECT_ID
require BILLING_ACCOUNT_ID
show_active_config

echo "Describing billing for project '${PROJECT_ID}'..."
GCLOUD_NO_PROJECT=1 gc billing projects describe "${PROJECT_ID}"

echo
echo "Verify above that:"
echo "  billingAccountName: billingAccounts/${BILLING_ACCOUNT_ID}"
echo "  billingEnabled:     true"
echo
echo "If billingAccountName shows a different (old) account, re-run"
echo "./scripts/bootstrap/01-link-billing.sh with the correct BILLING_ACCOUNT_ID."
