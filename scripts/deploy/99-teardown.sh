#!/usr/bin/env bash
# 99-teardown.sh — delete the resources created by the runbook (reverse order).
#
# Stops ongoing cost by removing the VM and supporting infra. Does NOT delete the
# project, the billing link, or the budget (those are free to keep). To nuke the
# whole project instead, see the note at the bottom.
#
# Idempotent: skips anything already gone. DESTRUCTIVE — prompts before acting.

source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
load_env
require_gcloud
require_isolated_config
require PROJECT_ID
show_active_config

SA_EMAIL="$(sa_email)"

cat <<EOF

This will DELETE (if present), in order:
  - VM instance      ${INSTANCE_NAME} (${ZONE})
  - firewall rules   ${NETWORK}-allow-ssh-from-iap, ${NETWORK}-deny-all-ingress
  - subnet           ${SUBNET} (${REGION})
  - VPC              ${NETWORK}
  - service account  ${SA_EMAIL}

Project, billing link, and budget are left intact.
EOF

if ! confirm "Tear down these resources?"; then
  echo "Aborted — nothing deleted."
  exit 1
fi

del() {  # del "<describe args>" "<delete args>"  — delete only if it exists
  if resource_exists "$1"; then
    echo "Deleting: gcloud compute $2"
    # shellcheck disable=SC2086
    gc compute $2 --quiet
  else
    echo "Skip (absent): $2"
  fi
}

del "instances describe ${INSTANCE_NAME} --zone=${ZONE}" \
    "instances delete ${INSTANCE_NAME} --zone=${ZONE}"
del "firewall-rules describe ${NETWORK}-allow-ssh-from-iap" \
    "firewall-rules delete ${NETWORK}-allow-ssh-from-iap"
del "firewall-rules describe ${NETWORK}-deny-all-ingress" \
    "firewall-rules delete ${NETWORK}-deny-all-ingress"
del "networks subnets describe ${SUBNET} --region=${REGION}" \
    "networks subnets delete ${SUBNET} --region=${REGION}"
del "networks describe ${NETWORK}" \
    "networks delete ${NETWORK}"

if gc iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1; then
  echo "Deleting service account ${SA_EMAIL}"
  gc iam service-accounts delete "${SA_EMAIL}" --quiet
else
  echo "Skip (absent): service account ${SA_EMAIL}"
fi

cat <<EOF

Teardown complete.

To delete the ENTIRE project (and everything in it) in one shot instead:
  gcloud projects delete ${PROJECT_ID}
EOF
