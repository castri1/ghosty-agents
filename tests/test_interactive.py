"""Tests for the friendly interactive control room."""

from types import SimpleNamespace

from ghosty import interactive
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


def _choices(monkeypatch, values):
    answers = iter(values)
    monkeypatch.setattr(interactive, "_choose", lambda _title, _choices: next(answers))


def test_interactive_select_agent_and_connect(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    agent = _agent()
    calls = []

    _choices(monkeypatch, ["select", "alba-nury", "connect", "back", "exit"])
    monkeypatch.setattr(interactive, "_load", lambda: cfg)
    monkeypatch.setattr(interactive.agents, "list_agents", lambda _cfg: [agent])
    monkeypatch.setattr(interactive.agents, "get_agent", lambda _cfg, name: agent if name == "alba-nury" else None)
    monkeypatch.setattr(interactive.agents, "ssh_agent", lambda _cfg, name: calls.append(name) or 0)

    interactive.run()

    assert calls == ["alba-nury"]


def test_interactive_agent_menu_can_show_harness(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    agent = _agent()
    calls = []

    _choices(monkeypatch, ["harness", "back"])
    monkeypatch.setattr(interactive.harness_browser, "live_harness", lambda _cfg, selected: calls.append(selected.name))
    monkeypatch.setattr(interactive.agents, "get_agent", lambda _cfg, name: agent if name == "alba-nury" else None)

    interactive._agent_menu(cfg, agent)

    assert calls == ["alba-nury"]


def test_interactive_prepare_check(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    checks = [SimpleNamespace(ok=True, name="gcloud", detail="ready", fix="")]

    _choices(monkeypatch, ["prepare", "check", "exit"])
    monkeypatch.setattr(interactive, "_load", lambda: cfg)
    monkeypatch.setattr(interactive.agents, "list_agents", lambda _cfg: [])
    monkeypatch.setattr(interactive, "run_checks", lambda _cfg, deep=True: checks)

    interactive.run()


def test_interactive_create_agent_starts_guided_setup(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    calls = []

    _choices(monkeypatch, ["create", "exit"])
    monkeypatch.setattr(interactive, "_load", lambda: cfg)
    monkeypatch.setattr(interactive.agents, "list_agents", lambda _cfg: [])
    monkeypatch.setattr(
        interactive.guided,
        "run_guided_setup",
        lambda selected, **kw: calls.append((selected, kw)),
    )

    interactive.run()

    assert calls == [(cfg, {"open_harness": True})]


def test_interactive_storage_sync(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    agent = _agent()
    calls = []

    _choices(monkeypatch, ["features", "storage", "sync", "exit"])
    monkeypatch.setattr(interactive, "_load", lambda: cfg)
    monkeypatch.setattr(interactive.agents, "list_agents", lambda _cfg: [agent])
    monkeypatch.setattr(
        interactive.bootstrap,
        "sync_storage",
        lambda _cfg, found: calls.append(found) or SimpleNamespace(bucket_uri="gs://agent-bucket"),
    )

    interactive.run()

    assert calls == [[agent]]


def test_interactive_add_chat(monkeypatch, tmp_path):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing", agent_instruction_delivery="off")
    agent = _agent()
    calls = []
    resources = interactive.bootstrap.GoogleChatResources(
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

    monkeypatch.setattr(interactive.typer, "prompt", lambda *_args, **_kw: "ghosty-agent-chat")
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kw: True)
    monkeypatch.setattr(interactive.bootstrap, "google_chat_resources", lambda *_args, **_kw: resources)
    monkeypatch.setattr(interactive.bootstrap, "ensure_google_chat_gateway", lambda _cfg, name, **kw: calls.append((name, kw)) or resources)
    monkeypatch.setattr(interactive.config_mod, "save_config", lambda _cfg: "/tmp/config.toml")

    interactive._add_chat(cfg, agent)

    assert calls == [("alba-nury", {"chat_project": "ghosty-agent-chat"})]


def test_interactive_add_notifications(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing", agent_instruction_delivery="off")
    agent = _agent()
    calls = []
    resources = interactive.bootstrap.webhook_resources(cfg, "alba-nury", "crm")

    monkeypatch.setattr(interactive.typer, "prompt", lambda *_args, **_kw: "crm")
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kw: True)
    monkeypatch.setattr(interactive.bootstrap, "webhook_resources", lambda *_args, **_kw: resources)
    monkeypatch.setattr(
        interactive.bootstrap,
        "ensure_webhook_gateway",
        lambda _cfg, found, **kw: calls.append((found, kw)) or SimpleNamespace(
            resources=resources,
            service_url="https://hook.example.run.app",
            secret="generated-secret",
        ),
    )
    monkeypatch.setattr(
        interactive.bootstrap,
        "install_webhook_consumer",
        lambda _cfg, found, selected: SimpleNamespace(installed=True, service_name="ghosty-crm-consumer.service"),
    )
    monkeypatch.setattr(interactive.config_mod, "save_config", lambda _cfg: "/tmp/config.toml")

    interactive._add_notifications(cfg, agent)

    assert calls == [(agent, {"name": "crm", "secret": None, "generate_secret": True})]


def test_interactive_agent_menu_can_sync_hermes(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    agent = _agent()
    calls = []

    _choices(monkeypatch, ["hermes", "sync", "back"])
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kw: True)
    monkeypatch.setattr(interactive.hermes_mod, "sync_hermes", lambda _cfg, selected: calls.append(selected.name))
    monkeypatch.setattr(interactive.agents, "get_agent", lambda _cfg, name: agent if name == "alba-nury" else None)

    interactive._agent_menu(cfg, agent)

    assert calls == ["alba-nury"]


def test_interactive_add_chat_can_brief_agent(monkeypatch, tmp_path):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    agent = _agent()
    delivered = []
    resources = interactive.bootstrap.GoogleChatResources(
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

    monkeypatch.setattr(interactive.typer, "prompt", lambda *_args, **_kw: "ghosty-agent-chat")
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kw: True)
    monkeypatch.setattr(interactive.bootstrap, "google_chat_resources", lambda *_args, **_kw: resources)
    monkeypatch.setattr(interactive.bootstrap, "ensure_google_chat_gateway", lambda *_args, **_kw: resources)
    monkeypatch.setattr(interactive.config_mod, "save_config", lambda _cfg: "/tmp/config.toml")
    monkeypatch.setattr(
        interactive.instructions,
        "deliver_instruction",
        lambda _cfg, selected, service, **kw: delivered.append((selected.name, service, kw)) or type(
            "Result",
            (),
            {"delivered": True},
        )(),
    )

    interactive._add_chat(cfg, agent)

    assert delivered == [("alba-nury", "chat", {"name": None})]


def test_interactive_add_chat_can_skip_brief(monkeypatch, tmp_path):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    agent = _agent()
    confirmations = iter([True, False])
    delivered = []
    resources = interactive.bootstrap.GoogleChatResources(
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

    monkeypatch.setattr(interactive.typer, "prompt", lambda *_args, **_kw: "ghosty-agent-chat")
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kw: next(confirmations))
    monkeypatch.setattr(interactive.bootstrap, "google_chat_resources", lambda *_args, **_kw: resources)
    monkeypatch.setattr(interactive.bootstrap, "ensure_google_chat_gateway", lambda *_args, **_kw: resources)
    monkeypatch.setattr(interactive.config_mod, "save_config", lambda _cfg: "/tmp/config.toml")
    monkeypatch.setattr(
        interactive.instructions,
        "deliver_instruction",
        lambda *_args, **_kw: delivered.append("called"),
    )

    interactive._add_chat(cfg, agent)

    assert delivered == []


def test_interactive_remove_agent(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    agent = _agent()
    calls = []

    _choices(monkeypatch, ["remove"])
    monkeypatch.setattr(interactive, "_confirm", lambda *_args, **_kw: True)
    monkeypatch.setattr(interactive.agents, "destroy_agent", lambda _cfg, name: calls.append(name))

    interactive._agent_menu(cfg, agent)

    assert calls == ["alba-nury"]
