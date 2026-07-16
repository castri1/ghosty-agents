#!/usr/bin/env bash
# 11-service-account.sh — Runbook §3: dedicated least-privilege service account
# for the agent VM.
#
# Why a dedicated SA: never run the VM as the default Compute SA (which is
# broadly privileged). This SA starts with NO project roles; grant only what the
# agent actually needs (logging + monitoring writers by default). Add more with
# explicit, reviewable bindings later.
#
# Idempotent: re-running re-asserts the same SA and role bindings.

source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
load_env
require_gcloud
require_isolated_config
require PROJECT_ID
require SA_ID
show_active_config

SA_EMAIL="$(sa_email)"

if gc iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1; then
  echo "Service account '${SA_EMAIL}' already exists — skipping creation."
else
  echo "Creating service account '${SA_EMAIL}'..."
  gc iam service-accounts create "${SA_ID}" \
    --display-name="${SA_DISPLAY_NAME}"
fi

# Least-privilege roles for an always-on agent box. Keep this list tight.
SA_ROLES=(
  roles/logging.logWriter
  roles/monitoring.metricWriter
)

echo "Granting ${#SA_ROLES[@]} project role(s) to the service account..."
for role in "${SA_ROLES[@]}"; do
  echo "  + ${role}"
  GCLOUD_NO_PROJECT=1 gc projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${role}" \
    --condition=None \
    --quiet >/dev/null
done

echo
echo "Service account ready: ${SA_EMAIL}"
echo "Done. Next: ./scripts/deploy/12-network.sh"
