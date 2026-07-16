#!/usr/bin/env bash
# 01-link-billing.sh — Step 0.4: link the NEW billing account to the project.
#
# Mandatory — without linked billing, Compute Engine refuses to create the VM.
# Make sure BILLING_ACCOUNT_ID in env.sh is the NEW dedicated account, not an
# existing one.

source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
load_env
require_gcloud
require_isolated_config
require PROJECT_ID
require BILLING_ACCOUNT_ID
show_active_config

# 'billing projects link' takes the project as a positional arg; the implicit
# --project from gc() is harmless and the positional is authoritative.
echo "Linking project '${PROJECT_ID}' to billing account '${BILLING_ACCOUNT_ID}'..."
GCLOUD_NO_PROJECT=1 gc billing projects link "${PROJECT_ID}" \
  --billing-account="${BILLING_ACCOUNT_ID}"

echo "Done. Next: ./scripts/bootstrap/02-verify-billing.sh"
