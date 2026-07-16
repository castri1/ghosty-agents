#!/usr/bin/env bash
# 15-create-vm.sh — Runbook §6: create the hardened agent VM.
#
# *** THIS IS THE FIRST SCRIPT THAT CREATES A BILLABLE RESOURCE. ***
#
# Hardening applied:
#   --no-address                  : no external IP (only reachable via IAP)
#   --shielded-secure-boot        : verified boot chain
#   --shielded-vtpm + integrity   : measured boot + tamper detection
#   enable-oslogin=TRUE           : IAM-managed SSH, no static metadata keys
#   block-project-ssh-keys=TRUE   : ignore project-wide SSH keys
#   dedicated SA + cloud-platform : authorize via IAM, not broad legacy scopes
#   custom subnet + network tag   : firewall (IAP-only) applies to this VM
#
# Idempotent: skips creation if the instance already exists.

source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
load_env
require_gcloud
require_isolated_config
require PROJECT_ID
require INSTANCE_NAME
show_active_config

SA_EMAIL="$(sa_email)"

if resource_exists "instances describe ${INSTANCE_NAME} --zone=${ZONE}"; then
  echo "Instance '${INSTANCE_NAME}' already exists in ${ZONE} — skipping creation."
  exit 0
fi

cat <<EOF

About to create a VM (this starts incurring cost):
  instance : ${INSTANCE_NAME}
  zone     : ${ZONE}
  machine  : ${MACHINE_TYPE}
  image    : ${IMAGE_FAMILY} (${IMAGE_PROJECT})
  disk     : ${BOOT_DISK_SIZE} ${BOOT_DISK_TYPE}
  network  : ${NETWORK} / ${SUBNET} (no external IP)
  sa       : ${SA_EMAIL}
EOF

if ! confirm "Create this VM?"; then
  echo "Aborted — no VM created."
  exit 1
fi

echo "Creating hardened VM '${INSTANCE_NAME}'..."
gc compute instances create "${INSTANCE_NAME}" \
  --zone="${ZONE}" \
  --machine-type="${MACHINE_TYPE}" \
  --image-family="${IMAGE_FAMILY}" \
  --image-project="${IMAGE_PROJECT}" \
  --boot-disk-size="${BOOT_DISK_SIZE}" \
  --boot-disk-type="${BOOT_DISK_TYPE}" \
  --network="${NETWORK}" \
  --subnet="${SUBNET}" \
  --no-address \
  --tags="${NETWORK_TAG}" \
  --service-account="${SA_EMAIL}" \
  --scopes="https://www.googleapis.com/auth/cloud-platform" \
  --shielded-secure-boot \
  --shielded-vtpm \
  --shielded-integrity-monitoring \
  --metadata=enable-oslogin=TRUE,block-project-ssh-keys=TRUE

echo
echo "VM created. Connect via IAP with: ./scripts/deploy/ssh-agent.sh"
echo "Next (cost safety): ./scripts/deploy/16-budget-alert.sh"
