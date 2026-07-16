#!/usr/bin/env bash
# 10-enable-apis.sh — Runbook §2: enable the APIs the deployment needs.
#
# Idempotent: enabling an already-enabled service is a no-op. Enabling APIs is
# free; you're billed for resources, not for turning APIs on.

source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
load_env
require_gcloud
require_isolated_config
require PROJECT_ID
show_active_config

# Minimal set for an IAP-only, hardened single-VM agent:
APIS=(
  compute.googleapis.com          # VMs, networking, firewall
  iap.googleapis.com              # Identity-Aware Proxy (SSH tunnel)
  oslogin.googleapis.com          # OS Login (IAM-managed SSH keys)
  iam.googleapis.com              # service accounts
  iamcredentials.googleapis.com   # short-lived credentials / impersonation
  logging.googleapis.com          # VM + audit logs
  monitoring.googleapis.com       # metrics for the budget/alerts story
  cloudbilling.googleapis.com     # billing reads
  billingbudgets.googleapis.com   # budget alert (§12)
)

echo "Enabling ${#APIS[@]} APIs on '${PROJECT_ID}' (this can take a minute)..."
gc services enable "${APIS[@]}"

echo
echo "Enabled services:"
gc services list --enabled --format='value(config.name)' \
  | grep -E 'compute|iap|oslogin|iam|logging|monitoring|billing' || true

echo
echo "Done. Next: ./scripts/deploy/11-service-account.sh"
