#!/usr/bin/env bash
# 00-create-project.sh — Step 0.3 (Option A): ensure the dedicated Hermes project
# exists. Idempotent — if PROJECT_ID already exists and is accessible, creation
# is skipped (safe to run when you created the project beforehand).
#
# Prerequisite: env.sh filled in and `gcloud auth login` already done.
# Under an org you need roles/resourcemanager.projectCreator (automatic on a
# personal/no-org account).

source "$(dirname "${BASH_SOURCE[0]}")/../lib.sh"
load_env
require_gcloud
require_isolated_config
require PROJECT_ID
show_active_config

# Idempotent: if the project already exists AND you can access it, skip creation.
# 'projects describe' must NOT assume the project is active context, so skip the
# implicit --project flag for these calls.
if GCLOUD_NO_PROJECT=1 gc projects describe "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "Project '${PROJECT_ID}' already exists and is accessible — skipping creation."
else
  echo "Creating project '${PROJECT_ID}' (name: '${PROJECT_NAME}')..."
  GCLOUD_NO_PROJECT=1 gc projects create "${PROJECT_ID}" --name="${PROJECT_NAME}"
fi

# The project is already pinned in the isolated config by setup-config.sh, so we
# deliberately do NOT run 'gcloud config set project' here (that would be global
# to the active config and risks touching a shared one).
echo "Done. Next: ./scripts/bootstrap/01-link-billing.sh"
