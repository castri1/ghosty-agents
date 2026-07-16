"""Tests for the guided agent setup interview and executor."""

from __future__ import annotations

from types import SimpleNamespace

from ghosty import guided
from ghosty.doctor import Check
from ghosty.models import Agent, Config


def _agent(name="alba-nury"):
    return Agent(
        name=name,
        instance=f"ghosty-{name}",
        status="RUNNING",
        zone="us-east1-b",
        machine_type="e2-small",
        internal_ip="10.10.0.2",
    )


def _ready(monkeypatch):
    monkeypatch.setattr(guided, "preflight_create", lambda _cfg: [
        Check("billing linked", True, "ready"),
        Check("APIs enabled", True, "ready"),
    ])
    monkeypatch.setattr(guided.gcloud, "exists", lambda _cfg, _args: True)


def _no_harness(monkeypatch):
    monkeypatch.setattr(guided.harness_browser, "live_harness", lambda *_args, **_kw: None)


def test_recommended_defaults_include_hermes_models_storage_and_internet_when_needed():
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")

    intent = guided.recommended_intent(cfg, "mira-sol", nat_enabled=False)

    assert intent.name == "mira-sol"
    assert intent.install_hermes
    assert intent.enable_models
    assert intent.private_storage
    assert intent.enable_internet
    assert intent.internet_recommended_for_hermes


def test_recommended_defaults_reuse_existing_shared_internet():
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")

    intent = guided.recommended_intent(cfg, "mira-sol", nat_enabled=True)

    assert intent.internet_already_enabled
    assert not intent.enable_internet
    assert not intent.internet_recommended_for_hermes


def test_execute_reuses_enabled_models_storage_and_internet(monkeypatch):
    cfg = Config(
        project_id="proj",
        account="me@example.com",
        billing_account_id="billing",
        google_ai_enabled=True,
        storage_enabled=True,
        storage_bucket="agent-bucket",
    )
    created = _agent("mira-sol")
    calls = []
    intent = guided.AgentSetupIntent(
        name="mira-sol",
        install_hermes=False,
        enable_models=True,
        private_storage=True,
        internet_already_enabled=True,
    )

    _ready(monkeypatch)
    _no_harness(monkeypatch)
    monkeypatch.setattr(guided.bootstrap, "ensure_network", lambda _cfg: calls.append("ensure-network"))
    monkeypatch.setattr(guided.bootstrap, "ensure_nat", lambda _cfg: calls.append("ensure-nat"))
    monkeypatch.setattr(guided.bootstrap, "enable_google_ai", lambda *_args, **_kw: calls.append("models"))
    monkeypatch.setattr(guided.bootstrap, "setup_storage", lambda *_args, **_kw: calls.append("setup-storage"))
    monkeypatch.setattr(
        guided.agents,
        "create_agent",
        lambda _cfg, name, **kw: calls.append(("create", name, kw)) or created,
    )
    monkeypatch.setattr(
        guided.bootstrap,
        "sync_storage",
        lambda _cfg, items: calls.append(("sync-storage", [item.name for item in items])) or guided.bootstrap.StorageSetupResult(
            bucket="agent-bucket",
            bucket_uri="gs://agent-bucket",
            location="us-east1",
            storage_class=cfg.storage_class,
            role=cfg.storage_role,
            agents=[
                guided.bootstrap.StorageAgentSyncResult(
                    agent="mira-sol",
                    service_account=cfg.sa_email("mira-sol"),
                    vm_env_updated=True,
                )
            ],
        ),
    )

    result = guided.execute_intent(cfg, intent)

    assert result.agent == created
    assert calls == [
        ("create", "mira-sol", {"startup_script": None}),
        ("sync-storage", ["mira-sol"]),
    ]
    assert "Models already enabled" in result.skipped_steps
    assert "Shared internet already enabled" in result.skipped_steps


def test_execute_notifications_installs_receiver_env_and_consumer(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    created = _agent()
    calls = []
    resources = guided.bootstrap.webhook_resources(cfg, "alba-nury", "agentmail")
    intent = guided.AgentSetupIntent(
        name="alba-nury",
        install_hermes=False,
        enable_models=False,
        private_storage=False,
        enable_notifications=True,
        notification_name="AgentMail",
        generate_notification_secret=True,
        install_notification_consumer=True,
    )

    _ready(monkeypatch)
    monkeypatch.setattr(guided.agents, "create_agent", lambda _cfg, name, **kw: calls.append(("create", name)) or created)
    monkeypatch.setattr(
        guided.bootstrap,
        "ensure_webhook_gateway",
        lambda _cfg, agent, **kw: calls.append(("notifications", agent.name, kw)) or guided.bootstrap.WebhookSetupResult(
            resources=resources,
            service_url="https://agentmail.example.run.app",
            secret="generated-secret",
            vm_env_updated=True,
        ),
    )
    monkeypatch.setattr(
        guided.bootstrap,
        "install_webhook_consumer",
        lambda _cfg, agent, selected: calls.append(("consumer", agent.name, selected.name_slug)) or guided.bootstrap.WebhookConsumerResult(
            resources=selected,
            script_path="~/.config/hermes/webhooks/agentmail-consumer.py",
            service_name="ghosty-agentmail-consumer.service",
            installed=True,
            active=True,
        ),
    )
    monkeypatch.setattr(guided.config_mod, "save_config", lambda _cfg: "/tmp/config.toml")

    result = guided.execute_intent(cfg, intent)

    assert ("notifications", "alba-nury", {
        "name": "AgentMail",
        "secret": None,
        "generate_secret": True,
    }) in calls
    assert ("consumer", "alba-nury", "agentmail") in calls
    assert "Notifications" in result.completed_steps
    assert "Notification event listener" in result.completed_steps
    assert any("Notification URL: https://agentmail.example.run.app" == item for item in result.manual_followups)


def test_execute_chat_records_manual_console_values(monkeypatch, tmp_path):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    created = _agent()
    resources = guided.bootstrap.GoogleChatResources(
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
    intent = guided.AgentSetupIntent(
        name="alba-nury",
        install_hermes=False,
        enable_models=False,
        private_storage=False,
        enable_chat=True,
        chat_project="ghosty-agent-chat",
        create_chat_project=False,
    )

    _ready(monkeypatch)
    monkeypatch.setattr(guided.agents, "create_agent", lambda _cfg, name, **kw: created)
    monkeypatch.setattr(
        guided.bootstrap,
        "ensure_google_chat_gateway",
        lambda _cfg, agent, **kw: calls.append((agent, kw)) or resources,
    )
    monkeypatch.setattr(guided.config_mod, "save_config", lambda _cfg: "/tmp/config.toml")

    result = guided.execute_intent(cfg, intent)

    assert calls == [("alba-nury", {"chat_project": "ghosty-agent-chat", "create_project": False})]
    assert "Chat" in result.completed_steps
    assert "Chat console topic: projects/ghosty-agent-chat/topics/alba-nury-chat-events" in result.manual_followups
    assert "Hermes subscription: projects/ghosty-agent-chat/subscriptions/alba-nury-chat-events-sub" in result.manual_followups


def test_optional_hermes_failure_preserves_created_agent(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    created = _agent()
    intent = guided.AgentSetupIntent(
        name="alba-nury",
        install_hermes=True,
        enable_models=False,
        private_storage=False,
    )

    _ready(monkeypatch)
    monkeypatch.setattr(guided.agents, "create_agent", lambda _cfg, name, **kw: created)
    monkeypatch.setattr(
        guided.hermes_mod,
        "install_hermes",
        lambda *_args, **_kw: guided.hermes_mod.HermesInstallResult(
            agent="alba-nury",
            installed=False,
            gateway_started=False,
            message="no internet",
        ),
    )
    monkeypatch.setattr(guided.hermes_mod, "configure_hermes", lambda *_args, **_kw: SimpleNamespace(configured=True))

    result = guided.execute_intent(cfg, intent)

    assert result.agent == created
    assert any(issue.step == "Hermes" and "no internet" in issue.message for issue in result.failed_optional_steps)
    assert "Agent created" in result.completed_steps
