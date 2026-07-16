"""Read-only live harness view for a configured agent."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable

from rich.console import Group, RenderableType
from rich.live import Live
from rich.markup import escape
from rich.text import Text

from ghosty import agents, bootstrap, config as config_mod, gcloud, ui
from ghosty.models import Agent, Config, sanitize_agent_name


READY = "ready"
ATTACHED = "attached"
ATTENTION = "attention"
OFF = "off"
UNKNOWN = "unknown"

_PULSE = (".", "o", "O", "o")
_STYLE = {
    READY: "green",
    ATTACHED: "green",
    ATTENTION: "yellow",
    OFF: "dim",
    UNKNOWN: "yellow",
}


@dataclass
class HarnessCapability:
    """One visible capability attached to an agent harness."""

    name: str
    state: str
    summary: str
    detail: str = ""
    advanced: list[tuple[str, str]] = field(default_factory=list)
    shared: bool = False


@dataclass
class HarnessSnapshot:
    """Complete read-only harness snapshot for one agent."""

    agent: Agent
    capabilities: list[HarnessCapability]
    advanced: list[tuple[str, str]] = field(default_factory=list)


def _advanced_to_json(values: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"label": str(label), "value": str(value)} for label, value in values]


def snapshot_to_dict(
    snapshot: HarnessSnapshot,
    *,
    refresh_interval: float = 2.0,
    generated_at: float | None = None,
) -> dict:
    """Serialize a harness snapshot for the browser viewer."""
    return {
        "generated_at": generated_at if generated_at is not None else time.time(),
        "refresh_interval": refresh_interval,
        "agent": {
            "name": snapshot.agent.name,
            "instance": snapshot.agent.instance,
            "status": snapshot.agent.status,
            "zone": snapshot.agent.zone,
            "machine_type": snapshot.agent.machine_type,
            "internal_ip": snapshot.agent.internal_ip,
            "created": snapshot.agent.created,
            "advanced": _advanced_to_json(snapshot.advanced),
        },
        "capabilities": [
            {
                "name": capability.name,
                "state": capability.state,
                "summary": capability.summary,
                "detail": capability.detail,
                "shared": capability.shared,
                "advanced": _advanced_to_json(capability.advanced),
            }
            for capability in snapshot.capabilities
        ],
    }


def _safe(fn: Callable):
    try:
        return fn(), ""
    except (gcloud.GcloudError, gcloud.GcloudNotFound, ValueError, RuntimeError) as exc:
        return None, str(exc)


def _unknown(name: str, error: str, *, shared: bool = False) -> HarnessCapability:
    return HarnessCapability(
        name=name,
        state=UNKNOWN,
        summary="unknown",
        detail="check failed",
        advanced=[("error", error or "unknown error")],
        shared=shared,
    )


def _connect_capability(agent: Agent) -> HarnessCapability:
    running = agent.status == "RUNNING"
    return HarnessCapability(
        name="Connect",
        state=READY if running else ATTENTION,
        summary="ready" if running else "start first",
        detail="ready to connect" if running else f"agent is {agent.status or 'not running'}",
        advanced=[
            ("instance", agent.instance),
            ("zone", agent.zone),
        ],
    )


def _key_project_id(path) -> str:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return ""
    project_id = data.get("project_id") if isinstance(data, dict) else ""
    return str(project_id) if project_id else ""


def _chat_project_candidates(config: Config, agent: Agent) -> list[tuple[str, str]]:
    slug = sanitize_agent_name(agent.name)
    candidates: list[tuple[str, str]] = []

    mapping = config.google_chat_projects if isinstance(config.google_chat_projects, dict) else {}
    for key in (slug, agent.name):
        project = mapping.get(key)
        if project:
            candidates.append((str(project), "saved config"))

    base = config_mod.config_dir() / "google-chat" / slug
    if base.exists():
        for item in sorted(base.iterdir()):
            if item.is_dir():
                if (item / "service-account.json").exists() or any(item.iterdir()):
                    candidates.append((item.name, "local key folder"))
            elif item.name.endswith("-service-account.json"):
                candidates.append((item.name[: -len("-service-account.json")], "local key file"))
            elif item.name == "service-account.json":
                project = _key_project_id(item)
                if project:
                    candidates.append((project, "local key file"))

    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for project, source in candidates:
        if project and project not in seen:
            seen.add(project)
            unique.append((project, source))
    return unique


def _chat_capability(config: Config, agent: Agent) -> HarnessCapability:
    candidates = _chat_project_candidates(config, agent)
    if not candidates:
        return HarnessCapability(
            name="Chat",
            state=OFF,
            summary="not added",
            detail="add chat when this agent should receive Google Chat events",
        )
    chat_project, source = candidates[0]
    resources, error = _safe(lambda: bootstrap.google_chat_resources(config, agent.name, str(chat_project)))
    if resources is None:
        return _unknown("Chat", error)
    return HarnessCapability(
        name="Chat",
        state=ATTACHED,
        summary="attached",
        detail=f"project {resources.chat_project}",
        advanced=[
            ("project", resources.chat_project),
            ("detected from", source),
            ("topic", resources.full_topic),
            ("subscription", resources.full_subscription),
            ("key path", resources.vm_key_path),
        ],
    )


def _notifications_capability(config: Config, agent: Agent) -> HarnessCapability:
    names, error = _safe(lambda: bootstrap.webhook_names(config, agent.name))
    if names is None:
        return _unknown("Notifications", error)
    if not names:
        return HarnessCapability(
            name="Notifications",
            state=OFF,
            summary="not added",
            detail="add notifications when external systems should call this agent",
        )

    agent_map = {}
    if isinstance(config.webhook_gateways, dict):
        raw = config.webhook_gateways.get(sanitize_agent_name(agent.name), {})
        agent_map = raw if isinstance(raw, dict) else {}

    advanced: list[tuple[str, str]] = []
    for name in names:
        resources, resource_error = _safe(lambda name=name: bootstrap.webhook_resources(config, agent.name, name))
        meta = agent_map.get(sanitize_agent_name(name), {})
        meta = meta if isinstance(meta, dict) else {}
        advanced.append((f"{name} url", str(meta.get("url") or "not recorded")))
        if resources is not None:
            advanced.append((f"{name} subscription", resources.full_subscription))
            advanced.append((f"{name} config path", resources.env_path))
        elif resource_error:
            advanced.append((f"{name} error", resource_error))

    return HarnessCapability(
        name="Notifications",
        state=ATTACHED,
        summary=f"{len(names)} path" + ("" if len(names) == 1 else "s"),
        detail=", ".join(names),
        advanced=advanced,
    )


def _storage_capability(config: Config, agent: Agent) -> HarnessCapability:
    configured = any([
        config.storage_enabled,
        config.storage_bucket,
        config.storage_public_enabled,
        config.storage_public_bucket,
        config.storage_signed_urls_enabled,
    ])
    if not configured:
        return HarnessCapability(
            name="Storage",
            state=OFF,
            summary="not added",
            detail="add storage when this agent needs shared files",
        )

    status, error = _safe(lambda: bootstrap.storage_status(config, [agent], check_vm_env=False))
    if status is None:
        return _unknown("Storage", error)
    agent_state = status.agents[0] if status.agents else None
    ready = bool(agent_state and agent_state.has_private_folder_role)
    advanced = [
        ("private files", status.bucket_uri),
        ("public files", status.public_bucket_uri if status.public_enabled else "off"),
        ("signed links", "enabled" if status.signed_urls_enabled else "off"),
    ]
    if agent_state:
        advanced.extend([
            ("private folder", agent_state.private_folder_uri),
            ("public folder", agent_state.public_folder_uri or "off"),
        ])
    return HarnessCapability(
        name="Storage",
        state=ATTACHED if ready else ATTENTION,
        summary="ready" if ready else "sync needed",
        detail="private files ready" if ready else "storage configured, access may need sync",
        advanced=advanced,
    )


def _models_capability(config: Config, agent: Agent) -> HarnessCapability:
    status, error = _safe(lambda: bootstrap.google_ai_status(config, agent_names=[agent.name]))
    if status is None:
        return _unknown("Models", error)
    agent_state = status.agents[0] if status.agents else None
    has_role = bool(agent_state and agent_state.has_role)
    attached = status.auto_grant_enabled or has_role
    if not attached:
        return HarnessCapability(
            name="Models",
            state=OFF,
            summary="not enabled",
            detail="enable models when this agent should call Google AI",
            advanced=[
                ("api", status.api),
                ("role", status.role),
            ],
        )
    ready = status.api_enabled and has_role
    return HarnessCapability(
        name="Models",
        state=READY if ready else ATTENTION,
        summary="ready" if ready else "access pending",
        detail="model access ready" if ready else "configured, but API or role is not fully ready",
        advanced=[
            ("api", status.api),
            ("api enabled", "yes" if status.api_enabled else "no"),
            ("role", status.role),
            ("service account", agent_state.service_account if agent_state else config.sa_email(agent.name)),
        ],
    )


def _internet_capability(config: Config) -> HarnessCapability:
    status, error = _safe(lambda: bootstrap.nat_status(config))
    if status is None:
        return _unknown("Internet", error, shared=True)
    return HarnessCapability(
        name="Internet",
        state=READY if status.enabled else OFF,
        summary="enabled" if status.enabled else "off",
        detail="shared for private agents",
        advanced=[
            ("scope", "shared"),
            ("region", status.region),
            ("router", status.router),
            ("nat", status.nat),
            ("subnet", status.subnet),
        ],
        shared=True,
    )


def collect_harness(config: Config, agent: Agent) -> HarnessSnapshot:
    """Collect a read-only snapshot of an agent's attached harness."""
    fresh, error = _safe(lambda: agents.get_agent(config, agent.name))
    current = fresh or agent
    advanced = [
        ("instance", current.instance),
        ("machine", current.machine_type),
        ("zone", current.zone),
        ("private IP", current.internal_ip or "-"),
    ]
    if fresh is None and error:
        advanced.append(("agent refresh", error))

    capabilities = [
        _connect_capability(current),
        _chat_capability(config, current),
        _notifications_capability(config, current),
        _storage_capability(config, current),
        _models_capability(config, current),
        _internet_capability(config),
    ]
    return HarnessSnapshot(agent=current, capabilities=capabilities, advanced=advanced)


def _marker(capability: HarnessCapability, frame: int) -> str:
    if capability.state in {READY, ATTACHED}:
        return _PULSE[frame % len(_PULSE)]
    if capability.state == ATTENTION:
        return "!"
    if capability.state == UNKNOWN:
        return "?"
    return "-"


def _capability_text(capability: HarnessCapability, frame: int) -> Text:
    style = _STYLE.get(capability.state, "white")
    text = Text()
    text.append(_marker(capability, frame), style=f"bold {style}")
    text.append(f" {capability.name}", style=f"bold {style}")
    text.append(f" [{capability.summary}]", style=style)
    if capability.shared:
        text.append(" shared", style="dim")
    return text


def _block_token(row: int, col: int, frame: int) -> str:
    tokens = ("###", "^^^", ":::")
    return tokens[(row + col + frame) % len(tokens)]


def _capability_code(capability: HarnessCapability, frame: int) -> str:
    code = {
        "Connect": "CON",
        "Chat": "CHT",
        "Notifications": "NTF",
        "Storage": "STR",
        "Models": "MDL",
        "Internet": "NET",
    }.get(capability.name, capability.name[:3].upper())
    marker = _marker(capability, frame)
    if capability.state == OFF:
        return "---"
    if capability.state in {ATTENTION, UNKNOWN}:
        return f"{marker}{code[:2]}"
    return code


def _append_voxel_row(text: Text, tiles: list[tuple[str, str]], row: int, *, offset: int) -> None:
    top = Text(" " * offset)
    side = Text(" " * offset)
    for label, style in tiles:
        top.append("/", style="bright_black")
        top.append(f"{label:^3}", style=f"bold {style}")
        top.append("\\", style="bright_black")
        top.append(" ")
        side.append("|", style="rgb(92,68,42)")
        side.append("###", style="rgb(92,68,42)")
        side.append("| ")
    text.append_text(top)
    text.append("\n")
    text.append_text(side)
    text.append(f"  layer:{row}\n", style="dim")


def _harness_map(snapshot: HarnessSnapshot, frame: int) -> Text:
    caps = {cap.name: cap for cap in snapshot.capabilities}
    agent_style = "green" if snapshot.agent.status == "RUNNING" else "yellow"
    text = Text()
    terrain_style = "rgb(78,151,64)"
    water_style = "blue"
    text.append("\n        VOXEL HARNESS", style="bold green")
    text.append(f"  tick:{frame % 10}", style="dim")
    text.append("  biome:private-agent\n\n", style="dim")

    layout: list[list[str | None]] = [
        [None, "Chat", None, None, "Models", None],
        ["Connect", None, None, None, None, "Notifications"],
        [None, None, "Agent", None, None, None],
        [None, "Storage", None, None, "Internet", None],
    ]

    for row_index, row in enumerate(layout):
        tiles: list[tuple[str, str]] = []
        for col_index, item in enumerate(row):
            if item == "Agent":
                tiles.append(("@", agent_style))
            elif item:
                capability = caps[item]
                tiles.append((_capability_code(capability, frame + row_index + col_index), _STYLE.get(capability.state, "white")))
            elif (row_index + col_index) % 5 == 0:
                tiles.append(("~~~", water_style))
            else:
                tiles.append((_block_token(row_index, col_index, frame), terrain_style))
        _append_voxel_row(text, tiles, row_index, offset=8 + (row_index % 2) * 3)

    text.append("\n        @ ", style=f"bold {agent_style}")
    text.append(snapshot.agent.name, style=f"bold {agent_style}")
    text.append(f"  {snapshot.agent.status or 'UNKNOWN'}", style=agent_style)
    text.append(f" | {snapshot.agent.machine_type or '-'} | {snapshot.agent.zone or '-'}\n\n", style="dim")
    text.append("        Beacons\n", style="bold green")
    for capability in snapshot.capabilities:
        text.append("          ")
        text.append_text(_capability_text(capability, frame))
        text.append("\n")
    return text


def _advanced_text(snapshot: HarnessSnapshot) -> Text:
    text = Text("\nADVANCED VALUES\n", style="bold cyan")
    text.append("  Agent\n", style="bold")
    for key, value in snapshot.advanced:
        text.append(f"    {key}: ", style="dim")
        text.append(f"{value}\n")
    for capability in snapshot.capabilities:
        text.append(f"  {capability.name}\n", style="bold")
        if capability.advanced:
            for key, value in capability.advanced:
                text.append(f"    {key}: ", style="dim")
                text.append(f"{value}\n")
        else:
            text.append("    no technical values\n", style="dim")
    return text


def render_harness(snapshot: HarnessSnapshot, frame: int = 0) -> RenderableType:
    """Build a Rich renderable for one harness snapshot."""
    header = Text()
    header.append("Live harness view for ", style="cyan")
    header.append(escape(snapshot.agent.name), style="bold cyan")
    header.append(". Press Ctrl+C to return to the agent menu.\n", style="cyan")
    return Group(header, _harness_map(snapshot, frame), _advanced_text(snapshot))


def live_harness(config: Config, agent: Agent, *, refresh_interval: float = 2.0) -> None:
    """Show a live harness dashboard until the user presses Ctrl+C."""
    frame = 0
    snapshot = collect_harness(config, agent)
    try:
        with Live(
            render_harness(snapshot, frame),
            console=ui.console,
            refresh_per_second=4,
            screen=ui.is_interactive_tty(),
        ) as live:
            while True:
                time.sleep(refresh_interval)
                frame += 1
                snapshot = collect_harness(config, snapshot.agent)
                live.update(render_harness(snapshot, frame))
    except KeyboardInterrupt:
        ui.skip("returned to agent menu")
