#!/usr/bin/env bash
# 14-iam-iap.sh — Runbook §2/§6: IAM bindings so YOU can reach the VM via IAP.
#
# To SSH through IAP with OS Login you need three things on your account:
#   - roles/iap.tunnelResourceAccessor : permission to open the IAP tunnel
#   - roles/compute.osLogin             : a normal (non-sudo) Linux login
#   - roles/iam.serviceAccountUser on the VM's SA : to "act as" the VM SA
#
# Swap roles/compute.osLogin for roles/compute.osAdminLogin if you need sudo on
# the box. Bindings are scoped to your MY_ACCOUNT only.
#
# Idempotent: add-iam-policy-binding is safe to re-run.

source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
load_env
require_gcloud
require_isolated_config
require PROJECT_ID
require MY_ACCOUNT
show_active_config

SA_EMAIL="$(sa_email)"
MEMBER="user:${MY_ACCOUNT}"

# Project-level roles for your account.
USER_ROLES=(
  roles/iap.tunnelResourceAccessor
  roles/compute.osLogin
)

echo "Granting IAP + OS Login roles to ${MEMBER}..."
for role in "${USER_ROLES[@]}"; do
  echo "  + ${role}"
  GCLOUD_NO_PROJECT=1 gc projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="${MEMBER}" \
    --role="${role}" \
    --condition=None \
    --quiet >/dev/null
done

# Allow your account to act as the VM's service account (required to SSH into a
# VM that runs as a non-default SA).
if gc iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1; then
  echo "  + roles/iam.serviceAccountUser on ${SA_EMAIL}"
  gc iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
    --member="${MEMBER}" \
    --role="roles/iam.serviceAccountUser" \
    --quiet >/dev/null
else
  echo "warning: service account ${SA_EMAIL} not found — run 11-service-account.sh first." >&2
fi

echo
echo "IAP access granted to ${MY_ACCOUNT}. Done. Next: ./scripts/deploy/15-create-vm.sh"
