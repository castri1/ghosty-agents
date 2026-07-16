#!/usr/bin/env bash
# lib.sh — shared helpers for Step 0 scripts.
# Sourced by the numbered scripts; not meant to be run directly.

set -euo pipefail

# Resolve repo root regardless of where the script is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

load_env() {
  if [[ ! -f "${REPO_ROOT}/env.sh" ]]; then
    echo "error: ${REPO_ROOT}/env.sh not found. Copy/edit it first." >&2
    exit 1
  fi
  # shellcheck source=/dev/null
  source "${REPO_ROOT}/env.sh"
}

require() {
  local var="$1"
  local val="${!var:-}"
  if [[ -z "${val}" || "${val}" == REPLACE_* ]]; then
    echo "error: ${var} is not set in env.sh (got: '${val}')." >&2
    exit 1
  fi
}

require_gcloud() {
  if ! command -v gcloud >/dev/null 2>&1; then
    echo "error: gcloud CLI not found on PATH. Install the Google Cloud SDK." >&2
    exit 1
  fi
}

# Enforce that we are operating inside the dedicated, isolated gcloud config —
# never the shared 'default' config another session may be using.
require_isolated_config() {
  if [[ -z "${CLOUDSDK_ACTIVE_CONFIG_NAME:-}" ]]; then
    echo "error: CLOUDSDK_ACTIVE_CONFIG_NAME is not set. Run 'source env.sh' first." >&2
    exit 1
  fi
  if [[ "${CLOUDSDK_ACTIVE_CONFIG_NAME}" == "default" ]]; then
    echo "error: refusing to run against the shared 'default' gcloud config." >&2
    echo "       Set a dedicated name in env.sh (CLOUDSDK_ACTIVE_CONFIG_NAME)." >&2
    exit 1
  fi
  if ! gcloud config configurations list --format='value(name)' 2>/dev/null \
        | grep -qx "${CLOUDSDK_ACTIVE_CONFIG_NAME}"; then
    echo "error: gcloud config '${CLOUDSDK_ACTIVE_CONFIG_NAME}' does not exist yet." >&2
    echo "       Run ./scripts/bootstrap/setup-config.sh first to create it." >&2
    exit 1
  fi
}

# All gcloud calls in the scripts should go through this wrapper. It pins the
# isolated configuration, the target project, and the account explicitly, so a
# concurrent session cannot influence (and is not influenced by) these commands.
# Override the project per-call by exporting GCLOUD_NO_PROJECT=1 (e.g. for
# 'projects create', which must not assume the project already exists).
gc() {
  local args=(--configuration="${CLOUDSDK_ACTIVE_CONFIG_NAME}")
  if [[ -n "${MY_ACCOUNT:-}" && "${MY_ACCOUNT}" != REPLACE_* ]]; then
    args+=(--account="${MY_ACCOUNT}")
  fi
  if [[ "${GCLOUD_NO_PROJECT:-0}" != "1" && -n "${PROJECT_ID:-}" && "${PROJECT_ID}" != REPLACE_* ]]; then
    args+=(--project="${PROJECT_ID}")
  fi
  gcloud "${args[@]}" "$@"
}

# Print the active config so the user can eyeball it before anything destructive.
show_active_config() {
  echo "Operating with gcloud config '${CLOUDSDK_ACTIVE_CONFIG_NAME}'"
  echo "  account: ${MY_ACCOUNT:-<unset>}"
  echo "  project: ${PROJECT_ID:-<unset>}"
}

# Google's fixed IAP TCP-forwarding source range. Firewall ingress for SSH is
# locked to this CIDR so the VM is reachable ONLY through Identity-Aware Proxy,
# never the public internet.
export IAP_CIDR="35.235.240.0/20"

# Full service-account email derived from SA_ID + PROJECT_ID.
sa_email() {
  echo "${SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
}

# Resolve the numeric project number (budgets and some IAM refs need it).
project_number() {
  GCLOUD_NO_PROJECT=1 gc projects describe "${PROJECT_ID}" \
    --format='value(projectNumber)'
}

# Interactive y/N confirmation before anything that creates or costs money.
# Auto-approves when ASSUME_YES=1 (used by deploy-all.sh after one prompt).
confirm() {
  local prompt="${1:-Proceed?}"
  if [[ "${ASSUME_YES:-0}" == "1" ]]; then
    return 0
  fi
  read -r -p "${prompt} [y/N] " reply
  [[ "${reply}" =~ ^[Yy]$ ]]
}

# True if a Compute resource of a given kind already exists (idempotency helper).
# Usage: resource_exists "networks describe ${NETWORK} --global"
resource_exists() {
  # shellcheck disable=SC2086
  gc compute $1 >/dev/null 2>&1
}
