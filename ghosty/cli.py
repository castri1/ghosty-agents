"""ghosty-agents CLI entrypoint (Typer)."""

from __future__ import annotations

import shutil
from typing import Optional

import typer
from rich.table import Table

from ghosty import agents, bootstrap, catalog, config as config_mod, discover, gcloud, guided as guided_mod, hermes as hermes_mod, instructions, interactive, ui
from ghosty.doctor import run_checks
from ghosty.models import Config, sanitize_agent_name

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    help="Create, connect to, and equip your Ghosty agents.",
)
config_app = typer.Typer(no_args_is_help=True, help="View or edit Ghosty settings.")
nat_app = typer.Typer(no_args_is_help=True, help="Manage shared internet for private agents.")
google_ai_app = typer.Typer(no_args_is_help=True, help="Let agents use Google AI models.")
google_chat_app = typer.Typer(no_args_is_help=True, help="Add or manage agent chat.")
webhook_app = typer.Typer(no_args_is_help=True, help="Add or manage external notifications.")
bucket_app = typer.Typer(no_args_is_help=True, help="Manage agent file storage.")
hermes_app = typer.Typer(no_args_is_help=True, help="Install and configure Hermes on agents.")
app.add_typer(config_app, name="settings")
app.add_typer(nat_app, name="internet")
app.add_typer(google_ai_app, name="models")
app.add_typer(google_chat_app, name="chat")
app.add_typer(webhook_app, name="notifications")
app.add_typer(bucket_app, name="storage")
app.add_typer(hermes_app, name="hermes")
app.add_typer(config_app, name="config", hidden=True)
app.add_typer(nat_app, name="nat", hidden=True)
app.add_typer(google_ai_app, name="google-ai", hidden=True)
app.add_typer(google_chat_app, name="google-chat", hidden=True)
app.add_typer(webhook_app, name="webhook", hidden=True)
app.add_typer(bucket_app, name="bucket", hidden=True)


@app.callback(invoke_without_command=True)
def _main(ctx: typer.Context):
    """Open the control room when called with no subcommand."""
    if ctx.invoked_subcommand is None:
        if ui.is_interactive_tty():
            interactive.run()
        else:
            ui.banner()
            ui.console.print(ctx.get_help())
        raise typer.Exit()


@app.command()
def console():
    """Open the interactive Ghosty control room."""
    interactive.run()


# --- helpers --------------------------------------------------------------

def _load() -> Config:
    return config_mod.load_config()


def _require_ready(cfg: Config) -> None:
    """Ensure config has the required identifiers; otherwise stop."""
    missing = cfg.missing_required()
    if missing:
        ui.error(f"config incomplete (missing: {', '.join(missing)}).")
        ui.warn("Run `ghosty-agents init` first.")
        raise typer.Exit(1)


def _guard(fn):
    """Run a block, converting gcloud errors into clean CLI failures."""
    try:
        return fn()
    except gcloud.GcloudNotFound as exc:
        ui.error(str(exc))
        raise typer.Exit(127)
    except gcloud.GcloudError as exc:
        ui.error(exc.args[0])
        raise typer.Exit(1)
    except ValueError as exc:
        ui.error(str(exc))
        raise typer.Exit(1)


def _should_brief_agent(cfg: Config, *, force: bool = False, prompt: bool = False) -> bool:
    if force:
        return True
    delivery = (cfg.agent_instruction_delivery or "ask").strip().lower()
    if delivery in {"off", "never", "no", "false", "disabled"}:
        return False
    if delivery == "always":
        return True
    if prompt:
        return typer.confirm("Brief the agent now?", default=True)
    return False


def _brief_agent_after_setup(
    cfg: Config,
    agent: object,
    service: str,
    *,
    name: str | None = None,
    force: bool = False,
    prompt: bool = False,
) -> instructions.InstructionDeliveryResult | None:
    if not _should_brief_agent(cfg, force=force, prompt=prompt):
        return None
    try:
        result = instructions.deliver_instruction(cfg, agent, service, name=name)
    except (gcloud.GcloudError, gcloud.GcloudNotFound, ValueError) as exc:
        ui.warn(f"Could not brief the agent: {exc}")
        return None
    if not result.delivered:
        retry = f"ghosty-agents instruct {result.agent} --service {service}"
        if name:
            retry += f" --name {name}"
        ui.warn(f"Briefing can be retried with: {retry}")
    return result


# --- init -----------------------------------------------------------------

def _validate_project_id(pid: str) -> Optional[str]:
    """Return an error string if the project id is invalid, else None."""
    import re
    if not (6 <= len(pid) <= 30):
        return "must be 6–30 characters"
    if not re.fullmatch(r"[a-z][a-z0-9-]*[a-z0-9]", pid):
        return "lowercase letters/digits/hyphens, must start with a letter"
    return None


@app.command()
def init(
    project_id: Optional[str] = typer.Option(None, help="GCP project ID."),
    billing_account_id: Optional[str] = typer.Option(None, help="Billing account ID."),
    account: Optional[str] = typer.Option(None, help="Your GCP login email."),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Fail instead of prompting."),
):
    """Friendly setup wizard: detect accounts, pick billing, store config."""
    cfg = _load()

    if non_interactive:
        # Old-school, no prompts: require everything via flags/existing config.
        cfg.project_id = project_id or cfg.project_id
        cfg.billing_account_id = billing_account_id or cfg.billing_account_id
        cfg.account = account or cfg.account
        missing = cfg.missing_required()
        if missing:
            ui.error(f"missing required value(s): {', '.join(missing)}")
            raise typer.Exit(1)
        config_mod.save_config(cfg)
        ui.success("config saved")
        if gcloud.gcloud_available():
            _guard(lambda: bootstrap.ensure_isolated_config(cfg))
        return

    ui.banner()
    has_gcloud = gcloud.gcloud_available()
    if not has_gcloud:
        ui.warn("gcloud CLI not found. You can still save config now, but you'll need")
        ui.warn("the Google Cloud SDK before bootstrapping: https://cloud.google.com/sdk/docs/install")
        ui.console.print()

    reconfiguring = not cfg.missing_required()
    if reconfiguring:
        ui.info(f"Found existing config for project '{cfg.project_id}'. Let's tweak it. {ui.GHOST}")
        ui.console.print()

    # --- 1) Account ------------------------------------------------------
    ui.console.print("[bold magenta]Step 1/3 · Who are you?[/]")
    if account:
        cfg.account = account
        ui.success(f"using account {account}")
    else:
        accounts = discover.active_accounts() if has_gcloud else []
        if accounts:
            opts = [(a, a) for a in accounts]
            default_idx = accounts.index(cfg.account) if cfg.account in accounts else 0
            cfg.account = ui.choose(
                "Which Google account should ghosty use?", opts,
                default_index=default_idx, custom_label="Use a different email",
            )
        else:
            if has_gcloud:
                ui.warn("No authenticated accounts found. Run `gcloud auth login` (you can do it later).")
            cfg.account = typer.prompt("Your GCP login email", default=cfg.account or None)
    ui.console.print()

    # --- 2) Billing account ---------------------------------------------
    ui.console.print("[bold magenta]Step 2/3 · Who's paying?[/]")
    if billing_account_id:
        cfg.billing_account_id = billing_account_id
        ui.success(f"using billing account {billing_account_id}")
    else:
        billing = discover.billing_accounts() if has_gcloud else []
        if billing:
            opts = []
            for b in billing:
                state = "OPEN" if b["open"] else "CLOSED"
                label = f"{b['name'] or '(unnamed)'}  ({b['id']})  [{state}]"
                opts.append((b["id"], label))
            default_idx = next(
                (i for i, b in enumerate(billing) if b["id"] == cfg.billing_account_id), 0
            )
            cfg.billing_account_id = ui.choose(
                "Pick the billing account dedicated to your agents:", opts,
                default_index=default_idx, custom_label="Enter an ID manually",
            )
        else:
            if has_gcloud:
                ui.warn("Couldn't list billing accounts (need one? create it in the console).")
            cfg.billing_account_id = typer.prompt(
                "Billing account ID (format 0X0X0X-0X0X0X-0X0X0X)",
                default=cfg.billing_account_id or None,
            )
    ui.console.print()

    # --- 3) Project ------------------------------------------------------
    ui.console.print("[bold magenta]Step 3/3 · Where do they live?[/]")
    if project_id:
        cfg.project_id = project_id
    else:
        suggestion = cfg.project_id or "ghosty-agents"
        while True:
            pid = typer.prompt("Project ID for your agent fleet", default=suggestion)
            err = _validate_project_id(pid)
            if err:
                ui.warn(f"'{pid}' is invalid ({err}). Try again.")
                continue
            cfg.project_id = pid
            break
    ui.success(f"project '{cfg.project_id}'")
    ui.console.print()

    # --- Optional: advanced knobs ---------------------------------------
    if typer.confirm("Customize advanced defaults (region, machine type, budget)?", default=False):
        # Region (pick from a list, or enter a custom one).
        region_opts = list(catalog.REGIONS)
        region_default = next((i for i, (r, _) in enumerate(region_opts) if r == cfg.region), 0)
        cfg.region = ui.choose(
            "Region:", region_opts, default_index=region_default,
            custom_label="Enter a different region",
        )

        # Zone within the chosen region — fetch REAL zones (don't fabricate; not
        # every region has a "-a", e.g. us-east1 is b/c/d).
        live_zones = discover.zones_for_region(cfg, cfg.region) if has_gcloud else []
        if live_zones:
            zone_opts = [(z, z) for z in live_zones]
            zone_default = next((i for i, z in enumerate(live_zones) if z == cfg.zone), 0)
            cfg.zone = ui.choose(
                "Zone:", zone_opts, default_index=zone_default,
                custom_label="Enter a different zone",
            )
        else:
            # Can't list zones yet (no gcloud/API) — ask, defaulting to <region>-b
            # which exists in more regions than -a.
            cfg.zone = typer.prompt("Zone", default=cfg.zone or f"{cfg.region}-b")

        # Machine type.
        mt_opts = list(catalog.MACHINE_TYPES)
        mt_default = next((i for i, (m, _) in enumerate(mt_opts) if m == cfg.machine_type), 0)
        cfg.machine_type = ui.choose(
            "Default machine type:", mt_opts, default_index=mt_default,
            custom_label="Enter a different machine type",
        )

        cfg.budget_amount = typer.prompt("Monthly budget amount", default=cfg.budget_amount)
        cfg.budget_currency = typer.prompt("Budget currency", default=cfg.budget_currency)
        ui.console.print()

    # --- Summary + save --------------------------------------------------
    summary = (
        f"[bold]account[/]  {cfg.account}\n"
        f"[bold]billing[/]  {cfg.billing_account_id}\n"
        f"[bold]project[/]  {cfg.project_id}\n"
        f"[bold]region [/]  {cfg.region}  ([dim]zone {cfg.zone}[/])\n"
        f"[bold]machine[/]  {cfg.machine_type}\n"
        f"[bold]budget [/]  {cfg.budget_amount} {cfg.budget_currency}/mo"
    )
    ui.panel(summary, title=f"{ui.GHOST} Your ghost HQ", style="magenta")

    if not typer.confirm("Save this and continue?", default=True):
        ui.skip("nothing saved — run `ghosty-agents init` again whenever you're ready.")
        raise typer.Exit(1)

    path = config_mod.save_config(cfg)
    ui.success(f"config saved to {path}")

    if has_gcloud:
        _guard(lambda: bootstrap.ensure_isolated_config(cfg))

    ui.celebrate("All set! Your config is stored and isolated.")

    # --- Offer the next steps -------------------------------------------
    if has_gcloud and typer.confirm("Run a quick health check now (doctor)?", default=True):
        ctx_checks = _guard(lambda: run_checks(cfg, deep=True))
        _print_checks(ctx_checks)
        if all(c.ok for c in ctx_checks):
            if typer.confirm("Everything looks good — bootstrap the project infra now?", default=False):
                _guard(lambda: bootstrap.bootstrap_all(cfg))
                ui.celebrate("Prepared! Create your first agent: ghosty-agents create <name>")
                return
    ui.step("Next: `ghosty-agents prepare`, then `ghosty-agents create <name>`.")


# --- doctor ---------------------------------------------------------------

def _print_checks(checks) -> bool:
    """Print check results; return True if all passed."""
    all_ok = True
    for c in checks:
        if c.ok:
            ui.success(f"{c.name}" + (f" — {c.detail}" if c.detail else ""))
        else:
            all_ok = False
            ui.error(f"{c.name} — {c.detail}")
            if c.fix:
                ui.warn(f"  fix: {c.fix}")
    return all_ok


@app.command(name="check")
def doctor(
    quick: bool = typer.Option(False, "--quick", help="Skip checks that call GCP."),
):
    """Check that everything needed to operate is in place."""
    cfg = _load()
    ui.console.print(f"[bold magenta]{ui.GHOST}  Running a séance to check your setup...[/]\n")
    checks = _guard(lambda: run_checks(cfg, deep=not quick))
    if not _print_checks(checks):
        ui.console.print()
        ui.warn("Some checks failed — see fixes above.")
        raise typer.Exit(1)
    ui.console.print()
    ui.celebrate("All checks passed. The spirits are pleased.")


app.command(name="doctor", hidden=True)(doctor)


# --- bootstrap ------------------------------------------------------------

def bootstrap_cmd(
    project_name: Optional[str] = typer.Option(None, "--project-name", help="Display name if the project must be created."),
    with_nat: bool = typer.Option(False, "--with-nat", help="Also enable shared internet for private agents (billable)."),
):
    """Prepare shared project resources."""
    cfg = _load()
    _require_ready(cfg)
    ui.console.print(f"[bold magenta]{ui.GHOST}  Preparing the haunted grounds...[/]\n")
    _guard(lambda: bootstrap.ensure_isolated_config(cfg))
    _guard(lambda: bootstrap.bootstrap_all(cfg, project_name, with_nat=with_nat))
    ui.console.print()
    ui.celebrate("Project prepared! Create an agent with `ghosty-agents create <name>`.")


# --- nat ------------------------------------------------------------------

@nat_app.command("status")
def nat_status_cmd():
    """Show shared internet state."""
    cfg = _load()
    _require_ready(cfg)
    status = _guard(lambda: bootstrap.nat_status(cfg))
    state = "ENABLED" if status.enabled else "DISABLED"
    detail = "router + NAT present"
    if not status.router_exists:
        detail = "router not found"
    elif not status.nat_exists:
        detail = "router exists, NAT not found"

    table = Table(title=f"Shared internet — {cfg.project_id}", show_header=False)
    table.add_column("field", style="bold cyan")
    table.add_column("value")
    table.add_row("status", state)
    table.add_row("detail", detail)
    table.add_row("region", status.region)
    table.add_row("advanced router", status.router)
    table.add_row("advanced NAT", status.nat)
    table.add_row("network", status.network)
    table.add_row("subnet", status.subnet)
    ui.console.print(table)


@nat_app.command("enable")
def nat_enable_cmd(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the cost confirmation."),
):
    """Enable shared internet for private agents."""
    cfg = _load()
    _require_ready(cfg)
    if not yes:
        ui.warn(
            f"This creates billable shared internet in {cfg.region} for private agents on {cfg.subnet}."
        )
        if not typer.confirm("Enable shared internet?"):
            ui.skip("aborted")
            raise typer.Exit(1)
    _guard(lambda: bootstrap.ensure_network(cfg))
    _guard(lambda: bootstrap.ensure_nat(cfg))
    ui.success("shared internet is enabled")


@nat_app.command("disable")
def nat_disable_cmd(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    """Disable shared internet."""
    cfg = _load()
    _require_ready(cfg)
    if not yes:
        ui.warn(f"This removes general outbound internet for private agents using {cfg.nat_name}.")
        if not typer.confirm("Disable shared internet?"):
            ui.skip("aborted")
            raise typer.Exit(1)
    _guard(lambda: bootstrap.disable_nat(cfg))


# --- google-ai -------------------------------------------------------------

@google_ai_app.command("status")
def google_ai_status_cmd():
    """Show model access state."""
    cfg = _load()
    _require_ready(cfg)
    status = _guard(lambda: bootstrap.google_ai_status(cfg))

    table = Table(title=f"Google AI / Agent Platform — {cfg.project_id}", show_header=False)
    table.add_column("field", style="bold cyan")
    table.add_column("value")
    table.add_row("api", status.api)
    table.add_row("api enabled", "YES" if status.api_enabled else "NO")
    table.add_row("future-agent auto-grant", "YES" if status.auto_grant_enabled else "NO")
    table.add_row("role", status.role)
    ui.console.print(table)

    agents_table = Table(title="Ghosty agent service accounts")
    agents_table.add_column("AGENT", style="bold")
    agents_table.add_column("SERVICE ACCOUNT")
    agents_table.add_column("HAS ROLE")
    if status.agents:
        for a in status.agents:
            agents_table.add_row(a.agent, a.service_account, "YES" if a.has_role else "NO")
    else:
        agents_table.add_row("-", "no Ghosty agents found", "-")
    ui.console.print(agents_table)


@google_ai_app.command("enable")
def google_ai_enable_cmd(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    brief_agent: bool = typer.Option(False, "--brief-agent", help="Brief existing agents about model access after enabling."),
):
    """Enable model access for Ghosty agents."""
    cfg = _load()
    _require_ready(cfg)
    if not yes:
        ui.warn(
            f"This enables {cfg.google_ai_api} and grants {cfg.google_ai_role} "
            "to Ghosty agent service accounts."
        )
        ui.warn("Using Google AI models may incur usage charges.")
        if not typer.confirm("Enable Google AI access?"):
            ui.skip("aborted")
            raise typer.Exit(1)
    _guard(lambda: bootstrap.enable_google_ai(cfg))
    path = config_mod.save_config(cfg)
    ui.success(f"Google AI access enabled ({path})")
    if brief_agent:
        found = _guard(lambda: agents.list_agents(cfg))
        if not found:
            ui.skip("no agents to brief")
        for agent in found:
            _brief_agent_after_setup(cfg, agent, "models", force=True)


@google_ai_app.command("disable")
def google_ai_disable_iam_cmd(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    """Turn off model access for Ghosty agents without disabling the API."""
    cfg = _load()
    _require_ready(cfg)
    if not yes:
        ui.warn("This removes model access from Ghosty agents.")
        ui.warn(f"Advanced: removes {cfg.google_ai_role} and leaves {cfg.google_ai_api} enabled.")
        if not typer.confirm("Turn off model access?"):
            ui.skip("aborted")
            raise typer.Exit(1)
    _guard(lambda: bootstrap.disable_google_ai_iam(cfg))
    path = config_mod.save_config(cfg)
    ui.success(f"model access disabled for Ghosty agents ({path})")


google_ai_app.command("disable-iam", hidden=True)(google_ai_disable_iam_cmd)


# --- google-chat -----------------------------------------------------------

def _print_google_chat_status(status: bootstrap.GoogleChatStatus) -> None:
    resources = status.resources
    table = Table(title=f"Google Chat gateway — {resources.agent}", show_header=False)
    table.add_column("field", style="bold cyan")
    table.add_column("value", overflow="fold")
    table.add_row("chat project", resources.chat_project)
    table.add_row("chat project exists", "YES" if status.chat_project_exists else "NO")
    table.add_row("chat API", "YES" if status.chat_api_enabled else "NO")
    table.add_row("pub/sub API", "YES" if status.pubsub_api_enabled else "NO")
    table.add_row("topic", resources.topic if status.topic_exists else f"{resources.topic} (missing)")
    table.add_row(
        "subscription",
        resources.subscription if status.subscription_exists else f"{resources.subscription} (missing)",
    )
    table.add_row(
        "service account",
        resources.service_account_email if status.service_account_exists else f"{resources.service_account_email} (missing)",
    )
    table.add_row("topic publisher", "YES" if status.topic_publisher_bound else "NO")
    table.add_row("subscription subscriber", "YES" if status.subscription_subscriber_bound else "NO")
    table.add_row("subscription viewer", "YES" if status.subscription_viewer_bound else "NO")
    table.add_row("local key", str(resources.local_key_path) if status.local_key_exists else "missing")
    table.add_row("VM key path", resources.vm_key_path)
    table.add_row("Chat console topic", resources.full_topic)
    table.add_row("Hermes project", resources.chat_project)
    table.add_row("Hermes subscription", resources.full_subscription)
    ui.console.print(table)
    ui.console.print()
    ui.console.print("[bold]Paste values[/]")
    ui.console.print(f"Chat console topic: {resources.full_topic}", soft_wrap=True)
    ui.console.print(f"Hermes project: {resources.chat_project}", soft_wrap=True)
    ui.console.print(f"Hermes subscription: {resources.full_subscription}", soft_wrap=True)


@google_chat_app.command("status")
def google_chat_status_cmd(
    agent: str = typer.Argument(..., help="Agent name, e.g. alba-nury."),
    chat_project: Optional[str] = typer.Option(None, "--chat-project", help="Google Chat app project ID."),
):
    """Show Google Chat Pub/Sub gateway state for an agent."""
    cfg = _load()
    _require_ready(cfg)
    status = _guard(lambda: bootstrap.google_chat_status(cfg, agent, chat_project=chat_project))
    _print_google_chat_status(status)


@google_chat_app.command("add")
def google_chat_setup_cmd(
    agent: str = typer.Argument(..., help="Agent name, e.g. alba-nury."),
    chat_project: Optional[str] = typer.Option(None, "--chat-project", help="Google Chat app project ID."),
    chat_folder: Optional[str] = typer.Option(None, "--chat-folder", help="Folder ID for auto-created Chat projects."),
    billing_account: Optional[str] = typer.Option(None, "--billing-account", help="Billing account for auto-created Chat projects."),
    create_project: bool = typer.Option(True, "--create-project/--no-create-project", help="Create the Chat project if it is missing."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    brief_agent: bool = typer.Option(False, "--brief-agent", help="Brief the agent about Chat after setup."),
):
    """Add chat to an agent."""
    cfg = _load()
    _require_ready(cfg)

    found = _guard(lambda: agents.get_agent(cfg, agent))
    if found is None:
        ui.error(f"agent '{agent}' not found.")
        ui.warn("Create it first with `ghosty-agents create <name>`.")
        raise typer.Exit(1)

    resources = bootstrap.google_chat_resources(cfg, agent, chat_project)
    if not yes:
        ui.warn(f"This configures Google Chat project '{resources.chat_project}'.")
        ui.warn("It enables Google Chat + Pub/Sub APIs, creates Pub/Sub resources,")
        ui.warn("creates a dedicated service-account JSON key, and uploads it to the VM.")
        if not typer.confirm(f"Set up Google Chat gateway for '{agent}'?"):
            ui.skip("aborted")
            raise typer.Exit(1)

    resources = _guard(lambda: bootstrap.ensure_google_chat_gateway(
        cfg,
        agent,
        chat_project=chat_project,
        folder_id=chat_folder,
        billing_account_id=billing_account,
        create_project=create_project,
    ))
    path = config_mod.save_config(cfg)
    ui.celebrate(f"Google Chat gateway resources are ready for '{agent}'.")
    body = (
        f"[bold]Connection type[/]  Cloud Pub/Sub\n"
        f"[bold]Topic[/]            {resources.full_topic}\n"
        f"[bold]Hermes project[/]   {resources.chat_project}\n"
        f"[bold]Subscription[/]     {resources.full_subscription}\n"
        f"[bold]Service account[/]  {resources.service_account_email}\n"
        f"[bold]Hermes key path[/]  {resources.vm_key_path}\n"
        f"[bold]Config saved[/]     {path}\n\n"
        "In Google Chat API Console > Configuration, select Cloud Pub/Sub, "
        "paste the topic above, enable 1:1 and group conversations as needed, "
        "restrict visibility, save, then install the app in a space."
    )
    ui.panel(body, title="Google Chat console values", style="cyan")
    _brief_agent_after_setup(cfg, found, "chat", force=brief_agent, prompt=not yes)


google_chat_app.command("setup", hidden=True)(google_chat_setup_cmd)


@google_chat_app.command("remove")
def google_chat_destroy_cmd(
    agent: str = typer.Argument(..., help="Agent name, e.g. alba-nury."),
    chat_project: Optional[str] = typer.Option(None, "--chat-project", help="Google Chat app project ID."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    """Remove chat resources for an agent."""
    cfg = _load()
    _require_ready(cfg)
    resources = bootstrap.google_chat_resources(cfg, agent, chat_project)
    if not yes:
        ui.warn(
            f"This deletes Pub/Sub topic '{resources.topic}' and subscription "
            f"'{resources.subscription}' in project '{resources.chat_project}'."
        )
        ui.warn("It leaves the service account and JSON keys in place.")
        if not typer.confirm(f"Destroy Google Chat gateway resources for '{agent}'?"):
            ui.skip("aborted")
            raise typer.Exit(1)
    resources = _guard(lambda: bootstrap.destroy_google_chat_gateway(
        cfg,
        agent,
        chat_project=chat_project,
    ))
    path = config_mod.save_config(cfg)
    ui.success(f"Google Chat Pub/Sub resources removed for '{agent}'")
    ui.warn("Manual follow-up: remove or update the Google Chat API console configuration.")
    ui.warn(f"Key/service-account cleanup left untouched: {resources.service_account_email}")
    ui.skip(f"config saved to {path}")


google_chat_app.command("destroy", hidden=True)(google_chat_destroy_cmd)


# --- hermes ---------------------------------------------------------------

def _require_agent(cfg: Config, name: str) -> object:
    found = _guard(lambda: agents.get_agent(cfg, name))
    if found is None:
        ui.error(f"agent '{name}' not found.")
        raise typer.Exit(1)
    return found


def _cleanup_agent_attached_resources(cfg: Config, name: str, found: object | None = None) -> None:
    """Remove cloud resources that are owned by one agent before deleting its VM/SA."""
    agent_obj = found or name
    slug = sanitize_agent_name(name)

    for webhook_name in bootstrap.webhook_names(cfg, name):
        ui.step(f"Removing notifications '{webhook_name}' for '{name}'")
        bootstrap.destroy_webhook_gateway(cfg, name, name=webhook_name)

    chat_projects = cfg.google_chat_projects if isinstance(cfg.google_chat_projects, dict) else {}
    if slug in chat_projects:
        ui.step(f"Removing chat resources for '{name}'")
        bootstrap.destroy_google_chat_gateway(cfg, name)

    if cfg.storage_bucket or cfg.storage_public_bucket:
        ui.step(f"Removing storage access for '{name}'")
        bootstrap.cleanup_storage_for_agent(cfg, agent_obj)

    if cfg.google_ai_enabled:
        ui.step(f"Removing model access for '{name}'")
        bootstrap.remove_google_ai_from_agent(cfg, name)

    for local_dir in (
        config_mod.config_dir() / "instructions" / slug,
        config_mod.config_dir() / "google-chat" / slug,
    ):
        if local_dir.exists():
            ui.step(f"Removing local agent artifacts '{local_dir}'")
            shutil.rmtree(local_dir)


def _print_hermes_status(status: hermes_mod.HermesStatus) -> None:
    table = Table(title=f"Hermes — {status.agent}", show_header=False)
    table.add_column("field", style="bold cyan")
    table.add_column("value", overflow="fold")
    table.add_row("installed", "YES" if status.installed else "NO")
    table.add_row("command", "YES" if status.command_exists else "NO")
    table.add_row("environment", "YES" if status.env_exists else "NO")
    table.add_row("config", "YES" if status.config_exists else "NO")
    table.add_row("gateway", "RUNNING" if status.gateway_active else "OFF")
    table.add_row("provider", status.provider or "-")
    table.add_row("model", status.model or "-")
    table.add_row("Vertex project", status.vertex_project or "-")
    table.add_row("Vertex region", status.vertex_region or "-")
    table.add_row("version", status.version or "-")
    if status.message:
        table.add_row("message", status.message)
    ui.console.print(table)


@hermes_app.command("install")
def hermes_install_cmd(
    agent: str = typer.Argument(..., help="Agent name, e.g. alba-nury."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    branch: str = typer.Option(hermes_mod.DEFAULT_BRANCH, "--branch", help="Hermes installer branch."),
    commit: Optional[str] = typer.Option(None, "--commit", help="Optional Hermes commit SHA to pin."),
    skip_browser: bool = typer.Option(False, "--skip-browser", help="Skip browser setup in the vendor installer."),
):
    """Install Hermes on an agent using the official installer."""
    cfg = _load()
    _require_ready(cfg)
    found = _require_agent(cfg, agent)
    if not yes:
        ui.warn("This connects to the agent and runs the official Hermes installer.")
        ui.warn("Default behavior tracks the vendor installer branch so Hermes can stay current.")
        if commit:
            ui.warn(f"Advanced: install is pinned to commit {commit}.")
        if not typer.confirm(f"Install Hermes on '{agent}'?"):
            ui.skip("aborted")
            raise typer.Exit(1)
    result = _guard(lambda: hermes_mod.install_hermes(
        cfg,
        found,
        branch=branch,
        commit=commit,
        skip_browser=skip_browser,
    ))
    if result and not result.installed:
        if result.message:
            ui.warn(result.message)
        raise typer.Exit(1)


@hermes_app.command("configure")
def hermes_configure_cmd(
    agent: str = typer.Argument(..., help="Agent name, e.g. alba-nury."),
    provider: str = typer.Option(hermes_mod.DEFAULT_PROVIDER, "--provider", help="Model provider."),
    model: str = typer.Option(hermes_mod.DEFAULT_MODEL, "--model", help="Default model."),
):
    """Configure Hermes to use Google models through the VM cloud identity."""
    cfg = _load()
    _require_ready(cfg)
    found = _require_agent(cfg, agent)
    result = _guard(lambda: hermes_mod.configure_hermes(cfg, found, provider=provider, model=model))
    if result and not result.configured:
        if result.message:
            ui.warn(result.message)
        raise typer.Exit(1)


@hermes_app.command("status")
def hermes_status_cmd(
    agent: str = typer.Argument(..., help="Agent name, e.g. alba-nury."),
):
    """Show Hermes install, model config, and gateway state."""
    cfg = _load()
    _require_ready(cfg)
    found = _require_agent(cfg, agent)
    status = _guard(lambda: hermes_mod.hermes_status(cfg, found))
    if status:
        _print_hermes_status(status)


@hermes_app.command("sync")
def hermes_sync_cmd(
    agent: str = typer.Argument(..., help="Agent name, e.g. alba-nury."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    branch: str = typer.Option(hermes_mod.DEFAULT_BRANCH, "--branch", help="Hermes installer branch."),
    commit: Optional[str] = typer.Option(None, "--commit", help="Optional Hermes commit SHA to pin."),
    skip_browser: bool = typer.Option(False, "--skip-browser", help="Skip browser setup in the vendor installer."),
    provider: str = typer.Option(hermes_mod.DEFAULT_PROVIDER, "--provider", help="Model provider."),
    model: str = typer.Option(hermes_mod.DEFAULT_MODEL, "--model", help="Default model."),
):
    """Install or update Hermes, apply model config, and check status."""
    cfg = _load()
    _require_ready(cfg)
    found = _require_agent(cfg, agent)
    if not yes:
        ui.warn("This installs or updates Hermes and reapplies model configuration.")
        if not typer.confirm(f"Sync Hermes on '{agent}'?"):
            ui.skip("aborted")
            raise typer.Exit(1)
    status = _guard(lambda: hermes_mod.sync_hermes(
        cfg,
        found,
        branch=branch,
        commit=commit,
        skip_browser=skip_browser,
        provider=provider,
        model=model,
    ))
    if status:
        _print_hermes_status(status)


# --- webhook ---------------------------------------------------------------

def _webhook_env_state(value: bool | None) -> str:
    if value is True:
        return "YES"
    if value is False:
        return "NO"
    return "UNKNOWN"


def _webhook_consumer_state(installed: bool | None, active: bool | None) -> str:
    if installed is None:
        return "UNKNOWN"
    if not installed:
        return "NO"
    if active is True:
        return "RUNNING"
    if active is False:
        return "INSTALLED"
    return "INSTALLED"


def _print_webhook_status(statuses: list[bootstrap.WebhookStatus]) -> None:
    table = Table(title="External notifications")
    table.add_column("AGENT", style="bold", no_wrap=True)
    table.add_column("NAME", no_wrap=True)
    table.add_column("URL", overflow="fold")
    table.add_column("TOPIC")
    table.add_column("SUBSCRIPTION")
    table.add_column("RUN")
    table.add_column("PUB/SUB")
    table.add_column("IAM")
    table.add_column("VM ENV")
    table.add_column("CONSUMER")
    if statuses:
        for status in statuses:
            resources = status.resources
            table.add_row(
                resources.agent,
                resources.name_slug,
                status.service_url or "-",
                "YES" if status.topic_exists else "NO",
                "YES" if status.subscription_exists else "NO",
                "YES" if status.service_exists else "NO",
                "YES" if status.pubsub_api_enabled else "NO",
                "YES" if all([status.publisher_bound, status.subscriber_bound, status.viewer_bound]) else "NO",
                _webhook_env_state(status.vm_env_exists),
                _webhook_consumer_state(status.consumer_installed, status.consumer_active),
            )
    else:
        table.add_row("-", "-", "-", "-", "-", "-", "-", "-", "-", "-")
    ui.console.print(table)

    for status in statuses:
        resources = status.resources
        ui.console.print()
        ui.console.print(f"[bold]Notification values — {resources.agent}/{resources.name_slug}[/]")
        ui.console.print(f"Notification URL: {status.service_url or '(receiver missing)'}", soft_wrap=True)
        ui.console.print(f"Secret header: {resources.secret_header}", soft_wrap=True)
        ui.console.print(f"Agent subscription: {resources.full_subscription}", soft_wrap=True)
        ui.console.print(f"Agent config path: {resources.env_path}", soft_wrap=True)
        ui.console.print(
            f"Agent consumer: {_webhook_consumer_state(status.consumer_installed, status.consumer_active)}",
            soft_wrap=True,
        )


def _webhook_external_guidance(result: bootstrap.WebhookSetupResult) -> str:
    resources = result.resources
    auth = f"Send header {resources.secret_header}: {result.secret}"
    return (
        f"[bold]Notification URL[/]  {result.service_url or '(pending; run status to refresh)'}\n"
        f"[bold]Secret[/]            {result.secret}\n"
        f"[bold]Auth setup[/]        {auth}\n"
        f"[bold]Agent subscription[/] {resources.full_subscription}\n"
        f"[bold]Agent config path[/] {resources.env_path}\n\n"
        "The agent stays private. Use `ghosty-agents notifications sync "
        f"{resources.agent} --name {resources.name_slug} --with-consumer` if you want Ghosty "
        "to install the Hermes event consumer on the VM."
    )


def _webhook_statuses_for_agent(cfg: Config, found: object, name: Optional[str]) -> list[bootstrap.WebhookStatus]:
    if name:
        return [_guard(lambda: bootstrap.webhook_status(cfg, found, name=name))]
    names = bootstrap.webhook_names(cfg, getattr(found, "name", ""))
    return [_guard(lambda item=item: bootstrap.webhook_status(cfg, found, name=item)) for item in names]


def _webhook_secret_saved(cfg: Config, resources: bootstrap.WebhookResources) -> bool:
    agent_map = cfg.webhook_gateways.get(resources.slug, {}) if isinstance(cfg.webhook_gateways, dict) else {}
    meta = agent_map.get(resources.name_slug, {}) if isinstance(agent_map, dict) else {}
    return bool(meta.get("secret")) if isinstance(meta, dict) else False


def _resolve_webhook_setup_inputs(
    cfg: Config,
    agent: str,
    name: Optional[str],
    secret: Optional[str],
    generate_secret: bool,
    yes: bool,
) -> tuple[str, Optional[str], bool, bootstrap.WebhookResources]:
    if name is None:
        name = "webhook" if yes else typer.prompt("Webhook name", default="webhook")

    resources = _guard(lambda: bootstrap.webhook_resources(cfg, agent, name))

    if secret is None and not generate_secret and not yes:
        if _webhook_secret_saved(cfg, resources):
            ui.skip("reusing saved webhook secret")
        elif typer.confirm("Generate a secure secret for request validation?", default=True):
            generate_secret = True
        else:
            secret = typer.prompt("Webhook secret", hide_input=True, confirmation_prompt=True)

    return name, secret, generate_secret, resources


@webhook_app.command("add")
def webhook_setup_cmd(
    agent: str = typer.Argument(..., help="Agent name, e.g. alba-nury."),
    name: Optional[str] = typer.Option(None, "--name", help="Notification name, e.g. crm. Omit to be prompted."),
    secret: Optional[str] = typer.Option(None, "--secret", help="Shared secret for request validation."),
    generate_secret: bool = typer.Option(False, "--generate-secret", help="Generate a new secret even if one exists."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    with_consumer: bool = typer.Option(False, "--with-consumer", help="Install the VM-side Hermes event consumer."),
    brief_agent: bool = typer.Option(False, "--brief-agent", help="Brief the agent about this notification path after setup."),
):
    """Add external notifications for an agent."""
    cfg = _load()
    _require_ready(cfg)
    found = _guard(lambda: agents.get_agent(cfg, agent))
    if found is None:
        ui.error(f"agent '{agent}' not found.")
        ui.warn("Create it first with `ghosty-agents create <name>`.")
        raise typer.Exit(1)

    name, secret, generate_secret, resources = _resolve_webhook_setup_inputs(
        cfg,
        agent,
        name,
        secret,
        generate_secret,
        yes,
    )
    if not yes:
        ui.warn(f"This creates a public notification address '{resources.service_name}'.")
        ui.warn("Requests are validated, published to Pub/Sub, then consumed by the private VM.")
        if not typer.confirm(f"Add notifications '{resources.name_slug}' for '{agent}'?"):
            ui.skip("aborted")
            raise typer.Exit(1)

    result = _guard(lambda: bootstrap.ensure_webhook_gateway(
        cfg,
        found,
        name=name,
        secret=secret,
        generate_secret=generate_secret,
    ))
    path = config_mod.save_config(cfg)
    ui.celebrate(f"notifications '{result.resources.name_slug}' are ready for '{agent}'")
    ui.panel(_webhook_external_guidance(result) + f"\n[bold]Config saved[/]     {path}", title="Notification values", style="cyan")
    if with_consumer:
        consumer = _guard(lambda: bootstrap.install_webhook_consumer(cfg, found, result.resources))
        if consumer:
            if consumer.installed:
                ui.success(f"Hermes event consumer is running: {consumer.service_name}")
            else:
                ui.warn(consumer.message or f"consumer was not installed: {consumer.service_name}")
    _brief_agent_after_setup(cfg, found, "notifications", name=result.resources.name_slug, force=brief_agent, prompt=not yes)


webhook_app.command("setup", hidden=True)(webhook_setup_cmd)


@webhook_app.command("status")
def webhook_status_cmd(
    agent: str = typer.Argument(..., help="Agent name, e.g. alba-nury."),
    name: Optional[str] = typer.Option(None, "--name", help="Notification name. Omit to list configured notifications."),
):
    """Show external notification state for an agent."""
    cfg = _load()
    _require_ready(cfg)
    found = _guard(lambda: agents.get_agent(cfg, agent))
    if found is None:
        ui.error(f"agent '{agent}' not found.")
        raise typer.Exit(1)
    statuses = _webhook_statuses_for_agent(cfg, found, name)
    if not statuses and not name:
        ui.skip(f"no notifications configured for '{agent}'")
        return
    _print_webhook_status(statuses)


@webhook_app.command("sync")
def webhook_sync_cmd(
    agent: str = typer.Argument(..., help="Agent name, e.g. alba-nury."),
    name: Optional[str] = typer.Option(None, "--name", help="Notification name. Omit to sync all configured notifications."),
    with_consumer: bool = typer.Option(False, "--with-consumer", help="Also install or refresh the Hermes event consumer."),
):
    """Refresh notification access and agent config."""
    cfg = _load()
    _require_ready(cfg)
    found = _guard(lambda: agents.get_agent(cfg, agent))
    if found is None:
        ui.error(f"agent '{agent}' not found.")
        raise typer.Exit(1)
    names = [name] if name else bootstrap.webhook_names(cfg, getattr(found, "name", agent))
    if not names:
        ui.skip(f"no notifications configured for '{agent}'")
        return
    results = [_guard(lambda item=item: bootstrap.sync_webhook_gateway(cfg, found, name=item)) for item in names]
    path = config_mod.save_config(cfg)
    for result in results:
        state = "updated" if result.vm_env_updated else "pending"
        ui.success(f"notifications '{result.resources.name_slug}' sync finished; agent config {state}")
        if result.message:
            ui.warn(result.message)
        if with_consumer:
            consumer = _guard(lambda result=result: bootstrap.install_webhook_consumer(cfg, found, result.resources))
            if consumer:
                if consumer.installed:
                    ui.success(f"Hermes event consumer is running: {consumer.service_name}")
                else:
                    ui.warn(consumer.message or f"consumer was not installed: {consumer.service_name}")
    ui.skip(f"config saved to {path}")


@webhook_app.command("remove")
def webhook_destroy_cmd(
    agent: str = typer.Argument(..., help="Agent name, e.g. alba-nury."),
    name: str = typer.Option(..., "--name", help="Notification name."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    """Remove one notification path."""
    cfg = _load()
    _require_ready(cfg)
    resources = _guard(lambda: bootstrap.webhook_resources(cfg, agent, name))
    if not yes:
        ui.warn(f"This removes notification address '{resources.service_name}' and its message queue.")
        ui.warn("It leaves VM-side code/logs untouched.")
        if not typer.confirm(f"Remove notifications '{resources.name_slug}' for '{agent}'?"):
            ui.skip("aborted")
            raise typer.Exit(1)
    resources = _guard(lambda: bootstrap.destroy_webhook_gateway(cfg, agent, name=name))
    path = config_mod.save_config(cfg)
    ui.success(f"notifications '{resources.name_slug}' removed for '{agent}'")
    ui.warn("Manual follow-up: remove the notification URL from the external system.")
    ui.skip(f"config saved to {path}")


webhook_app.command("destroy", hidden=True)(webhook_destroy_cmd)


# --- bucket ----------------------------------------------------------------

def _env_state(value: bool | None) -> str:
    if value is True:
        return "YES"
    if value is False:
        return "NO"
    return "UNKNOWN"


def _print_storage_sync_summary(result: bootstrap.StorageSetupResult) -> None:
    table = Table(title=f"Storage sync — {result.bucket_uri}")
    table.add_column("AGENT", style="bold")
    table.add_column("SERVICE ACCOUNT")
    table.add_column("PRIVATE FOLDER")
    table.add_column("PUBLIC FOLDER")
    table.add_column("SIGNED URLS")
    table.add_column("VM ENV")
    table.add_column("NOTE")
    if result.agents:
        for row in result.agents:
            table.add_row(
                row.agent,
                row.service_account,
                "UPDATED" if row.private_folder_iam_updated else "-",
                "UPDATED" if row.public_folder_iam_updated else "-",
                "UPDATED" if row.signed_url_iam_updated else "-",
                "UPDATED" if row.vm_env_updated else "PENDING",
                row.message or "-",
            )
    else:
        table.add_row("-", "no Ghosty agents found", "-", "-", "-", "-", "-")
    ui.console.print(table)


@bucket_app.command("status")
def bucket_status_cmd():
    """Show private/public storage folder, IAM, and VM env-file state."""
    cfg = _load()
    _require_ready(cfg)
    status = _guard(lambda: bootstrap.storage_status(cfg))

    table = Table(title=f"Shared Agent Storage — {cfg.project_id}", show_header=False)
    table.add_column("field", style="bold cyan")
    table.add_column("value")
    table.add_row("storage API", "YES" if status.storage_api_enabled else "NO")
    table.add_row("storage JSON API", "YES" if status.storage_json_api_enabled else "NO")
    table.add_row("signing API", "YES" if status.signing_api_enabled else "NO")
    table.add_row("future-agent auto-grant", "YES" if status.auto_grant_enabled else "NO")
    table.add_row("private bucket", status.bucket_uri if status.bucket_exists else f"{status.bucket_uri} (missing)")
    table.add_row("private location", status.location)
    table.add_row("private class", status.storage_class)
    table.add_row("private role", status.role)
    table.add_row("public enabled", "YES" if status.public_enabled else "NO")
    table.add_row(
        "public bucket",
        status.public_bucket_uri if status.public_bucket_exists else f"{status.public_bucket_uri} (missing)",
    )
    table.add_row("public location", status.public_location)
    table.add_row("public class", status.public_storage_class)
    table.add_row("public viewer role", status.public_viewer_role)
    table.add_row("signed URLs", "YES" if status.signed_urls_enabled else "NO")
    ui.console.print(table)

    agents_table = Table(title="Ghosty agent storage access")
    agents_table.add_column("AGENT", style="bold", no_wrap=True)
    agents_table.add_column("PRIVATE")
    agents_table.add_column("PRIVATE IAM")
    agents_table.add_column("PUBLIC")
    agents_table.add_column("PUBLIC IAM")
    agents_table.add_column("PUBLIC READ")
    agents_table.add_column("SIGNED URL")
    agents_table.add_column("LEGACY BROAD")
    agents_table.add_column("VM ENV")
    if status.agents:
        for agent in status.agents:
            agents_table.add_row(
                agent.agent,
                "YES" if agent.private_folder_exists else "NO",
                "YES" if agent.has_private_folder_role else "NO",
                "YES" if agent.public_folder_exists else ("-" if not agent.public_folder_uri else "NO"),
                "YES" if agent.has_public_folder_role else ("-" if not agent.public_folder_uri else "NO"),
                "YES" if agent.public_folder_is_public else ("-" if not agent.public_folder_uri else "NO"),
                "YES" if agent.has_signed_url_iam else ("-" if not status.signed_urls_enabled else "NO"),
                "YES" if agent.has_legacy_bucket_role else "NO",
                _env_state(agent.vm_env_exists),
            )
    else:
        agents_table.add_row("-", "-", "-", "-", "-", "-", "-", "-", "-")
    ui.console.print(agents_table)


@bucket_app.command("add")
def bucket_setup_cmd(
    bucket: Optional[str] = typer.Option(None, "--bucket", help="Bucket name. Defaults to <project-id>-ghosty-agent-storage."),
    public_bucket: Optional[str] = typer.Option(None, "--public-bucket", help="Public bucket name. Defaults to <project-id>-ghosty-agent-public."),
    location: Optional[str] = typer.Option(None, "--location", help="Bucket location. Defaults to the configured region."),
    with_public: bool = typer.Option(False, "--with-public", help="Also create per-agent public publishing folders."),
    with_signed_urls: bool = typer.Option(False, "--with-signed-urls", help="Let agents generate signed URLs for private objects."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    brief_agent: bool = typer.Option(False, "--brief-agent", help="Brief synced agents about storage after setup."),
):
    """Add private storage folders and optional public sharing."""
    cfg = _load()
    _require_ready(cfg)
    bucket_name = bootstrap.storage_bucket_name(cfg, bucket)
    public_bucket_name = bootstrap.storage_public_bucket_name(cfg, public_bucket)
    chosen_location = bootstrap.storage_location(cfg, location)
    if not yes:
        ui.warn(
            f"This creates or configures private Cloud Storage bucket gs://{bucket_name} "
            f"in {chosen_location}."
        )
        ui.warn("Each Ghosty agent gets object access only to its own managed folder.")
        if with_public or public_bucket or cfg.storage_public_enabled:
            ui.warn(f"Public publishing will use gs://{public_bucket_name} with per-agent public folders.")
        if with_signed_urls or cfg.storage_signed_urls_enabled:
            ui.warn("Signed URLs will grant each agent service account self-signing permission.")
        if not typer.confirm("Set up shared agent storage?"):
            ui.skip("aborted")
            raise typer.Exit(1)

    result = _guard(lambda: bootstrap.setup_storage(
        cfg,
        bucket=bucket,
        public_bucket=public_bucket,
        location=location,
        with_public=with_public,
        with_signed_urls=with_signed_urls,
    ))
    path = config_mod.save_config(cfg)
    ui.success(f"shared agent storage enabled ({path})")
    _print_storage_sync_summary(result)
    if brief_agent:
        for row in result.agents:
            _brief_agent_after_setup(cfg, row, "storage", force=True)


bucket_app.command("setup", hidden=True)(bucket_setup_cmd)


@bucket_app.command("sync")
def bucket_sync_cmd(
    agent: Optional[str] = typer.Argument(None, help="Optional agent name. Omit to sync all agents."),
    brief_agent: bool = typer.Option(False, "--brief-agent", help="Brief synced agents about storage after sync."),
):
    """Reapply per-agent storage IAM and Hermes env config to one or all agents."""
    cfg = _load()
    _require_ready(cfg)
    if not cfg.storage_bucket:
        ui.error("no storage bucket configured.")
        ui.warn("Run `ghosty-agents storage add` first.")
        raise typer.Exit(1)

    if agent:
        found = _guard(lambda: agents.get_agent(cfg, agent))
        if found is None:
            ui.error(f"agent '{agent}' not found.")
            raise typer.Exit(1)
        agent_items = [found]
    else:
        agent_items = _guard(lambda: agents.list_agents(cfg))

    result = _guard(lambda: bootstrap.sync_storage(cfg, agent_items))
    ui.success(f"storage sync finished for {result.bucket_uri}")
    _print_storage_sync_summary(result)
    if brief_agent:
        for row in result.agents:
            _brief_agent_after_setup(cfg, row, "storage", force=True)


@bucket_app.command("disable")
def bucket_disable_cmd(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    """Remove Ghosty agents' storage IAM/env config without deleting data."""
    cfg = _load()
    _require_ready(cfg)
    if not any([
        cfg.storage_enabled,
        cfg.storage_bucket,
        cfg.storage_public_enabled,
        cfg.storage_public_bucket,
        cfg.storage_signed_urls_enabled,
    ]):
        ui.skip("agent storage is already disabled")
        return

    bucket_name = bootstrap.storage_bucket_name(cfg)
    if not yes:
        ui.warn(f"This removes Ghosty service-account access to gs://{bucket_name}.")
        ui.warn("The bucket and all objects are left in place.")
        if not typer.confirm("Disable shared agent storage?"):
            ui.skip("aborted")
            raise typer.Exit(1)

    result = _guard(lambda: bootstrap.disable_storage(cfg))
    path = config_mod.save_config(cfg)
    ui.success(f"agent storage disabled ({path})")
    _print_storage_sync_summary(result)
    ui.warn(f"Bucket data was not deleted: {result.bucket_uri}")


# Register under the friendly name first; keep the old technical command hidden.
app.command(name="prepare")(bootstrap_cmd)
app.command(name="bootstrap", hidden=True)(bootstrap_cmd)


# --- up / create ----------------------------------------------------------

@app.command(name="create")
def up(
    name: str = typer.Argument(..., help="Agent name, e.g. worker-1."),
    startup_script: Optional[str] = typer.Option(None, "--startup-script", help="Path to a startup script to run on first boot."),
    with_hermes: bool = typer.Option(False, "--with-hermes", help="Install and configure Hermes after the VM is ready."),
    guided: bool = typer.Option(False, "--guided", help="Ask a friendly setup interview before creating the agent."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the cost confirmation."),
):
    """Create an agent."""
    cfg = _load()
    _require_ready(cfg)

    if guided:
        _guard(lambda: guided_mod.run_guided_setup(
            cfg,
            name=name,
            startup_script=startup_script,
            open_harness=ui.is_interactive_tty(),
        ))
        return

    # Preflight: billing must be active and the Compute API enabled, or the VM
    # create fails with a cryptic "Permission denied on locations/<zone>".
    from ghosty.doctor import preflight_create
    pf = _guard(lambda: preflight_create(cfg))
    if not _print_checks(pf):
        ui.console.print()
        ui.error("Can't create a VM until the above is fixed.")
        ui.warn("If billing is the issue, ensure your billing account is open and run")
        ui.warn("`ghosty-agents prepare` to (re)link it. For APIs, `prepare` enables them.")
        raise typer.Exit(1)

    # Authoritative zone check: a misconfigured zone (e.g. us-east1-a, which does
    # not exist) otherwise fails with a cryptic "Permission denied on locations".
    if not _guard(lambda: gcloud.exists(cfg, ["compute", "zones", "describe", cfg.zone])):
        ui.error(f"zone '{cfg.zone}' doesn't exist in region '{cfg.region}'.")
        valid = _guard(lambda: discover.zones_for_region(cfg, cfg.region))
        if valid:
            ui.warn(f"Valid zones in {cfg.region}: {', '.join(valid)}")
            ui.warn(f"Set one with: ghosty-agents settings set zone {valid[0]}")
        raise typer.Exit(1)

    if not yes:
        ui.warn(
            f"This conjures a billable VM ({cfg.machine_type}) in {cfg.zone}."
        )
        if not typer.confirm(f"Summon agent '{name}'?"):
            ui.skip("aborted — the spirit rests.")
            raise typer.Exit(1)
    ui.console.print(f"[magenta]{ui.GHOST}  Summoning '{name}'...[/]")
    created = _guard(lambda: agents.create_agent(cfg, name, startup_script=startup_script))
    ui.celebrate(f"Agent '{name}' has materialized!")
    if created and cfg.storage_enabled:
        _guard(lambda: bootstrap.sync_storage(cfg, [created]))
    if created and with_hermes:
        installed = _guard(lambda: hermes_mod.install_hermes(cfg, created))
        if installed and installed.installed:
            configured = _guard(lambda: hermes_mod.configure_hermes(cfg, created))
            if configured and configured.configured:
                ui.success("Hermes is installed and configured for Google models.")
        elif installed and installed.message:
            ui.warn(installed.message)
    ui.step(f"Connect with: ghosty-agents connect {name}")


app.command(name="up", hidden=True)(up)


# --- list -----------------------------------------------------------------

@app.command(name="agents")
def list_cmd():
    """Show the agent inventory (live from GCP)."""
    cfg = _load()
    _require_ready(cfg)
    found = _guard(lambda: agents.list_agents(cfg))
    if not found:
        ui.console.print(f"[dim]{ui.GHOST}  No ghosts haunting '{cfg.project_id}' yet.[/]")
        ui.step("Create one with `ghosty-agents create <name>`.")
        return
    running = sum(1 for a in found if a.status == "RUNNING")
    table = Table(title=f"{ui.GHOST} Ghost fleet — {cfg.project_id}  ({running}/{len(found)} awake)")
    table.add_column("NAME", style="bold")
    table.add_column("STATUS")
    table.add_column("ZONE")
    table.add_column("MACHINE")
    table.add_column("INTERNAL IP")
    table.add_column("CREATED")
    for a in found:
        status_style = "green" if a.status == "RUNNING" else "yellow"
        table.add_row(
            a.name,
            f"[{status_style}]{a.status}[/]",
            a.zone,
            a.machine_type,
            a.internal_ip or "-",
            (a.created or "")[:19].replace("T", " "),
        )
    ui.console.print(table)


app.command(name="list", hidden=True)(list_cmd)
app.command(name="ls", hidden=True)(list_cmd)


# --- status ---------------------------------------------------------------

@app.command(name="details")
def status(name: str = typer.Argument(..., help="Agent name.")):
    """Show details for one agent."""
    cfg = _load()
    _require_ready(cfg)
    agent = _guard(lambda: agents.get_agent(cfg, name))
    if agent is None:
        ui.error(f"agent '{name}' not found.")
        raise typer.Exit(1)
    table = Table(show_header=False, title=f"Agent: {agent.name}")
    table.add_column("field", style="bold cyan")
    table.add_column("value")
    table.add_row("instance", agent.instance)
    table.add_row("status", agent.status)
    table.add_row("zone", agent.zone)
    table.add_row("machine", agent.machine_type)
    table.add_row("internal IP", agent.internal_ip or "-")
    table.add_row("created", agent.created)
    ui.console.print(table)


# --- ssh ------------------------------------------------------------------

app.command(name="status", hidden=True)(status)


# --- instruct -------------------------------------------------------------

@app.command(name="instruct")
def instruct_cmd(
    name: str = typer.Argument(..., help="Agent name."),
    service: str = typer.Option(..., "--service", help="Service to brief: chat, notifications, storage, or models."),
    service_name: Optional[str] = typer.Option(None, "--name", help="Notification name when service=notifications."),
):
    """Send service setup instructions to Hermes on an agent."""
    cfg = _load()
    _require_ready(cfg)
    found = _guard(lambda: agents.get_agent(cfg, name))
    if found is None:
        ui.error(f"agent '{name}' not found.")
        raise typer.Exit(1)
    result = _guard(lambda: instructions.deliver_instruction(cfg, found, service, name=service_name))
    ui.console.print(f"Prompt path on agent: {result.remote_prompt_path}", soft_wrap=True)
    if result.message:
        ui.warn(result.message)
    if not result.delivered:
        raise typer.Exit(1)


@app.command(name="connect", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def ssh(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Agent name."),
):
    """Connect to an agent. Extra args after -- are passed to the remote shell."""
    cfg = _load()
    _require_ready(cfg)
    code = _guard(lambda: agents.ssh_agent(cfg, name, list(ctx.args)))
    raise typer.Exit(code)


app.command(name="ssh", hidden=True, context_settings={"allow_extra_args": True, "ignore_unknown_options": True})(ssh)


# --- start / stop ---------------------------------------------------------

@app.command()
def start(name: str = typer.Argument(..., help="Agent name.")):
    """Start a stopped agent."""
    cfg = _load()
    _require_ready(cfg)
    _guard(lambda: agents.start_agent(cfg, name))
    ui.success(f"agent '{name}' started")


@app.command()
def stop(name: str = typer.Argument(..., help="Agent name.")):
    """Stop an agent to save cost (keeps the disk)."""
    cfg = _load()
    _require_ready(cfg)
    _guard(lambda: agents.stop_agent(cfg, name))
    ui.success(f"agent '{name}' stopped")


# --- down / destroy -------------------------------------------------------

@app.command(name="remove")
def down(
    name: str = typer.Argument(..., help="Agent name."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    """Remove one agent."""
    cfg = _load()
    _require_ready(cfg)
    found = _guard(lambda: agents.get_agent(cfg, name))
    if not yes and not typer.confirm(f"Remove agent '{name}', its cloud identity, and attached resources?"):
        ui.skip("aborted")
        raise typer.Exit(1)
    _guard(lambda: _cleanup_agent_attached_resources(cfg, name, found))
    _guard(lambda: agents.destroy_agent(cfg, name))
    path = config_mod.save_config(cfg)
    ui.success(f"agent '{name}' removed")
    ui.skip(f"config saved to {path}")


app.command(name="down", hidden=True)(down)
app.command(name="destroy", hidden=True)(down)


# --- teardown -------------------------------------------------------------

@app.command(name="clean-up")
def teardown(
    all_agents: bool = typer.Option(False, "--all-agents", help="Also delete every agent VM + SA first."),
    delete_project: bool = typer.Option(False, "--delete-project", help="Delete the entire project (irreversible-ish; 30-day soft delete)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    """Remove shared project resources. Optionally agents and/or the project."""
    cfg = _load()
    _require_ready(cfg)

    if delete_project:
        if not yes and not typer.confirm(f"DELETE the entire project '{cfg.project_id}'?"):
            ui.skip("aborted")
            raise typer.Exit(1)
        _guard(lambda: gcloud.run(cfg, ["projects", "delete", cfg.project_id, "--quiet"], no_project=True))
        ui.success(f"project '{cfg.project_id}' scheduled for deletion")
        return

    if all_agents:
        found = _guard(lambda: agents.list_agents(cfg))
        if found and not yes and not typer.confirm(f"Delete {len(found)} agent(s)?"):
            ui.skip("aborted")
            raise typer.Exit(1)
        for a in found:
            _guard(lambda a=a: _cleanup_agent_attached_resources(cfg, a.name, a))
            _guard(lambda a=a: agents.destroy_agent(cfg, a.name))
        config_mod.save_config(cfg)

    if not yes and not typer.confirm("Delete shared network + firewall rules?"):
        ui.skip("aborted")
        raise typer.Exit(1)
    _guard(lambda: _teardown_shared(cfg))
    ui.success("shared resources removed")


app.command(name="teardown", hidden=True)(teardown)


def _teardown_shared(cfg: Config) -> None:
    bootstrap.disable_nat(cfg)
    bootstrap.delete_nat_router(cfg)
    for rule in (f"{cfg.network}-allow-ssh-from-iap", f"{cfg.network}-deny-all-ingress"):
        if gcloud.exists(cfg, ["compute", "firewall-rules", "describe", rule]):
            ui.step(f"Deleting firewall '{rule}'")
            gcloud.run(cfg, ["compute", "firewall-rules", "delete", rule, "--quiet"])
    if gcloud.exists(cfg, ["compute", "networks", "subnets", "describe", cfg.subnet, f"--region={cfg.region}"]):
        ui.step(f"Deleting subnet '{cfg.subnet}'")
        gcloud.run(cfg, ["compute", "networks", "subnets", "delete", cfg.subnet, f"--region={cfg.region}", "--quiet"])
    if gcloud.exists(cfg, ["compute", "networks", "describe", cfg.network]):
        ui.step(f"Deleting VPC '{cfg.network}'")
        gcloud.run(cfg, ["compute", "networks", "delete", cfg.network, "--quiet"])


# --- config show/set ------------------------------------------------------

@config_app.command("show")
def config_show():
    """Print the current stored config."""
    cfg = _load()
    if not config_mod.config_exists():
        ui.warn(f"no config yet at {config_mod.config_path()}. Run `ghosty-agents init`.")
        raise typer.Exit(1)
    table = Table(title=str(config_mod.config_path()), show_header=False)
    table.add_column("key", style="bold cyan")
    table.add_column("value")
    for k, v in cfg.to_dict().items():
        table.add_row(k, str(v))
    ui.console.print(table)


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Config key, e.g. machine_type."),
    value: str = typer.Argument(..., help="New value."),
):
    """Set a single config value."""
    cfg = _load()
    if key not in cfg.to_dict():
        ui.error(f"unknown config key '{key}'. Use `ghosty-agents config show` to list keys.")
        raise typer.Exit(1)
    setattr(cfg, key, value)
    path = config_mod.save_config(cfg)
    ui.success(f"set {key} = {value} ({path})")


if __name__ == "__main__":
    app()
