"""Render and deliver post-setup instructions to agents over IAP SSH."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from ghosty import bootstrap, config as config_mod, gcloud, ui
from ghosty.models import Agent, Config, instance_name, sanitize_agent_name


SERVICES = {"chat", "notifications", "storage", "models"}
DEFAULT_INSTRUCTION_TIMEOUT_SECONDS = 600


@dataclass
class InstructionDeliveryResult:
    """Result of briefing an agent after a service is attached."""

    agent: str
    service: str
    remote_prompt_path: str
    local_prompt_path: Path
    uploaded: bool
    delivered: bool
    message: str = ""


def instruction_prompt_path(config: Config, service: str) -> str:
    """Remote path for a service handoff prompt."""
    service_slug = sanitize_agent_name(service)
    return f"{config.agent_instruction_dir.rstrip('/')}/{service_slug}-setup.md"


def local_prompt_path(agent: str, service: str) -> Path:
    """Local retained prompt copy under Ghosty's config directory."""
    return (
        config_mod.config_dir()
        / "instructions"
        / sanitize_agent_name(agent)
        / f"{sanitize_agent_name(service)}-setup.md"
    )


def _agent_name(agent: Agent | str) -> str:
    return str(getattr(agent, "name", getattr(agent, "agent", agent)))


def _agent_status(agent: Agent | str) -> str:
    return str(getattr(agent, "status", ""))


def _metadata_line(label: str, value: str | None) -> str:
    return f"- {label}: `{value or '-'}`"


def _notification_meta(config: Config, agent: str, name: str) -> dict:
    agent_map = config.webhook_gateways.get(sanitize_agent_name(agent), {}) if isinstance(config.webhook_gateways, dict) else {}
    agent_map = agent_map if isinstance(agent_map, dict) else {}
    meta = agent_map.get(sanitize_agent_name(name), {})
    return meta if isinstance(meta, dict) else {}


def _resolve_notification_name(config: Config, agent: str, name: str | None) -> str:
    if name:
        return name
    names = bootstrap.webhook_names(config, agent)
    if len(names) == 1:
        return names[0]
    if not names:
        raise ValueError(f"no notifications configured for '{agent}'")
    raise ValueError("notification name is required because this agent has multiple notification paths")


def render_instruction_prompt(config: Config, agent: Agent | str, service: str, *, name: str | None = None) -> str:
    """Build a service-specific Hermes instruction prompt."""
    agent_name = _agent_name(agent)
    service = sanitize_agent_name(service)
    if service not in SERVICES:
        raise ValueError(f"unknown service '{service}'. Use one of: {', '.join(sorted(SERVICES))}")

    if service == "chat":
        resources = bootstrap.google_chat_resources(config, agent_name)
        values = [
            _metadata_line("GCP project ID", resources.chat_project),
            _metadata_line("Full Pub/Sub subscription", resources.full_subscription),
            _metadata_line("Service-account JSON key path", resources.vm_key_path),
            _metadata_line("Chat Console topic", resources.full_topic),
        ]
        goal = (
            "Configure your Google Chat gateway to use the project, pull subscription, "
            "and service-account key below. Verify you can pull an event and then tell me "
            "what still needs manual Google Chat Console work, if anything."
        )
        title = "Google Chat"
    elif service == "notifications":
        notification_name = _resolve_notification_name(config, agent_name, name)
        resources = bootstrap.webhook_resources(config, agent_name, notification_name)
        meta = _notification_meta(config, agent_name, notification_name)
        values = [
            _metadata_line("Notification name", resources.name_slug),
            _metadata_line("Provider", resources.provider),
            _metadata_line("Webhook URL", str(meta.get("url") or "not recorded")),
            _metadata_line("Env file path", resources.env_path),
            _metadata_line("Full Pub/Sub subscription", resources.full_subscription),
            _metadata_line("Event format", "ghosty.webhook.v1"),
        ]
        goal = (
            "Configure your notification consumer to read the env file below and pull "
            "events from the subscription. Do not expose the VM publicly; consume from "
            "Pub/Sub using the VM service account."
        )
        title = f"Notifications ({resources.name_slug})"
    elif service == "storage":
        if not config.storage_bucket:
            raise ValueError("no storage bucket configured")
        private_folder = bootstrap.storage_agent_folder_uri(config, agent_name, bootstrap.storage_bucket_name(config))
        public_folder = (
            bootstrap.storage_agent_public_folder_uri(config, agent_name, bootstrap.storage_public_bucket_name(config))
            if config.storage_public_enabled else ""
        )
        values = [
            _metadata_line("Storage env file path", config.storage_env_path),
            _metadata_line("Private bucket", f"gs://{bootstrap.storage_bucket_name(config)}"),
            _metadata_line("Private folder", private_folder),
            _metadata_line("Public bucket", f"gs://{bootstrap.storage_public_bucket_name(config)}" if config.storage_public_enabled else "off"),
            _metadata_line("Public folder", public_folder or "off"),
            _metadata_line("Signed URLs", "enabled" if config.storage_signed_urls_enabled else "off"),
        ]
        goal = (
            "Configure file handling to read the storage env file and keep this agent's "
            "private files under its own folder. Only publish files intentionally through "
            "the public folder or signed-link flow when available."
        )
        title = "Storage"
    else:
        values = [
            _metadata_line("GCP project ID", config.project_id),
            _metadata_line("Google AI API", config.google_ai_api),
            _metadata_line("Required role", config.google_ai_role),
            _metadata_line("Agent service account", config.sa_email(agent_name)),
        ]
        goal = (
            "Configure model access using Application Default Credentials from the VM "
            "service account. Do not use personal credentials. Verify a lightweight model "
            "client/auth check and report any missing IAM or API errors."
        )
        title = "Models"

    return "\n".join([
        f"# Configure {title} for {agent_name}",
        "",
        goal,
        "",
        "## Values",
        *values,
        "",
        "## Expected response",
        "- Confirm what you configured.",
        "- Mention any command, env file, or service you changed.",
        "- Report any missing manual step or error clearly.",
        "",
    ])


def write_local_prompt(config: Config, agent: Agent | str, service: str, *, name: str | None = None) -> Path:
    """Render and retain a local prompt copy before upload."""
    agent_name = _agent_name(agent)
    path = local_prompt_path(agent_name, service)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_instruction_prompt(config, agent, service, name=name), encoding="utf-8")
    return path


def _remote_assignment(var: str, value: str) -> str:
    if value.startswith("~/"):
        suffix = value[2:].replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
        return f'{var}="${{HOME}}/{suffix}"; '
    return f"{var}={shlex.quote(value)}; "


def _remote_command(config: Config, agent: str, service: str, remote_prompt_path: str) -> str:
    timeout_seconds = int(getattr(config, "agent_instruction_timeout_seconds", DEFAULT_INSTRUCTION_TIMEOUT_SECONDS) or DEFAULT_INSTRUCTION_TIMEOUT_SECONDS)
    return (
        _remote_assignment("GHOSTY_PROMPT_FILE", remote_prompt_path)
        + f"export GHOSTY_PROMPT_FILE GHOSTY_AGENT_NAME={shlex.quote(agent)} "
        + f"GHOSTY_SERVICE_NAME={shlex.quote(service)}; "
        + f"timeout {timeout_seconds}s bash -lc {shlex.quote(config.agent_instruction_command)}"
    )


def _remote_mkdir_command(path: str) -> str:
    return (
        _remote_assignment("instruction_dir", path)
        + 'mkdir -p "$instruction_dir" && chmod 700 "$instruction_dir"'
    )


def _delivery_failure_message(proc, timeout_seconds: int) -> str:
    stdout = (getattr(proc, "stdout", "") or "").strip()
    stderr = (getattr(proc, "stderr", "") or "").strip()
    parts: list[str] = []
    if getattr(proc, "returncode", None) == 124:
        parts.append(f"Hermes command timed out after {timeout_seconds} seconds.")
    else:
        parts.append(f"SSH exited with return code {getattr(proc, 'returncode', 'unknown')}.")
    if stdout:
        parts.append(f"stdout:\n{stdout}")
    if stderr:
        parts.append(f"stderr:\n{stderr}")
    return "\n".join(parts)


def deliver_instruction(config: Config, agent: Agent | str, service: str, *, name: str | None = None) -> InstructionDeliveryResult:
    """Upload a rendered prompt and run the configured Hermes command over IAP."""
    agent_name = _agent_name(agent)
    service = sanitize_agent_name(service)
    remote_prompt_path = instruction_prompt_path(config, service)
    local_path = write_local_prompt(config, agent, service, name=name)

    if _agent_status(agent) and _agent_status(agent) != "RUNNING":
        message = f"agent is {_agent_status(agent)}; start it and rerun `ghosty-agents instruct {agent_name} --service {service}`"
        ui.warn(f"Skipped briefing for '{agent_name}': {message}")
        return InstructionDeliveryResult(
            agent=agent_name,
            service=service,
            remote_prompt_path=remote_prompt_path,
            local_prompt_path=local_path,
            uploaded=False,
            delivered=False,
            message=message,
        )

    vm = instance_name(agent_name)
    try:
        ui.step(f"Uploading agent briefing to {vm}:{remote_prompt_path}")
        gcloud.run(config, [
            "compute", "ssh", vm,
            f"--zone={config.zone}",
            "--tunnel-through-iap",
            f"--command={_remote_mkdir_command(config.agent_instruction_dir)}",
            "--quiet",
        ])
        gcloud.run(config, [
            "compute", "scp", str(local_path),
            f"{vm}:{remote_prompt_path}",
            f"--zone={config.zone}",
            "--tunnel-through-iap",
            "--quiet",
        ])
        ui.step(f"Sending briefing to Hermes on '{agent_name}'")
        proc = gcloud.run(config, [
            "compute", "ssh", vm,
            f"--zone={config.zone}",
            "--tunnel-through-iap",
            f"--command={_remote_command(config, agent_name, service, remote_prompt_path)}",
            "--quiet",
        ], check=False)
    except gcloud.GcloudError as exc:
        message = exc.args[0]
        ui.warn(f"Could not brief '{agent_name}'. Prompt kept at {local_path}.")
        return InstructionDeliveryResult(
            agent=agent_name,
            service=service,
            remote_prompt_path=remote_prompt_path,
            local_prompt_path=local_path,
            uploaded=False,
            delivered=False,
            message=message,
        )

    if proc.returncode == 0:
        ui.success(f"agent '{agent_name}' was briefed about {service}")
        return InstructionDeliveryResult(
            agent=agent_name,
            service=service,
            remote_prompt_path=remote_prompt_path,
            local_prompt_path=local_path,
            uploaded=True,
            delivered=True,
        )

    timeout_seconds = int(getattr(config, "agent_instruction_timeout_seconds", DEFAULT_INSTRUCTION_TIMEOUT_SECONDS) or DEFAULT_INSTRUCTION_TIMEOUT_SECONDS)
    message = _delivery_failure_message(proc, timeout_seconds)
    ui.warn(f"Hermes briefing failed on '{agent_name}'. Prompt is on the VM at {remote_prompt_path}.")
    return InstructionDeliveryResult(
        agent=agent_name,
        service=service,
        remote_prompt_path=remote_prompt_path,
        local_prompt_path=local_path,
        uploaded=True,
        delivered=False,
        message=message,
    )
