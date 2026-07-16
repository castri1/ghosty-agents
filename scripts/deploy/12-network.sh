#!/usr/bin/env bash
# 12-network.sh — Runbook §4: custom VPC + single subnet for the agent.
#
# A custom-mode VPC (no auto subnets) gives a small, well-defined surface: one
# subnet in one region, no surprise ranges in other regions. The VM gets NO
# external IP (set in 15-create-vm.sh), so egress for package installs etc. is
# handled separately if needed (e.g. Cloud NAT — not created here to keep cost
# and surface minimal; add it only if the agent needs outbound internet).
#
# Idempotent: skips resources that already exist.

source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
load_env
require_gcloud
require_isolated_config
require PROJECT_ID
show_active_config

# --- VPC (custom mode) ----------------------------------------------------
if resource_exists "networks describe ${NETWORK}"; then
  echo "VPC '${NETWORK}' already exists — skipping."
else
  echo "Creating custom-mode VPC '${NETWORK}'..."
  gc compute networks create "${NETWORK}" \
    --subnet-mode=custom \
    --bgp-routing-mode=regional
fi

# --- Subnet ---------------------------------------------------------------
# Private Google Access lets the no-external-IP VM reach Google APIs (logging,
# monitoring, etc.) without a public address.
if resource_exists "networks subnets describe ${SUBNET} --region=${REGION}"; then
  echo "Subnet '${SUBNET}' already exists — skipping."
else
  echo "Creating subnet '${SUBNET}' (${SUBNET_RANGE}) in ${REGION}..."
  gc compute networks subnets create "${SUBNET}" \
    --network="${NETWORK}" \
    --region="${REGION}" \
    --range="${SUBNET_RANGE}" \
    --enable-private-ip-google-access
fi

echo
echo "Done. Next: ./scripts/deploy/13-firewall-iap.sh"
