"""Data models for ghosty-agents."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


# Labels used to identify and group ghosty-managed resources in GCP. These are
# the source of truth for the inventory (`list`), so the local config never has
# to track which agents exist.
MANAGED_BY_LABEL = "managed-by"
MANAGED_BY_VALUE = "ghosty-agents"
AGENT_NAME_LABEL = "ghosty-agent"

# Google's fixed IAP TCP-forwarding source range. SSH ingress is locked to this
# CIDR so VMs are reachable ONLY through Identity-Aware Proxy.
IAP_CIDR = "35.235.240.0/20"


@dataclass
class Config:
    """Everything ghosty-agents needs to operate. Persisted as config.toml.

    This replaces the old env.sh. It stores identifiers and defaults only —
    never the agent inventory (that lives in GCP, queried via labels).
    """

    # --- identity / billing (required) ---
    project_id: str = ""
    billing_account_id: str = ""
    account: str = ""

    # --- isolation ---
    # Dedicated gcloud configuration name, scoped per-process so concurrent
    # gcloud sessions in other projects are never disturbed.
    gcloud_config_name: str = "ghosty-agents"

    # --- location ---
    region: str = "us-central1"
    zone: str = "us-central1-a"

    # --- shared network (created once by bootstrap) ---
    network: str = "ghosty-vpc"
    subnet: str = "ghosty-subnet"
    subnet_range: str = "10.10.0.0/24"

    # --- shared outbound internet (optional Cloud NAT) ---
    nat_router: str = "ghosty-router"
    nat_name: str = "ghosty-nat"

    # --- Google AI / Agent Platform access (optional) ---
    google_ai_enabled: bool = False
    google_ai_api: str = "aiplatform.googleapis.com"
    google_ai_role: str = "roles/aiplatform.user"

    # --- Google Chat app projects (optional, per-agent gateway) ---
    google_chat_project_prefix: str = ""
    google_chat_folder_id: str = ""
    google_chat_billing_account_id: str = ""
    google_chat_projects: dict = field(default_factory=dict)

    # --- Cloud Run webhook gateways (optional, per-agent external ingress) ---
    webhook_gateways: dict = field(default_factory=dict)

    # --- post-setup agent instruction delivery (optional) ---
    agent_instruction_delivery: str = "ask"
    agent_instruction_command: str = '"$HOME/.local/bin/hermes" -z "$(cat "$GHOSTY_PROMPT_FILE")"'
    agent_instruction_dir: str = "~/.config/hermes/inbox"
    agent_instruction_timeout_seconds: int = 600

    # --- shared agent storage bucket (optional) ---
    storage_enabled: bool = False
    storage_bucket: str = ""
    storage_location: str = ""
    storage_class: str = "STANDARD"
    storage_role: str = "roles/storage.objectUser"
    storage_env_path: str = "~/.config/hermes/storage.env"
    storage_public_enabled: bool = False
    storage_public_bucket: str = ""
    storage_public_location: str = ""
    storage_public_class: str = "STANDARD"
    storage_public_viewer_role: str = "roles/storage.objectViewer"
    storage_signed_urls_enabled: bool = False
    storage_signing_api: str = "iamcredentials.googleapis.com"
    storage_signing_role: str = "roles/iam.serviceAccountTokenCreator"
    storage_signed_url_default_duration: str = "1h"

    # --- per-agent VM defaults ---
    machine_type: str = "e2-small"
    boot_disk_size: str = "20GB"
    boot_disk_type: str = "pd-balanced"
    image_family: str = "debian-12"
    image_project: str = "debian-cloud"

    # --- budget (created once by bootstrap) ---
    budget_amount: str = "50"
    budget_currency: str = "USD"
    budget_name: str = "Ghosty Monthly Budget"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    def missing_required(self) -> list[str]:
        """Return the names of required fields that are still unset."""
        required = ("project_id", "billing_account_id", "account")
        return [name for name in required if not getattr(self, name)]

    # --- derived helpers -------------------------------------------------
    def sa_email(self, agent: str) -> str:
        """Per-agent service-account email."""
        return f"{agent_sa_id(agent)}@{self.project_id}.iam.gserviceaccount.com"


@dataclass
class Agent:
    """A single agent VM, as reported by GCP."""

    name: str  # the ghosty agent name (from the label), e.g. "worker-1"
    instance: str  # the GCE instance name, e.g. "ghosty-worker-1"
    status: str  # RUNNING, TERMINATED, etc.
    zone: str
    machine_type: str
    internal_ip: str = ""
    created: str = ""
    labels: dict = field(default_factory=dict)


# --- naming helpers ------------------------------------------------------

# GCE resource names: lowercase letters, digits, hyphens; must start with a
# letter; max 63 chars. We prefix agent instances/SAs with "ghosty-".
_INSTANCE_PREFIX = "ghosty-"
_SA_SUFFIX = "-sa"


def sanitize_agent_name(name: str) -> str:
    """Normalize a user-supplied agent name to a safe slug."""
    slug = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug:
        raise ValueError("agent name must contain at least one letter or digit")
    if not slug[0].isalpha():
        slug = f"a{slug}"
    return slug


def instance_name(agent: str) -> str:
    """GCE instance name for an agent."""
    name = f"{_INSTANCE_PREFIX}{sanitize_agent_name(agent)}"
    if len(name) > 63:
        raise ValueError(f"instance name too long (>63 chars): {name}")
    return name


def agent_sa_id(agent: str) -> str:
    """Service-account ID (the part before @) for an agent. Max 30 chars."""
    sa_id = f"{_INSTANCE_PREFIX}{sanitize_agent_name(agent)}{_SA_SUFFIX}"
    if len(sa_id) > 30:
        # Trim the agent slug to fit the 30-char SA-id limit.
        budget = 30 - len(_INSTANCE_PREFIX) - len(_SA_SUFFIX)
        slug = sanitize_agent_name(agent)[:budget].strip("-")
        sa_id = f"{_INSTANCE_PREFIX}{slug}{_SA_SUFFIX}"
    return sa_id


def agent_name_from_instance(instance: str) -> str:
    """Reverse of instance_name(): strip the ghosty- prefix."""
    if instance.startswith(_INSTANCE_PREFIX):
        return instance[len(_INSTANCE_PREFIX):]
    return instance
