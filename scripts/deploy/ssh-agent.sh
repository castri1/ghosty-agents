#!/usr/bin/env bash
# ssh-agent.sh — connect to the agent VM through IAP (no public IP needed).
#
# Uses `gcloud compute ssh --tunnel-through-iap`, which opens an authenticated
# IAP tunnel to port 22. Requires the IAM bindings from 14-iam-iap.sh.
#
# Any extra args are passed to the remote shell, e.g.:
#   ./scripts/deploy/ssh-agent.sh -- uptime

source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
load_env
require_gcloud
require_isolated_config
require PROJECT_ID
require INSTANCE_NAME

echo "Opening IAP SSH tunnel to '${INSTANCE_NAME}' (${ZONE})..."
gc compute ssh "${INSTANCE_NAME}" \
  --zone="${ZONE}" \
  --tunnel-through-iap \
  "$@"
