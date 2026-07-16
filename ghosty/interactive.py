"""Friendly interactive control room for ghosty-agents."""

from __future__ import annotations

from typing import Callable, Sequence

import typer
from rich.table import Table

from ghosty import agents, bootstrap, config as config_mod, gcloud, guided, harness_browser, hermes as hermes_mod, instructions, ui
from ghosty.doctor import run_checks
from ghosty.models import Agent, Config


Choice = tuple[str, str]


def _load() -> Config:
    return config_mod.load_config()


def _ready(cfg: Config) -> bool:
    return not cfg.missing_required()


def _guard(fn: Callable):
    try:
        return fn()
    except gcloud.GcloudNotFound as exc:
        ui.error(str(exc))
    except gcloud.GcloudError as exc:
        ui.error(exc.args[0])
    except ValueError as exc:
        ui.error(str(exc))
    return None


def _choose(title: str, choices: Sequence[Choice]) -> str:
    return ui.choose(title, choices, allow_custom=False)


def _confirm(prompt: str, *, default: bool = False) -> bool:
    return typer.confirm(prompt, default=default)


def _maybe_brief_agent(cfg: Config, agent: Agent, service: str, *, name: str | None = None) -> None:
    delivery = (cfg.agent_instruction_delivery or "ask").strip().lower()
    if delivery in {"off", "never", "no", "false", "disabled"}:
        return
    should_brief = delivery == "always" or _confirm("Brief the agent now?", default=True)
    if not should_brief:
        return
    try:
        result = instructions.deliver_instruction(cfg, agent, service, name=name)
    except (gcloud.GcloudError, gcloud.GcloudNotFound, ValueError) as exc:
        ui.warn(f"Could not brief the agent: {exc}")
        return
    if not result.delivered:
        retry = f"ghosty-agents instruct {agent.name} --service {service}"
        if name:
            retry += f" --name {name}"
        ui.warn(f"Briefing can be retried with: {retry}")


def _print_config_summary(cfg: Config) -> None:
    table = Table(title="Ghosty control room", show_header=False)
    table.add_column("field", style="bold cyan")
    table.add_column("value")
    table.add_row("project", cfg.project_id or "not configured")
    table.add_row("account", cfg.account or "not configured")
    table.add_row("region", cfg.region)
    table.add_row("zone", cfg.zone)
    table.add_row("models", "enabled" if cfg.google_ai_enabled else "off")
    table.add_row("storage", cfg.storage_bucket or "off")
    table.add_row("internet", f"{cfg.nat_name} (check status)" if cfg.nat_name else "off")
    ui.console.print(table)


def _print_agents(found: Sequence[Agent]) -> None:
    table = Table(title=f"Agents ({len(found)})")
    table.add_column("NAME", style="bold")
    table.add_column("STATE")
    table.add_column("MACHINE")
    table.add_column("ZONE")
    table.add_column("PRIVATE IP")
    if found:
        for agent in found:
            style = "green" if agent.status == "RUNNING" else "yellow"
            table.add_row(
                agent.name,
                f"[{style}]{agent.status or '-'}[/]",
                agent.machine_type or "-",
                agent.zone or "-",
                agent.internal_ip or "-",
            )
    else:
        table.add_row("-", "No agents yet", "-", "-", "-")
    ui.console.print(table)


def _print_agent_details(agent: Agent) -> None:
    table = Table(title=f"Agent: {agent.name}", show_header=False)
    table.add_column("field", style="bold cyan")
    table.add_column("value")
    table.add_row("name", agent.name)
    table.add_row("state", agent.status or "-")
    table.add_row("machine", agent.machine_type or "-")
    table.add_row("zone", agent.zone or "-")
    table.add_row("private IP", agent.internal_ip or "-")
    table.add_row("created", agent.created or "-")
    table.add_row("technical instance", agent.instance)
    ui.console.print(table)


def _print_checks(checks) -> bool:
    ok = True
    for check in checks:
        if check.ok:
            ui.success(f"{check.name}" + (f" - {check.detail}" if check.detail else ""))
        else:
            ok = False
            ui.error(f"{check.name} - {check.detail}")
            if check.fix:
                ui.warn(f"fix: {check.fix}")
    return ok


def _create_agent(cfg: Config) -> None:
    _guard(lambda: guided.run_guided_setup(cfg, open_harness=True))


def _prepare_project(cfg: Config) -> None:
    ui.warn("This prepares the project, network, access rules, and budget.")
    with_internet = _confirm("Also enable shared internet for private agents?", default=False)
    if not _confirm("Prepare project now?", default=True):
        ui.skip("cancelled")
        return
    _guard(lambda: bootstrap.ensure_isolated_config(cfg))
    _guard(lambda: bootstrap.bootstrap_all(cfg, with_nat=with_internet))


def _check_setup(cfg: Config) -> None:
    checks = _guard(lambda: run_checks(cfg, deep=True))
    if checks is not None and _print_checks(checks):
        ui.celebrate("Everything looks ready.")


def _settings(cfg: Config) -> None:
    _print_config_summary(cfg)
    ui.console.print(f"Config file: {config_mod.config_path()}")


def _internet_menu(cfg: Config) -> None:
    action = _choose("Internet", [
        ("status", "Check internet status"),
        ("enable", "Enable internet"),
        ("disable", "Disable internet"),
        ("back", "Back"),
    ])
    if action == "status":
        status = _guard(lambda: bootstrap.nat_status(cfg))
        if status:
            state = "enabled" if status.enabled else "off"
            ui.panel(
                f"Status: {state}\nRegion: {status.region}\nAdvanced: router={status.router}, nat={status.nat}",
                title="Internet",
            )
    elif action == "enable":
        ui.warn("This creates a billable shared internet gateway for private agents.")
        if _confirm("Enable internet?", default=False):
            _guard(lambda: bootstrap.ensure_network(cfg))
            _guard(lambda: bootstrap.ensure_nat(cfg))
    elif action == "disable":
        ui.warn("This removes general outbound internet for private agents.")
        if _confirm("Disable internet?", default=False):
            _guard(lambda: bootstrap.disable_nat(cfg))


def _models_menu(cfg: Config) -> None:
    action = _choose("Models", [
        ("status", "Check model access"),
        ("enable", "Enable models"),
        ("disable", "Turn off future model access"),
        ("back", "Back"),
    ])
    if action == "status":
        status = _guard(lambda: bootstrap.google_ai_status(cfg))
        if status:
            ui.panel(
                f"API: {'enabled' if status.api_enabled else 'off'}\n"
                f"Future agents: {'enabled' if status.auto_grant_enabled else 'off'}\n"
                f"Advanced: {status.api}, {status.role}",
            title="Models",
        )
    elif action == "enable":
        ui.warn("This enables model access and may create usage charges.")
        if _confirm("Enable models?", default=False):
            _guard(lambda: bootstrap.enable_google_ai(cfg))
            config_mod.save_config(cfg)
    elif action == "disable":
        ui.warn("This removes model access from current agents and future agents.")
        if _confirm("Turn off model access?", default=False):
            _guard(lambda: bootstrap.disable_google_ai_iam(cfg))
            config_mod.save_config(cfg)


def _storage_menu(cfg: Config, agent: Agent | None = None) -> None:
    choices = [
        ("status", "Check storage"),
        ("add", "Add storage"),
        ("sync", "Sync storage"),
        ("disable", "Remove storage access"),
        ("back", "Back"),
    ]
    action = _choose("Storage", choices)
    if action == "status":
        status = _guard(lambda: bootstrap.storage_status(cfg))
        if status:
            ui.panel(
                f"Storage: {'enabled' if status.auto_grant_enabled else 'off'}\n"
                f"Private files: {status.bucket_uri}\n"
                f"Public files: {status.public_bucket_uri if status.public_enabled else 'off'}",
                title="Storage",
            )
    elif action == "add":
        ui.warn("This creates shared storage with private folders for each agent.")
        with_public = _confirm("Also add public publishing folders?", default=False)
        with_signed_urls = _confirm("Allow temporary private file links?", default=False)
        if _confirm("Add storage?", default=True):
            result = _guard(lambda: bootstrap.setup_storage(
                cfg,
                with_public=with_public,
                with_signed_urls=with_signed_urls,
            ))
            if result:
                config_mod.save_config(cfg)
                ui.success(f"storage ready: {result.bucket_uri}")
                if agent:
                    _maybe_brief_agent(cfg, agent, "storage")
    elif action == "sync":
        if agent:
            result = _guard(lambda: bootstrap.sync_storage(cfg, [agent]))
        else:
            found = _guard(lambda: agents.list_agents(cfg)) or []
            result = _guard(lambda: bootstrap.sync_storage(cfg, found))
        if result:
            ui.success(f"storage synced: {result.bucket_uri}")
            if agent:
                _maybe_brief_agent(cfg, agent, "storage")
    elif action == "disable":
        ui.warn("This removes agent access but keeps files.")
        if _confirm("Remove storage access?", default=False):
            result = _guard(lambda: bootstrap.disable_storage(cfg))
            if result:
                config_mod.save_config(cfg)
                ui.warn(f"files kept: {result.bucket_uri}")


def _features_menu(cfg: Config) -> None:
    action = _choose("Shared features", [
        ("internet", "Manage internet"),
        ("models", "Manage models"),
        ("storage", "Manage storage"),
        ("back", "Back"),
    ])
    if action == "internet":
        _internet_menu(cfg)
    elif action == "models":
        _models_menu(cfg)
    elif action == "storage":
        _storage_menu(cfg)


def _add_chat(cfg: Config, agent: Agent) -> None:
    chat_project = typer.prompt("Chat project ID (blank for automatic)", default="")
    kwargs = {"chat_project": chat_project or None}
    resources = _guard(lambda: bootstrap.google_chat_resources(cfg, agent.name, kwargs["chat_project"]))
    if resources is None:
        return
    ui.warn(f"This prepares chat for '{agent.name}' in project '{resources.chat_project}'.")
    if not _confirm("Add chat?", default=True):
        ui.skip("cancelled")
        return
    resources = _guard(lambda: bootstrap.ensure_google_chat_gateway(cfg, agent.name, **kwargs))
    if resources:
        config_mod.save_config(cfg)
        ui.panel(
            f"Chat console topic: {resources.full_topic}\n"
            f"Hermes project: {resources.chat_project}\n"
            f"Hermes subscription: {resources.full_subscription}\n"
            f"Key path on agent: {resources.vm_key_path}",
            title="Advanced values",
        )
        _maybe_brief_agent(cfg, agent, "chat")


def _add_notifications(cfg: Config, agent: Agent) -> None:
    name = typer.prompt("Notification name", default="webhook")
    generate_secret = _confirm("Generate a secure secret?", default=True)
    secret = None if generate_secret else typer.prompt("Shared secret", hide_input=True, confirmation_prompt=True)
    resources = _guard(lambda: bootstrap.webhook_resources(cfg, agent.name, name))
    if resources is None:
        return
    ui.warn(f"This creates a public notification address for '{agent.name}'.")
    if not _confirm("Add notifications?", default=True):
        ui.skip("cancelled")
        return
    result = _guard(lambda: bootstrap.ensure_webhook_gateway(
        cfg,
        agent,
        name=name,
        secret=secret,
        generate_secret=generate_secret,
    ))
    if result:
        config_mod.save_config(cfg)
        ui.panel(
            f"Notification URL: {result.service_url}\n"
            f"Secret header: {resources.secret_header}\n"
            f"Secret: {result.secret}\n"
            f"Agent subscription: {resources.full_subscription}",
            title="Advanced values",
        )
        if _confirm("Install the Hermes event consumer on this agent?", default=True):
            consumer = _guard(lambda: bootstrap.install_webhook_consumer(cfg, agent, result.resources))
            if consumer and consumer.installed:
                ui.success(f"consumer running: {consumer.service_name}")
        _maybe_brief_agent(cfg, agent, "notifications", name=resources.name_slug)


def _notifications_menu(cfg: Config, agent: Agent) -> None:
    action = _choose("Notifications", [
        ("add", "Add notifications"),
        ("status", "Check notifications"),
        ("sync", "Sync notifications"),
        ("remove", "Remove notifications"),
        ("back", "Back"),
    ])
    if action == "add":
        _add_notifications(cfg, agent)
    elif action == "status":
        names = bootstrap.webhook_names(cfg, agent.name)
        if not names:
            ui.skip("no notifications configured")
            return
        for name in names:
            status = _guard(lambda name=name: bootstrap.webhook_status(cfg, agent, name=name))
            if status:
                ui.panel(
                    f"Name: {status.resources.name_slug}\nURL: {status.service_url or 'missing'}\n"
                    f"Agent config: {status.resources.env_path}",
                    title="Notifications",
                )
    elif action == "sync":
        names = bootstrap.webhook_names(cfg, agent.name)
        if not names:
            ui.skip("no notifications configured")
            return
        with_consumer = _confirm("Also refresh the Hermes event consumer?", default=True)
        for name in names:
            result = _guard(lambda name=name: bootstrap.sync_webhook_gateway(cfg, agent, name=name))
            if with_consumer and result:
                _guard(lambda result=result: bootstrap.install_webhook_consumer(cfg, agent, result.resources))
        config_mod.save_config(cfg)
    elif action == "remove":
        names = bootstrap.webhook_names(cfg, agent.name)
        if not names:
            ui.skip("no notifications configured")
            return
        name = _choose("Which notification?", [(item, item) for item in names])
        if _confirm(f"Remove notifications '{name}'?", default=False):
            _guard(lambda: bootstrap.destroy_webhook_gateway(cfg, agent.name, name=name))
            config_mod.save_config(cfg)


def _hermes_menu(cfg: Config, agent: Agent) -> None:
    action = _choose("Hermes", [
        ("status", "Check Hermes"),
        ("install", "Install Hermes"),
        ("configure", "Configure Google models"),
        ("sync", "Install and configure"),
        ("back", "Back"),
    ])
    if action == "status":
        status = _guard(lambda: hermes_mod.hermes_status(cfg, agent))
        if status:
            ui.panel(
                f"Installed: {'yes' if status.installed else 'no'}\n"
                f"Gateway: {'running' if status.gateway_active else 'off'}\n"
                f"Model: {status.model or '-'}\n"
                f"Provider: {status.provider or '-'}",
                title="Hermes",
            )
    elif action == "install":
        if _confirm("Install Hermes with the official installer?", default=True):
            _guard(lambda: hermes_mod.install_hermes(cfg, agent))
    elif action == "configure":
        model = typer.prompt("Model", default=hermes_mod.DEFAULT_MODEL)
        _guard(lambda: hermes_mod.configure_hermes(cfg, agent, model=model))
    elif action == "sync":
        if _confirm("Install/update Hermes and configure Google models?", default=True):
            _guard(lambda: hermes_mod.sync_hermes(cfg, agent))


def _cleanup_agent_attached_resources(cfg: Config, agent: Agent) -> None:
    for webhook_name in bootstrap.webhook_names(cfg, agent.name):
        ui.step(f"Removing notifications '{webhook_name}' for '{agent.name}'")
        _guard(lambda webhook_name=webhook_name: bootstrap.destroy_webhook_gateway(cfg, agent.name, name=webhook_name))
    if isinstance(cfg.google_chat_projects, dict) and agent.name in cfg.google_chat_projects:
        ui.step(f"Removing chat resources for '{agent.name}'")
        _guard(lambda: bootstrap.destroy_google_chat_gateway(cfg, agent.name))
    if cfg.storage_bucket or cfg.storage_public_bucket:
        ui.step(f"Removing storage access for '{agent.name}'")
        _guard(lambda: bootstrap.cleanup_storage_for_agent(cfg, agent))
    if cfg.google_ai_enabled:
        ui.step(f"Removing model access for '{agent.name}'")
        _guard(lambda: bootstrap.remove_google_ai_from_agent(cfg, agent.name))


def _agent_menu(cfg: Config, agent: Agent) -> None:
    while True:
        _print_agent_details(agent)
        action = _choose(f"What should '{agent.name}' do?", [
            ("harness", "View harness"),
            ("connect", "Connect"),
            ("start", "Start"),
            ("stop", "Stop"),
            ("chat", "Add chat"),
            ("notifications", "Add or manage notifications"),
            ("storage", "Sync storage"),
            ("hermes", "Install or check Hermes"),
            ("details", "View details"),
            ("remove", "Remove agent"),
            ("back", "Back"),
        ])
        if action == "harness":
            harness_browser.live_harness(cfg, agent)
        elif action == "connect":
            _guard(lambda: agents.ssh_agent(cfg, agent.name))
        elif action == "start":
            _guard(lambda: agents.start_agent(cfg, agent.name))
        elif action == "stop":
            _guard(lambda: agents.stop_agent(cfg, agent.name))
        elif action == "chat":
            _add_chat(cfg, agent)
        elif action == "notifications":
            _notifications_menu(cfg, agent)
        elif action == "storage":
            _storage_menu(cfg, agent)
        elif action == "hermes":
            _hermes_menu(cfg, agent)
        elif action == "details":
            _print_agent_details(agent)
        elif action == "remove":
            ui.warn("This removes the agent machine, cloud identity, and attached resources.")
            if _confirm(f"Remove agent '{agent.name}'?", default=False):
                _cleanup_agent_attached_resources(cfg, agent)
                _guard(lambda: agents.destroy_agent(cfg, agent.name))
                config_mod.save_config(cfg)
                return
        elif action == "back":
            return
        refreshed = _guard(lambda: agents.get_agent(cfg, agent.name))
        if refreshed:
            agent = refreshed


def _select_agent(cfg: Config, found: Sequence[Agent]) -> None:
    if not found:
        ui.skip("No agents yet.")
        return
    name = _choose("Select an agent", [(agent.name, agent.name) for agent in found] + [("back", "Back")])
    if name == "back":
        return
    agent = next((item for item in found if item.name == name), None)
    if agent:
        _agent_menu(cfg, agent)


def run() -> None:
    """Run the interactive Ghosty control room."""
    ui.banner("Ghosty control room")
    while True:
        cfg = _load()
        _print_config_summary(cfg)
        found: list[Agent] = []
        if _ready(cfg):
            found = _guard(lambda: agents.list_agents(cfg)) or []
            _print_agents(found)
        else:
            missing = ", ".join(cfg.missing_required())
            ui.warn(f"Setup is incomplete: {missing}. Use settings or `ghosty-agents init`.")

        action = _choose("What do you want to do?", [
            ("refresh", "Refresh"),
            ("create", "Create agent"),
            ("select", "Select agent"),
            ("prepare", "Prepare/check project"),
            ("features", "Manage internet, models, and storage"),
            ("settings", "Settings"),
            ("exit", "Exit"),
        ])
        if action == "refresh":
            continue
        if action == "exit":
            return
        if action == "settings":
            _settings(cfg)
            continue
        if not _ready(cfg):
            ui.warn("Run `ghosty-agents init` before managing agents.")
            continue
        if action == "create":
            _create_agent(cfg)
        elif action == "select":
            _select_agent(cfg, found)
        elif action == "prepare":
            nested = _choose("Prepare/check project", [
                ("prepare", "Prepare project"),
                ("check", "Check setup"),
                ("back", "Back"),
            ])
            if nested == "prepare":
                _prepare_project(cfg)
            elif nested == "check":
                _check_setup(cfg)
        elif action == "features":
            _features_menu(cfg)
