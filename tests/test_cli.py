"""Tests for CLI command wiring."""

from typer.testing import CliRunner

from ghosty import cli
from ghosty.models import Agent, Config


def test_root_no_args_opens_interactive_on_tty(monkeypatch):
    calls = []

    monkeypatch.setattr(cli.ui, "is_interactive_tty", lambda: True)
    monkeypatch.setattr(cli.interactive, "run", lambda: calls.append("run"))

    result = CliRunner().invoke(cli.app, [])

    assert result.exit_code == 0
    assert calls == ["run"]


def test_root_no_args_non_tty_shows_friendly_help(monkeypatch):
    monkeypatch.setattr(cli.ui, "is_interactive_tty", lambda: False)

    result = CliRunner().invoke(cli.app, [])

    assert result.exit_code == 0
    assert "connect" in result.output
    assert "notifications" in result.output
    assert "storage" in result.output
    assert " ssh " not in result.output
    assert "webhook" not in result.output


def test_console_command_opens_interactive(monkeypatch):
    calls = []

    monkeypatch.setattr(cli.interactive, "run", lambda: calls.append("run"))

    result = CliRunner().invoke(cli.app, ["console"])

    assert result.exit_code == 0
    assert calls == ["run"]


def test_bootstrap_with_nat_passes_flag(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    calls = {}

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.bootstrap, "ensure_isolated_config", lambda _cfg: None)

    def fake_bootstrap_all(_cfg, project_name=None, *, with_nat=False):
        calls["project_name"] = project_name
        calls["with_nat"] = with_nat

    monkeypatch.setattr(cli.bootstrap, "bootstrap_all", fake_bootstrap_all)

    result = CliRunner().invoke(cli.app, ["bootstrap", "--with-nat"])

    assert result.exit_code == 0
    assert calls == {"project_name": None, "with_nat": True}


def test_prepare_alias_passes_bootstrap_flag(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    calls = {}

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.bootstrap, "ensure_isolated_config", lambda _cfg: None)

    def fake_bootstrap_all(_cfg, project_name=None, *, with_nat=False):
        calls["project_name"] = project_name
        calls["with_nat"] = with_nat

    monkeypatch.setattr(cli.bootstrap, "bootstrap_all", fake_bootstrap_all)

    result = CliRunner().invoke(cli.app, ["prepare", "--with-nat"])

    assert result.exit_code == 0
    assert calls == {"project_name": None, "with_nat": True}


def test_connect_alias_and_ssh_compat_dispatch(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    calls = []

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.agents, "ssh_agent", lambda _cfg, name, extra=None: calls.append((name, extra)) or 0)

    result = CliRunner().invoke(cli.app, ["connect", "alba-nury", "--", "uptime"])
    compat = CliRunner().invoke(cli.app, ["ssh", "alba-nury", "--", "date"])

    assert result.exit_code == 0
    assert compat.exit_code == 0
    assert calls == [("alba-nury", ["uptime"]), ("alba-nury", ["date"])]


def test_instruct_command_delivers_prompt(monkeypatch, tmp_path):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    agent = Agent(
        name="alba-nury",
        instance="ghosty-alba-nury",
        status="RUNNING",
        zone="us-east1-b",
        machine_type="e2-small",
    )
    calls = []

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.agents, "get_agent", lambda _cfg, name: agent if name == "alba-nury" else None)
    monkeypatch.setattr(
        cli.instructions,
        "deliver_instruction",
        lambda _cfg, selected, service, **kw: calls.append((selected.name, service, kw)) or cli.instructions.InstructionDeliveryResult(
            agent=selected.name,
            service=service,
            remote_prompt_path="~/.config/hermes/inbox/notifications-setup.md",
            local_prompt_path=tmp_path / "notifications-setup.md",
            uploaded=True,
            delivered=True,
        ),
    )

    result = CliRunner().invoke(cli.app, ["instruct", "alba-nury", "--service", "notifications", "--name", "crm"])

    assert result.exit_code == 0
    assert calls == [("alba-nury", "notifications", {"name": "crm"})]
    assert "~/.config/hermes/inbox/notifications-setup.md" in result.output


def test_hermes_install_configure_status_and_sync_commands(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    agent = Agent(
        name="alba-nury",
        instance="ghosty-alba-nury",
        status="RUNNING",
        zone="us-east1-b",
        machine_type="e2-small",
    )
    calls = []

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.agents, "get_agent", lambda _cfg, name: agent if name == "alba-nury" else None)
    monkeypatch.setattr(
        cli.hermes_mod,
        "install_hermes",
        lambda _cfg, selected, **kw: calls.append(("install", selected.name, kw)) or cli.hermes_mod.HermesInstallResult(
            agent=selected.name,
            installed=True,
            gateway_started=True,
        ),
    )
    monkeypatch.setattr(
        cli.hermes_mod,
        "configure_hermes",
        lambda _cfg, selected, **kw: calls.append(("configure", selected.name, kw)) or cli.hermes_mod.HermesConfigureResult(
            agent=selected.name,
            provider=kw.get("provider", "vertex"),
            model=kw.get("model", "google/gemini-3.1-pro-preview"),
            vertex_project="proj",
            vertex_region="global",
            configured=True,
        ),
    )
    monkeypatch.setattr(
        cli.hermes_mod,
        "hermes_status",
        lambda _cfg, selected: calls.append(("status", selected.name, {})) or cli.hermes_mod.HermesStatus(
            agent=selected.name,
            installed=True,
            command_exists=True,
            env_exists=True,
            config_exists=True,
            gateway_active=True,
            provider="vertex",
            model="google/gemini-3.1-pro-preview",
            vertex_project="proj",
            vertex_region="global",
            version="hermes 1.2.3",
        ),
    )
    monkeypatch.setattr(
        cli.hermes_mod,
        "sync_hermes",
        lambda _cfg, selected, **kw: calls.append(("sync", selected.name, kw)) or cli.hermes_mod.HermesStatus(
            agent=selected.name,
            installed=True,
            command_exists=True,
            env_exists=True,
            config_exists=True,
            gateway_active=True,
            provider=kw.get("provider", "vertex"),
            model=kw.get("model", "google/gemini-3.1-pro-preview"),
            vertex_project="proj",
            vertex_region="global",
        ),
    )

    install = CliRunner().invoke(cli.app, ["hermes", "install", "alba-nury", "--yes", "--branch", "release", "--commit", "abc123"])
    configure = CliRunner().invoke(cli.app, ["hermes", "configure", "alba-nury", "--provider", "vertex", "--model", "google/gemini"])
    status = CliRunner().invoke(cli.app, ["hermes", "status", "alba-nury"])
    sync = CliRunner().invoke(cli.app, ["hermes", "sync", "alba-nury", "--yes"])

    assert install.exit_code == 0
    assert configure.exit_code == 0
    assert status.exit_code == 0
    assert sync.exit_code == 0
    assert calls[0] == ("install", "alba-nury", {"branch": "release", "commit": "abc123", "skip_browser": False})
    assert calls[1] == ("configure", "alba-nury", {"provider": "vertex", "model": "google/gemini"})
    assert calls[2] == ("status", "alba-nury", {})
    assert calls[3][0] == "sync"
    assert "google/gemini-3.1-pro-preview" in sync.output


def test_agents_and_details_aliases_dispatch(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    agent = Agent(
        name="alba-nury",
        instance="ghosty-alba-nury",
        status="RUNNING",
        zone="us-east1-b",
        machine_type="e2-small",
        internal_ip="10.10.0.2",
    )

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.agents, "list_agents", lambda _cfg: [agent])
    monkeypatch.setattr(cli.agents, "get_agent", lambda _cfg, name: agent if name == "alba-nury" else None)

    agents_result = CliRunner().invoke(cli.app, ["agents"])
    list_result = CliRunner().invoke(cli.app, ["list"])
    details_result = CliRunner().invoke(cli.app, ["details", "alba-nury"])
    status_result = CliRunner().invoke(cli.app, ["status", "alba-nury"])

    assert agents_result.exit_code == 0
    assert list_result.exit_code == 0
    assert details_result.exit_code == 0
    assert status_result.exit_code == 0
    assert "alba-nury" in agents_result.output
    assert "ghosty-alba-nury" in details_result.output


def test_create_with_hermes_installs_configures_and_syncs_storage(monkeypatch):
    cfg = Config(
        project_id="proj",
        account="me@example.com",
        billing_account_id="billing",
        storage_enabled=True,
        storage_bucket="agent-bucket",
    )
    created = Agent(
        name="alba-nury",
        instance="ghosty-alba-nury",
        status="RUNNING",
        zone="us-east1-b",
        machine_type="e2-small",
    )
    calls = []

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr("ghosty.doctor.preflight_create", lambda _cfg: [])
    monkeypatch.setattr(cli.gcloud, "exists", lambda _cfg, args: True)
    monkeypatch.setattr(
        cli.agents,
        "create_agent",
        lambda _cfg, name, **kw: calls.append(("create", name, kw)) or created,
    )
    monkeypatch.setattr(
        cli.bootstrap,
        "sync_storage",
        lambda _cfg, items: calls.append(("storage", [item.name for item in items], {})) or cli.bootstrap.StorageSetupResult(
            bucket="agent-bucket",
            bucket_uri="gs://agent-bucket",
            location="us-east1",
            storage_class=cfg.storage_class,
            role=cfg.storage_role,
            agents=[],
        ),
    )
    monkeypatch.setattr(
        cli.hermes_mod,
        "install_hermes",
        lambda _cfg, agent, **kw: calls.append(("install", agent.name, kw)) or cli.hermes_mod.HermesInstallResult(
            agent=agent.name,
            installed=True,
            gateway_started=True,
        ),
    )
    monkeypatch.setattr(
        cli.hermes_mod,
        "configure_hermes",
        lambda _cfg, agent, **kw: calls.append(("configure", agent.name, kw)) or cli.hermes_mod.HermesConfigureResult(
            agent=agent.name,
            provider="vertex",
            model="google/gemini-3.1-pro-preview",
            vertex_project="proj",
            vertex_region="global",
            configured=True,
        ),
    )

    result = CliRunner().invoke(cli.app, ["create", "alba-nury", "--with-hermes", "--yes"])

    assert result.exit_code == 0
    assert calls == [
        ("create", "alba-nury", {"startup_script": None}),
        ("storage", ["alba-nury"], {}),
        ("install", "alba-nury", {}),
        ("configure", "alba-nury", {}),
    ]
    assert "Hermes is installed and configured" in result.output


def test_create_guided_dispatches_to_guided_flow(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    calls = []

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.ui, "is_interactive_tty", lambda: False)
    monkeypatch.setattr(
        cli.guided_mod,
        "run_guided_setup",
        lambda selected, **kw: calls.append((selected, kw)),
    )

    result = CliRunner().invoke(cli.app, ["create", "alba-nury", "--guided", "--startup-script", "/tmp/start.sh"])

    assert result.exit_code == 0
    assert calls == [(
        cfg,
        {
            "name": "alba-nury",
            "startup_script": "/tmp/start.sh",
            "open_harness": False,
        },
    )]


def test_remove_agent_cleans_attached_resources_before_vm_and_sa(monkeypatch):
    cfg = Config(
        project_id="proj",
        account="me@example.com",
        billing_account_id="billing",
        google_ai_enabled=True,
        storage_bucket="agent-bucket",
        storage_public_bucket="public-bucket",
        google_chat_projects={"mira-sol": "ghosty-mira-sol-chat"},
        webhook_gateways={"mira-sol": {"intake": {"provider": "generic"}}},
    )
    agent = Agent(
        name="mira-sol",
        instance="ghosty-mira-sol",
        status="RUNNING",
        zone="us-east1-b",
        machine_type="e2-small",
    )
    calls = []
    saved = []

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.agents, "get_agent", lambda _cfg, name: agent if name == "mira-sol" else None)
    monkeypatch.setattr(cli.bootstrap, "webhook_names", lambda _cfg, name: ["intake"] if name == "mira-sol" else [])
    monkeypatch.setattr(cli.bootstrap, "destroy_webhook_gateway", lambda _cfg, agent_name, **kw: calls.append(("webhook", agent_name, kw)))
    monkeypatch.setattr(cli.bootstrap, "destroy_google_chat_gateway", lambda _cfg, agent_name: calls.append(("chat", agent_name, {})))
    monkeypatch.setattr(cli.bootstrap, "cleanup_storage_for_agent", lambda _cfg, selected: calls.append(("storage", selected.name, {})))
    monkeypatch.setattr(cli.bootstrap, "remove_google_ai_from_agent", lambda _cfg, name: calls.append(("models", name, {})))
    monkeypatch.setattr(cli.agents, "destroy_agent", lambda _cfg, name: calls.append(("destroy", name, {})))
    monkeypatch.setattr(cli.config_mod, "save_config", lambda config: saved.append(config) or "/tmp/config.toml")
    local_instructions = cli.config_mod.config_dir() / "instructions" / "mira-sol"
    local_chat = cli.config_mod.config_dir() / "google-chat" / "mira-sol"
    local_instructions.mkdir(parents=True)
    local_chat.mkdir(parents=True)
    (local_instructions / "notifications-setup.md").write_text("prompt", encoding="utf-8")
    (local_chat / "service-account.json").write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(cli.app, ["remove", "mira-sol", "--yes"])

    assert result.exit_code == 0
    assert calls == [
        ("webhook", "mira-sol", {"name": "intake"}),
        ("chat", "mira-sol", {}),
        ("storage", "mira-sol", {}),
        ("models", "mira-sol", {}),
        ("destroy", "mira-sol", {}),
    ]
    assert saved == [cfg]
    assert "Removing notifications" in result.output
    assert not local_instructions.exists()
    assert not local_chat.exists()


def test_google_ai_status_command(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(
        cli.bootstrap,
        "google_ai_status",
        lambda _cfg: cli.bootstrap.GoogleAiStatus(
            api_enabled=True,
            auto_grant_enabled=False,
            api="aiplatform.googleapis.com",
            role="roles/aiplatform.user",
            agents=[
                cli.bootstrap.GoogleAiAgentStatus(
                    agent="worker-1",
                    service_account="ghosty-worker-1-sa@proj.iam.gserviceaccount.com",
                    has_role=True,
                )
            ],
        ),
    )

    result = CliRunner().invoke(cli.app, ["google-ai", "status"])

    assert result.exit_code == 0
    assert "aiplatform.googleapis.com" in result.output
    assert "worker-1" in result.output


def test_google_ai_enable_command_saves_config(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    saved = []

    def fake_enable_google_ai(config):
        config.google_ai_enabled = True

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.bootstrap, "enable_google_ai", fake_enable_google_ai)
    monkeypatch.setattr(cli.config_mod, "save_config", lambda config: saved.append(config) or "/tmp/config.toml")

    result = CliRunner().invoke(cli.app, ["google-ai", "enable", "--yes"])

    assert result.exit_code == 0
    assert cfg.google_ai_enabled
    assert saved == [cfg]


def test_google_ai_disable_iam_command_saves_config(monkeypatch):
    cfg = Config(
        project_id="proj",
        account="me@example.com",
        billing_account_id="billing",
        google_ai_enabled=True,
    )
    saved = []

    def fake_disable_google_ai_iam(config):
        config.google_ai_enabled = False

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.bootstrap, "disable_google_ai_iam", fake_disable_google_ai_iam)
    monkeypatch.setattr(cli.config_mod, "save_config", lambda config: saved.append(config) or "/tmp/config.toml")

    result = CliRunner().invoke(cli.app, ["google-ai", "disable-iam", "--yes"])

    assert result.exit_code == 0
    assert not cfg.google_ai_enabled
    assert saved == [cfg]


def test_google_chat_status_command(monkeypatch, tmp_path):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    resources = cli.bootstrap.GoogleChatResources(
        agent="alba-nury",
        slug="alba-nury",
        chat_project="ghosty-agent-chat",
        topic="alba-nury-chat-events",
        subscription="alba-nury-chat-events-sub",
        service_account_id="alba-nury-chat-gw-sa",
        service_account_email="alba-nury-chat-gw-sa@ghosty-agent-chat.iam.gserviceaccount.com",
        full_topic="projects/ghosty-agent-chat/topics/alba-nury-chat-events",
        full_subscription="projects/ghosty-agent-chat/subscriptions/alba-nury-chat-events-sub",
        local_key_path=tmp_path / "service-account.json",
    )

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(
        cli.bootstrap,
        "google_chat_status",
            lambda _cfg, _agent, chat_project=None: cli.bootstrap.GoogleChatStatus(
                resources=resources,
                chat_project_exists=True,
                chat_api_enabled=True,
                pubsub_api_enabled=True,
            topic_exists=True,
            subscription_exists=True,
            service_account_exists=True,
            topic_publisher_bound=True,
            subscription_subscriber_bound=True,
            subscription_viewer_bound=True,
            local_key_exists=True,
        ),
    )

    result = CliRunner().invoke(cli.app, ["google-chat", "status", "alba-nury"])

    assert result.exit_code == 0
    assert "alba-nury-chat-events" in result.output
    assert "ghosty-agent-chat" in result.output
    assert "projects/ghosty-agent-chat/topics/alba-nury-chat-events" in result.output


def test_google_chat_setup_command(monkeypatch, tmp_path):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    resources = cli.bootstrap.GoogleChatResources(
        agent="alba-nury",
        slug="alba-nury",
        chat_project="ghosty-agent-chat",
        topic="alba-nury-chat-events",
        subscription="alba-nury-chat-events-sub",
        service_account_id="alba-nury-chat-gw-sa",
        service_account_email="alba-nury-chat-gw-sa@ghosty-agent-chat.iam.gserviceaccount.com",
        full_topic="projects/ghosty-agent-chat/topics/alba-nury-chat-events",
        full_subscription="projects/ghosty-agent-chat/subscriptions/alba-nury-chat-events-sub",
        local_key_path=tmp_path / "service-account.json",
    )
    calls = []
    saved = []

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.agents, "get_agent", lambda _cfg, agent: object() if agent == "alba-nury" else None)
    monkeypatch.setattr(cli.bootstrap, "google_chat_resources", lambda _cfg, _agent, chat_project=None: resources)

    def fake_ensure_google_chat_gateway(_cfg, agent, **kwargs):
        calls.append((agent, kwargs))
        _cfg.google_chat_projects["alba-nury"] = kwargs["chat_project"]
        return resources

    monkeypatch.setattr(cli.bootstrap, "ensure_google_chat_gateway", fake_ensure_google_chat_gateway)
    monkeypatch.setattr(cli.config_mod, "save_config", lambda config: saved.append(config) or "/tmp/config.toml")

    result = CliRunner().invoke(cli.app, [
        "google-chat", "setup", "alba-nury",
        "--chat-project", "ghosty-agent-chat",
        "--no-create-project",
        "--yes",
    ])

    assert result.exit_code == 0
    assert calls == [("alba-nury", {
        "chat_project": "ghosty-agent-chat",
        "folder_id": None,
        "billing_account_id": None,
        "create_project": False,
    })]
    assert saved == [cfg]
    assert "Cloud Pub/Sub" in result.output
    assert "projects/ghosty-agent-chat/topics/alba-nury-chat-events" in result.output
    assert "projects/ghosty-agent-chat/subscriptions/alba-nury-chat-events-sub" in result.output


def test_google_chat_setup_requires_existing_agent(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.agents, "get_agent", lambda _cfg, _agent: None)

    result = CliRunner().invoke(cli.app, ["google-chat", "setup", "missing", "--yes"])

    assert result.exit_code == 1
    assert "not found" in result.output


def test_google_chat_setup_can_brief_agent(monkeypatch, tmp_path):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    found = Agent(
        name="alba-nury",
        instance="ghosty-alba-nury",
        status="RUNNING",
        zone="us-east1-b",
        machine_type="e2-small",
    )
    resources = cli.bootstrap.GoogleChatResources(
        agent="alba-nury",
        slug="alba-nury",
        chat_project="ghosty-agent-chat",
        topic="alba-nury-chat-events",
        subscription="alba-nury-chat-events-sub",
        service_account_id="alba-nury-chat-gw-sa",
        service_account_email="alba-nury-chat-gw-sa@ghosty-agent-chat.iam.gserviceaccount.com",
        full_topic="projects/ghosty-agent-chat/topics/alba-nury-chat-events",
        full_subscription="projects/ghosty-agent-chat/subscriptions/alba-nury-chat-events-sub",
        local_key_path=tmp_path / "service-account.json",
    )
    delivered = []

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.agents, "get_agent", lambda _cfg, agent: found if agent == "alba-nury" else None)
    monkeypatch.setattr(cli.bootstrap, "google_chat_resources", lambda _cfg, _agent, chat_project=None: resources)
    monkeypatch.setattr(cli.bootstrap, "ensure_google_chat_gateway", lambda *_args, **_kw: resources)
    monkeypatch.setattr(cli.config_mod, "save_config", lambda config: "/tmp/config.toml")
    monkeypatch.setattr(
        cli.instructions,
        "deliver_instruction",
        lambda _cfg, agent, service, **kw: delivered.append((agent.name, service, kw)) or cli.instructions.InstructionDeliveryResult(
            agent=agent.name,
            service=service,
            remote_prompt_path="~/.config/hermes/inbox/chat-setup.md",
            local_prompt_path=tmp_path / "chat-setup.md",
            uploaded=True,
            delivered=True,
        ),
    )

    result = CliRunner().invoke(cli.app, [
        "chat", "add", "alba-nury",
        "--chat-project", "ghosty-agent-chat",
        "--yes",
        "--brief-agent",
    ])

    assert result.exit_code == 0
    assert delivered == [("alba-nury", "chat", {"name": None})]


def test_google_chat_setup_brief_failure_does_not_fail_setup(monkeypatch, tmp_path):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    found = Agent(
        name="alba-nury",
        instance="ghosty-alba-nury",
        status="RUNNING",
        zone="us-east1-b",
        machine_type="e2-small",
    )
    resources = cli.bootstrap.GoogleChatResources(
        agent="alba-nury",
        slug="alba-nury",
        chat_project="ghosty-agent-chat",
        topic="alba-nury-chat-events",
        subscription="alba-nury-chat-events-sub",
        service_account_id="alba-nury-chat-gw-sa",
        service_account_email="alba-nury-chat-gw-sa@ghosty-agent-chat.iam.gserviceaccount.com",
        full_topic="projects/ghosty-agent-chat/topics/alba-nury-chat-events",
        full_subscription="projects/ghosty-agent-chat/subscriptions/alba-nury-chat-events-sub",
        local_key_path=tmp_path / "service-account.json",
    )

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.agents, "get_agent", lambda _cfg, agent: found if agent == "alba-nury" else None)
    monkeypatch.setattr(cli.bootstrap, "google_chat_resources", lambda _cfg, _agent, chat_project=None: resources)
    monkeypatch.setattr(cli.bootstrap, "ensure_google_chat_gateway", lambda *_args, **_kw: resources)
    monkeypatch.setattr(cli.config_mod, "save_config", lambda config: "/tmp/config.toml")
    monkeypatch.setattr(
        cli.instructions,
        "deliver_instruction",
        lambda _cfg, agent, service, **kw: cli.instructions.InstructionDeliveryResult(
            agent=agent.name,
            service=service,
            remote_prompt_path="~/.config/hermes/inbox/chat-setup.md",
            local_prompt_path=tmp_path / "chat-setup.md",
            uploaded=True,
            delivered=False,
            message="hermes failed",
        ),
    )

    result = CliRunner().invoke(cli.app, [
        "chat", "add", "alba-nury",
        "--chat-project", "ghosty-agent-chat",
        "--yes",
        "--brief-agent",
    ])

    assert result.exit_code == 0
    assert "Google Chat gateway resources are ready" in result.output


def test_google_chat_destroy_command(monkeypatch, tmp_path):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    resources = cli.bootstrap.GoogleChatResources(
        agent="alba-nury",
        slug="alba-nury",
        chat_project="ghosty-agent-chat",
        topic="alba-nury-chat-events",
        subscription="alba-nury-chat-events-sub",
        service_account_id="alba-nury-chat-gw-sa",
        service_account_email="alba-nury-chat-gw-sa@ghosty-agent-chat.iam.gserviceaccount.com",
        full_topic="projects/ghosty-agent-chat/topics/alba-nury-chat-events",
        full_subscription="projects/ghosty-agent-chat/subscriptions/alba-nury-chat-events-sub",
        local_key_path=tmp_path / "service-account.json",
    )
    calls = []
    saved = []
    cfg.google_chat_projects["alba-nury"] = "ghosty-agent-chat"

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.bootstrap, "google_chat_resources", lambda _cfg, _agent, chat_project=None: resources)

    def fake_destroy_google_chat_gateway(_cfg, agent, chat_project=None):
        calls.append((agent, chat_project))
        _cfg.google_chat_projects.pop("alba-nury", None)
        return resources

    monkeypatch.setattr(cli.bootstrap, "destroy_google_chat_gateway", fake_destroy_google_chat_gateway)
    monkeypatch.setattr(cli.config_mod, "save_config", lambda config: saved.append(config) or "/tmp/config.toml")

    result = CliRunner().invoke(cli.app, [
        "google-chat", "destroy", "alba-nury",
        "--chat-project", "ghosty-agent-chat",
        "--yes",
    ])

    assert result.exit_code == 0
    assert calls == [("alba-nury", "ghosty-agent-chat")]
    assert saved == [cfg]
    assert cfg.google_chat_projects == {}
    assert "left untouched" in result.output


def _webhook_resources():
    return cli.bootstrap.WebhookResources(
        agent="alba-nury",
        slug="alba-nury",
        name="github",
        name_slug="github",
        provider="generic",
        service_name="ghosty-alba-nury-webhook-github",
        topic="alba-nury-webhook-github-events",
        subscription="alba-nury-webhook-github-events-sub",
        full_topic="projects/proj/topics/alba-nury-webhook-github-events",
        full_subscription="projects/proj/subscriptions/alba-nury-webhook-github-events-sub",
        run_service_account_id="alba-nury-github-wh-run-sa",
        run_service_account_email="alba-nury-github-wh-run-sa@proj.iam.gserviceaccount.com",
        agent_service_account_email="ghosty-alba-nury-sa@proj.iam.gserviceaccount.com",
        env_path="~/.config/hermes/webhooks/github.env",
    )


def test_webhook_setup_command_saves_config(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    resources = _webhook_resources()
    found = object()
    calls = []
    saved = []

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.agents, "get_agent", lambda _cfg, agent: found if agent == "alba-nury" else None)
    monkeypatch.setattr(cli.bootstrap, "webhook_resources", lambda _cfg, _agent, _name, _provider=None: resources)

    def fake_ensure_webhook_gateway(_cfg, agent, **kwargs):
        calls.append((agent, kwargs))
        _cfg.webhook_gateways["alba-nury"] = {"github": {"provider": "generic", "secret": kwargs["secret"]}}
        return cli.bootstrap.WebhookSetupResult(
            resources=resources,
            service_url="https://hook.example.run.app",
            secret=kwargs["secret"],
            vm_env_updated=True,
        )

    monkeypatch.setattr(cli.bootstrap, "ensure_webhook_gateway", fake_ensure_webhook_gateway)
    monkeypatch.setattr(cli.config_mod, "save_config", lambda config: saved.append(config) or "/tmp/config.toml")

    result = CliRunner().invoke(cli.app, [
        "webhook", "setup", "alba-nury",
        "--name", "github",
        "--secret", "topsecret",
        "--yes",
    ])

    assert result.exit_code == 0
    assert calls == [(found, {
        "name": "github",
        "secret": "topsecret",
        "generate_secret": False,
    })]
    assert saved == [cfg]
    assert "https://hook.example.run.app" in result.output
    assert "projects/proj/subscriptions/alba-nury-webhook-github-events-sub" in result.output


def test_webhook_setup_with_consumer_installs_vm_consumer(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    resources = _webhook_resources()
    found = object()
    consumer_calls = []

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.agents, "get_agent", lambda _cfg, agent: found if agent == "alba-nury" else None)
    monkeypatch.setattr(cli.bootstrap, "webhook_resources", lambda _cfg, _agent, _name, _provider=None: resources)
    monkeypatch.setattr(cli.config_mod, "save_config", lambda config: "/tmp/config.toml")
    monkeypatch.setattr(
        cli.bootstrap,
        "ensure_webhook_gateway",
        lambda _cfg, agent, **kw: cli.bootstrap.WebhookSetupResult(
            resources=resources,
            service_url="https://hook.example.run.app",
            secret="topsecret",
            vm_env_updated=True,
        ),
    )
    monkeypatch.setattr(
        cli.bootstrap,
        "install_webhook_consumer",
        lambda _cfg, agent, selected: consumer_calls.append((agent, selected)) or cli.bootstrap.WebhookConsumerResult(
            resources=selected,
            script_path="~/.local/bin/ghosty-github-consumer",
            service_name="ghosty-github-consumer.service",
            installed=True,
            active=True,
        ),
    )

    result = CliRunner().invoke(cli.app, [
        "notifications", "add", "alba-nury",
        "--name", "github",
        "--secret", "topsecret",
        "--with-consumer",
        "--yes",
    ])

    assert result.exit_code == 0
    assert consumer_calls == [(found, resources)]
    assert "Hermes event consumer is running" in result.output


def test_webhook_setup_command_prompts_for_missing_inputs(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    resources = _webhook_resources()
    found = object()
    calls = []

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.agents, "get_agent", lambda _cfg, agent: found if agent == "alba-nury" else None)
    monkeypatch.setattr(cli.bootstrap, "webhook_resources", lambda _cfg, _agent, _name, _provider=None: resources)
    monkeypatch.setattr(cli.config_mod, "save_config", lambda config: "/tmp/config.toml")

    def fake_ensure_webhook_gateway(_cfg, agent, **kwargs):
        calls.append((agent, kwargs))
        return cli.bootstrap.WebhookSetupResult(
            resources=resources,
            service_url="https://hook.example.run.app",
            secret="generated-secret",
            vm_env_updated=True,
        )

    monkeypatch.setattr(cli.bootstrap, "ensure_webhook_gateway", fake_ensure_webhook_gateway)

    result = CliRunner().invoke(
        cli.app,
        ["webhook", "setup", "alba-nury"],
        input="github\ny\ny\nn\n",
    )

    assert result.exit_code == 0
    assert calls == [(found, {
        "name": "github",
        "secret": None,
        "generate_secret": True,
    })]
    assert "What kind of webhook is this?" not in result.output
    assert "Webhook name" in result.output


def test_webhook_setup_yes_uses_defaults_without_prompts(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    resources = cli.bootstrap.WebhookResources(
        agent="alba-nury",
        slug="alba-nury",
        name="generic",
        name_slug="generic",
        provider="generic",
        service_name="ghosty-alba-nury-webhook-generic",
        topic="alba-nury-webhook-generic-events",
        subscription="alba-nury-webhook-generic-events-sub",
        full_topic="projects/proj/topics/alba-nury-webhook-generic-events",
        full_subscription="projects/proj/subscriptions/alba-nury-webhook-generic-events-sub",
        run_service_account_id="alba-nury-generic-wh-run-sa",
        run_service_account_email="alba-nury-generic-wh-run-sa@proj.iam.gserviceaccount.com",
        agent_service_account_email="ghosty-alba-nury-sa@proj.iam.gserviceaccount.com",
        env_path="~/.config/hermes/webhooks/generic.env",
    )
    found = object()
    calls = []

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.agents, "get_agent", lambda _cfg, agent: found if agent == "alba-nury" else None)
    monkeypatch.setattr(cli.bootstrap, "webhook_resources", lambda _cfg, _agent, _name, _provider=None: resources)
    monkeypatch.setattr(cli.config_mod, "save_config", lambda config: "/tmp/config.toml")

    def fake_ensure_webhook_gateway(_cfg, agent, **kwargs):
        calls.append((agent, kwargs))
        return cli.bootstrap.WebhookSetupResult(
            resources=resources,
            service_url="https://hook.example.run.app",
            secret="generated-secret",
            vm_env_updated=True,
        )

    monkeypatch.setattr(cli.bootstrap, "ensure_webhook_gateway", fake_ensure_webhook_gateway)

    result = CliRunner().invoke(cli.app, ["webhook", "setup", "alba-nury", "--yes"])

    assert result.exit_code == 0
    assert calls == [(found, {
        "name": "webhook",
        "secret": None,
        "generate_secret": False,
    })]
    assert "What kind of webhook is this?" not in result.output


def test_webhook_status_command(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    resources = _webhook_resources()
    found = object()

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.agents, "get_agent", lambda _cfg, agent: found if agent == "alba-nury" else None)
    monkeypatch.setattr(
        cli.bootstrap,
        "webhook_status",
        lambda _cfg, _agent, name: cli.bootstrap.WebhookStatus(
            resources=resources,
            run_api_enabled=True,
            artifactregistry_api_enabled=True,
            cloudbuild_api_enabled=True,
            pubsub_api_enabled=True,
            iam_api_enabled=True,
            topic_exists=True,
            subscription_exists=True,
            run_service_account_exists=True,
            service_exists=True,
            publisher_bound=True,
            subscriber_bound=True,
            viewer_bound=True,
            vm_env_exists=True,
            service_url="https://hook.example.run.app",
            secret_configured=True,
        ),
    )

    result = CliRunner().invoke(cli.app, ["webhook", "status", "alba-nury", "--name", "github"])

    assert result.exit_code == 0
    assert "github" in result.output
    assert "https://hook.example.run.app" in result.output
    assert "projects/proj/subscriptions/alba-nury-webhook-github-events-sub" in result.output


def test_webhook_sync_command_saves_config(monkeypatch):
    cfg = Config(
        project_id="proj",
        account="me@example.com",
        billing_account_id="billing",
        webhook_gateways={"alba-nury": {"github": {"provider": "generic", "secret": "topsecret"}}},
    )
    resources = _webhook_resources()
    found = object()
    calls = []
    saved = []

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.agents, "get_agent", lambda _cfg, agent: found if agent == "alba-nury" else None)

    def fake_sync_webhook_gateway(_cfg, agent, *, name):
        calls.append((agent, name))
        return cli.bootstrap.WebhookSetupResult(
            resources=resources,
            service_url="https://hook.example.run.app",
            secret="topsecret",
            vm_env_updated=True,
        )

    monkeypatch.setattr(cli.bootstrap, "sync_webhook_gateway", fake_sync_webhook_gateway)
    monkeypatch.setattr(cli.config_mod, "save_config", lambda config: saved.append(config) or "/tmp/config.toml")

    result = CliRunner().invoke(cli.app, ["webhook", "sync", "alba-nury", "--name", "github"])

    assert result.exit_code == 0
    assert calls == [(found, "github")]
    assert saved == [cfg]
    assert "sync finished" in result.output


def test_webhook_sync_with_consumer_refreshes_vm_consumer(monkeypatch):
    cfg = Config(
        project_id="proj",
        account="me@example.com",
        billing_account_id="billing",
        webhook_gateways={"alba-nury": {"github": {"provider": "generic", "secret": "topsecret"}}},
    )
    resources = _webhook_resources()
    found = object()
    consumer_calls = []

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.agents, "get_agent", lambda _cfg, agent: found if agent == "alba-nury" else None)
    monkeypatch.setattr(
        cli.bootstrap,
        "sync_webhook_gateway",
        lambda _cfg, agent, *, name: cli.bootstrap.WebhookSetupResult(
            resources=resources,
            service_url="https://hook.example.run.app",
            secret="topsecret",
            vm_env_updated=True,
        ),
    )
    monkeypatch.setattr(
        cli.bootstrap,
        "install_webhook_consumer",
        lambda _cfg, agent, selected: consumer_calls.append((agent, selected)) or cli.bootstrap.WebhookConsumerResult(
            resources=selected,
            script_path="~/.local/bin/ghosty-github-consumer",
            service_name="ghosty-github-consumer.service",
            installed=True,
            active=True,
        ),
    )
    monkeypatch.setattr(cli.config_mod, "save_config", lambda config: "/tmp/config.toml")

    result = CliRunner().invoke(cli.app, [
        "notifications", "sync", "alba-nury",
        "--name", "github",
        "--with-consumer",
    ])

    assert result.exit_code == 0
    assert consumer_calls == [(found, resources)]
    assert "Hermes event consumer is running" in result.output


def test_webhook_destroy_command_saves_config(monkeypatch):
    cfg = Config(
        project_id="proj",
        account="me@example.com",
        billing_account_id="billing",
        webhook_gateways={"alba-nury": {"github": {"provider": "generic", "secret": "topsecret"}}},
    )
    resources = _webhook_resources()
    calls = []
    saved = []

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.bootstrap, "webhook_resources", lambda _cfg, _agent, _name, _provider=None: resources)

    def fake_destroy_webhook_gateway(_cfg, agent, *, name):
        calls.append((agent, name))
        _cfg.webhook_gateways = {}
        return resources

    monkeypatch.setattr(cli.bootstrap, "destroy_webhook_gateway", fake_destroy_webhook_gateway)
    monkeypatch.setattr(cli.config_mod, "save_config", lambda config: saved.append(config) or "/tmp/config.toml")

    result = CliRunner().invoke(cli.app, [
        "webhook", "destroy", "alba-nury",
        "--name", "github",
        "--yes",
    ])

    assert result.exit_code == 0
    assert calls == [("alba-nury", "github")]
    assert saved == [cfg]
    assert cfg.webhook_gateways == {}
    assert "remove the notification URL" in result.output


def test_bucket_status_command(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(
        cli.bootstrap,
        "storage_status",
        lambda _cfg: cli.bootstrap.StorageStatus(
            storage_api_enabled=True,
            storage_json_api_enabled=True,
            signing_api_enabled=True,
            auto_grant_enabled=True,
            public_enabled=True,
            signed_urls_enabled=True,
            bucket="proj-ghosty-agent-storage",
            bucket_uri="gs://proj-ghosty-agent-storage",
            public_bucket="proj-ghosty-agent-public",
            public_bucket_uri="gs://proj-ghosty-agent-public",
            location="us-east1",
            public_location="us-east1",
            storage_class="STANDARD",
            public_storage_class="STANDARD",
            role="roles/storage.objectUser",
            public_viewer_role="roles/storage.objectViewer",
            bucket_exists=True,
            public_bucket_exists=True,
            agents=[
                cli.bootstrap.StorageStatusAgent(
                    agent="worker-1",
                    service_account="ghosty-worker-1-sa@proj.iam.gserviceaccount.com",
                    private_folder_uri="gs://proj-ghosty-agent-storage/agents/worker-1/",
                    private_folder_exists=True,
                    has_private_folder_role=True,
                    public_folder_uri="gs://proj-ghosty-agent-public/agents/worker-1/",
                    public_folder_exists=True,
                    has_public_folder_role=True,
                    public_folder_is_public=True,
                    has_signed_url_iam=True,
                    has_legacy_bucket_role=False,
                    vm_env_path="~/.config/hermes/storage.env",
                    vm_env_exists=True,
                )
            ],
        ),
    )

    result = CliRunner().invoke(cli.app, ["bucket", "status"])

    assert result.exit_code == 0
    assert "gs://proj-ghosty-agent-storage" in result.output
    assert "gs://proj-ghosty-agent-public" in result.output
    assert "worker-1" in result.output


def test_bucket_setup_command_saves_config(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    saved = []

    def fake_setup_storage(
        config,
        *,
        bucket=None,
        public_bucket=None,
        location=None,
        with_public=False,
        with_signed_urls=False,
    ):
        config.storage_enabled = True
        config.storage_bucket = bucket or "custom-bucket"
        config.storage_location = location or "us-east1"
        config.storage_public_enabled = with_public
        config.storage_public_bucket = public_bucket or "custom-public"
        config.storage_signed_urls_enabled = with_signed_urls
        return cli.bootstrap.StorageSetupResult(
            bucket=config.storage_bucket,
            bucket_uri=f"gs://{config.storage_bucket}",
            location=config.storage_location,
            storage_class=config.storage_class,
            role=config.storage_role,
            agents=[],
            public_bucket=config.storage_public_bucket if with_public else "",
            public_bucket_uri=f"gs://{config.storage_public_bucket}" if with_public else "",
            public_location=config.storage_location if with_public else "",
            public_storage_class=config.storage_public_class if with_public else "",
        )

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.bootstrap, "setup_storage", fake_setup_storage)
    monkeypatch.setattr(cli.config_mod, "save_config", lambda config: saved.append(config) or "/tmp/config.toml")

    result = CliRunner().invoke(cli.app, [
        "bucket", "setup",
        "--bucket", "custom-bucket",
        "--public-bucket", "custom-public",
        "--location", "us-east1",
        "--with-public",
        "--with-signed-urls",
        "--yes",
    ])

    assert result.exit_code == 0
    assert cfg.storage_enabled
    assert cfg.storage_bucket == "custom-bucket"
    assert cfg.storage_public_enabled
    assert cfg.storage_public_bucket == "custom-public"
    assert cfg.storage_signed_urls_enabled
    assert saved == [cfg]


def test_bucket_sync_command_for_one_agent(monkeypatch):
    cfg = Config(
        project_id="proj",
        account="me@example.com",
        billing_account_id="billing",
        storage_bucket="agent-bucket",
    )
    calls = []
    found = object()

    def fake_sync_storage(config, agent_items):
        calls.append((config, agent_items))
        return cli.bootstrap.StorageSetupResult(
            bucket="agent-bucket",
            bucket_uri="gs://agent-bucket",
            location="us-east1",
            storage_class=config.storage_class,
            role=config.storage_role,
            agents=[
                cli.bootstrap.StorageAgentSyncResult(
                    agent="worker-1",
                    service_account="ghosty-worker-1-sa@proj.iam.gserviceaccount.com",
                    private_folder_uri="gs://agent-bucket/agents/worker-1/",
                    private_folder_iam_updated=True,
                    vm_env_updated=True,
                )
            ],
        )

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.agents, "get_agent", lambda _cfg, agent: found if agent == "worker-1" else None)
    monkeypatch.setattr(cli.bootstrap, "sync_storage", fake_sync_storage)

    result = CliRunner().invoke(cli.app, ["bucket", "sync", "worker-1"])

    assert result.exit_code == 0
    assert calls == [(cfg, [found])]
    assert "storage sync finished" in result.output


def test_bucket_sync_brief_agent_flag(monkeypatch, tmp_path):
    cfg = Config(
        project_id="proj",
        account="me@example.com",
        billing_account_id="billing",
        storage_bucket="agent-bucket",
    )
    found = Agent(
        name="worker-1",
        instance="ghosty-worker-1",
        status="RUNNING",
        zone="us-east1-b",
        machine_type="e2-small",
    )
    delivered = []

    def fake_sync_storage(config, agent_items):
        return cli.bootstrap.StorageSetupResult(
            bucket="agent-bucket",
            bucket_uri="gs://agent-bucket",
            location="us-east1",
            storage_class=config.storage_class,
            role=config.storage_role,
            agents=[
                cli.bootstrap.StorageAgentSyncResult(
                    agent="worker-1",
                    service_account="ghosty-worker-1-sa@proj.iam.gserviceaccount.com",
                    private_folder_uri="gs://agent-bucket/agents/worker-1/",
                    vm_env_updated=True,
                )
            ],
        )

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.agents, "get_agent", lambda _cfg, agent: found if agent == "worker-1" else None)
    monkeypatch.setattr(cli.bootstrap, "sync_storage", fake_sync_storage)
    monkeypatch.setattr(
        cli.instructions,
        "deliver_instruction",
        lambda _cfg, agent, service, **kw: delivered.append((agent.agent, service, kw)) or cli.instructions.InstructionDeliveryResult(
            agent=agent.agent,
            service=service,
            remote_prompt_path="~/.config/hermes/inbox/storage-setup.md",
            local_prompt_path=tmp_path / "storage-setup.md",
            uploaded=True,
            delivered=True,
        ),
    )

    result = CliRunner().invoke(cli.app, ["storage", "sync", "worker-1", "--brief-agent"])

    assert result.exit_code == 0
    assert delivered == [("worker-1", "storage", {"name": None})]


def test_bucket_sync_requires_configured_bucket(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)

    result = CliRunner().invoke(cli.app, ["bucket", "sync"])

    assert result.exit_code == 1
    assert "no storage bucket configured" in result.output


def test_bucket_disable_command_saves_config(monkeypatch):
    cfg = Config(
        project_id="proj",
        account="me@example.com",
        billing_account_id="billing",
        storage_enabled=True,
        storage_bucket="agent-bucket",
    )
    saved = []

    def fake_disable_storage(config):
        config.storage_enabled = False
        config.storage_bucket = ""
        config.storage_public_enabled = False
        config.storage_public_bucket = ""
        config.storage_signed_urls_enabled = False
        return cli.bootstrap.StorageSetupResult(
            bucket="agent-bucket",
            bucket_uri="gs://agent-bucket",
            location="us-east1",
            storage_class=config.storage_class,
            role=config.storage_role,
            agents=[],
        )

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.bootstrap, "disable_storage", fake_disable_storage)
    monkeypatch.setattr(cli.config_mod, "save_config", lambda config: saved.append(config) or "/tmp/config.toml")

    result = CliRunner().invoke(cli.app, ["bucket", "disable", "--yes"])

    assert result.exit_code == 0
    assert not cfg.storage_enabled
    assert cfg.storage_bucket == ""
    assert saved == [cfg]


def test_feature_group_aliases_dispatch(monkeypatch, tmp_path):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    saved = []
    calls = []
    chat_resources = cli.bootstrap.GoogleChatResources(
        agent="alba-nury",
        slug="alba-nury",
        chat_project="ghosty-agent-chat",
        topic="alba-nury-chat-events",
        subscription="alba-nury-chat-events-sub",
        service_account_id="alba-nury-chat-gw-sa",
        service_account_email="alba-nury-chat-gw-sa@ghosty-agent-chat.iam.gserviceaccount.com",
        full_topic="projects/ghosty-agent-chat/topics/alba-nury-chat-events",
        full_subscription="projects/ghosty-agent-chat/subscriptions/alba-nury-chat-events-sub",
        local_key_path=tmp_path / "service-account.json",
    )
    webhook_resources = _webhook_resources()

    monkeypatch.setattr(cli.config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(cli.config_mod, "save_config", lambda config: saved.append(config) or "/tmp/config.toml")
    monkeypatch.setattr(cli.config_mod, "config_exists", lambda: True)
    monkeypatch.setattr(cli.agents, "get_agent", lambda _cfg, agent: object() if agent == "alba-nury" else None)
    monkeypatch.setattr(cli.bootstrap, "nat_status", lambda _cfg: cli.bootstrap.NatStatus(
        router_exists=True,
        nat_exists=True,
        router="ghosty-router",
        nat="ghosty-nat",
        region="us-east1",
        network="ghosty-vpc",
        subnet="ghosty-subnet",
    ))
    monkeypatch.setattr(cli.bootstrap, "google_chat_resources", lambda _cfg, _agent, chat_project=None: chat_resources)
    monkeypatch.setattr(cli.bootstrap, "ensure_google_chat_gateway", lambda _cfg, agent, **kw: calls.append(("chat", agent, kw)) or chat_resources)
    monkeypatch.setattr(cli.bootstrap, "webhook_resources", lambda _cfg, _agent, _name, _provider=None: webhook_resources)
    monkeypatch.setattr(cli.bootstrap, "ensure_webhook_gateway", lambda _cfg, agent, **kw: calls.append(("notifications", kw)) or cli.bootstrap.WebhookSetupResult(
        resources=webhook_resources,
        service_url="https://hook.example.run.app",
        secret=kw["secret"],
        vm_env_updated=True,
    ))
    monkeypatch.setattr(cli.bootstrap, "setup_storage", lambda _cfg, **kw: calls.append(("storage", kw)) or cli.bootstrap.StorageSetupResult(
        bucket="agent-bucket",
        bucket_uri="gs://agent-bucket",
        location="us-east1",
        storage_class=cfg.storage_class,
        role=cfg.storage_role,
        agents=[],
    ))
    monkeypatch.setattr(cli.bootstrap, "disable_google_ai_iam", lambda _cfg: calls.append(("models", None)))

    internet = CliRunner().invoke(cli.app, ["internet", "status"])
    settings = CliRunner().invoke(cli.app, ["settings", "show"])
    chat = CliRunner().invoke(cli.app, [
        "chat", "add", "alba-nury",
        "--chat-project", "ghosty-agent-chat",
        "--no-create-project",
        "--yes",
    ])
    notifications = CliRunner().invoke(cli.app, [
        "notifications", "add", "alba-nury",
        "--name", "github",
        "--secret", "topsecret",
        "--yes",
    ])
    storage = CliRunner().invoke(cli.app, ["storage", "add", "--bucket", "agent-bucket", "--yes"])
    models = CliRunner().invoke(cli.app, ["models", "disable", "--yes"])

    assert internet.exit_code == 0
    assert settings.exit_code == 0
    assert chat.exit_code == 0
    assert notifications.exit_code == 0
    assert storage.exit_code == 0
    assert models.exit_code == 0
    assert "Shared internet" in internet.output
    assert "Notification URL" in notifications.output
    assert ("models", None) in calls
