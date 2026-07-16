#!/usr/bin/env bash
# env.sh — central configuration for the Hermes agent deployment.
#
# Usage:
#   1. Fill in the values below (see docs/step-0-billing-and-project.md).
#   2. Run:  source env.sh
#   3. Proceed to the hardened deployment runbook (section 2 onward).
#
# This file is sourced by the helper scripts in ./scripts and is intended to be
# the single source of truth for project-level identifiers.
#
# NOTE: This file can contain account/project identifiers. It is git-ignored by
# default (see .gitignore). Keep real values out of version control.

# ---------------------------------------------------------------------------
# Required — from Step 0 (billing & project bootstrap)
# ---------------------------------------------------------------------------

# The DEDICATED billing account ID for Hermes (format: 0X0X0X-0X0X0X-0X0X0X).
# Get it from: gcloud billing accounts list   (match by the NAME you chose).
export BILLING_ACCOUNT_ID="REPLACE_WITH_BILLING_ACCOUNT_ID"

# Globally-unique project ID (6–30 chars, lowercase/digits/hyphens).
# e.g. hermes-prod-7f3a
export PROJECT_ID="hermes-prod-7f3a"

# Human-friendly project display name.
export PROJECT_NAME="Hermes Prod"

# The GCP login email you are signed in as (used for IAP/IAM bindings later).
export MY_ACCOUNT="REPLACE_WITH_YOUR_GCP_EMAIL"

# ---------------------------------------------------------------------------
# Isolation — keep this work from colliding with other gcloud sessions
# ---------------------------------------------------------------------------
#
# CLOUDSDK_ACTIVE_CONFIG_NAME overrides the active gcloud configuration for THIS
# shell process only. It does NOT modify ~/.config/gcloud/active_config, so any
# other terminal/process running gcloud (same user, same account, different
# project) keeps using its own active config untouched.
#
# Run ./scripts/bootstrap/setup-config.sh once to create + populate this config.
export CLOUDSDK_ACTIVE_CONFIG_NAME="${CLOUDSDK_ACTIVE_CONFIG_NAME:-hermes}"

# ---------------------------------------------------------------------------
# Deployment defaults — the hardened runbook (section 2 onward)
# ---------------------------------------------------------------------------

# Compute region/zone for the agent VM.
export REGION="${REGION:-us-central1}"
export ZONE="${ZONE:-us-central1-a}"

# --- VM instance ----------------------------------------------------------
export INSTANCE_NAME="${INSTANCE_NAME:-hermes-agent}"
# Small + cheap default; bump if the agent needs more memory/CPU.
export MACHINE_TYPE="${MACHINE_TYPE:-e2-small}"
export BOOT_DISK_SIZE="${BOOT_DISK_SIZE:-20GB}"
export BOOT_DISK_TYPE="${BOOT_DISK_TYPE:-pd-balanced}"
export IMAGE_FAMILY="${IMAGE_FAMILY:-debian-12}"
export IMAGE_PROJECT="${IMAGE_PROJECT:-debian-cloud}"
# Network tag the firewall rule targets; only tagged VMs get IAP SSH ingress.
export NETWORK_TAG="${NETWORK_TAG:-hermes-agent}"

# --- Network (custom VPC, no auto subnets) --------------------------------
export NETWORK="${NETWORK:-hermes-vpc}"
export SUBNET="${SUBNET:-hermes-subnet}"
export SUBNET_RANGE="${SUBNET_RANGE:-10.10.0.0/24}"

# --- Dedicated least-privilege service account for the VM -----------------
# Short ID (before the @); the full email is derived in lib.sh.
export SA_ID="${SA_ID:-hermes-agent-sa}"
export SA_DISPLAY_NAME="${SA_DISPLAY_NAME:-Hermes Agent VM}"

# --- Budget alert (runbook §12) -------------------------------------------
# Monthly budget threshold, in the billing account's currency.
export BUDGET_AMOUNT="${BUDGET_AMOUNT:-50}"
export BUDGET_NAME="${BUDGET_NAME:-Hermes Monthly Budget}"
# Currency must match the billing account's fixed currency (set at creation).
export BUDGET_CURRENCY="${BUDGET_CURRENCY:-USD}"

# ---------------------------------------------------------------------------
# Sanity check: warn if placeholders are still present.
# ---------------------------------------------------------------------------
if [[ "${BILLING_ACCOUNT_ID}" == REPLACE_* || "${MY_ACCOUNT}" == REPLACE_* ]]; then
  echo "env.sh: WARNING — placeholder values detected. Edit env.sh before deploying." >&2
fi
