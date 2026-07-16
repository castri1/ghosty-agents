#!/usr/bin/env bash
# list-billing-accounts.sh — Step 0.2 helper: list billing accounts so you can
# copy the NEW account's ID (match by the NAME you chose) into env.sh.

source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
load_env
require_gcloud

# Read-only and account-scoped (no project context needed), so this runs before
# the isolated config exists. We still pin --account when known so it can't be
# affected by another session's active account.
acct_flag=()
if [[ -n "${MY_ACCOUNT:-}" && "${MY_ACCOUNT}" != REPLACE_* ]]; then
  acct_flag=(--account="${MY_ACCOUNT}")
fi

echo "Billing accounts visible to your login:"
gcloud billing accounts list "${acct_flag[@]}"
echo
echo "Copy the ACCOUNT_ID of the NEW account (OPEN: True) into env.sh -> BILLING_ACCOUNT_ID"
