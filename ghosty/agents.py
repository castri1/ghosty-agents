"""Per-agent VM lifecycle: create, list, status, ssh, start/stop, destroy."""

from __future__ import annotations

import time

from ghosty import gcloud, ui
from ghosty.models import (
    AGENT_NAME_LABEL,
    MANAGED_BY_LABEL,
    MANAGED_BY_VALUE,
    Agent,
    agent_name_from_instance,
    agent_sa_id,
    instance_name,
)
from ghosty.models import Config


AGENT_PROJECT_ROLES = ("roles/logging.logWriter", "roles/monitoring.metricWriter")


def _short_machine_type(mt: str) -> str:
    """gcloud returns a full URL for machineType; keep the trailing name."""
    return mt.rsplit("/", 1)[-1] if mt else ""


def list_agents(config: Config) -> list[Agent]:
    """Inventory from live GCP query, filtered by the ghosty label."""
    rows = gcloud.run_json(config, [
        "compute", "instances", "list",
        f"--filter=labels.{MANAGED_BY_LABEL}={MANAGED_BY_VALUE}",
    ])
    agents: list[Agent] = []
    for r in rows:
        labels = r.get("labels", {}) or {}
        instance = r.get("name", "")
        agent_name = labels.get(AGENT_NAME_LABEL) or agent_name_from_instance(instance)
        nics = r.get("networkInterfaces", []) or [{}]
        agents.append(Agent(
            name=agent_name,
            instance=instance,
            status=r.get("status", ""),
            zone=(r.get("zone", "").rsplit("/", 1)[-1]),
            machine_type=_short_machine_type(r.get("machineType", "")),
            internal_ip=nics[0].get("networkIP", "") if nics else "",
            created=r.get("creationTimestamp", ""),
            labels=labels,
        ))
    agents.sort(key=lambda a: a.name)
    return agents


def agent_exists(config: Config, agent: str) -> bool:
    return gcloud.exists(config, [
        "compute", "instances", "describe", instance_name(agent),
        f"--zone={config.zone}",
    ])


def get_agent(config: Config, agent: str) -> Agent | None:
    name = instance_name(agent)
    proc = gcloud.run(config, [
        "compute", "instances", "describe", name, f"--zone={config.zone}",
        "--format=json",
    ], check=False)
    if proc.returncode != 0:
        return None
    import json
    r = json.loads(proc.stdout)
    labels = r.get("labels", {}) or {}
    nics = r.get("networkInterfaces", []) or [{}]
    return Agent(
        name=labels.get(AGENT_NAME_LABEL) or agent_name_from_instance(name),
        instance=name,
        status=r.get("status", ""),
        zone=r.get("zone", "").rsplit("/", 1)[-1],
        machine_type=_short_machine_type(r.get("machineType", "")),
        internal_ip=nics[0].get("networkIP", "") if nics else "",
        created=r.get("creationTimestamp", ""),
        labels=labels,
    )


# --- create ---------------------------------------------------------------

# Newly created service accounts (and their IAM membership) are eventually
# consistent — a binding that references a brand-new SA can fail with
# "does not exist" / "INVALID_ARGUMENT" for a few seconds while it propagates.
_PROPAGATION_HINTS = ("does not exist", "INVALID_ARGUMENT", "NOT_FOUND")


def _wait_for_sa(config: Config, sa_email: str, *, attempts: int = 10, delay: float = 3.0) -> None:
    """Poll until the service account is visible (handles creation lag)."""
    for i in range(attempts):
        if gcloud.exists(config, ["iam", "service-accounts", "describe", sa_email]):
            return
        if i < attempts - 1:
            time.sleep(delay)
    ui.warn(f"service account '{sa_email}' not visible yet after waiting; continuing")


def _retry_propagation(fn, *, attempts: int = 8, delay: float = 3.0):
    """Run a gcloud call, retrying while GCP reports the SA hasn't propagated."""
    last: gcloud.GcloudError | None = None
    for i in range(attempts):
        try:
            return fn()
        except gcloud.GcloudError as exc:
            if not any(h in exc.stderr for h in _PROPAGATION_HINTS):
                raise
            last = exc
            if i < attempts - 1:
                ui.skip("waiting for service account to propagate...")
                time.sleep(delay)
    assert last is not None
    raise last


# A just-enabled Compute Engine API (or one whose zonal backend is still warming
# up) can transiently reject VM creation with these signatures.
_TRANSIENT_VM_HINTS = (
    "Permission denied on 'locations",
    "it may not exist",
    "may not exist",
    "was not found",
    "is not ready",
    "Please try again",
)


def _retry_transient(fn, *, attempts: int = 4, delay: float = 30.0):
    """Run a gcloud call, retrying transient compute-provisioning errors."""
    last: gcloud.GcloudError | None = None
    for i in range(attempts):
        try:
            return fn()
        except gcloud.GcloudError as exc:
            if not any(h in exc.stderr for h in _TRANSIENT_VM_HINTS):
                raise
            last = exc
            if i < attempts - 1:
                ui.skip("project compute backend still warming up; retrying...")
                time.sleep(delay)
    assert last is not None
    raise last


def _ensure_agent_sa(config: Config, agent: str) -> str:
    sa_id = agent_sa_id(agent)
    sa_email = config.sa_email(agent)
    if gcloud.exists(config, ["iam", "service-accounts", "describe", sa_email]):
        ui.skip(f"service account '{sa_email}' already exists")
    else:
        ui.step(f"Creating service account '{sa_id}'")
        gcloud.run(config, [
            "iam", "service-accounts", "create", sa_id,
            f"--display-name=Ghosty agent {agent}",
        ])
        # Wait for the new SA to become visible before referencing it in IAM.
        _wait_for_sa(config, sa_email)
    # Least-privilege roles for an agent box (retried while the SA propagates).
    for role in AGENT_PROJECT_ROLES:
        _retry_propagation(lambda role=role: gcloud.run(config, [
            "projects", "add-iam-policy-binding", config.project_id,
            f"--member=serviceAccount:{sa_email}", f"--role={role}",
            "--condition=None", "--quiet",
        ], no_project=True))
    # Allow the operator to act as this SA (needed to SSH into the VM).
    if config.account:
        _retry_propagation(lambda: gcloud.run(config, [
            "iam", "service-accounts", "add-iam-policy-binding", sa_email,
            f"--member=user:{config.account}",
            "--role=roles/iam.serviceAccountUser", "--quiet",
        ]))
    if config.google_ai_enabled:
        from ghosty import bootstrap
        _retry_propagation(lambda: bootstrap.grant_google_ai_to_agent(config, agent))
    return sa_email


def create_agent(config: Config, agent: str, *, startup_script: str | None = None) -> Agent:
    """Create one hardened agent VM. Idempotent (returns existing if present)."""
    name = instance_name(agent)
    if agent_exists(config, agent):
        ui.skip(f"agent '{agent}' (instance {name}) already exists")
        existing = get_agent(config, agent)
        assert existing is not None
        return existing

    sa_email = _ensure_agent_sa(config, agent)

    ui.step(f"Creating hardened VM '{name}' in {config.zone}")
    args = [
        "compute", "instances", "create", name,
        f"--zone={config.zone}",
        f"--machine-type={config.machine_type}",
        f"--image-family={config.image_family}",
        f"--image-project={config.image_project}",
        f"--boot-disk-size={config.boot_disk_size}",
        f"--boot-disk-type={config.boot_disk_type}",
        f"--network={config.network}",
        f"--subnet={config.subnet}",
        "--no-address",
        "--tags=ghosty-agent",
        f"--service-account={sa_email}",
        "--scopes=https://www.googleapis.com/auth/cloud-platform",
        "--shielded-secure-boot",
        "--shielded-vtpm",
        "--shielded-integrity-monitoring",
        "--metadata=enable-oslogin=TRUE,block-project-ssh-keys=TRUE",
        f"--labels={MANAGED_BY_LABEL}={MANAGED_BY_VALUE},{AGENT_NAME_LABEL}={agent}",
    ]
    if startup_script:
        args.append(f"--metadata-from-file=startup-script={startup_script}")

    try:
        _retry_transient(lambda: gcloud.run(config, args))
    except gcloud.GcloudError as exc:
        if any(h in exc.stderr for h in _TRANSIENT_VM_HINTS):
            ui.error("Compute couldn't use this zone yet.")
            ui.warn("Likely the Compute Engine API was just enabled (still propagating)")
            ui.warn("or billing isn't fully active. Check with `ghosty-agents doctor`,")
            ui.warn(f"then retry in a minute: ghosty-agents up {agent}")
        raise

    ui.success(f"agent '{agent}' created")
    result = get_agent(config, agent)
    assert result is not None
    return result


# --- lifecycle ------------------------------------------------------------

def start_agent(config: Config, agent: str) -> None:
    gcloud.run(config, [
        "compute", "instances", "start", instance_name(agent),
        f"--zone={config.zone}",
    ])


def stop_agent(config: Config, agent: str) -> None:
    gcloud.run(config, [
        "compute", "instances", "stop", instance_name(agent),
        f"--zone={config.zone}",
    ])


def ssh_agent(config: Config, agent: str, extra: list[str] | None = None) -> int:
    args = [
        "compute", "ssh", instance_name(agent),
        f"--zone={config.zone}", "--tunnel-through-iap",
    ]
    if extra:
        args += extra
    return gcloud.interactive(config, args)


def destroy_agent(config: Config, agent: str, *, delete_sa: bool = True) -> None:
    name = instance_name(agent)
    sa_email = config.sa_email(agent)
    if agent_exists(config, agent):
        ui.step(f"Deleting VM '{name}'")
        gcloud.run(config, [
            "compute", "instances", "delete", name,
            f"--zone={config.zone}", "--quiet",
        ])
        ui.success(f"deleted VM '{name}'")
    else:
        ui.skip(f"VM '{name}' not found")

    if delete_sa:
        for role in AGENT_PROJECT_ROLES:
            ui.step(f"Removing {role} from '{sa_email}'")
            gcloud.run(config, [
                "projects", "remove-iam-policy-binding", config.project_id,
                f"--member=serviceAccount:{sa_email}", f"--role={role}",
                "--quiet",
            ], no_project=True, check=False)
        if gcloud.exists(config, ["iam", "service-accounts", "describe", sa_email]):
            ui.step(f"Deleting service account '{sa_email}'")
            gcloud.run(config, [
                "iam", "service-accounts", "delete", sa_email, "--quiet",
            ])
            ui.success("service account deleted")
        else:
            ui.skip(f"service account '{sa_email}' not found")
