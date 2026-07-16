#!/usr/bin/env bash
# setup-config.sh — create an ISOLATED gcloud configuration for Hermes.
#
# Why: another process is running gcloud as the same user/account but in a
# different project. We must never touch the shared 'default' configuration or
# the global active_config pointer. Setting CLOUDSDK_ACTIVE_CONFIG_NAME (done in
# env.sh) scopes everything below to a named config used by THIS shell only.
#
# Idempotent: safe to re-run. Creates the named config if missing, then writes
# project/account INTO that config (never into 'default').
#
# Run once after editing env.sh:
#   source env.sh && ./scripts/bootstrap/setup-config.sh

source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
load_env
require_gcloud
require PROJECT_ID
require MY_ACCOUNT

CONFIG_NAME="${CLOUDSDK_ACTIVE_CONFIG_NAME:?CLOUDSDK_ACTIVE_CONFIG_NAME must be set (source env.sh)}"

# Create the named config if it doesn't already exist. Use --no-activate so we
# never flip the GLOBAL active_config pointer that the other session relies on;
# this shell still uses it because CLOUDSDK_ACTIVE_CONFIG_NAME is exported.
if gcloud config configurations list --format='value(name)' | grep -qx "${CONFIG_NAME}"; then
  echo "Config '${CONFIG_NAME}' already exists — reusing it."
else
  echo "Creating isolated gcloud config '${CONFIG_NAME}'..."
  gcloud config configurations create "${CONFIG_NAME}" --no-activate
fi

# Populate ONLY this config. Because CLOUDSDK_ACTIVE_CONFIG_NAME is exported,
# these writes land in the '${CONFIG_NAME}' config file, not 'default'.
echo "Setting account=${MY_ACCOUNT}, project=${PROJECT_ID} in config '${CONFIG_NAME}'..."
gcloud config set account "${MY_ACCOUNT}"
gcloud config set project "${PROJECT_ID}"

echo
echo "Active config for THIS shell:"
gcloud config configurations list --filter="name=${CONFIG_NAME}"
echo
echo "Verify your OTHER session is unaffected — its active_config is unchanged."
echo "Next: ./scripts/bootstrap/00-create-project.sh"
