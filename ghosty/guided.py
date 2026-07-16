"""Guided non-technical setup interview for new Ghosty agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

import typer
from rich.table import Table

from ghosty import agents, bootstrap, config as config_mod, discover, gcloud, harness_browser, hermes as hermes_mod, instructions, ui
from ghosty.doctor import Check, preflight_create
from ghosty.models import Agent, Config


@dataclass
class AgentSetupIntent:
    """The harness the user wants Ghosty to create around a new agent."""

    name: str
    create_only: bool = False
    install_hermes: bool = True
    enable_models: bool = True
    private_storage: bool = True
    public_storage: bool = False
    signed_links: bool = False
    enable_internet: bool = False
    internet_already_enabled: bool = False
    internet_recommended_for_hermes: bool = False
    enable_chat: bool = False
    chat_project: str | None = None
    create_chat_project: bool = True
    enable_notifications: bool = False
    notification_name: str = "webhook"
    notification_secret: str | None = None
    generate_notification_secret: bool = True
    install_notification_consumer: bool = True
    brief_agent: bool = False
    startup_script: str | None = None


@dataclass
class SetupIssue:
    """A non-fatal setup issue with a retry hint."""

    step: str
    message: str
    retry_command: str = ""


@dataclass
class AgentSetupResult:
    """Summary of a guided setup run."""

    agent: Agent | None = None
    completed_steps: list[str] = field(default_factory=list)
    skipped_steps: list[str] = field(default_factory=list)
    failed_optional_steps: list[SetupIssue] = field(default_factory=list)
    manual_followups: list[str] = field(default_factory=list)
    harness_opened: bool = False


def _choice(title: str, options: Sequence[tuple[str, str]], *, default_index: int = 0) -> str:
    return ui.choose(title, options, default_index=default_index, allow_custom=False)


def _confirm(prompt: str, *, default: bool = False) -> bool:
    return typer.confirm(prompt, default=default)


def _nat_enabled(config: Config) -> bool:
    try:
        return bootstrap.nat_status(config).enabled
    except (gcloud.GcloudError, gcloud.GcloudNotFound, ValueError):
        return False


def recommended_intent(config: Config, name: str, *, nat_enabled: bool | None = None) -> AgentSetupIntent:
    """Return Ghosty's recommended default harness intent."""
    shared_internet = _nat_enabled(config) if nat_enabled is None else nat_enabled
    return AgentSetupIntent(
        name=name,
        install_hermes=True,
        enable_models=True,
        private_storage=True,
        enable_internet=not shared_internet,
        internet_already_enabled=shared_internet,
        internet_recommended_for_hermes=not shared_internet,
    )


def collect_intent(
    config: Config,
    *,
    name: str | None = None,
    startup_script: str | None = None,
) -> AgentSetupIntent | None:
    """Interview the user and collect the desired agent setup."""
    agent_name = name or typer.prompt("Agent name")
    internet_enabled = _nat_enabled(config)

    style = _choice(
        "How much should Ghosty set up?",
        [
            ("recommended", "Recommended setup"),
            ("create-only", "Create only"),
        ],
    )
    if style == "create-only":
        return AgentSetupIntent(
            name=agent_name,
            create_only=True,
            install_hermes=False,
            enable_models=False,
            private_storage=False,
            internet_already_enabled=internet_enabled,
            startup_script=startup_script,
        )

    intent = recommended_intent(config, agent_name, nat_enabled=internet_enabled)
    intent.startup_script = startup_script

    intent.install_hermes = _confirm("Install Hermes on the agent?", default=True)
    intent.enable_models = _confirm("Let the agent use Google AI models?", default=True)
    intent.private_storage = _confirm("Give the agent a private file space?", default=True)
    if intent.private_storage:
        intent.public_storage = _confirm("Allow the agent to publish approved public files?", default=False)
        intent.signed_links = _confirm("Allow temporary private file links?", default=False)

    intent.enable_chat = _confirm("Should people talk to this agent in Google Chat?", default=False)
    if intent.enable_chat:
        chat_mode = _choice(
            "How should Ghosty handle the Google Chat app project?",
            [
                ("auto", "Create or reuse one for me"),
                ("existing", "I already have a Chat project"),
            ],
        )
        if chat_mode == "existing":
            intent.chat_project = typer.prompt("Chat project ID")
            intent.create_chat_project = False

    intent.enable_notifications = _confirm("Should outside systems be able to notify it?", default=False)
    if intent.enable_notifications:
        intent.notification_name = typer.prompt("Notification name", default="webhook")
        intent.generate_notification_secret = _confirm("Generate a secure secret?", default=True)
        if not intent.generate_notification_secret:
            intent.notification_secret = typer.prompt(
                "Shared secret",
                hide_input=True,
                confirmation_prompt=True,
            )
        intent.install_notification_consumer = _confirm(
            "Install the Hermes event listener on the agent?",
            default=True,
        )

    intent.internet_already_enabled = internet_enabled
    if internet_enabled:
        intent.enable_internet = False
        intent.internet_recommended_for_hermes = False
    elif intent.install_hermes:
        ui.warn("Hermes is installed from the vendor, so the private agent needs shared internet during setup.")
        intent.enable_internet = _confirm("Enable shared internet for setup?", default=True)
        intent.internet_recommended_for_hermes = True
    else:
        intent.enable_internet = _confirm("Enable shared internet for this agent?", default=False)
        intent.internet_recommended_for_hermes = False

    services_to_brief = any([
        intent.enable_models,
        intent.private_storage,
        intent.enable_chat,
        intent.enable_notifications,
    ])
    delivery = (config.agent_instruction_delivery or "ask").strip().lower()
    if intent.install_hermes and services_to_brief and delivery not in {"off", "never", "no", "false", "disabled"}:
        intent.brief_agent = delivery == "always" or _confirm(
            "After setup, brief the agent about these new abilities?",
            default=True,
        )

    return intent


def render_review(config: Config, intent: AgentSetupIntent) -> str:
    """Return the plain-language review shown before execution."""
    lines = [
        f"[bold]Agent[/]              {intent.name}",
        f"[bold]Machine[/]            {config.machine_type} in {config.zone}",
    ]
    if intent.create_only:
        lines.append("[bold]Setup style[/]        Create only")
        lines.append("[bold]Harness[/]            No extra abilities will be added now")
    else:
        lines.append(f"[bold]Hermes[/]             {'install and configure' if intent.install_hermes else 'skip'}")
        lines.append(f"[bold]Models[/]             {'enable Google model access' if intent.enable_models else 'skip'}")
        storage = "private file space"
        if intent.public_storage:
            storage += " + public sharing"
        if intent.signed_links:
            storage += " + temporary links"
        lines.append(f"[bold]Files[/]              {storage if intent.private_storage else 'skip'}")
        chat = "add Google Chat" if intent.enable_chat else "skip"
        if intent.enable_chat and intent.chat_project:
            chat += f" ({intent.chat_project})"
        lines.append(f"[bold]Chat[/]               {chat}")
        notify = "skip"
        if intent.enable_notifications:
            notify = f"add notification address '{intent.notification_name}'"
            if intent.install_notification_consumer:
                notify += " + event listener"
        lines.append(f"[bold]Notifications[/]      {notify}")

    if intent.internet_already_enabled:
        internet = "already available"
    elif intent.enable_internet:
        internet = "enable shared internet (billable)"
    else:
        internet = "leave off"
    lines.append(f"[bold]Shared internet[/]    {internet}")

    costs = [
        "The agent machine is billable while it exists.",
    ]
    if intent.enable_internet:
        costs.append("Shared internet is a billable shared component.")
    if intent.enable_models:
        costs.append("Model usage may create Google AI usage charges.")
    if intent.private_storage:
        costs.append("File storage may create storage charges.")
    if intent.enable_notifications:
        costs.append("Notifications use a small public receiver and message queue.")
    if costs:
        lines.append("")
        lines.append("[bold]Cost notes[/]")
        lines.extend(f"- {item}" for item in costs)

    if intent.enable_chat:
        lines.append("")
        lines.append("[bold]Manual follow-up[/]")
        lines.append("- Google Chat still needs one Console configuration step; Ghosty will print exact values.")
    return "\n".join(lines)


def _print_checks(checks: Sequence[Check]) -> bool:
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


def _prepare_project(config: Config, intent: AgentSetupIntent) -> None:
    ui.warn("Ghosty needs to prepare the shared project pieces before it can create this agent.")
    if not _confirm("Prepare the project now?", default=True):
        raise ValueError("project is not ready; run `ghosty-agents prepare` and try again")
    bootstrap.ensure_isolated_config(config)
    bootstrap.bootstrap_all(config, with_nat=intent.enable_internet)


def _ensure_required_readiness(config: Config, intent: AgentSetupIntent) -> None:
    checks = preflight_create(config)
    if not _print_checks(checks):
        _prepare_project(config, intent)

    if not gcloud.exists(config, ["compute", "zones", "describe", config.zone]):
        valid = discover.zones_for_region(config, config.region)
        hint = f" Try: ghosty-agents settings set zone {valid[0]}" if valid else ""
        raise ValueError(f"zone '{config.zone}' is not available.{hint}")

    if not gcloud.exists(config, ["compute", "networks", "describe", config.network]):
        _prepare_project(config, intent)


def _optional(
    result: AgentSetupResult,
    step: str,
    retry_command: str,
    fn: Callable[[], object],
):
    try:
        value = fn()
    except (gcloud.GcloudError, gcloud.GcloudNotFound, ValueError, RuntimeError) as exc:
        result.failed_optional_steps.append(SetupIssue(step=step, message=str(exc), retry_command=retry_command))
        ui.warn(f"{step} did not finish. You can retry later.")
        return None
    result.completed_steps.append(step)
    return value


def _maybe_enable_internet(config: Config, intent: AgentSetupIntent, result: AgentSetupResult) -> None:
    if intent.internet_already_enabled:
        result.skipped_steps.append("Shared internet already enabled")
        return
    if not intent.enable_internet:
        result.skipped_steps.append("Shared internet left off")
        return

    def work() -> None:
        bootstrap.ensure_network(config)
        bootstrap.ensure_nat(config)

    _optional(
        result,
        "Shared internet",
        "ghosty-agents internet enable --yes",
        work,
    )


def _maybe_enable_models(config: Config, intent: AgentSetupIntent, result: AgentSetupResult) -> None:
    if not intent.enable_models:
        result.skipped_steps.append("Models skipped")
        return
    if config.google_ai_enabled:
        result.skipped_steps.append("Models already enabled")
        return

    def work() -> None:
        bootstrap.enable_google_ai(config, agent_names=[])
        config_mod.save_config(config)

    _optional(result, "Models", "ghosty-agents models enable --yes", work)


def _storage_needs_setup(config: Config, intent: AgentSetupIntent) -> bool:
    return bool(
        intent.private_storage
        and (
            not config.storage_enabled
            or not config.storage_bucket
            or (intent.public_storage and not config.storage_public_enabled)
            or (intent.signed_links and not config.storage_signed_urls_enabled)
        )
    )


def _maybe_setup_storage(config: Config, intent: AgentSetupIntent, result: AgentSetupResult, agent: Agent) -> None:
    if not intent.private_storage:
        result.skipped_steps.append("Private file space skipped")
        return

    if _storage_needs_setup(config, intent):
        storage_result = _optional(
            result,
            "Private file space",
            f"ghosty-agents storage add --yes && ghosty-agents storage sync {agent.name}",
            lambda: bootstrap.setup_storage(
                config,
                with_public=intent.public_storage,
                with_signed_urls=intent.signed_links,
                agent_items=[agent],
            ),
        )
        if storage_result:
            config_mod.save_config(config)
            _record_storage_partial_failures(result, agent, storage_result)
        return

    storage_result = _optional(
        result,
        "Private file space",
        f"ghosty-agents storage sync {agent.name}",
        lambda: bootstrap.sync_storage(config, [agent]),
    )
    if storage_result:
        _record_storage_partial_failures(result, agent, storage_result)


def _record_storage_partial_failures(result: AgentSetupResult, agent: Agent, storage_result: object) -> None:
    for row in getattr(storage_result, "agents", []) or []:
        if getattr(row, "agent", agent.name) == agent.name and not getattr(row, "vm_env_updated", True):
            result.failed_optional_steps.append(SetupIssue(
                step="Private file space on agent",
                message=getattr(row, "message", "") or "The file-space settings were not written to the VM.",
                retry_command=f"ghosty-agents storage sync {agent.name}",
            ))


def _maybe_install_hermes(config: Config, intent: AgentSetupIntent, result: AgentSetupResult, agent: Agent) -> None:
    if not intent.install_hermes:
        result.skipped_steps.append("Hermes skipped")
        return

    def work() -> None:
        installed = hermes_mod.install_hermes(config, agent)
        if not installed.installed:
            raise RuntimeError(installed.message or "Hermes installer did not complete")
        configured = hermes_mod.configure_hermes(config, agent)
        if not configured.configured:
            raise RuntimeError(configured.message or "Hermes model configuration did not complete")

    _optional(result, "Hermes", f"ghosty-agents hermes sync {agent.name} --yes", work)


def _maybe_setup_notifications(config: Config, intent: AgentSetupIntent, result: AgentSetupResult, agent: Agent) -> object | None:
    if not intent.enable_notifications:
        result.skipped_steps.append("Notifications skipped")
        return None

    setup = _optional(
        result,
        "Notifications",
        f"ghosty-agents notifications add {agent.name} --name {intent.notification_name} --generate-secret --yes",
        lambda: bootstrap.ensure_webhook_gateway(
            config,
            agent,
            name=intent.notification_name,
            secret=intent.notification_secret,
            generate_secret=intent.generate_notification_secret,
        ),
    )
    if not setup:
        return None

    config_mod.save_config(config)
    resources = getattr(setup, "resources", None)
    service_url = getattr(setup, "service_url", "")
    secret = getattr(setup, "secret", "")
    if resources:
        result.manual_followups.extend([
            f"Notification URL: {service_url or '(run notifications status to refresh)'}",
            f"Secret header: {resources.secret_header}",
            f"Secret: {secret}",
            f"Agent subscription: {resources.full_subscription}",
        ])
        if not getattr(setup, "vm_env_updated", True):
            result.failed_optional_steps.append(SetupIssue(
                step="Notification settings on agent",
                message=getattr(setup, "message", "") or "Notification settings were not written to the VM.",
                retry_command=f"ghosty-agents notifications sync {agent.name} --name {resources.name_slug}",
            ))

    if intent.install_notification_consumer and resources:
        consumer = _optional(
            result,
            "Notification event listener",
            f"ghosty-agents notifications sync {agent.name} --name {resources.name_slug} --with-consumer",
            lambda: bootstrap.install_webhook_consumer(config, agent, resources),
        )
        if consumer and not getattr(consumer, "installed", False):
            if "Notification event listener" in result.completed_steps:
                result.completed_steps.remove("Notification event listener")
            result.failed_optional_steps.append(SetupIssue(
                step="Notification event listener",
                message=getattr(consumer, "message", "") or "The event listener was not installed.",
                retry_command=f"ghosty-agents notifications sync {agent.name} --name {resources.name_slug} --with-consumer",
            ))
    return setup


def _maybe_setup_chat(config: Config, intent: AgentSetupIntent, result: AgentSetupResult, agent: Agent) -> object | None:
    if not intent.enable_chat:
        result.skipped_steps.append("Chat skipped")
        return None

    resources = _optional(
        result,
        "Chat",
        f"ghosty-agents chat add {agent.name} --yes",
        lambda: bootstrap.ensure_google_chat_gateway(
            config,
            agent.name,
            chat_project=intent.chat_project,
            create_project=intent.create_chat_project,
        ),
    )
    if not resources:
        return None

    config_mod.save_config(config)
    result.manual_followups.extend([
        "Google Chat Console: choose Cloud Pub/Sub as the connection type.",
        f"Chat console topic: {resources.full_topic}",
        f"Hermes project: {resources.chat_project}",
        f"Hermes subscription: {resources.full_subscription}",
        f"Hermes key path on agent: {resources.vm_key_path}",
    ])
    return resources


def _maybe_brief_agent(
    config: Config,
    intent: AgentSetupIntent,
    result: AgentSetupResult,
    agent: Agent,
    services: Sequence[tuple[str, str | None]],
) -> None:
    if not intent.brief_agent:
        return
    for service, name in services:
        retry = f"ghosty-agents instruct {agent.name} --service {service}"
        if name:
            retry += f" --name {name}"

        def work(service=service, name=name):
            delivered = instructions.deliver_instruction(config, agent, service, name=name)
            if not delivered.delivered:
                raise RuntimeError(delivered.message or "The instruction prompt was uploaded but Hermes did not run it.")

        _optional(result, f"Brief agent about {service}", retry, work)


def execute_intent(
    config: Config,
    intent: AgentSetupIntent,
    *,
    open_harness: bool = False,
) -> AgentSetupResult:
    """Run a guided setup intent. Required failures raise; optional ones are collected."""
    result = AgentSetupResult()
    _ensure_required_readiness(config, intent)
    _maybe_enable_internet(config, intent, result)
    _maybe_enable_models(config, intent, result)

    ui.warn(f"This creates a billable machine ({config.machine_type}) in {config.zone}.")
    created = agents.create_agent(config, intent.name, startup_script=intent.startup_script)
    result.agent = created
    result.completed_steps.append("Agent created")

    if intent.create_only:
        result.skipped_steps.append("Guided harness skipped")
    else:
        _maybe_setup_storage(config, intent, result, created)
        _maybe_install_hermes(config, intent, result, created)
        notification_setup = _maybe_setup_notifications(config, intent, result, created)
        chat_resources = _maybe_setup_chat(config, intent, result, created)

        brief_services: list[tuple[str, str | None]] = []
        if intent.enable_models:
            brief_services.append(("models", None))
        if intent.private_storage:
            brief_services.append(("storage", None))
        if chat_resources:
            brief_services.append(("chat", None))
        if notification_setup and getattr(notification_setup, "resources", None):
            brief_services.append(("notifications", notification_setup.resources.name_slug))
        if intent.brief_agent and intent.install_hermes and "Hermes" not in result.completed_steps:
            result.skipped_steps.append("Agent briefing skipped because Hermes did not finish")
        else:
            _maybe_brief_agent(config, intent, result, created, brief_services)

    if open_harness:
        harness_browser.live_harness(config, created)
        result.harness_opened = True
    return result


def print_result(result: AgentSetupResult) -> None:
    """Print a concise setup receipt."""
    table = Table(title="Guided setup receipt")
    table.add_column("DONE", style="bold green")
    if result.completed_steps:
        for item in result.completed_steps:
            table.add_row(item)
    else:
        table.add_row("-")
    ui.console.print(table)

    if result.skipped_steps:
        ui.console.print("[bold]Skipped[/]")
        for item in result.skipped_steps:
            ui.console.print(f"- {item}")

    if result.failed_optional_steps:
        ui.console.print("[bold yellow]Needs retry[/]")
        for issue in result.failed_optional_steps:
            ui.warn(f"{issue.step}: {issue.message}")
            if issue.retry_command:
                ui.console.print(f"  Retry: {issue.retry_command}", soft_wrap=True)

    if result.manual_followups:
        ui.panel("\n".join(result.manual_followups), title="Follow-up values", style="cyan")


def run_guided_setup(
    config: Config,
    *,
    name: str | None = None,
    startup_script: str | None = None,
    open_harness: bool = True,
) -> AgentSetupResult | None:
    """Collect, review, execute, and optionally open the visual harness."""
    intent = collect_intent(config, name=name, startup_script=startup_script)
    if intent is None:
        return None
    ui.panel(render_review(config, intent), title="Review", style="cyan")
    if not _confirm("Create this agent setup?", default=True):
        ui.skip("cancelled")
        return None
    result = execute_intent(config, intent, open_harness=False)
    print_result(result)
    if result.agent:
        ui.celebrate(f"Agent '{result.agent.name}' is ready.")
        if open_harness:
            harness_browser.live_harness(config, result.agent)
            result.harness_opened = True
    return result
