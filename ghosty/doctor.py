"""Environment + readiness checks (the `doctor` command)."""

from __future__ import annotations

from dataclasses import dataclass

from ghosty import gcloud
from ghosty.models import Config


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""


def _gcloud_installed() -> Check:
    if gcloud.gcloud_available():
        return Check("gcloud installed", True)
    return Check(
        "gcloud installed",
        False,
        "gcloud not found on PATH",
        fix="Install the Google Cloud SDK: https://cloud.google.com/sdk/docs/install",
    )


def _config_complete(config: Config) -> Check:
    missing = config.missing_required()
    if not missing:
        return Check("config complete", True, f"project={config.project_id}")
    return Check(
        "config complete",
        False,
        f"missing: {', '.join(missing)}",
        fix="Run `ghosty-agents init`.",
    )


def _isolated_config_exists(config: Config) -> Check:
    if not config.gcloud_config_name:
        return Check("isolated gcloud config", False, "no config name set",
                     fix="Run `ghosty-agents init`.")
    try:
        names = [
            c.get("name")
            for c in gcloud.run_json(config, ["config", "configurations", "list"], raw=True)
        ]
    except gcloud.GcloudError as exc:
        return Check("isolated gcloud config", False, exc.stderr)
    if config.gcloud_config_name in names:
        return Check("isolated gcloud config", True, config.gcloud_config_name)
    return Check(
        "isolated gcloud config",
        False,
        f"'{config.gcloud_config_name}' not found",
        fix="Run `ghosty-agents init` to create it.",
    )


def _authenticated(config: Config) -> Check:
    try:
        accounts = gcloud.run_json(
            config, ["auth", "list", "--filter=status:ACTIVE"]
        )
    except gcloud.GcloudError as exc:
        return Check("authenticated", False, exc.stderr,
                     fix="Run `gcloud auth login`.")
    active = [a.get("account") for a in accounts]
    if config.account and config.account in active:
        return Check("authenticated", True, config.account)
    if active:
        return Check(
            "authenticated",
            False,
            f"active={active}, expected {config.account}",
            fix=f"Run `gcloud auth login {config.account}`.",
        )
    return Check("authenticated", False, "no active account",
                 fix="Run `gcloud auth login`.")


def _project_exists(config: Config) -> Check:
    if not config.project_id:
        return Check("project exists", False, "no project_id")
    ok = gcloud.exists(
        config, ["projects", "describe", config.project_id], no_project=True
    )
    if ok:
        return Check("project exists", True, config.project_id)
    return Check(
        "project exists",
        False,
        f"cannot access {config.project_id}",
        fix="Create it (`ghosty-agents bootstrap`) or check the account has access.",
    )


def _billing_linked(config: Config) -> Check:
    if not config.project_id:
        return Check("billing linked", False, "no project_id")
    try:
        info = gcloud.run_json(
            config,
            ["billing", "projects", "describe", config.project_id],
            no_project=True,
        )
    except gcloud.GcloudError as exc:
        return Check("billing linked", False, exc.stderr,
                     fix="Run `ghosty-agents bootstrap`.")
    enabled = bool(info.get("billingEnabled")) if isinstance(info, dict) else False
    acct = info.get("billingAccountName", "") if isinstance(info, dict) else ""
    if enabled:
        return Check("billing linked", True, acct)
    return Check("billing linked", False, "billing not enabled",
                 fix="Run `ghosty-agents bootstrap`.")


# APIs required for the hardened, IAP-only single-VM agent fleet.
REQUIRED_APIS = [
    "compute.googleapis.com",
    "iap.googleapis.com",
    "oslogin.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "cloudbilling.googleapis.com",
    "billingbudgets.googleapis.com",
]


def _apis_enabled(config: Config) -> Check:
    if not config.project_id:
        return Check("APIs enabled", False, "no project_id")
    try:
        enabled = {
            s.get("config", {}).get("name")
            for s in gcloud.run_json(config, ["services", "list", "--enabled"])
        }
    except gcloud.GcloudError as exc:
        return Check("APIs enabled", False, exc.stderr,
                     fix="Run `ghosty-agents bootstrap`.")
    missing = [a for a in REQUIRED_APIS if a not in enabled]
    if not missing:
        return Check("APIs enabled", True, f"{len(REQUIRED_APIS)} APIs")
    return Check(
        "APIs enabled",
        False,
        f"missing: {', '.join(missing)}",
        fix="Run `ghosty-agents bootstrap`.",
    )


def preflight_create(config: Config) -> list[Check]:
    """Fast checks that must pass before creating a VM (billing + Compute API)."""
    return [_billing_linked(config), _apis_enabled(config)]


def run_checks(config: Config, *, deep: bool = True) -> list[Check]:
    """Run readiness checks. `deep` includes checks that call GCP (slower)."""
    checks = [_gcloud_installed(), _config_complete(config)]
    # Only proceed to network-touching checks if the basics pass.
    if not all(c.ok for c in checks):
        return checks
    checks.append(_isolated_config_exists(config))
    checks.append(_authenticated(config))
    if deep:
        checks.append(_project_exists(config))
        checks.append(_billing_linked(config))
        checks.append(_apis_enabled(config))
    return checks
