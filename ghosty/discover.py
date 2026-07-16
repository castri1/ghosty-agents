"""Discovery helpers used by the `init` wizard.

These run BEFORE the isolated gcloud configuration exists, so they call gcloud in
`raw` mode (no injected --configuration/--account/--project).
"""

from __future__ import annotations

from ghosty import gcloud
from ghosty.models import Config

_EMPTY = Config()


def active_accounts() -> list[str]:
    """Authenticated accounts (active first)."""
    try:
        rows = gcloud.run_json(_EMPTY, ["auth", "list"], raw=True)
    except (gcloud.GcloudError, gcloud.GcloudNotFound):
        return []
    rows = sorted(rows, key=lambda r: r.get("status") != "ACTIVE")
    return [r.get("account", "") for r in rows if r.get("account")]


def zones_for_region(config: Config, region: str) -> list[str]:
    """Real zones for a region (e.g. us-east1 has b/c/d — there is no -a).

    Uses raw mode with explicit --project/--account because the isolated config
    may not exist yet during `init`. Returns [] if it can't be determined.
    """
    if not config.project_id:
        return []
    args = [
        "compute", "zones", "list",
        f"--filter=region:{region}",
        f"--project={config.project_id}",
    ]
    if config.account:
        args.append(f"--account={config.account}")
    try:
        rows = gcloud.run_json(config, args, raw=True)
    except (gcloud.GcloudError, gcloud.GcloudNotFound):
        return []
    return sorted(r.get("name", "") for r in rows if r.get("name"))


def billing_accounts() -> list[dict]:
    """Open/closed billing accounts visible to the user.

    Returns list of {"id", "name", "open"}.
    """
    try:
        rows = gcloud.run_json(_EMPTY, ["billing", "accounts", "list"], raw=True)
    except (gcloud.GcloudError, gcloud.GcloudNotFound):
        return []
    out = []
    for r in rows:
        # name looks like "billingAccounts/0X0X0X-..."; keep the id portion.
        raw_name = r.get("name", "")
        acct_id = raw_name.split("/", 1)[-1] if raw_name else ""
        out.append({
            "id": acct_id,
            "name": r.get("displayName", ""),
            "open": bool(r.get("open")),
        })
    return out
