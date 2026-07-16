"""One-time, idempotent project + shared-infra setup.

Ports the logic from scripts/bootstrap/* and scripts/deploy/{10..14,16}.sh into
Python so it runs anywhere gcloud does. Every step checks for existence first
and skips if already present.
"""

from __future__ import annotations

import shlex
import hashlib
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ghosty import gcloud, ui
from ghosty.doctor import REQUIRED_APIS
from ghosty.models import IAP_CIDR, Config, instance_name, sanitize_agent_name


# --- isolated gcloud configuration ---------------------------------------

def ensure_isolated_config(config: Config) -> None:
    """Create + populate the dedicated gcloud configuration (idempotent).

    Uses --no-activate so the GLOBAL active config another session relies on is
    never flipped; ghosty always targets its config explicitly via --configuration.
    """
    name = config.gcloud_config_name
    # List/create the configuration itself with raw=True: these manage the
    # config and must NOT pin --configuration to a config that may not exist yet.
    existing = [
        c.get("name")
        for c in gcloud.run_json(config, ["config", "configurations", "list"], raw=True)
    ]
    if name in existing:
        ui.skip(f"gcloud config '{name}' already exists")
    else:
        ui.step(f"Creating isolated gcloud config '{name}'")
        gcloud.run(config, ["config", "configurations", "create", name, "--no-activate"], raw=True)
        ui.success(f"created gcloud config '{name}'")

    # Populate account/project INTO this config (writes land in '{name}',
    # never 'default', because --configuration is pinned).
    if config.account:
        gcloud.run(config, ["config", "set", "account", config.account])
    if config.project_id:
        gcloud.run(config, ["config", "set", "project", config.project_id])


# --- project + billing ----------------------------------------------------

def ensure_project(config: Config, project_name: str | None = None) -> None:
    if gcloud.exists(config, ["projects", "describe", config.project_id], no_project=True):
        ui.skip(f"project '{config.project_id}' already exists")
        return
    ui.step(f"Creating project '{config.project_id}'")
    args = ["projects", "create", config.project_id]
    if project_name:
        args.append(f"--name={project_name}")
    gcloud.run(config, args, no_project=True)
    ui.success(f"created project '{config.project_id}'")


def link_billing(config: Config) -> None:
    try:
        info = gcloud.run_json(
            config, ["billing", "projects", "describe", config.project_id],
            no_project=True,
        )
    except gcloud.GcloudError:
        info = {}
    if isinstance(info, dict) and info.get("billingEnabled"):
        ui.skip(f"billing already linked ({info.get('billingAccountName', '')})")
        return
    ui.step(f"Linking billing account {config.billing_account_id}")
    gcloud.run(
        config,
        [
            "billing", "projects", "link", config.project_id,
            f"--billing-account={config.billing_account_id}",
        ],
        no_project=True,
    )
    ui.success("billing linked")


# --- APIs ------------------------------------------------------------------

def enable_apis(config: Config) -> None:
    enabled = {
        s.get("config", {}).get("name")
        for s in gcloud.run_json(config, ["services", "list", "--enabled"])
    }
    missing = [a for a in REQUIRED_APIS if a not in enabled]
    if not missing:
        ui.skip("all required APIs already enabled")
        return
    ui.step(f"Enabling {len(missing)} API(s) (this can take a minute)")
    gcloud.run(config, ["services", "enable", *missing])
    ui.success("APIs enabled")


def service_enabled(config: Config, service: str) -> bool:
    """True if a specific service/API is enabled on the project."""
    enabled = {
        s.get("config", {}).get("name")
        for s in gcloud.run_json(config, ["services", "list", "--enabled"])
    }
    return service in enabled


def ensure_service_enabled(config: Config, service: str) -> None:
    """Enable one service/API if needed."""
    if service_enabled(config, service):
        ui.skip(f"API '{service}' already enabled")
        return
    ui.step(f"Enabling API '{service}'")
    gcloud.run(config, ["services", "enable", service])
    ui.success(f"API '{service}' enabled")


# --- network ---------------------------------------------------------------

def ensure_network(config: Config) -> None:
    if gcloud.exists(config, ["compute", "networks", "describe", config.network]):
        ui.skip(f"VPC '{config.network}' already exists")
    else:
        ui.step(f"Creating custom-mode VPC '{config.network}'")
        gcloud.run(config, [
            "compute", "networks", "create", config.network,
            "--subnet-mode=custom", "--bgp-routing-mode=regional",
        ])
        ui.success(f"created VPC '{config.network}'")

    if gcloud.exists(config, [
        "compute", "networks", "subnets", "describe", config.subnet,
        f"--region={config.region}",
    ]):
        ui.skip(f"subnet '{config.subnet}' already exists")
    else:
        ui.step(f"Creating subnet '{config.subnet}' ({config.subnet_range})")
        gcloud.run(config, [
            "compute", "networks", "subnets", "create", config.subnet,
            f"--network={config.network}", f"--region={config.region}",
            f"--range={config.subnet_range}", "--enable-private-ip-google-access",
        ])
        ui.success(f"created subnet '{config.subnet}'")


def ensure_firewall(config: Config) -> None:
    allow = f"{config.network}-allow-ssh-from-iap"
    deny = f"{config.network}-deny-all-ingress"

    if gcloud.exists(config, ["compute", "firewall-rules", "describe", allow]):
        ui.skip(f"firewall '{allow}' already exists")
    else:
        ui.step(f"Creating IAP-only SSH ingress rule (source {IAP_CIDR})")
        gcloud.run(config, [
            "compute", "firewall-rules", "create", allow,
            f"--network={config.network}", "--direction=INGRESS", "--action=ALLOW",
            "--rules=tcp:22", f"--source-ranges={IAP_CIDR}",
            "--target-tags=ghosty-agent", "--priority=1000",
        ])
        ui.success(f"created firewall '{allow}'")

    if gcloud.exists(config, ["compute", "firewall-rules", "describe", deny]):
        ui.skip(f"firewall '{deny}' already exists")
    else:
        ui.step("Creating catch-all deny-ingress rule")
        gcloud.run(config, [
            "compute", "firewall-rules", "create", deny,
            f"--network={config.network}", "--direction=INGRESS", "--action=DENY",
            "--rules=all", "--source-ranges=0.0.0.0/0", "--priority=65534",
        ])
        ui.success(f"created firewall '{deny}'")


# --- Cloud NAT (optional shared outbound internet) -------------------------

@dataclass
class NatStatus:
    """Current state of the shared regional Cloud NAT."""

    router_exists: bool
    nat_exists: bool
    router: str
    nat: str
    region: str
    network: str
    subnet: str

    @property
    def enabled(self) -> bool:
        return self.router_exists and self.nat_exists


def _router_exists(config: Config) -> bool:
    return gcloud.exists(config, [
        "compute", "routers", "describe", config.nat_router,
        f"--region={config.region}",
    ])


def _nat_exists(config: Config) -> bool:
    return gcloud.exists(config, [
        "compute", "routers", "nats", "describe", config.nat_name,
        f"--router={config.nat_router}", f"--region={config.region}",
    ])


def nat_status(config: Config) -> NatStatus:
    """Return NAT state, treating missing router/NAT as a normal disabled state."""
    router_exists = _router_exists(config)
    nat_exists = _nat_exists(config) if router_exists else False
    return NatStatus(
        router_exists=router_exists,
        nat_exists=nat_exists,
        router=config.nat_router,
        nat=config.nat_name,
        region=config.region,
        network=config.network,
        subnet=config.subnet,
    )


def ensure_nat(config: Config) -> None:
    """Create the shared regional Cloud NAT, idempotently."""
    if _router_exists(config):
        ui.skip(f"Cloud Router '{config.nat_router}' already exists")
    else:
        ui.step(f"Creating Cloud Router '{config.nat_router}' in {config.region}")
        gcloud.run(config, [
            "compute", "routers", "create", config.nat_router,
            f"--network={config.network}", f"--region={config.region}",
        ])
        ui.success(f"created Cloud Router '{config.nat_router}'")

    if _nat_exists(config):
        ui.skip(f"Cloud NAT '{config.nat_name}' already exists")
        return

    ui.step(f"Creating Cloud NAT '{config.nat_name}' for subnet '{config.subnet}'")
    gcloud.run(config, [
        "compute", "routers", "nats", "create", config.nat_name,
        f"--router={config.nat_router}",
        f"--region={config.region}",
        "--type=PUBLIC",
        "--auto-allocate-nat-external-ips",
        f"--nat-custom-subnet-ip-ranges={config.subnet}:ALL",
    ])
    ui.success("Cloud NAT enabled")


def disable_nat(config: Config) -> None:
    """Delete the shared Cloud NAT if present. Leaves the router for teardown."""
    if not _router_exists(config):
        ui.skip(f"Cloud Router '{config.nat_router}' not found; NAT already disabled")
        return
    if not _nat_exists(config):
        ui.skip(f"Cloud NAT '{config.nat_name}' not found; already disabled")
        return
    ui.step(f"Deleting Cloud NAT '{config.nat_name}'")
    gcloud.run(config, [
        "compute", "routers", "nats", "delete", config.nat_name,
        f"--router={config.nat_router}",
        f"--region={config.region}",
        "--quiet",
    ])
    ui.success("Cloud NAT disabled")


def delete_nat_router(config: Config) -> None:
    """Delete the Cloud Router after its NAT has been removed."""
    if _router_exists(config):
        ui.step(f"Deleting Cloud Router '{config.nat_router}'")
        gcloud.run(config, [
            "compute", "routers", "delete", config.nat_router,
            f"--region={config.region}",
            "--quiet",
        ])
        ui.success("Cloud Router deleted")


# --- IAM (your account's IAP + OS Login access) ---------------------------

def grant_iap_access(config: Config) -> None:
    member = f"user:{config.account}"
    roles = ["roles/iap.tunnelResourceAccessor", "roles/compute.osLogin"]
    ui.step(f"Granting IAP + OS Login to {config.account}")
    for role in roles:
        gcloud.run(config, [
            "projects", "add-iam-policy-binding", config.project_id,
            f"--member={member}", f"--role={role}", "--condition=None", "--quiet",
        ], no_project=True)
    ui.success("IAP access granted")


# --- Google AI / Agent Platform -------------------------------------------

@dataclass
class GoogleAiAgentStatus:
    """Google AI IAM state for one Ghosty agent service account."""

    agent: str
    service_account: str
    has_role: bool


@dataclass
class GoogleAiStatus:
    """Current Agent Platform API + Ghosty agent IAM state."""

    api_enabled: bool
    auto_grant_enabled: bool
    api: str
    role: str
    agents: list[GoogleAiAgentStatus]


def _project_iam_policy(config: Config) -> dict:
    return gcloud.run_json(
        config,
        ["projects", "get-iam-policy", config.project_id],
        no_project=True,
    )


def _members_for_role(policy: dict, role: str) -> set[str]:
    members: set[str] = set()
    for binding in policy.get("bindings", []) if isinstance(policy, dict) else []:
        if binding.get("role") == role:
            members.update(binding.get("members", []) or [])
    return members


def _google_ai_role_members(config: Config) -> set[str]:
    return _members_for_role(_project_iam_policy(config), config.google_ai_role)


def grant_google_ai_to_agent(config: Config, agent: str) -> None:
    """Grant Agent Platform user access to one Ghosty agent service account."""
    sa_email = config.sa_email(agent)
    gcloud.run(config, [
        "projects", "add-iam-policy-binding", config.project_id,
        f"--member=serviceAccount:{sa_email}",
        f"--role={config.google_ai_role}",
        "--condition=None", "--quiet",
    ], no_project=True)


def remove_google_ai_from_agent(config: Config, agent: str) -> None:
    """Remove Agent Platform user access from one Ghosty agent service account."""
    sa_email = config.sa_email(agent)
    gcloud.run(config, [
        "projects", "remove-iam-policy-binding", config.project_id,
        f"--member=serviceAccount:{sa_email}",
        f"--role={config.google_ai_role}",
        "--quiet",
    ], no_project=True, check=False)


def google_ai_status(config: Config, agent_names: Sequence[str] | None = None) -> GoogleAiStatus:
    """Return Agent Platform API and per-agent IAM state."""
    if agent_names is None:
        from ghosty import agents
        agent_names = [a.name for a in agents.list_agents(config)]

    role_members = _google_ai_role_members(config)
    agent_states = [
        GoogleAiAgentStatus(
            agent=name,
            service_account=config.sa_email(name),
            has_role=f"serviceAccount:{config.sa_email(name)}" in role_members,
        )
        for name in sorted(agent_names)
    ]
    return GoogleAiStatus(
        api_enabled=service_enabled(config, config.google_ai_api),
        auto_grant_enabled=config.google_ai_enabled,
        api=config.google_ai_api,
        role=config.google_ai_role,
        agents=agent_states,
    )


def enable_google_ai(config: Config, agent_names: Sequence[str] | None = None) -> None:
    """Enable Agent Platform API and grant model access to Ghosty agents."""
    ensure_service_enabled(config, config.google_ai_api)
    if agent_names is None:
        from ghosty import agents
        agent_names = [a.name for a in agents.list_agents(config)]
    for name in sorted(agent_names):
        ui.step(f"Granting {config.google_ai_role} to {config.sa_email(name)}")
        grant_google_ai_to_agent(config, name)
    config.google_ai_enabled = True


def disable_google_ai_iam(config: Config, agent_names: Sequence[str] | None = None) -> None:
    """Remove Agent Platform IAM from Ghosty agents without disabling the API."""
    if agent_names is None:
        from ghosty import agents
        agent_names = [a.name for a in agents.list_agents(config)]
    for name in sorted(agent_names):
        ui.step(f"Removing {config.google_ai_role} from {config.sa_email(name)}")
        remove_google_ai_from_agent(config, name)
    config.google_ai_enabled = False


# --- Google Chat gateway --------------------------------------------------

GOOGLE_CHAT_API = "chat.googleapis.com"
PUBSUB_API = "pubsub.googleapis.com"
IAM_API = "iam.googleapis.com"
GOOGLE_CHAT_PUBLISHER = "chat-api-push@system.gserviceaccount.com"
GOOGLE_CHAT_VM_KEY_PATH = "~/.config/hermes/google-chat-service-account.json"


@dataclass
class GoogleChatResources:
    """Resource names for one agent's Google Chat gateway."""

    agent: str
    slug: str
    chat_project: str
    topic: str
    subscription: str
    service_account_id: str
    service_account_email: str
    full_topic: str
    full_subscription: str
    local_key_path: Path
    vm_key_path: str = GOOGLE_CHAT_VM_KEY_PATH


@dataclass
class GoogleChatStatus:
    """Current Google Chat gateway setup state for one agent."""

    resources: GoogleChatResources
    chat_project_exists: bool
    chat_api_enabled: bool
    pubsub_api_enabled: bool
    topic_exists: bool
    subscription_exists: bool
    service_account_exists: bool
    topic_publisher_bound: bool
    subscription_subscriber_bound: bool
    subscription_viewer_bound: bool
    local_key_exists: bool


def google_chat_service_account_id(agent: str) -> str:
    """Dedicated Google Chat gateway service-account ID for an agent."""
    suffix = "-chat-gw-sa"
    slug = sanitize_agent_name(agent)
    sa_id = f"{slug}{suffix}"
    if len(sa_id) > 30:
        slug = slug[: 30 - len(suffix)].strip("-")
        sa_id = f"{slug}{suffix}"
    return sa_id


def _chat_project_slug(project_id: str) -> str:
    return sanitize_agent_name(project_id)


def google_chat_key_path(agent: str, chat_project: str) -> Path:
    """Local key-file path under the Ghosty config directory."""
    from ghosty import config as config_mod

    return (
        config_mod.config_dir()
        / "google-chat"
        / sanitize_agent_name(agent)
        / _chat_project_slug(chat_project)
        / "service-account.json"
    )


def _config_for_project(config: Config, project_id: str) -> Config:
    data = config.to_dict()
    data["project_id"] = project_id
    return Config.from_dict(data)


def _google_chat_project_mapping(config: Config) -> dict:
    if not isinstance(config.google_chat_projects, dict):
        config.google_chat_projects = {}
    return config.google_chat_projects


def derive_google_chat_project_id(config: Config, agent: str) -> str:
    """Derive a valid default project ID for one agent's Google Chat app."""
    slug = sanitize_agent_name(agent)
    prefix = sanitize_agent_name(config.google_chat_project_prefix or config.project_id)
    candidate = f"{prefix}-{slug}-chat"
    if len(candidate) <= 30:
        return candidate

    digest = hashlib.sha1(candidate.encode("utf-8")).hexdigest()[:6]
    tail = f"-{digest}-chat"
    head = f"{prefix}-{slug}"[: 30 - len(tail)].strip("-")
    if not head:
        head = "ghosty"
    if not head[0].isalpha():
        head = f"g{head}"[: 30 - len(tail)].strip("-")
    return f"{head}{tail}"


def google_chat_project_id(config: Config, agent: str, chat_project: str | None = None) -> str:
    """Resolve the Chat app project for one agent."""
    if chat_project:
        return chat_project
    slug = sanitize_agent_name(agent)
    mapped = _google_chat_project_mapping(config).get(slug)
    if mapped:
        return str(mapped)
    return derive_google_chat_project_id(config, agent)


def google_chat_resources(
    config: Config,
    agent: str,
    chat_project: str | None = None,
) -> GoogleChatResources:
    """Derive deterministic Google Chat gateway resource names."""
    slug = sanitize_agent_name(agent)
    project = google_chat_project_id(config, agent, chat_project)
    topic = f"{slug}-chat-events"
    subscription = f"{topic}-sub"
    sa_id = google_chat_service_account_id(agent)
    return GoogleChatResources(
        agent=agent,
        slug=slug,
        chat_project=project,
        topic=topic,
        subscription=subscription,
        service_account_id=sa_id,
        service_account_email=f"{sa_id}@{project}.iam.gserviceaccount.com",
        full_topic=f"projects/{project}/topics/{topic}",
        full_subscription=f"projects/{project}/subscriptions/{subscription}",
        local_key_path=google_chat_key_path(agent, project),
    )


def _project_exists(config: Config, project_id: str) -> bool:
    return gcloud.exists(
        config,
        ["projects", "describe", project_id],
        no_project=True,
    )


def _main_project_parent(config: Config) -> dict:
    info = gcloud.run_json(
        config,
        ["projects", "describe", config.project_id],
        no_project=True,
    )
    return info.get("parent", {}) if isinstance(info, dict) else {}


def ensure_google_chat_project(
    config: Config,
    chat_project: str,
    *,
    folder_id: str | None = None,
    billing_account_id: str | None = None,
    create_project: bool = True,
) -> None:
    """Create/link a Chat app project if needed."""
    if _project_exists(config, chat_project):
        ui.skip(f"Google Chat project '{chat_project}' already exists")
    else:
        if not create_project:
            raise gcloud.GcloudError(
                ["projects", "describe", chat_project],
                1,
                f"Google Chat project '{chat_project}' not found; rerun without --no-create-project or create it first.",
            )

        ui.step(f"Creating Google Chat project '{chat_project}'")
        args = ["projects", "create", chat_project, f"--name={chat_project}"]
        chosen_folder = folder_id or config.google_chat_folder_id
        if chosen_folder:
            args.append(f"--folder={chosen_folder}")
        else:
            parent = _main_project_parent(config)
            parent_id = parent.get("id")
            parent_type = parent.get("type")
            if parent_id and parent_type == "folder":
                args.append(f"--folder={parent_id}")
            elif parent_id and parent_type == "organization":
                args.append(f"--organization={parent_id}")
        gcloud.run(config, args, no_project=True)
        ui.success(f"created Google Chat project '{chat_project}'")

    billing = billing_account_id or config.google_chat_billing_account_id or config.billing_account_id
    if billing:
        try:
            info = gcloud.run_json(
                config,
                ["billing", "projects", "describe", chat_project],
                no_project=True,
            )
        except gcloud.GcloudError:
            info = {}
        if isinstance(info, dict) and info.get("billingEnabled"):
            ui.skip(f"billing already linked for '{chat_project}'")
        else:
            ui.step(f"Linking billing account {billing} to '{chat_project}'")
            gcloud.run(
                config,
                ["billing", "projects", "link", chat_project, f"--billing-account={billing}"],
                no_project=True,
            )


def _topic_exists(config: Config, topic: str) -> bool:
    return gcloud.exists(config, ["pubsub", "topics", "describe", topic])


def _subscription_exists(config: Config, subscription: str) -> bool:
    return gcloud.exists(config, ["pubsub", "subscriptions", "describe", subscription])


def _service_account_exists(config: Config, service_account_email: str) -> bool:
    return gcloud.exists(config, ["iam", "service-accounts", "describe", service_account_email])


def _pubsub_members(config: Config, kind: str, resource: str, role: str) -> set[str]:
    policy = gcloud.run_json(
        config,
        ["pubsub", kind, "get-iam-policy", resource],
    )
    return _members_for_role(policy, role)


def _topic_has_chat_publisher(config: Config, topic: str) -> bool:
    if not _topic_exists(config, topic):
        return False
    return (
        f"serviceAccount:{GOOGLE_CHAT_PUBLISHER}"
        in _pubsub_members(config, "topics", topic, "roles/pubsub.publisher")
    )


def _subscription_has_gateway_subscriber(
    config: Config,
    subscription: str,
    service_account_email: str,
) -> bool:
    if not _subscription_exists(config, subscription):
        return False
    return (
        f"serviceAccount:{service_account_email}"
        in _pubsub_members(config, "subscriptions", subscription, "roles/pubsub.subscriber")
    )


def _subscription_has_gateway_viewer(
    config: Config,
    subscription: str,
    service_account_email: str,
) -> bool:
    if not _subscription_exists(config, subscription):
        return False
    return (
        f"serviceAccount:{service_account_email}"
        in _pubsub_members(config, "subscriptions", subscription, "roles/pubsub.viewer")
    )


_IAM_PROPAGATION_HINTS = ("does not exist", "INVALID_ARGUMENT", "NOT_FOUND")


def _retry_iam_propagation(fn, *, attempts: int = 8, delay: float = 3.0):
    last: gcloud.GcloudError | None = None
    for i in range(attempts):
        try:
            return fn()
        except gcloud.GcloudError as exc:
            if not any(h in exc.stderr for h in _IAM_PROPAGATION_HINTS):
                raise
            last = exc
            if i < attempts - 1:
                ui.skip("waiting for service account to propagate...")
                time.sleep(delay)
    assert last is not None
    raise last


def google_chat_status(
    config: Config,
    agent: str,
    chat_project: str | None = None,
) -> GoogleChatStatus:
    """Return Google Chat gateway state for one agent."""
    resources = google_chat_resources(config, agent, chat_project)
    chat_project_exists = _project_exists(config, resources.chat_project)
    if not chat_project_exists:
        return GoogleChatStatus(
            resources=resources,
            chat_project_exists=False,
            chat_api_enabled=False,
            pubsub_api_enabled=False,
            topic_exists=False,
            subscription_exists=False,
            service_account_exists=False,
            topic_publisher_bound=False,
            subscription_subscriber_bound=False,
            subscription_viewer_bound=False,
            local_key_exists=resources.local_key_path.is_file(),
        )

    chat_config = _config_for_project(config, resources.chat_project)
    return GoogleChatStatus(
        resources=resources,
        chat_project_exists=True,
        chat_api_enabled=service_enabled(chat_config, GOOGLE_CHAT_API),
        pubsub_api_enabled=service_enabled(chat_config, PUBSUB_API),
        topic_exists=_topic_exists(chat_config, resources.topic),
        subscription_exists=_subscription_exists(chat_config, resources.subscription),
        service_account_exists=_service_account_exists(chat_config, resources.service_account_email),
        topic_publisher_bound=_topic_has_chat_publisher(chat_config, resources.topic),
        subscription_subscriber_bound=_subscription_has_gateway_subscriber(
            chat_config,
            resources.subscription,
            resources.service_account_email,
        ),
        subscription_viewer_bound=_subscription_has_gateway_viewer(
            chat_config,
            resources.subscription,
            resources.service_account_email,
        ),
        local_key_exists=resources.local_key_path.is_file(),
    )


def ensure_google_chat_gateway(
    config: Config,
    agent: str,
    *,
    chat_project: str | None = None,
    folder_id: str | None = None,
    billing_account_id: str | None = None,
    create_project: bool = True,
) -> GoogleChatResources:
    """Provision Pub/Sub + service-account resources for one Google Chat gateway."""
    resources = google_chat_resources(config, agent, chat_project)
    ensure_google_chat_project(
        config,
        resources.chat_project,
        folder_id=folder_id,
        billing_account_id=billing_account_id,
        create_project=create_project,
    )
    chat_config = _config_for_project(config, resources.chat_project)
    ensure_service_enabled(chat_config, GOOGLE_CHAT_API)
    ensure_service_enabled(chat_config, PUBSUB_API)
    ensure_service_enabled(chat_config, IAM_API)

    if _topic_exists(chat_config, resources.topic):
        ui.skip(f"Pub/Sub topic '{resources.topic}' already exists")
    else:
        ui.step(f"Creating Pub/Sub topic '{resources.topic}'")
        gcloud.run(chat_config, ["pubsub", "topics", "create", resources.topic])

    if _subscription_exists(chat_config, resources.subscription):
        ui.skip(f"Pub/Sub subscription '{resources.subscription}' already exists")
    else:
        ui.step(f"Creating pull subscription '{resources.subscription}'")
        gcloud.run(chat_config, [
            "pubsub", "subscriptions", "create", resources.subscription,
            f"--topic={resources.topic}",
        ])

    if _service_account_exists(chat_config, resources.service_account_email):
        ui.skip(f"service account '{resources.service_account_email}' already exists")
    else:
        ui.step(f"Creating service account '{resources.service_account_id}'")
        gcloud.run(chat_config, [
            "iam", "service-accounts", "create", resources.service_account_id,
            f"--display-name=Hermes Google Chat gateway {resources.slug}",
        ])

    ui.step("Granting Google Chat publisher on the topic")
    gcloud.run(chat_config, [
        "pubsub", "topics", "add-iam-policy-binding", resources.topic,
        f"--member=serviceAccount:{GOOGLE_CHAT_PUBLISHER}",
        "--role=roles/pubsub.publisher",
        "--quiet",
    ])

    ui.step("Granting gateway service account subscriber on the subscription")
    _retry_iam_propagation(lambda: gcloud.run(chat_config, [
        "pubsub", "subscriptions", "add-iam-policy-binding", resources.subscription,
        f"--member=serviceAccount:{resources.service_account_email}",
        "--role=roles/pubsub.subscriber",
        "--quiet",
    ]))

    ui.step("Granting gateway service account viewer on the subscription")
    _retry_iam_propagation(lambda: gcloud.run(chat_config, [
        "pubsub", "subscriptions", "add-iam-policy-binding", resources.subscription,
        f"--member=serviceAccount:{resources.service_account_email}",
        "--role=roles/pubsub.viewer",
        "--quiet",
    ]))

    if resources.local_key_path.is_file():
        ui.skip(f"local key already exists at {resources.local_key_path}")
    else:
        ui.step(f"Creating local service-account key at {resources.local_key_path}")
        resources.local_key_path.parent.mkdir(parents=True, exist_ok=True)
        _retry_iam_propagation(lambda: gcloud.run(chat_config, [
            "iam", "service-accounts", "keys", "create",
            str(resources.local_key_path),
            f"--iam-account={resources.service_account_email}",
        ]))

    _google_chat_project_mapping(config)[resources.slug] = resources.chat_project
    upload_google_chat_key(config, resources)
    return resources


def upload_google_chat_key(config: Config, resources: GoogleChatResources) -> None:
    """Copy the gateway JSON key to the target VM over IAP."""
    remote_dir = resources.vm_key_path.rsplit("/", 1)[0]
    vm = instance_name(resources.agent)
    ui.step(f"Uploading key to {vm}:{resources.vm_key_path}")
    gcloud.run(config, [
        "compute", "ssh", vm,
        f"--zone={config.zone}",
        "--tunnel-through-iap",
        f"--command=mkdir -p {remote_dir} && chmod 700 {remote_dir}",
        "--quiet",
    ])
    gcloud.run(config, [
        "compute", "scp", str(resources.local_key_path),
        f"{vm}:{resources.vm_key_path}",
        f"--zone={config.zone}",
        "--tunnel-through-iap",
        "--quiet",
    ])


def destroy_google_chat_gateway(
    config: Config,
    agent: str,
    chat_project: str | None = None,
) -> GoogleChatResources:
    """Remove Pub/Sub gateway resources, preserving service-account keys."""
    resources = google_chat_resources(config, agent, chat_project)
    chat_config = _config_for_project(config, resources.chat_project)

    if _topic_exists(chat_config, resources.topic):
        ui.step("Removing Google Chat publisher from the topic")
        gcloud.run(chat_config, [
            "pubsub", "topics", "remove-iam-policy-binding", resources.topic,
            f"--member=serviceAccount:{GOOGLE_CHAT_PUBLISHER}",
            "--role=roles/pubsub.publisher",
            "--quiet",
        ], check=False)

    if _subscription_exists(chat_config, resources.subscription):
        ui.step("Removing gateway service account subscriber from the subscription")
        gcloud.run(chat_config, [
            "pubsub", "subscriptions", "remove-iam-policy-binding", resources.subscription,
            f"--member=serviceAccount:{resources.service_account_email}",
            "--role=roles/pubsub.subscriber",
            "--quiet",
        ], check=False)
        ui.step("Removing gateway service account viewer from the subscription")
        gcloud.run(chat_config, [
            "pubsub", "subscriptions", "remove-iam-policy-binding", resources.subscription,
            f"--member=serviceAccount:{resources.service_account_email}",
            "--role=roles/pubsub.viewer",
            "--quiet",
        ], check=False)
        ui.step(f"Deleting Pub/Sub subscription '{resources.subscription}'")
        gcloud.run(chat_config, [
            "pubsub", "subscriptions", "delete", resources.subscription,
            "--quiet",
        ])
    else:
        ui.skip(f"Pub/Sub subscription '{resources.subscription}' not found")

    if _topic_exists(chat_config, resources.topic):
        ui.step(f"Deleting Pub/Sub topic '{resources.topic}'")
        gcloud.run(chat_config, ["pubsub", "topics", "delete", resources.topic, "--quiet"])
    else:
        ui.skip(f"Pub/Sub topic '{resources.topic}' not found")

    mapping = _google_chat_project_mapping(config)
    if mapping.get(resources.slug) == resources.chat_project:
        mapping.pop(resources.slug, None)
    return resources


# --- Cloud Run webhook gateway -------------------------------------------

RUN_API = "run.googleapis.com"
ARTIFACT_REGISTRY_API = "artifactregistry.googleapis.com"
CLOUD_BUILD_API = "cloudbuild.googleapis.com"
CLOUD_RUN_BUILDER_ROLE = "roles/run.builder"
WEBHOOK_SECRET_HEADER = "X-Ghosty-Webhook-Secret"
WEBHOOK_EVENT_FORMAT = "ghosty.webhook.v1"
WEBHOOK_PROVIDER = "generic"


@dataclass
class WebhookResources:
    """Resource names for one agent webhook gateway."""

    agent: str
    slug: str
    name: str
    name_slug: str
    provider: str
    service_name: str
    topic: str
    subscription: str
    full_topic: str
    full_subscription: str
    run_service_account_id: str
    run_service_account_email: str
    agent_service_account_email: str
    env_path: str
    secret_header: str = WEBHOOK_SECRET_HEADER


@dataclass
class WebhookStatus:
    """Current Cloud Run webhook gateway state."""

    resources: WebhookResources
    run_api_enabled: bool
    artifactregistry_api_enabled: bool
    cloudbuild_api_enabled: bool
    pubsub_api_enabled: bool
    iam_api_enabled: bool
    topic_exists: bool
    subscription_exists: bool
    run_service_account_exists: bool
    service_exists: bool
    publisher_bound: bool
    subscriber_bound: bool
    viewer_bound: bool
    vm_env_exists: bool | None
    service_url: str
    secret_configured: bool
    consumer_installed: bool | None = None
    consumer_active: bool | None = None


@dataclass
class WebhookSetupResult:
    """Result for setup/sync of a single webhook gateway."""

    resources: WebhookResources
    service_url: str
    secret: str
    vm_env_updated: bool
    message: str = ""


@dataclass
class WebhookConsumerResult:
    """Result for installing or checking a VM-side webhook consumer."""

    resources: WebhookResources
    script_path: str
    service_name: str
    installed: bool
    active: bool | None
    message: str = ""


def _shorten_name(value: str, max_len: int, *, suffix: str = "") -> str:
    if len(value) <= max_len:
        return value
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:6]
    tail = f"-{digest}{suffix}"
    head = value[: max_len - len(tail)].strip("-")
    if not head:
        head = "ghosty"
    if not head[0].isalpha():
        head = f"g{head}"[: max_len - len(tail)].strip("-")
    return f"{head}{tail}"


def webhook_provider(provider: str | None = None) -> str:
    value = (provider or "generic").strip().lower()
    if value != WEBHOOK_PROVIDER:
        raise ValueError("webhook gateways use the generic shared-secret receiver")
    return value


def webhook_service_name(agent: str, name: str) -> str:
    value = f"ghosty-{sanitize_agent_name(agent)}-webhook-{sanitize_agent_name(name)}"
    return _shorten_name(value, 63)


def webhook_run_service_account_id(agent: str, name: str) -> str:
    value = f"{sanitize_agent_name(agent)}-{sanitize_agent_name(name)}-wh-run-sa"
    return _shorten_name(value, 30)


def webhook_env_path(name: str) -> str:
    return f"~/.config/hermes/webhooks/{sanitize_agent_name(name)}.env"


def webhook_consumer_script_path(name: str) -> str:
    """Managed VM-side consumer script path for one notification path."""
    return f"~/.local/bin/ghosty-{sanitize_agent_name(name)}-consumer"


def webhook_consumer_service_name(name: str) -> str:
    """Managed user systemd service for one notification path."""
    return f"ghosty-{sanitize_agent_name(name)}-consumer.service"


def webhook_resources(
    config: Config,
    agent: str,
    name: str,
    provider: str | None = None,
) -> WebhookResources:
    """Derive deterministic Cloud Run webhook gateway resource names."""
    prov = webhook_provider(provider)
    slug = sanitize_agent_name(agent)
    name_slug = sanitize_agent_name(name)
    service = webhook_service_name(agent, name)
    topic = f"{slug}-webhook-{name_slug}-events"
    subscription = f"{topic}-sub"
    run_sa_id = webhook_run_service_account_id(agent, name)
    return WebhookResources(
        agent=agent,
        slug=slug,
        name=name,
        name_slug=name_slug,
        provider=prov,
        service_name=service,
        topic=topic,
        subscription=subscription,
        full_topic=f"projects/{config.project_id}/topics/{topic}",
        full_subscription=f"projects/{config.project_id}/subscriptions/{subscription}",
        run_service_account_id=run_sa_id,
        run_service_account_email=f"{run_sa_id}@{config.project_id}.iam.gserviceaccount.com",
        agent_service_account_email=config.sa_email(agent),
        env_path=webhook_env_path(name),
    )


def _webhook_mapping(config: Config) -> dict:
    if not isinstance(config.webhook_gateways, dict):
        config.webhook_gateways = {}
    return config.webhook_gateways


def _webhook_agent_mapping(config: Config, agent: str) -> dict:
    mapping = _webhook_mapping(config)
    slug = sanitize_agent_name(agent)
    if not isinstance(mapping.get(slug), dict):
        mapping[slug] = {}
    return mapping[slug]


def _webhook_metadata(config: Config, agent: str, name: str) -> dict:
    data = _webhook_mapping(config).get(sanitize_agent_name(agent), {})
    if not isinstance(data, dict):
        return {}
    meta = data.get(sanitize_agent_name(name), {})
    return meta if isinstance(meta, dict) else {}


def _set_webhook_metadata(
    config: Config,
    resources: WebhookResources,
    *,
    secret: str,
    service_url: str,
) -> None:
    _webhook_agent_mapping(config, resources.agent)[resources.name_slug] = {
        "provider": resources.provider,
        "secret": secret,
        "secret_header": resources.secret_header,
        "service": resources.service_name,
        "topic": resources.topic,
        "subscription": resources.subscription,
        "url": service_url,
    }


def _clear_webhook_metadata(config: Config, resources: WebhookResources) -> None:
    mapping = _webhook_mapping(config)
    agent_map = mapping.get(resources.slug)
    if isinstance(agent_map, dict):
        agent_map.pop(resources.name_slug, None)
        if not agent_map:
            mapping.pop(resources.slug, None)


def _webhook_secret(config: Config, agent: str, name: str, secret: str | None, generate_secret: bool) -> str:
    if secret and generate_secret:
        raise ValueError("use either --secret or --generate-secret, not both")
    if secret:
        if any(ch in secret for ch in "\n\r,"):
            raise ValueError("webhook secret cannot contain newline or comma characters")
        return secret
    existing = _webhook_metadata(config, agent, name).get("secret")
    if existing and not generate_secret:
        return str(existing)
    return secrets.token_urlsafe(32)


def webhook_receiver_source_dir() -> Path:
    return Path(__file__).parent / "webhook_receiver"


def _cloud_run_service_exists(config: Config, service: str) -> bool:
    return gcloud.exists(config, [
        "run", "services", "describe", service,
        f"--region={config.region}",
    ])


def _cloud_run_service_url(config: Config, service: str) -> str:
    if not _cloud_run_service_exists(config, service):
        return ""
    info = gcloud.run_json(config, [
        "run", "services", "describe", service,
        f"--region={config.region}",
    ])
    if not isinstance(info, dict):
        return ""
    return (
        info.get("status", {}).get("url")
        or info.get("uri")
        or info.get("url")
        or ""
    )


def cloud_run_source_build_service_account(config: Config) -> str:
    """Return the default build identity used by Cloud Run source deployments."""
    project_number = _project_number(config)
    if not project_number:
        raise RuntimeError(f"Could not determine project number for '{config.project_id}'")
    return f"{project_number}-compute@developer.gserviceaccount.com"


def ensure_cloud_run_source_build_iam(config: Config) -> str:
    """Grant the source-deploy build identity the Cloud Run Builder role."""
    service_account = cloud_run_source_build_service_account(config)
    ui.step(f"Granting Cloud Run Builder to source build service account '{service_account}'")
    gcloud.run(config, [
        "projects", "add-iam-policy-binding", config.project_id,
        f"--member=serviceAccount:{service_account}",
        f"--role={CLOUD_RUN_BUILDER_ROLE}",
        "--condition=None",
        "--quiet",
    ], no_project=True)
    return service_account


def _deploy_webhook_receiver(config: Config, resources: WebhookResources, secret: str) -> str:
    source = webhook_receiver_source_dir()
    env_vars = ",".join([
        f"GHOSTY_WEBHOOK_PROVIDER={resources.provider}",
        f"GHOSTY_WEBHOOK_NAME={resources.name_slug}",
        f"GHOSTY_WEBHOOK_AGENT={resources.slug}",
        f"GHOSTY_WEBHOOK_TOPIC={resources.full_topic}",
        f"GHOSTY_WEBHOOK_SECRET={secret}",
        f"GHOSTY_WEBHOOK_SECRET_HEADER={resources.secret_header}",
        f"GHOSTY_WEBHOOK_EVENT_FORMAT={WEBHOOK_EVENT_FORMAT}",
    ])
    ui.step(f"Deploying Cloud Run receiver '{resources.service_name}'")
    gcloud.run(config, [
        "run", "deploy", resources.service_name,
        f"--source={source}",
        f"--region={config.region}",
        f"--service-account={resources.run_service_account_email}",
        "--allow-unauthenticated",
        f"--set-env-vars={env_vars}",
        "--quiet",
    ])
    return _cloud_run_service_url(config, resources.service_name)


def _topic_has_publisher(config: Config, topic: str, service_account_email: str) -> bool:
    if not _topic_exists(config, topic):
        return False
    return (
        f"serviceAccount:{service_account_email}"
        in _pubsub_members(config, "topics", topic, "roles/pubsub.publisher")
    )


def _webhook_env_lines(
    config: Config,
    resources: WebhookResources,
    service_url: str = "",
) -> list[str]:
    lines = [
        f"GHOSTY_WEBHOOK_NAME={resources.name_slug}",
        f"GHOSTY_WEBHOOK_PROVIDER={resources.provider}",
        f"GHOSTY_WEBHOOK_SUBSCRIPTION={resources.full_subscription}",
        f"GHOSTY_WEBHOOK_TOPIC={resources.full_topic}",
        f"GHOSTY_WEBHOOK_EVENT_FORMAT={WEBHOOK_EVENT_FORMAT}",
        f"GOOGLE_CLOUD_PROJECT={config.project_id}",
    ]
    if service_url:
        lines.append(f"GHOSTY_WEBHOOK_URL={service_url}")
    return lines


def _remote_var_path_assignment(var: str, path: str) -> str:
    if path.startswith("~/"):
        suffix = path[2:].replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
        return f'{var}="${{HOME}}/{suffix}"; '
    return f"{var}={shlex.quote(path)}; "


def _webhook_remote_path_assignment(path: str) -> str:
    return _remote_var_path_assignment("env_path", path)


def _write_webhook_env_command(
    config: Config,
    resources: WebhookResources,
    service_url: str = "",
) -> str:
    lines = " ".join(shlex.quote(line) for line in _webhook_env_lines(config, resources, service_url))
    return (
        _webhook_remote_path_assignment(resources.env_path)
        + 'env_dir="${env_path%/*}"; '
        + 'mkdir -p "$env_dir" && chmod 700 "$env_dir" && '
        + f'printf "%s\\n" {lines} > "$env_path" && chmod 600 "$env_path"'
    )


def _test_webhook_env_command(resources: WebhookResources) -> str:
    return _webhook_remote_path_assignment(resources.env_path) + 'test -f "$env_path"'


def _run_webhook_agent_ssh_command(config: Config, agent: str, command: str):
    return gcloud.run(config, [
        "compute", "ssh", instance_name(agent),
        f"--zone={config.zone}",
        "--tunnel-through-iap",
        f"--command={command}",
        "--quiet",
    ], check=False)


def upload_webhook_env_to_agent(
    config: Config,
    agent: object,
    resources: WebhookResources,
    service_url: str = "",
    *,
    attempts: int = 6,
    delay: float = 10.0,
) -> tuple[bool, str]:
    agent_name = _agent_name(agent)
    status = _agent_status(agent)
    if status and status != "RUNNING":
        message = f"agent is {status}; start it and rerun `ghosty-agents notifications sync {agent_name} --name {resources.name_slug}`"
        ui.warn(f"Skipped webhook env write for '{agent_name}': {message}")
        return False, message

    command = _write_webhook_env_command(config, resources, service_url)
    proc = None
    for attempt in range(max(1, attempts)):
        proc = _run_webhook_agent_ssh_command(config, agent_name, command)
        if proc.returncode == 0:
            return True, ""
        if attempt < max(1, attempts) - 1:
            ui.skip(f"agent '{agent_name}' is not ready for notification config yet; retrying...")
            time.sleep(delay)

    assert proc is not None
    message = _proc_message(proc) or f"SSH exited with return code {proc.returncode}"
    ui.warn(
        f"Could not write webhook env on '{agent_name}'. "
        f"Rerun `ghosty-agents notifications sync {agent_name} --name {resources.name_slug}`."
    )
    return False, message


def webhook_env_exists_on_agent(config: Config, agent: object, resources: WebhookResources) -> bool | None:
    agent_name = _agent_name(agent)
    status = _agent_status(agent)
    if status and status != "RUNNING":
        return None
    proc = _run_webhook_agent_ssh_command(config, agent_name, _test_webhook_env_command(resources))
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    return None


def webhook_consumer_script(resources: WebhookResources, *, hermes_timeout_seconds: int = 180) -> str:
    """Return the managed VM-side Pub/Sub consumer script for a notification path."""
    script = r'''#!/usr/bin/env python3
import base64
import json
import subprocess
import time
import urllib.request
from pathlib import Path

HOME = Path.home()
ENV_PATH = Path("__ENV_PATH__").expanduser()
EVENT_DIR = HOME / ".config/hermes/inbox/events/__NAME_SLUG__"
HERMES = HOME / ".local/bin/hermes"
POLL_SECONDS = 5
HERMES_TIMEOUT = __HERMES_TIMEOUT__


def load_env():
    values = {}
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if "=" in raw and not raw.startswith("#"):
            key, value = raw.split("=", 1)
            values[key] = value
    return values


def token():
    req = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)["access_token"]


def pubsub_request(subscription, method, payload):
    req = urllib.request.Request(
        f"https://pubsub.googleapis.com/v1/{subscription}:{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read()
    return json.loads(body.decode("utf-8")) if body else {}


def pull_once(subscription):
    return pubsub_request(subscription, "pull", {"maxMessages": 1}).get("receivedMessages") or []


def acknowledge(subscription, ack_id):
    pubsub_request(subscription, "acknowledge", {"ackIds": [ack_id]})


def handle(subscription, received):
    message = received["message"]
    message_id = message.get("messageId") or str(int(time.time()))
    event = json.loads(base64.b64decode(message["data"]).decode("utf-8"))
    event_path = EVENT_DIR / f"{message_id}.json"
    log_path = EVENT_DIR / f"{message_id}.log"
    event_path.write_text(json.dumps(event, indent=2, sort_keys=True), encoding="utf-8")
    acknowledge(subscription, received["ackId"])

    if log_path.exists():
        print(json.dumps({
            "message_id": message_id,
            "event_path": str(event_path),
            "hermes_status": "duplicate-skipped",
        }), flush=True)
        return

    if not HERMES.exists():
        result = {"status": "hermes-missing", "returncode": None, "stdout": "", "stderr": str(HERMES)}
    else:
        prompt = (
            f"A Ghosty notification arrived for this agent. Read the saved event at {event_path} "
            "and decide whether any action is needed. Start by summarizing the event in one sentence. "
            "Do not expose secrets."
        )
        try:
            completed = subprocess.run(
                [str(HERMES), "-z", prompt],
                cwd=str(HOME),
                text=True,
                capture_output=True,
                timeout=HERMES_TIMEOUT,
            )
            result = {
                "status": "completed",
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "status": "timeout",
                "timeout_seconds": HERMES_TIMEOUT,
                "stdout": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
                "stderr": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
            }
    log_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "message_id": message_id,
        "event_path": str(event_path),
        "hermes_status": result["status"],
    }), flush=True)


def main():
    env = load_env()
    subscription = env["GHOSTY_WEBHOOK_SUBSCRIPTION"]
    EVENT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"ghosty __NAME_SLUG__ consumer listening on {subscription}", flush=True)
    while True:
        try:
            messages = pull_once(subscription)
            if not messages:
                time.sleep(POLL_SECONDS)
                continue
            for received in messages:
                handle(subscription, received)
        except Exception as exc:
            print(f"consumer error: {exc!r}", flush=True)
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
'''
    return (
        script
        .replace("__ENV_PATH__", resources.env_path)
        .replace("__NAME_SLUG__", resources.name_slug)
        .replace("__HERMES_TIMEOUT__", str(hermes_timeout_seconds))
    )


def _webhook_consumer_service(resources: WebhookResources) -> str:
    script_path = webhook_consumer_script_path(resources.name_slug).removeprefix("~/")
    return "\n".join([
        "[Unit]",
        f"Description=Ghosty {resources.name_slug} notification consumer",
        "After=network-online.target hermes-gateway.service",
        "",
        "[Service]",
        "Type=simple",
        f"ExecStart=%h/{script_path}",
        "Restart=always",
        "RestartSec=10",
        "Environment=PYTHONUNBUFFERED=1",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ])


def _write_webhook_consumer_command(resources: WebhookResources) -> str:
    script_path = webhook_consumer_script_path(resources.name_slug)
    service_name = webhook_consumer_service_name(resources.name_slug)
    service_path = f"~/.config/systemd/user/{service_name}"
    return (
        "set -eu\n"
        + _remote_var_path_assignment("script_path", script_path)
        + _remote_var_path_assignment("service_path", service_path)
        + 'script_dir="${script_path%/*}"; service_dir="${service_path%/*}"; '
        + 'mkdir -p "$script_dir" "$service_dir" && chmod 700 "$service_dir"\n'
        + 'cat > "$script_path" <<\'GHOSTY_CONSUMER\'\n'
        + webhook_consumer_script(resources)
        + "\nGHOSTY_CONSUMER\n"
        + 'chmod 700 "$script_path"\n'
        + 'cat > "$service_path" <<\'GHOSTY_SERVICE\'\n'
        + _webhook_consumer_service(resources)
        + "GHOSTY_SERVICE\n"
        + "systemctl --user daemon-reload\n"
        + f"systemctl --user enable --now {shlex.quote(service_name)}\n"
        + 'loginctl enable-linger "$USER" >/dev/null 2>&1 || true\n'
        + f"systemctl --user is-active --quiet {shlex.quote(service_name)}"
    )


def _webhook_consumer_status_command(resources: WebhookResources) -> str:
    script_path = webhook_consumer_script_path(resources.name_slug)
    service_path = f"~/.config/systemd/user/{webhook_consumer_service_name(resources.name_slug)}"
    service_name = webhook_consumer_service_name(resources.name_slug)
    return (
        _remote_var_path_assignment("script_path", script_path)
        + _remote_var_path_assignment("service_path", service_path)
        + 'installed=0; active=0; '
        + '[ -f "$script_path" ] && [ -f "$service_path" ] && installed=1; '
        + f"systemctl --user is-active --quiet {shlex.quote(service_name)} && active=1 || true; "
        + 'printf "%s %s\\n" "$installed" "$active"'
    )


def install_webhook_consumer(config: Config, agent: object, resources: WebhookResources) -> WebhookConsumerResult:
    """Install or refresh the VM-side consumer for one notification path."""
    agent_name = _agent_name(agent)
    status = _agent_status(agent)
    script_path = webhook_consumer_script_path(resources.name_slug)
    service_name = webhook_consumer_service_name(resources.name_slug)
    if status and status != "RUNNING":
        message = f"agent is {status}; start it and rerun `ghosty-agents notifications sync {agent_name} --name {resources.name_slug} --with-consumer`"
        ui.warn(f"Skipped notification consumer install for '{agent_name}': {message}")
        return WebhookConsumerResult(resources, script_path, service_name, installed=False, active=None, message=message)
    ui.step(f"Installing notification consumer '{service_name}' on '{agent_name}'")
    proc = _run_webhook_agent_ssh_command(config, agent_name, _write_webhook_consumer_command(resources))
    if proc.returncode == 0:
        return WebhookConsumerResult(resources, script_path, service_name, installed=True, active=True)
    message = _proc_message(proc) or f"SSH exited with return code {proc.returncode}"
    ui.warn(f"Could not install notification consumer on '{agent_name}'.")
    return WebhookConsumerResult(resources, script_path, service_name, installed=False, active=False, message=message)


def webhook_consumer_status_on_agent(config: Config, agent: object, resources: WebhookResources) -> tuple[bool | None, bool | None]:
    """Return (installed, active) for a VM-side notification consumer."""
    agent_name = _agent_name(agent)
    status = _agent_status(agent)
    if status and status != "RUNNING":
        return None, None
    proc = _run_webhook_agent_ssh_command(config, agent_name, _webhook_consumer_status_command(resources))
    if proc.returncode != 0:
        return None, None
    parts = (proc.stdout or "").strip().split()
    if len(parts) < 2:
        return None, None
    return parts[0] == "1", parts[1] == "1"


def _ensure_webhook_pubsub_and_iam(config: Config, resources: WebhookResources) -> None:
    if _topic_exists(config, resources.topic):
        ui.skip(f"Pub/Sub topic '{resources.topic}' already exists")
    else:
        ui.step(f"Creating Pub/Sub topic '{resources.topic}'")
        gcloud.run(config, ["pubsub", "topics", "create", resources.topic])

    if _subscription_exists(config, resources.subscription):
        ui.skip(f"Pub/Sub subscription '{resources.subscription}' already exists")
    else:
        ui.step(f"Creating pull subscription '{resources.subscription}'")
        gcloud.run(config, [
            "pubsub", "subscriptions", "create", resources.subscription,
            f"--topic={resources.topic}",
        ])

    if _service_account_exists(config, resources.run_service_account_email):
        ui.skip(f"service account '{resources.run_service_account_email}' already exists")
    else:
        ui.step(f"Creating Cloud Run service account '{resources.run_service_account_id}'")
        gcloud.run(config, [
            "iam", "service-accounts", "create", resources.run_service_account_id,
            f"--display-name=Ghosty webhook receiver {resources.slug}/{resources.name_slug}",
        ])

    ui.step("Granting Cloud Run publisher on the webhook topic")
    _retry_iam_propagation(lambda: gcloud.run(config, [
        "pubsub", "topics", "add-iam-policy-binding", resources.topic,
        f"--member=serviceAccount:{resources.run_service_account_email}",
        "--role=roles/pubsub.publisher",
        "--quiet",
    ]))

    ui.step("Granting agent service account subscriber on the webhook subscription")
    _retry_iam_propagation(lambda: gcloud.run(config, [
        "pubsub", "subscriptions", "add-iam-policy-binding", resources.subscription,
        f"--member=serviceAccount:{resources.agent_service_account_email}",
        "--role=roles/pubsub.subscriber",
        "--quiet",
    ]))

    ui.step("Granting agent service account viewer on the webhook subscription")
    _retry_iam_propagation(lambda: gcloud.run(config, [
        "pubsub", "subscriptions", "add-iam-policy-binding", resources.subscription,
        f"--member=serviceAccount:{resources.agent_service_account_email}",
        "--role=roles/pubsub.viewer",
        "--quiet",
    ]))


def ensure_webhook_gateway(
    config: Config,
    agent: object,
    *,
    name: str,
    provider: str | None = None,
    secret: str | None = None,
    generate_secret: bool = False,
) -> WebhookSetupResult:
    """Provision Cloud Run + Pub/Sub resources for one external webhook gateway."""
    agent_name = _agent_name(agent)
    webhook_provider(provider)
    chosen_provider = WEBHOOK_PROVIDER
    resources = webhook_resources(config, agent_name, name, chosen_provider)
    chosen_secret = _webhook_secret(config, agent_name, name, secret, generate_secret)

    for api in (RUN_API, ARTIFACT_REGISTRY_API, CLOUD_BUILD_API, PUBSUB_API, IAM_API):
        ensure_service_enabled(config, api)

    _ensure_webhook_pubsub_and_iam(config, resources)
    ensure_cloud_run_source_build_iam(config)
    service_url = _deploy_webhook_receiver(config, resources, chosen_secret)
    _set_webhook_metadata(config, resources, secret=chosen_secret, service_url=service_url)
    vm_env_updated, message = upload_webhook_env_to_agent(config, agent, resources, service_url)
    return WebhookSetupResult(
        resources=resources,
        service_url=service_url,
        secret=chosen_secret,
        vm_env_updated=vm_env_updated,
        message=message,
    )


def sync_webhook_gateway(config: Config, agent: object, *, name: str) -> WebhookSetupResult:
    """Reapply webhook Pub/Sub IAM and VM env without redeploying Cloud Run."""
    agent_name = _agent_name(agent)
    meta = _webhook_metadata(config, agent_name, name)
    if not meta:
        raise ValueError(f"webhook gateway '{sanitize_agent_name(name)}' is not configured for '{agent_name}'")
    resources = webhook_resources(config, agent_name, name)
    _ensure_webhook_pubsub_and_iam(config, resources)
    service_url = _cloud_run_service_url(config, resources.service_name) or str(meta.get("url", ""))
    _set_webhook_metadata(config, resources, secret=str(meta.get("secret", "")), service_url=service_url)
    vm_env_updated, message = upload_webhook_env_to_agent(config, agent, resources, service_url)
    return WebhookSetupResult(
        resources=resources,
        service_url=service_url,
        secret=str(meta.get("secret", "")),
        vm_env_updated=vm_env_updated,
        message=message,
    )


def webhook_status(config: Config, agent: object, *, name: str) -> WebhookStatus:
    """Return state for one configured or named webhook gateway."""
    agent_name = _agent_name(agent)
    meta = _webhook_metadata(config, agent_name, name)
    resources = webhook_resources(config, agent_name, name)
    service_exists = _cloud_run_service_exists(config, resources.service_name)
    service_url = _cloud_run_service_url(config, resources.service_name) if service_exists else str(meta.get("url", ""))
    consumer_installed, consumer_active = webhook_consumer_status_on_agent(config, agent, resources)
    return WebhookStatus(
        resources=resources,
        run_api_enabled=service_enabled(config, RUN_API),
        artifactregistry_api_enabled=service_enabled(config, ARTIFACT_REGISTRY_API),
        cloudbuild_api_enabled=service_enabled(config, CLOUD_BUILD_API),
        pubsub_api_enabled=service_enabled(config, PUBSUB_API),
        iam_api_enabled=service_enabled(config, IAM_API),
        topic_exists=_topic_exists(config, resources.topic),
        subscription_exists=_subscription_exists(config, resources.subscription),
        run_service_account_exists=_service_account_exists(config, resources.run_service_account_email),
        service_exists=service_exists,
        publisher_bound=_topic_has_publisher(config, resources.topic, resources.run_service_account_email),
        subscriber_bound=_subscription_has_gateway_subscriber(
            config,
            resources.subscription,
            resources.agent_service_account_email,
        ),
        viewer_bound=_subscription_has_gateway_viewer(
            config,
            resources.subscription,
            resources.agent_service_account_email,
        ),
        vm_env_exists=webhook_env_exists_on_agent(config, agent, resources),
        consumer_installed=consumer_installed,
        consumer_active=consumer_active,
        service_url=service_url,
        secret_configured=bool(meta.get("secret")),
    )


def webhook_names(config: Config, agent: str) -> list[str]:
    data = _webhook_mapping(config).get(sanitize_agent_name(agent), {})
    if not isinstance(data, dict):
        return []
    return sorted(data)


def destroy_webhook_gateway(config: Config, agent: str, *, name: str) -> WebhookResources:
    """Remove Cloud Run and Pub/Sub resources for one webhook gateway."""
    meta = _webhook_metadata(config, agent, name)
    resources = webhook_resources(config, agent, name)

    if _cloud_run_service_exists(config, resources.service_name):
        ui.step(f"Deleting Cloud Run service '{resources.service_name}'")
        gcloud.run(config, [
            "run", "services", "delete", resources.service_name,
            f"--region={config.region}",
            "--quiet",
        ])
    else:
        ui.skip(f"Cloud Run service '{resources.service_name}' not found")

    if _topic_exists(config, resources.topic):
        ui.step("Removing Cloud Run publisher from the webhook topic")
        gcloud.run(config, [
            "pubsub", "topics", "remove-iam-policy-binding", resources.topic,
            f"--member=serviceAccount:{resources.run_service_account_email}",
            "--role=roles/pubsub.publisher",
            "--quiet",
        ], check=False)

    if _subscription_exists(config, resources.subscription):
        ui.step("Removing agent subscriber from the webhook subscription")
        gcloud.run(config, [
            "pubsub", "subscriptions", "remove-iam-policy-binding", resources.subscription,
            f"--member=serviceAccount:{resources.agent_service_account_email}",
            "--role=roles/pubsub.subscriber",
            "--quiet",
        ], check=False)
        ui.step("Removing agent viewer from the webhook subscription")
        gcloud.run(config, [
            "pubsub", "subscriptions", "remove-iam-policy-binding", resources.subscription,
            f"--member=serviceAccount:{resources.agent_service_account_email}",
            "--role=roles/pubsub.viewer",
            "--quiet",
        ], check=False)
        ui.step(f"Deleting Pub/Sub subscription '{resources.subscription}'")
        gcloud.run(config, ["pubsub", "subscriptions", "delete", resources.subscription, "--quiet"])
    else:
        ui.skip(f"Pub/Sub subscription '{resources.subscription}' not found")

    if _topic_exists(config, resources.topic):
        ui.step(f"Deleting Pub/Sub topic '{resources.topic}'")
        gcloud.run(config, ["pubsub", "topics", "delete", resources.topic, "--quiet"])
    else:
        ui.skip(f"Pub/Sub topic '{resources.topic}' not found")

    if _service_account_exists(config, resources.run_service_account_email):
        ui.step(f"Deleting notification service account '{resources.run_service_account_email}'")
        gcloud.run(config, [
            "iam", "service-accounts", "delete", resources.run_service_account_email,
            "--quiet",
        ], check=False)
    else:
        ui.skip(f"notification service account '{resources.run_service_account_email}' not found")

    _clear_webhook_metadata(config, resources)
    return resources


# --- shared agent storage bucket -----------------------------------------

STORAGE_API = "storage.googleapis.com"
STORAGE_JSON_API = "storage-api.googleapis.com"
DEFAULT_STORAGE_PREFIX_ROOT = "agents"


@dataclass
class StorageAgentSyncResult:
    """Result for applying storage IAM/env state to one agent."""

    agent: str
    service_account: str
    private_folder_uri: str = ""
    public_folder_uri: str = ""
    private_folder_iam_updated: bool = False
    public_folder_iam_updated: bool = False
    public_viewer_updated: bool = False
    signed_url_iam_updated: bool = False
    legacy_bucket_iam_removed: bool = False
    vm_env_updated: bool = False
    message: str = ""


@dataclass
class StorageStatusAgent:
    """Storage state for one Ghosty agent service account + VM."""

    agent: str
    service_account: str
    private_folder_uri: str
    private_folder_exists: bool
    has_private_folder_role: bool
    public_folder_uri: str
    public_folder_exists: bool
    has_public_folder_role: bool
    public_folder_is_public: bool
    has_signed_url_iam: bool
    has_legacy_bucket_role: bool
    vm_env_path: str
    vm_env_exists: bool | None


@dataclass
class StorageStatus:
    """Current shared Cloud Storage bucket state."""

    storage_api_enabled: bool
    storage_json_api_enabled: bool
    signing_api_enabled: bool
    auto_grant_enabled: bool
    public_enabled: bool
    signed_urls_enabled: bool
    bucket: str
    bucket_uri: str
    public_bucket: str
    public_bucket_uri: str
    location: str
    public_location: str
    storage_class: str
    public_storage_class: str
    role: str
    public_viewer_role: str
    bucket_exists: bool
    public_bucket_exists: bool
    agents: list[StorageStatusAgent]


@dataclass
class StorageSetupResult:
    """Bucket setup/sync summary."""

    bucket: str
    bucket_uri: str
    location: str
    storage_class: str
    role: str
    agents: list[StorageAgentSyncResult]
    public_bucket: str = ""
    public_bucket_uri: str = ""
    public_location: str = ""
    public_storage_class: str = ""


def storage_bucket_name(config: Config, bucket: str | None = None) -> str:
    """Return the configured, requested, or default shared bucket name."""
    return bucket or config.storage_bucket or f"{config.project_id}-ghosty-agent-storage"


def storage_public_bucket_name(config: Config, bucket: str | None = None) -> str:
    """Return the configured, requested, or default public bucket name."""
    return bucket or config.storage_public_bucket or f"{config.project_id}-ghosty-agent-public"


def storage_location(config: Config, location: str | None = None) -> str:
    """Return the configured, requested, or regional bucket location."""
    return location or config.storage_location or config.region


def storage_public_location(config: Config, location: str | None = None) -> str:
    """Return the configured, requested, or regional public bucket location."""
    return location or config.storage_public_location or config.storage_location or config.region


def storage_agent_prefix(agent: str) -> str:
    """Agent-scoped object prefix inside private/public buckets."""
    return f"{DEFAULT_STORAGE_PREFIX_ROOT}/{sanitize_agent_name(agent)}/"


def storage_agent_folder_uri(config: Config, agent: str, bucket: str | None = None) -> str:
    """Managed folder URI for one agent's private storage."""
    return f"gs://{storage_bucket_name(config, bucket)}/{storage_agent_prefix(agent)}"


def storage_agent_public_folder_uri(config: Config, agent: str, bucket: str | None = None) -> str:
    """Managed folder URI for one agent's public storage."""
    return f"gs://{storage_public_bucket_name(config, bucket)}/{storage_agent_prefix(agent)}"


def storage_agent_public_base_url(config: Config, agent: str, bucket: str | None = None) -> str:
    """Anonymous HTTPS base URL for one agent's public objects."""
    return f"https://storage.googleapis.com/{storage_public_bucket_name(config, bucket)}/{storage_agent_prefix(agent)}"


def _storage_bucket_exists(config: Config, bucket: str) -> bool:
    return gcloud.exists(config, ["storage", "buckets", "describe", f"gs://{bucket}"])


def _bucket_role_members(config: Config, bucket: str, role: str) -> set[str]:
    if not _storage_bucket_exists(config, bucket):
        return set()
    policy = gcloud.run_json(
        config,
        ["storage", "buckets", "get-iam-policy", f"gs://{bucket}"],
    )
    return _members_for_role(policy, role)


def ensure_storage_bucket(
    config: Config,
    bucket: str,
    location: str,
    *,
    storage_class: str | None = None,
    public_access_prevention: bool = True,
) -> None:
    """Create the shared storage bucket if it is missing."""
    pap_flag = "--public-access-prevention" if public_access_prevention else "--no-public-access-prevention"
    if _storage_bucket_exists(config, bucket):
        ui.skip(f"storage bucket 'gs://{bucket}' already exists")
        ui.step(f"Configuring storage bucket 'gs://{bucket}'")
        gcloud.run(config, [
            "storage", "buckets", "update", f"gs://{bucket}",
            "--uniform-bucket-level-access",
            pap_flag,
        ])
        return

    ui.step(f"Creating storage bucket 'gs://{bucket}' in {location}")
    gcloud.run(config, [
        "storage", "buckets", "create", f"gs://{bucket}",
        f"--location={location}",
        f"--default-storage-class={storage_class or config.storage_class}",
        "--uniform-bucket-level-access",
        pap_flag,
    ])
    ui.success(f"created storage bucket 'gs://{bucket}'")


def _managed_folder_exists(config: Config, folder_uri: str) -> bool:
    return gcloud.exists(config, ["storage", "managed-folders", "describe", folder_uri])


def ensure_storage_managed_folder(config: Config, folder_uri: str) -> None:
    """Create a managed folder if it is missing."""
    if _managed_folder_exists(config, folder_uri):
        ui.skip(f"managed folder '{folder_uri}' already exists")
        return
    ui.step(f"Creating managed folder '{folder_uri}'")
    gcloud.run(config, ["storage", "managed-folders", "create", folder_uri])


def _managed_folder_role_members(config: Config, folder_uri: str, role: str) -> set[str]:
    if not _managed_folder_exists(config, folder_uri):
        return set()
    policy = gcloud.run_json(
        config,
        ["storage", "managed-folders", "get-iam-policy", folder_uri],
    )
    return _members_for_role(policy, role)


def grant_storage_to_agent_folder(config: Config, agent: str, folder_uri: str) -> None:
    """Grant object access to one Ghosty agent service account on a managed folder."""
    gcloud.run(config, [
        "storage", "managed-folders", "add-iam-policy-binding", folder_uri,
        f"--member=serviceAccount:{config.sa_email(agent)}",
        f"--role={config.storage_role}",
        "--condition=None",
        "--quiet",
    ])


def remove_storage_from_agent_folder(config: Config, agent: str, folder_uri: str) -> None:
    """Remove object access from one Ghosty agent service account on a managed folder."""
    gcloud.run(config, [
        "storage", "managed-folders", "remove-iam-policy-binding", folder_uri,
        f"--member=serviceAccount:{config.sa_email(agent)}",
        f"--role={config.storage_role}",
        "--condition=None",
        "--quiet",
    ], check=False)


def grant_public_viewer_to_folder(config: Config, folder_uri: str) -> None:
    """Make one managed folder anonymously readable."""
    gcloud.run(config, [
        "storage", "managed-folders", "add-iam-policy-binding", folder_uri,
        "--member=allUsers",
        f"--role={config.storage_public_viewer_role}",
        "--condition=None",
        "--quiet",
    ])


def remove_public_viewer_from_folder(config: Config, folder_uri: str) -> None:
    """Remove anonymous read access from one managed folder."""
    gcloud.run(config, [
        "storage", "managed-folders", "remove-iam-policy-binding", folder_uri,
        "--member=allUsers",
        f"--role={config.storage_public_viewer_role}",
        "--condition=None",
        "--quiet",
    ], check=False)


def remove_legacy_bucket_storage_from_agent(
    config: Config,
    agent: str,
    bucket: str | None = None,
) -> None:
    """Remove old broad bucket-level object access from one agent."""
    bucket_name = bucket or storage_bucket_name(config)
    gcloud.run(config, [
        "storage", "buckets", "remove-iam-policy-binding", f"gs://{bucket_name}",
        f"--member=serviceAccount:{config.sa_email(agent)}",
        f"--role={config.storage_role}",
        "--quiet",
    ], check=False)


def grant_storage_signing_to_agent(config: Config, agent: str) -> None:
    """Let one agent service account sign URLs as itself."""
    sa_email = config.sa_email(agent)
    gcloud.run(config, [
        "iam", "service-accounts", "add-iam-policy-binding", sa_email,
        f"--member=serviceAccount:{sa_email}",
        f"--role={config.storage_signing_role}",
        "--quiet",
    ])


def remove_storage_signing_from_agent(config: Config, agent: str) -> None:
    """Remove self-signing access from one agent service account."""
    sa_email = config.sa_email(agent)
    gcloud.run(config, [
        "iam", "service-accounts", "remove-iam-policy-binding", sa_email,
        f"--member=serviceAccount:{sa_email}",
        f"--role={config.storage_signing_role}",
        "--quiet",
    ], check=False)


def delete_storage_managed_folder(config: Config, folder_uri: str) -> None:
    """Delete one managed folder resource when it exists."""
    if not _managed_folder_exists(config, folder_uri):
        ui.skip(f"storage managed folder '{folder_uri}' not found")
        return
    ui.step(f"Deleting storage managed folder '{folder_uri}'")
    gcloud.run(config, [
        "storage", "managed-folders", "delete", folder_uri,
        "--quiet",
    ], check=False)


def _service_account_role_members(config: Config, sa_email: str, role: str) -> set[str]:
    policy = gcloud.run_json(
        config,
        ["iam", "service-accounts", "get-iam-policy", sa_email],
    )
    return _members_for_role(policy, role)


def _agent_name(agent: object) -> str:
    return getattr(agent, "name", str(agent))


def _agent_status(agent: object) -> str:
    return getattr(agent, "status", "")


def _storage_env_lines(
    config: Config,
    agent: str,
    bucket: str,
    public_bucket: str | None = None,
) -> list[str]:
    prefix = storage_agent_prefix(agent)
    lines = [
        f"GHOSTY_BUCKET={bucket}",
        f"GHOSTY_BUCKET_URI=gs://{bucket}/{prefix}",
        f"GHOSTY_BUCKET_PREFIX={prefix}",
    ]
    if config.storage_public_enabled:
        public_bucket_name = public_bucket or storage_public_bucket_name(config)
        lines.extend([
            f"GHOSTY_PUBLIC_BUCKET={public_bucket_name}",
            f"GHOSTY_PUBLIC_BUCKET_URI=gs://{public_bucket_name}/{prefix}",
            f"GHOSTY_PUBLIC_BUCKET_PREFIX={prefix}",
            f"GHOSTY_PUBLIC_BASE_URL={storage_agent_public_base_url(config, agent, public_bucket_name)}",
        ])
    if config.storage_signed_urls_enabled:
        lines.extend([
            f"GHOSTY_SIGNING_SERVICE_ACCOUNT={config.sa_email(agent)}",
            f"GHOSTY_SIGNED_URL_DEFAULT_DURATION={config.storage_signed_url_default_duration}",
        ])
    lines.append(f"GOOGLE_CLOUD_PROJECT={config.project_id}")
    return lines


def _remote_path_assignment(path: str) -> str:
    """Build a shell assignment for a remote path, preserving ~/ expansion."""
    if path.startswith("~/"):
        suffix = path[2:].replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
        return f'env_path="${{HOME}}/{suffix}"; '
    return f"env_path={shlex.quote(path)}; "


def _write_storage_env_command(
    config: Config,
    agent: str,
    bucket: str,
    public_bucket: str | None = None,
) -> str:
    lines = " ".join(shlex.quote(line) for line in _storage_env_lines(config, agent, bucket, public_bucket))
    return (
        _remote_path_assignment(config.storage_env_path)
        + 'env_dir="${env_path%/*}"; '
        + 'mkdir -p "$env_dir" && chmod 700 "$env_dir" && '
        + f'printf "%s\\n" {lines} > "$env_path" && chmod 600 "$env_path"'
    )


def _remove_storage_env_command(config: Config) -> str:
    return _remote_path_assignment(config.storage_env_path) + 'rm -f "$env_path"'


def _test_storage_env_command(config: Config) -> str:
    return _remote_path_assignment(config.storage_env_path) + 'test -f "$env_path"'


def _run_agent_ssh_command(config: Config, agent: str, command: str):
    return gcloud.run(config, [
        "compute", "ssh", instance_name(agent),
        f"--zone={config.zone}",
        "--tunnel-through-iap",
        f"--command={command}",
        "--quiet",
    ], check=False)


def _proc_message(proc) -> str:
    return ((getattr(proc, "stderr", "") or getattr(proc, "stdout", "")) or "").strip()


def upload_storage_env_to_agent(
    config: Config,
    agent: object,
    bucket: str | None = None,
    public_bucket: str | None = None,
    *,
    attempts: int = 6,
    delay: float = 10.0,
) -> StorageAgentSyncResult:
    """Write Hermes storage.env to a running VM over IAP."""
    agent_name = _agent_name(agent)
    status = _agent_status(agent)
    bucket_name = storage_bucket_name(config, bucket)
    private_folder_uri = storage_agent_folder_uri(config, agent_name, bucket_name)
    public_folder_uri = (
        storage_agent_public_folder_uri(config, agent_name, public_bucket)
        if config.storage_public_enabled else ""
    )
    if status and status != "RUNNING":
        message = f"agent is {status}; start it and rerun `ghosty-agents storage sync {agent_name}`"
        ui.warn(f"Skipped VM env write for '{agent_name}': {message}")
        return StorageAgentSyncResult(
            agent=agent_name,
            service_account=config.sa_email(agent_name),
            private_folder_uri=private_folder_uri,
            public_folder_uri=public_folder_uri,
            vm_env_updated=False,
            message=message,
        )

    command = _write_storage_env_command(config, agent_name, bucket_name, public_bucket)
    proc = None
    for attempt in range(max(1, attempts)):
        proc = _run_agent_ssh_command(config, agent_name, command)
        if proc.returncode == 0:
            return StorageAgentSyncResult(
                agent=agent_name,
                service_account=config.sa_email(agent_name),
                private_folder_uri=private_folder_uri,
                public_folder_uri=public_folder_uri,
                vm_env_updated=True,
            )
        if attempt < max(1, attempts) - 1:
            ui.skip(f"agent '{agent_name}' is not ready for storage config yet; retrying...")
            time.sleep(delay)

    assert proc is not None
    message = _proc_message(proc) or f"SSH exited with return code {proc.returncode}"
    ui.warn(
        f"Could not write storage env on '{agent_name}'. "
        f"Rerun `ghosty-agents storage sync {agent_name}`."
    )
    return StorageAgentSyncResult(
        agent=agent_name,
        service_account=config.sa_email(agent_name),
        private_folder_uri=private_folder_uri,
        public_folder_uri=public_folder_uri,
        vm_env_updated=False,
        message=message,
    )


def remove_storage_env_from_agent(config: Config, agent: object) -> StorageAgentSyncResult:
    """Remove Hermes storage.env from a running VM over IAP."""
    agent_name = _agent_name(agent)
    status = _agent_status(agent)
    if status and status != "RUNNING":
        message = f"agent is {status}; remove the env file later if needed"
        ui.warn(f"Skipped VM env removal for '{agent_name}': {message}")
        return StorageAgentSyncResult(
            agent=agent_name,
            service_account=config.sa_email(agent_name),
            private_folder_uri=storage_agent_folder_uri(config, agent_name),
            public_folder_uri=storage_agent_public_folder_uri(config, agent_name) if config.storage_public_enabled else "",
            vm_env_updated=False,
            message=message,
        )

    proc = _run_agent_ssh_command(config, agent_name, _remove_storage_env_command(config))
    if proc.returncode == 0:
        return StorageAgentSyncResult(
            agent=agent_name,
            service_account=config.sa_email(agent_name),
            private_folder_uri=storage_agent_folder_uri(config, agent_name),
            public_folder_uri=storage_agent_public_folder_uri(config, agent_name) if config.storage_public_enabled else "",
            vm_env_updated=True,
        )

    message = _proc_message(proc) or f"SSH exited with return code {proc.returncode}"
    ui.warn(f"Could not remove storage env on '{agent_name}'.")
    return StorageAgentSyncResult(
        agent=agent_name,
        service_account=config.sa_email(agent_name),
        private_folder_uri=storage_agent_folder_uri(config, agent_name),
        public_folder_uri=storage_agent_public_folder_uri(config, agent_name) if config.storage_public_enabled else "",
        vm_env_updated=False,
        message=message,
    )


def storage_env_exists_on_agent(config: Config, agent: object) -> bool | None:
    """Check whether storage.env exists on a running VM. None means unreachable/skipped."""
    agent_name = _agent_name(agent)
    status = _agent_status(agent)
    if status and status != "RUNNING":
        return None
    proc = _run_agent_ssh_command(config, agent_name, _test_storage_env_command(config))
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    return None


def cleanup_storage_for_agent(
    config: Config,
    agent: object,
    *,
    delete_managed_folders: bool = True,
) -> StorageAgentSyncResult:
    """Remove one agent's storage access/env and optional managed folder resources."""
    agent_name = _agent_name(agent)
    bucket_name = storage_bucket_name(config)
    public_bucket_name = storage_public_bucket_name(config)
    private_folder_uri = storage_agent_folder_uri(config, agent_name, bucket_name)
    public_folder_uri = (
        storage_agent_public_folder_uri(config, agent_name, public_bucket_name)
        if config.storage_public_enabled or config.storage_public_bucket else ""
    )

    private_bucket_exists = bool(bucket_name) and _storage_bucket_exists(config, bucket_name)
    public_bucket_exists = bool(public_bucket_name) and (
        config.storage_public_enabled or bool(config.storage_public_bucket)
    ) and _storage_bucket_exists(config, public_bucket_name)

    if private_bucket_exists:
        ui.step(f"Removing {config.storage_role} on {private_folder_uri} from {config.sa_email(agent_name)}")
        remove_storage_from_agent_folder(config, agent_name, private_folder_uri)
        remove_legacy_bucket_storage_from_agent(config, agent_name, bucket_name)
        if delete_managed_folders:
            delete_storage_managed_folder(config, private_folder_uri)
    else:
        ui.skip(f"storage bucket 'gs://{bucket_name}' not found")

    if public_folder_uri:
        if public_bucket_exists:
            ui.step(f"Removing public storage IAM on {public_folder_uri}")
            remove_storage_from_agent_folder(config, agent_name, public_folder_uri)
            remove_public_viewer_from_folder(config, public_folder_uri)
            remove_legacy_bucket_storage_from_agent(config, agent_name, public_bucket_name)
            if delete_managed_folders:
                delete_storage_managed_folder(config, public_folder_uri)
        else:
            ui.skip(f"public storage bucket 'gs://{public_bucket_name}' not found")

    if config.storage_signed_urls_enabled:
        remove_storage_signing_from_agent(config, agent_name)

    result = remove_storage_env_from_agent(config, agent)
    result.private_folder_uri = private_folder_uri
    result.public_folder_uri = public_folder_uri
    result.private_folder_iam_updated = private_bucket_exists
    result.public_folder_iam_updated = public_bucket_exists
    result.public_viewer_updated = public_bucket_exists
    result.signed_url_iam_updated = config.storage_signed_urls_enabled
    result.legacy_bucket_iam_removed = private_bucket_exists or public_bucket_exists
    return result


def sync_storage_for_agent(
    config: Config,
    agent: object,
    bucket: str | None = None,
) -> StorageAgentSyncResult:
    """Grant managed-folder IAM and upload Hermes env config for one agent."""
    agent_name = _agent_name(agent)
    bucket_name = storage_bucket_name(config, bucket)
    private_folder_uri = storage_agent_folder_uri(config, agent_name, bucket_name)

    ensure_storage_managed_folder(config, private_folder_uri)
    ui.step(f"Granting {config.storage_role} on {private_folder_uri} to {config.sa_email(agent_name)}")
    _retry_iam_propagation(lambda: grant_storage_to_agent_folder(config, agent_name, private_folder_uri))
    remove_legacy_bucket_storage_from_agent(config, agent_name, bucket_name)

    public_folder_uri = ""
    if config.storage_public_enabled:
        public_bucket_name = storage_public_bucket_name(config)
        public_folder_uri = storage_agent_public_folder_uri(config, agent_name, public_bucket_name)
        ensure_storage_managed_folder(config, public_folder_uri)
        ui.step(f"Granting {config.storage_role} on {public_folder_uri} to {config.sa_email(agent_name)}")
        _retry_iam_propagation(lambda: grant_storage_to_agent_folder(config, agent_name, public_folder_uri))
        ui.step(f"Making {public_folder_uri} anonymously readable")
        grant_public_viewer_to_folder(config, public_folder_uri)
        remove_legacy_bucket_storage_from_agent(config, agent_name, public_bucket_name)

    if config.storage_signed_urls_enabled:
        ui.step(f"Granting signed URL self-signing to {config.sa_email(agent_name)}")
        _retry_iam_propagation(lambda: grant_storage_signing_to_agent(config, agent_name))

    result = upload_storage_env_to_agent(config, agent, bucket_name, storage_public_bucket_name(config) if config.storage_public_enabled else None)
    result.private_folder_iam_updated = True
    result.public_folder_iam_updated = config.storage_public_enabled
    result.public_viewer_updated = config.storage_public_enabled
    result.signed_url_iam_updated = config.storage_signed_urls_enabled
    result.legacy_bucket_iam_removed = True
    result.private_folder_uri = private_folder_uri
    result.public_folder_uri = public_folder_uri
    return result


def sync_storage(
    config: Config,
    agent_items: Sequence[object] | None = None,
    bucket: str | None = None,
) -> StorageSetupResult:
    """Reapply shared bucket IAM/env config to one or all Ghosty agents."""
    if agent_items is None:
        from ghosty import agents
        agent_items = agents.list_agents(config)

    bucket_name = storage_bucket_name(config, bucket)
    public_bucket_name = storage_public_bucket_name(config) if config.storage_public_enabled else ""
    results = [
        sync_storage_for_agent(config, agent, bucket_name)
        for agent in sorted(agent_items, key=_agent_name)
    ]
    return StorageSetupResult(
        bucket=bucket_name,
        bucket_uri=f"gs://{bucket_name}",
        location=storage_location(config),
        storage_class=config.storage_class,
        role=config.storage_role,
        agents=results,
        public_bucket=public_bucket_name,
        public_bucket_uri=f"gs://{public_bucket_name}" if public_bucket_name else "",
        public_location=storage_public_location(config) if public_bucket_name else "",
        public_storage_class=config.storage_public_class if public_bucket_name else "",
    )


def setup_storage(
    config: Config,
    *,
    bucket: str | None = None,
    public_bucket: str | None = None,
    location: str | None = None,
    with_public: bool = False,
    with_signed_urls: bool = False,
    agent_items: Sequence[object] | None = None,
) -> StorageSetupResult:
    """Enable APIs, create/configure the shared bucket, and sync agents."""
    requested_bucket = bool(bucket or config.storage_bucket)
    bucket_name = storage_bucket_name(config, bucket)
    chosen_location = storage_location(config, location)
    public_requested = bool(public_bucket or config.storage_public_bucket)
    enable_public = with_public or bool(public_bucket) or config.storage_public_enabled
    enable_signed_urls = with_signed_urls or config.storage_signed_urls_enabled

    ensure_service_enabled(config, STORAGE_API)
    ensure_service_enabled(config, STORAGE_JSON_API)
    if enable_signed_urls:
        ensure_service_enabled(config, config.storage_signing_api)
    try:
        ensure_storage_bucket(
            config,
            bucket_name,
            chosen_location,
            storage_class=config.storage_class,
            public_access_prevention=True,
        )
    except gcloud.GcloudError as exc:
        if not requested_bucket:
            hint = (
                f"\nDefault bucket name 'gs://{bucket_name}' is unavailable or inaccessible. "
                "Rerun with `ghosty-agents bucket setup --bucket NAME`."
            )
            raise gcloud.GcloudError(exc.args_list, exc.returncode, f"{exc.stderr}{hint}") from exc
        raise

    config.storage_enabled = True
    config.storage_bucket = bucket_name
    config.storage_location = chosen_location

    if enable_public:
        public_bucket_name = storage_public_bucket_name(config, public_bucket)
        chosen_public_location = storage_public_location(config, location)
        try:
            ensure_storage_bucket(
                config,
                public_bucket_name,
                chosen_public_location,
                storage_class=config.storage_public_class,
                public_access_prevention=False,
            )
        except gcloud.GcloudError as exc:
            if not public_requested:
                hint = (
                    f"\nDefault public bucket name 'gs://{public_bucket_name}' is unavailable or inaccessible. "
                    "Rerun with `ghosty-agents bucket setup --with-public --public-bucket NAME`."
                )
                raise gcloud.GcloudError(exc.args_list, exc.returncode, f"{exc.stderr}{hint}") from exc
            raise
        config.storage_public_enabled = True
        config.storage_public_bucket = public_bucket_name
        config.storage_public_location = chosen_public_location

    if enable_signed_urls:
        config.storage_signed_urls_enabled = True

    return sync_storage(config, agent_items, bucket_name)


def storage_status(
    config: Config,
    agent_items: Sequence[object] | None = None,
    *,
    check_vm_env: bool = True,
) -> StorageStatus:
    """Return API, bucket, IAM, and VM env-file state."""
    if agent_items is None:
        from ghosty import agents
        agent_items = agents.list_agents(config)

    bucket_name = storage_bucket_name(config)
    public_bucket_name = storage_public_bucket_name(config)
    exists = _storage_bucket_exists(config, bucket_name)
    public_exists = (
        _storage_bucket_exists(config, public_bucket_name)
        if config.storage_public_enabled or config.storage_public_bucket else False
    )
    legacy_members = _bucket_role_members(config, bucket_name, config.storage_role) if exists else set()
    public_legacy_members = (
        _bucket_role_members(config, public_bucket_name, config.storage_role)
        if public_exists else set()
    )
    agent_states = []
    for agent in sorted(agent_items, key=_agent_name):
        name = _agent_name(agent)
        sa_email = config.sa_email(name)
        private_folder_uri = storage_agent_folder_uri(config, name, bucket_name)
        private_folder_exists = _managed_folder_exists(config, private_folder_uri)
        private_members = (
            _managed_folder_role_members(config, private_folder_uri, config.storage_role)
            if private_folder_exists else set()
        )
        public_folder_uri = ""
        public_folder_exists = False
        public_members: set[str] = set()
        public_viewer_members: set[str] = set()
        if config.storage_public_enabled or config.storage_public_bucket:
            public_folder_uri = storage_agent_public_folder_uri(config, name, public_bucket_name)
            public_folder_exists = _managed_folder_exists(config, public_folder_uri)
            if public_folder_exists:
                public_members = _managed_folder_role_members(config, public_folder_uri, config.storage_role)
                public_viewer_members = _managed_folder_role_members(
                    config,
                    public_folder_uri,
                    config.storage_public_viewer_role,
                )
        signed_members = (
            _service_account_role_members(config, sa_email, config.storage_signing_role)
            if config.storage_signed_urls_enabled else set()
        )
        env_exists = storage_env_exists_on_agent(config, agent) if check_vm_env else None
        agent_states.append(StorageStatusAgent(
            agent=name,
            service_account=sa_email,
            private_folder_uri=private_folder_uri,
            private_folder_exists=private_folder_exists,
            has_private_folder_role=f"serviceAccount:{sa_email}" in private_members,
            public_folder_uri=public_folder_uri,
            public_folder_exists=public_folder_exists,
            has_public_folder_role=f"serviceAccount:{sa_email}" in public_members,
            public_folder_is_public="allUsers" in public_viewer_members,
            has_signed_url_iam=f"serviceAccount:{sa_email}" in signed_members,
            has_legacy_bucket_role=(
                f"serviceAccount:{sa_email}" in legacy_members
                or f"serviceAccount:{sa_email}" in public_legacy_members
            ),
            vm_env_path=config.storage_env_path,
            vm_env_exists=env_exists,
        ))

    return StorageStatus(
        storage_api_enabled=service_enabled(config, STORAGE_API),
        storage_json_api_enabled=service_enabled(config, STORAGE_JSON_API),
        signing_api_enabled=service_enabled(config, config.storage_signing_api),
        auto_grant_enabled=config.storage_enabled,
        public_enabled=config.storage_public_enabled,
        signed_urls_enabled=config.storage_signed_urls_enabled,
        bucket=bucket_name,
        bucket_uri=f"gs://{bucket_name}",
        public_bucket=public_bucket_name,
        public_bucket_uri=f"gs://{public_bucket_name}",
        location=storage_location(config),
        public_location=storage_public_location(config),
        storage_class=config.storage_class,
        public_storage_class=config.storage_public_class,
        role=config.storage_role,
        public_viewer_role=config.storage_public_viewer_role,
        bucket_exists=exists,
        public_bucket_exists=public_exists,
        agents=agent_states,
    )


def disable_storage(
    config: Config,
    agent_items: Sequence[object] | None = None,
) -> StorageSetupResult:
    """Remove Ghosty agent access/env files without deleting the bucket or data."""
    if agent_items is None:
        from ghosty import agents
        agent_items = agents.list_agents(config)

    bucket_name = storage_bucket_name(config)
    public_bucket_name = storage_public_bucket_name(config)
    results: list[StorageAgentSyncResult] = []
    bucket_exists = _storage_bucket_exists(config, bucket_name)
    public_bucket_exists = (
        _storage_bucket_exists(config, public_bucket_name)
        if config.storage_public_enabled or config.storage_public_bucket else False
    )
    for agent in sorted(agent_items, key=_agent_name):
        name = _agent_name(agent)
        if bucket_exists:
            private_folder_uri = storage_agent_folder_uri(config, name, bucket_name)
            ui.step(f"Removing {config.storage_role} on {private_folder_uri} from {config.sa_email(name)}")
            remove_storage_from_agent_folder(config, name, private_folder_uri)
            remove_legacy_bucket_storage_from_agent(config, name, bucket_name)
        else:
            ui.skip(f"storage bucket 'gs://{bucket_name}' not found")
        if public_bucket_exists:
            public_folder_uri = storage_agent_public_folder_uri(config, name, public_bucket_name)
            ui.step(f"Removing public storage IAM on {public_folder_uri}")
            remove_storage_from_agent_folder(config, name, public_folder_uri)
            remove_public_viewer_from_folder(config, public_folder_uri)
            remove_legacy_bucket_storage_from_agent(config, name, public_bucket_name)
        if config.storage_signed_urls_enabled:
            remove_storage_signing_from_agent(config, name)
        results.append(remove_storage_env_from_agent(config, agent))

    previous_location = storage_location(config)
    previous_public_location = storage_public_location(config)
    previous_class = config.storage_class
    previous_public_class = config.storage_public_class
    previous_role = config.storage_role
    config.storage_enabled = False
    config.storage_bucket = ""
    config.storage_location = ""
    config.storage_public_enabled = False
    config.storage_public_bucket = ""
    config.storage_public_location = ""
    config.storage_signed_urls_enabled = False

    return StorageSetupResult(
        bucket=bucket_name,
        bucket_uri=f"gs://{bucket_name}",
        public_bucket=public_bucket_name if public_bucket_exists else "",
        public_bucket_uri=f"gs://{public_bucket_name}" if public_bucket_exists else "",
        location=previous_location,
        public_location=previous_public_location,
        storage_class=previous_class,
        public_storage_class=previous_public_class,
        role=previous_role,
        agents=results,
    )


# --- budget ----------------------------------------------------------------

def _project_number(config: Config) -> str:
    proc = gcloud.run(
        config,
        ["projects", "describe", config.project_id, "--format=value(projectNumber)"],
        no_project=True,
    )
    return (proc.stdout or "").strip()


def ensure_budget(config: Config) -> None:
    existing = gcloud.run_json(
        config,
        ["billing", "budgets", "list", f"--billing-account={config.billing_account_id}"],
        no_project=True,
    )
    names = [b.get("displayName") for b in existing] if isinstance(existing, list) else []
    if config.budget_name in names:
        ui.skip(f"budget '{config.budget_name}' already exists")
        return
    number = _project_number(config)
    ui.step(f"Creating budget '{config.budget_name}' = {config.budget_amount}{config.budget_currency}")
    gcloud.run(config, [
        "billing", "budgets", "create",
        f"--billing-account={config.billing_account_id}",
        f"--display-name={config.budget_name}",
        f"--budget-amount={config.budget_amount}{config.budget_currency}",
        f"--filter-projects=projects/{number}",
        "--threshold-rule=percent=0.5",
        "--threshold-rule=percent=0.9",
        "--threshold-rule=percent=1.0",
    ], no_project=True)
    ui.success("budget alert created")


def bootstrap_all(
    config: Config,
    project_name: str | None = None,
    *,
    with_nat: bool = False,
) -> None:
    """Run the full one-time setup in order. Idempotent."""
    ensure_project(config, project_name)
    link_billing(config)
    enable_apis(config)
    ensure_network(config)
    if with_nat:
        ensure_nat(config)
    ensure_firewall(config)
    grant_iap_access(config)
    ensure_budget(config)
