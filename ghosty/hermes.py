"""Hermes installation and configuration helpers for Ghosty VMs."""

from __future__ import annotations

import json
import shlex
import time
from dataclasses import dataclass

from ghosty import gcloud, ui
from ghosty.models import Agent, Config, instance_name


INSTALLER_URL = "https://hermes-agent.nousresearch.com/install.sh"
DEFAULT_BRANCH = "main"
DEFAULT_PROVIDER = "vertex"
DEFAULT_MODEL = "google/gemini-3.1-pro-preview"
DEFAULT_VERTEX_REGION = "global"
HERMES_BIN = '"$HOME/.local/bin/hermes"'
_TRANSIENT_SSH_HINTS = (
    "Connection refused",
    "Permission denied (publickey)",
    "exited with return code [255]",
    "ssh: connect",
    "Could not SSH",
)


@dataclass
class HermesInstallResult:
    """Result of installing Hermes on one agent VM."""

    agent: str
    installed: bool
    gateway_started: bool
    message: str = ""


@dataclass
class HermesConfigureResult:
    """Result of applying Hermes model configuration."""

    agent: str
    provider: str
    model: str
    vertex_project: str
    vertex_region: str
    configured: bool
    message: str = ""


@dataclass
class HermesStatus:
    """Read-only Hermes state on one agent VM."""

    agent: str
    installed: bool
    command_exists: bool
    env_exists: bool
    config_exists: bool
    gateway_active: bool
    version: str = ""
    provider: str = ""
    model: str = ""
    vertex_project: str = ""
    vertex_region: str = ""
    message: str = ""


def _agent_name(agent: Agent | str) -> str:
    return str(getattr(agent, "name", agent))


def _agent_status(agent: Agent | str) -> str:
    return str(getattr(agent, "status", ""))


def _ssh_command(config: Config, agent: Agent | str, command: str, *, check: bool = False):
    agent_name = _agent_name(agent)
    return gcloud.run(config, [
        "compute", "ssh", instance_name(agent_name),
        f"--zone={config.zone}",
        "--tunnel-through-iap",
        f"--command={command}",
        "--quiet",
    ], check=check)


def _proc_message(proc) -> str:
    return ((getattr(proc, "stderr", "") or getattr(proc, "stdout", "")) or "").strip()


def _transient_ssh_failure(proc) -> bool:
    message = _proc_message(proc)
    return getattr(proc, "returncode", None) == 255 or any(hint in message for hint in _TRANSIENT_SSH_HINTS)


def install_command(
    *,
    branch: str = DEFAULT_BRANCH,
    commit: str | None = None,
    skip_browser: bool = False,
) -> str:
    """Return the remote shell command that installs Hermes."""
    args = ["--skip-setup", "--non-interactive", "--branch", branch]
    if commit:
        args += ["--commit", commit]
    if skip_browser:
        args.append("--skip-browser")
    quoted_args = " ".join(shlex.quote(arg) for arg in args)
    return "\n".join([
        "set -eu",
        f"curl -fsSL {shlex.quote(INSTALLER_URL)} -o /tmp/ghosty-hermes-install.sh",
        "chmod +x /tmp/ghosty-hermes-install.sh",
        f"bash /tmp/ghosty-hermes-install.sh {quoted_args}",
        f"{HERMES_BIN} gateway --accept-hooks install",
    ])


def install_hermes(
    config: Config,
    agent: Agent | str,
    *,
    branch: str = DEFAULT_BRANCH,
    commit: str | None = None,
    skip_browser: bool = False,
    attempts: int = 3,
    delay: float = 20.0,
) -> HermesInstallResult:
    """Install Hermes on a running agent VM with the official vendor installer."""
    agent_name = _agent_name(agent)
    status = _agent_status(agent)
    if status and status != "RUNNING":
        message = f"agent is {status}; start it before installing Hermes"
        ui.warn(message)
        return HermesInstallResult(agent=agent_name, installed=False, gateway_started=False, message=message)

    ui.step(f"Installing Hermes on '{agent_name}'")
    command = install_command(branch=branch, commit=commit, skip_browser=skip_browser)
    proc = None
    for attempt in range(max(1, attempts)):
        proc = _ssh_command(config, agent, command, check=False)
        if proc.returncode == 0:
            ui.success(f"Hermes installed on '{agent_name}'")
            return HermesInstallResult(agent=agent_name, installed=True, gateway_started=True)
        if _transient_ssh_failure(proc) and attempt < max(1, attempts) - 1:
            ui.skip(f"agent '{agent_name}' is not ready for Hermes install yet; retrying...")
            time.sleep(delay)
            continue
        break

    assert proc is not None
    if proc.returncode == 0:
        ui.success(f"Hermes installed on '{agent_name}'")
        return HermesInstallResult(agent=agent_name, installed=True, gateway_started=True)
    message = _proc_message(proc) or f"SSH exited with return code {proc.returncode}"
    ui.warn(f"Hermes install failed on '{agent_name}'")
    return HermesInstallResult(agent=agent_name, installed=False, gateway_started=False, message=message)


def configure_command(
    config: Config,
    *,
    provider: str = DEFAULT_PROVIDER,
    model: str = DEFAULT_MODEL,
    vertex_region: str = DEFAULT_VERTEX_REGION,
) -> str:
    """Return the remote shell command that writes Hermes model config."""
    project = config.project_id
    return "\n".join([
        "set -eu",
        f"{HERMES_BIN} config set model.default {shlex.quote(model)}",
        f"{HERMES_BIN} config set model.provider {shlex.quote(provider)}",
        f"{HERMES_BIN} config set vertex.project_id {shlex.quote(project)}",
        f"{HERMES_BIN} config set vertex.region {shlex.quote(vertex_region)}",
    ])


def configure_hermes(
    config: Config,
    agent: Agent | str,
    *,
    provider: str = DEFAULT_PROVIDER,
    model: str = DEFAULT_MODEL,
    vertex_region: str = DEFAULT_VERTEX_REGION,
) -> HermesConfigureResult:
    """Configure Hermes to use the Ghosty Google AI service-account path."""
    agent_name = _agent_name(agent)
    ui.step(f"Configuring Hermes on '{agent_name}'")
    proc = _ssh_command(
        config,
        agent,
        configure_command(config, provider=provider, model=model, vertex_region=vertex_region),
        check=False,
    )
    if proc.returncode == 0:
        ui.success(f"Hermes model configured on '{agent_name}'")
        return HermesConfigureResult(
            agent=agent_name,
            provider=provider,
            model=model,
            vertex_project=config.project_id,
            vertex_region=vertex_region,
            configured=True,
        )
    message = ((proc.stderr or proc.stdout) or f"SSH exited with return code {proc.returncode}").strip()
    ui.warn(f"Hermes configure failed on '{agent_name}'")
    return HermesConfigureResult(
        agent=agent_name,
        provider=provider,
        model=model,
        vertex_project=config.project_id,
        vertex_region=vertex_region,
        configured=False,
        message=message,
    )


def _status_command() -> str:
    return r"""python3 - <<'PY'
import json
import subprocess
from pathlib import Path

home = Path.home()
bin_path = home / ".local/bin/hermes"
env_path = home / ".hermes/.env"
config_path = home / ".hermes/config.yaml"
payload = {
    "command_exists": bin_path.exists(),
    "env_exists": env_path.exists(),
    "config_exists": config_path.exists(),
    "gateway_active": False,
    "version": "",
    "provider": "",
    "model": "",
    "vertex_project": "",
    "vertex_region": "",
}

if bin_path.exists():
    try:
        payload["version"] = subprocess.run(
            [str(bin_path), "--version"],
            text=True,
            capture_output=True,
            timeout=20,
        ).stdout.strip()
    except Exception as exc:
        payload["version"] = f"version check failed: {exc}"

if config_path.exists():
    section = ""
    for raw in config_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw.startswith((" ", "\t")) and raw.rstrip().endswith(":"):
            section = raw.strip().rstrip(":")
            continue
        if ":" not in raw:
            continue
        key, value = raw.strip().split(":", 1)
        value = value.strip().strip("\"'")
        if section == "model" and key == "default":
            payload["model"] = value
        elif section == "model" and key == "provider":
            payload["provider"] = value
        elif section == "vertex" and key == "project_id":
            payload["vertex_project"] = value
        elif section == "vertex" and key == "region":
            payload["vertex_region"] = value

try:
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", "hermes-gateway.service"],
        timeout=10,
    )
    payload["gateway_active"] = result.returncode == 0
except Exception:
    payload["gateway_active"] = False

payload["installed"] = payload["command_exists"]
print(json.dumps(payload, sort_keys=True))
PY"""


def hermes_status(config: Config, agent: Agent | str) -> HermesStatus:
    """Return read-only Hermes state from an agent VM."""
    agent_name = _agent_name(agent)
    status = _agent_status(agent)
    if status and status != "RUNNING":
        return HermesStatus(
            agent=agent_name,
            installed=False,
            command_exists=False,
            env_exists=False,
            config_exists=False,
            gateway_active=False,
            message=f"agent is {status}",
        )

    proc = _ssh_command(config, agent, _status_command(), check=False)
    if proc.returncode != 0:
        message = ((proc.stderr or proc.stdout) or f"SSH exited with return code {proc.returncode}").strip()
        return HermesStatus(
            agent=agent_name,
            installed=False,
            command_exists=False,
            env_exists=False,
            config_exists=False,
            gateway_active=False,
            message=message,
        )
    try:
        data = json.loads((proc.stdout or "{}").strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        return HermesStatus(
            agent=agent_name,
            installed=False,
            command_exists=False,
            env_exists=False,
            config_exists=False,
            gateway_active=False,
            message=f"could not parse Hermes status: {exc}",
        )
    return HermesStatus(
        agent=agent_name,
        installed=bool(data.get("installed")),
        command_exists=bool(data.get("command_exists")),
        env_exists=bool(data.get("env_exists")),
        config_exists=bool(data.get("config_exists")),
        gateway_active=bool(data.get("gateway_active")),
        version=str(data.get("version") or ""),
        provider=str(data.get("provider") or ""),
        model=str(data.get("model") or ""),
        vertex_project=str(data.get("vertex_project") or ""),
        vertex_region=str(data.get("vertex_region") or ""),
    )


def sync_hermes(
    config: Config,
    agent: Agent | str,
    *,
    branch: str = DEFAULT_BRANCH,
    commit: str | None = None,
    skip_browser: bool = False,
    provider: str = DEFAULT_PROVIDER,
    model: str = DEFAULT_MODEL,
    vertex_region: str = DEFAULT_VERTEX_REGION,
) -> HermesStatus:
    """Install, configure, and return Hermes status for one agent VM."""
    install_hermes(config, agent, branch=branch, commit=commit, skip_browser=skip_browser)
    configure_hermes(config, agent, provider=provider, model=model, vertex_region=vertex_region)
    return hermes_status(config, agent)
