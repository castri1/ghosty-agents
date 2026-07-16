#!/usr/bin/env bash
# 13-firewall-iap.sh — Runbook §5: lock SSH ingress to IAP only.
#
# Two rules:
#   1. allow-ssh-from-iap : tcp:22 ingress ONLY from Google's IAP range
#      (35.235.240.0/20), scoped to the agent's network tag.
#   2. deny-all-ingress   : a low-priority catch-all deny so nothing else can
#      reach the VM even if a later rule is added loosely.
#
# Combined with a VM that has no external IP, the only path in is:
#   you -> IAP (Google-authenticated) -> VM:22
#
# Idempotent: skips rules that already exist.

source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
load_env
require_gcloud
require_isolated_config
require PROJECT_ID
show_active_config

# --- Allow SSH from IAP only ----------------------------------------------
if resource_exists "firewall-rules describe ${NETWORK}-allow-ssh-from-iap"; then
  echo "Firewall '${NETWORK}-allow-ssh-from-iap' already exists — skipping."
else
  echo "Creating IAP-only SSH ingress rule (source ${IAP_CIDR})..."
  gc compute firewall-rules create "${NETWORK}-allow-ssh-from-iap" \
    --network="${NETWORK}" \
    --direction=INGRESS \
    --action=ALLOW \
    --rules=tcp:22 \
    --source-ranges="${IAP_CIDR}" \
    --target-tags="${NETWORK_TAG}" \
    --priority=1000
fi

# --- Explicit low-priority deny-all ingress -------------------------------
if resource_exists "firewall-rules describe ${NETWORK}-deny-all-ingress"; then
  echo "Firewall '${NETWORK}-deny-all-ingress' already exists — skipping."
else
  echo "Creating catch-all deny-ingress rule (priority 65534)..."
  gc compute firewall-rules create "${NETWORK}-deny-all-ingress" \
    --network="${NETWORK}" \
    --direction=INGRESS \
    --action=DENY \
    --rules=all \
    --source-ranges=0.0.0.0/0 \
    --priority=65534
fi

echo
echo "Ingress is now IAP-only. Done. Next: ./scripts/deploy/14-iam-iap.sh"
