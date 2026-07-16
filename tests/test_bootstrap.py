"""Tests for shared bootstrap infrastructure helpers."""

from types import SimpleNamespace

import pytest

from ghosty import bootstrap, gcloud
from ghosty.models import Config


@pytest.fixture
def cfg():
    return Config(
        project_id="proj",
        account="me@example.com",
        billing_account_id="billing",
        region="us-east1",
        network="ghosty-vpc",
        subnet="ghosty-subnet",
        nat_router="ghosty-router",
        nat_name="ghosty-nat",
    )


def _completed(stdout="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


def _is_router_describe(args):
    return args[:4] == ["compute", "routers", "describe", "ghosty-router"]


def _is_nat_describe(args):
    return args[:5] == ["compute", "routers", "nats", "describe", "ghosty-nat"]


def test_ensure_nat_creates_router_and_nat(monkeypatch, cfg):
    issued = []
    monkeypatch.setattr(gcloud, "exists", lambda _cfg, _args, **_kw: False)
    monkeypatch.setattr(gcloud, "run", lambda _cfg, args, **_kw: issued.append(args) or _completed())

    bootstrap.ensure_nat(cfg)

    assert [
        "compute", "routers", "create", "ghosty-router",
        "--network=ghosty-vpc", "--region=us-east1",
    ] in issued

    nat_create = [args for args in issued if args[:4] == ["compute", "routers", "nats", "create"]]
    assert len(nat_create) == 1
    assert nat_create[0] == [
        "compute", "routers", "nats", "create", "ghosty-nat",
        "--router=ghosty-router",
        "--region=us-east1",
        "--type=PUBLIC",
        "--auto-allocate-nat-external-ips",
        "--nat-custom-subnet-ip-ranges=ghosty-subnet:ALL",
    ]


def test_ensure_nat_skips_existing_router_and_nat(monkeypatch, cfg):
    monkeypatch.setattr(gcloud, "exists", lambda _cfg, _args, **_kw: True)

    def fail_run(*_args, **_kw):
        raise AssertionError("gcloud.run should not be called")

    monkeypatch.setattr(gcloud, "run", fail_run)
    bootstrap.ensure_nat(cfg)


def test_nat_status_reports_router_and_nat_states(monkeypatch, cfg):
    states = {"router": False, "nat": False}

    def fake_exists(_cfg, args, **_kw):
        if _is_router_describe(args):
            return states["router"]
        if _is_nat_describe(args):
            return states["nat"]
        return False

    monkeypatch.setattr(gcloud, "exists", fake_exists)

    status = bootstrap.nat_status(cfg)
    assert not status.router_exists
    assert not status.nat_exists
    assert not status.enabled

    states["router"] = True
    status = bootstrap.nat_status(cfg)
    assert status.router_exists
    assert not status.nat_exists
    assert not status.enabled

    states["nat"] = True
    status = bootstrap.nat_status(cfg)
    assert status.router_exists
    assert status.nat_exists
    assert status.enabled


def test_disable_nat_deletes_nat_when_present(monkeypatch, cfg):
    issued = []

    def fake_exists(_cfg, args, **_kw):
        return _is_router_describe(args) or _is_nat_describe(args)

    monkeypatch.setattr(gcloud, "exists", fake_exists)
    monkeypatch.setattr(gcloud, "run", lambda _cfg, args, **_kw: issued.append(args) or _completed())

    bootstrap.disable_nat(cfg)

    assert issued == [[
        "compute", "routers", "nats", "delete", "ghosty-nat",
        "--router=ghosty-router",
        "--region=us-east1",
        "--quiet",
    ]]


def test_disable_nat_noops_when_router_or_nat_missing(monkeypatch, cfg):
    issued = []
    monkeypatch.setattr(gcloud, "run", lambda _cfg, args, **_kw: issued.append(args) or _completed())

    monkeypatch.setattr(gcloud, "exists", lambda _cfg, _args, **_kw: False)
    bootstrap.disable_nat(cfg)
    assert issued == []

    def only_router_exists(_cfg, args, **_kw):
        return _is_router_describe(args)

    monkeypatch.setattr(gcloud, "exists", only_router_exists)
    bootstrap.disable_nat(cfg)
    assert issued == []


def test_bootstrap_all_calls_nat_after_network_when_enabled(monkeypatch, cfg):
    order = []
    monkeypatch.setattr(bootstrap, "ensure_project", lambda _cfg, _name=None: order.append("project"))
    monkeypatch.setattr(bootstrap, "link_billing", lambda _cfg: order.append("billing"))
    monkeypatch.setattr(bootstrap, "enable_apis", lambda _cfg: order.append("apis"))
    monkeypatch.setattr(bootstrap, "ensure_network", lambda _cfg: order.append("network"))
    monkeypatch.setattr(bootstrap, "ensure_nat", lambda _cfg: order.append("nat"))
    monkeypatch.setattr(bootstrap, "ensure_firewall", lambda _cfg: order.append("firewall"))
    monkeypatch.setattr(bootstrap, "grant_iap_access", lambda _cfg: order.append("iam"))
    monkeypatch.setattr(bootstrap, "ensure_budget", lambda _cfg: order.append("budget"))

    bootstrap.bootstrap_all(cfg, with_nat=True)

    assert order == ["project", "billing", "apis", "network", "nat", "firewall", "iam", "budget"]


def test_google_ai_status_reports_api_and_agent_iam(monkeypatch, cfg):
    policy = {"bindings": []}
    enabled = [{"config": {"name": "compute.googleapis.com"}}]

    def fake_run_json(_cfg, args, **_kw):
        if args[:2] == ["services", "list"]:
            return enabled
        if args[:3] == ["projects", "get-iam-policy", "proj"]:
            return policy
        raise AssertionError(args)

    monkeypatch.setattr(gcloud, "run_json", fake_run_json)

    status = bootstrap.google_ai_status(cfg, agent_names=["worker-1"])
    assert not status.api_enabled
    assert not status.agents[0].has_role

    enabled.append({"config": {"name": "aiplatform.googleapis.com"}})
    policy["bindings"] = [{
        "role": "roles/aiplatform.user",
        "members": ["serviceAccount:ghosty-worker-1-sa@proj.iam.gserviceaccount.com"],
    }]
    cfg.google_ai_enabled = True

    status = bootstrap.google_ai_status(cfg, agent_names=["worker-1"])
    assert status.api_enabled
    assert status.auto_grant_enabled
    assert status.agents[0].has_role


def test_enable_google_ai_enables_api_and_grants_agents(monkeypatch, cfg):
    issued = []

    def fake_run_json(_cfg, args, **_kw):
        if args[:2] == ["services", "list"]:
            return [{"config": {"name": "compute.googleapis.com"}}]
        raise AssertionError(args)

    monkeypatch.setattr(gcloud, "run_json", fake_run_json)
    monkeypatch.setattr(gcloud, "run", lambda _cfg, args, **_kw: issued.append(args) or _completed())

    bootstrap.enable_google_ai(cfg, agent_names=["worker-1"])

    assert cfg.google_ai_enabled
    assert ["services", "enable", "aiplatform.googleapis.com"] in issued
    assert [
        "projects", "add-iam-policy-binding", "proj",
        "--member=serviceAccount:ghosty-worker-1-sa@proj.iam.gserviceaccount.com",
        "--role=roles/aiplatform.user",
        "--condition=None", "--quiet",
    ] in issued


def test_disable_google_ai_iam_removes_agent_roles_and_clears_flag(monkeypatch, cfg):
    cfg.google_ai_enabled = True
    issued = []

    def fake_run(_cfg, args, **kw):
        issued.append((args, kw))
        return _completed()

    monkeypatch.setattr(gcloud, "run", fake_run)

    bootstrap.disable_google_ai_iam(cfg, agent_names=["worker-1"])

    assert not cfg.google_ai_enabled
    assert issued == [([
        "projects", "remove-iam-policy-binding", "proj",
        "--member=serviceAccount:ghosty-worker-1-sa@proj.iam.gserviceaccount.com",
        "--role=roles/aiplatform.user",
        "--quiet",
    ], {"no_project": True, "check": False})]


def test_google_chat_resource_names(monkeypatch, tmp_path, cfg):
    monkeypatch.setenv("GHOSTY_CONFIG_DIR", str(tmp_path))

    resources = bootstrap.google_chat_resources(cfg, "Alba Nury", "ghosty-agent-chat")

    assert resources.slug == "alba-nury"
    assert resources.chat_project == "ghosty-agent-chat"
    assert resources.topic == "alba-nury-chat-events"
    assert resources.subscription == "alba-nury-chat-events-sub"
    assert resources.service_account_id == "alba-nury-chat-gw-sa"
    assert resources.service_account_email == "alba-nury-chat-gw-sa@ghosty-agent-chat.iam.gserviceaccount.com"
    assert resources.full_topic == "projects/ghosty-agent-chat/topics/alba-nury-chat-events"
    assert resources.full_subscription == "projects/ghosty-agent-chat/subscriptions/alba-nury-chat-events-sub"
    assert resources.local_key_path == tmp_path / "google-chat" / "alba-nury" / "ghosty-agent-chat" / "service-account.json"
    assert resources.vm_key_path == "~/.config/hermes/google-chat-service-account.json"

    long_id = bootstrap.google_chat_service_account_id("a-very-long-agent-name-that-exceeds-limits")
    assert len(long_id) <= 30
    assert long_id.endswith("-chat-gw-sa")


def test_google_chat_project_id_derives_and_truncates(cfg):
    assert bootstrap.derive_google_chat_project_id(cfg, "alba-nury") == "proj-alba-nury-chat"

    cfg.project_id = "ghosty-agents-with-long-prefix"
    project = bootstrap.derive_google_chat_project_id(cfg, "a-very-long-agent-name")
    assert len(project) <= 30
    assert project.endswith("-chat")
    assert project[0].isalpha()

    cfg.google_chat_projects["alba-nury"] = "ghosty-agent-chat"
    assert bootstrap.google_chat_project_id(cfg, "alba-nury") == "ghosty-agent-chat"
    assert bootstrap.google_chat_project_id(cfg, "alba-nury", "override-chat") == "override-chat"


def test_google_chat_setup_creates_resources_and_uploads_key(monkeypatch, tmp_path, cfg):
    monkeypatch.setenv("GHOSTY_CONFIG_DIR", str(tmp_path))
    enabled = set()
    state = {"project": True, "topic": False, "subscription": False, "sa": False}
    issued = []

    def fake_run_json(_cfg, args, **_kw):
        if args[:2] == ["services", "list"]:
            return [{"config": {"name": service}} for service in sorted(enabled)]
        if args[:3] == ["billing", "projects", "describe"]:
            return {"billingEnabled": True}
        raise AssertionError(args)

    def fake_exists(_cfg, args, **_kw):
        if args[:2] == ["projects", "describe"]:
            return state["project"]
        if args[:3] == ["pubsub", "topics", "describe"]:
            return state["topic"]
        if args[:3] == ["pubsub", "subscriptions", "describe"]:
            return state["subscription"]
        if args[:3] == ["iam", "service-accounts", "describe"]:
            return state["sa"]
        return False

    def fake_run(_cfg, args, **kw):
        issued.append((_cfg.project_id, args, kw))
        if args[:2] == ["services", "enable"]:
            enabled.add(args[2])
        if args[:3] == ["pubsub", "topics", "create"]:
            state["topic"] = True
        if args[:3] == ["pubsub", "subscriptions", "create"]:
            state["subscription"] = True
        if args[:3] == ["iam", "service-accounts", "create"]:
            state["sa"] = True
        return _completed()

    monkeypatch.setattr(gcloud, "run_json", fake_run_json)
    monkeypatch.setattr(gcloud, "exists", fake_exists)
    monkeypatch.setattr(gcloud, "run", fake_run)

    resources = bootstrap.ensure_google_chat_gateway(
        cfg,
        "alba-nury",
        chat_project="ghosty-agent-chat",
    )

    issued_args = [args for _project, args, _kw in issued]
    issued_projects = [(project, args) for project, args, _kw in issued]
    assert cfg.google_chat_projects["alba-nury"] == "ghosty-agent-chat"
    assert ["services", "enable", "chat.googleapis.com"] in issued_args
    assert ["services", "enable", "pubsub.googleapis.com"] in issued_args
    assert ["services", "enable", "iam.googleapis.com"] in issued_args
    assert ["pubsub", "topics", "create", "alba-nury-chat-events"] in issued_args
    assert ("ghosty-agent-chat", ["pubsub", "topics", "create", "alba-nury-chat-events"]) in issued_projects
    assert [
        "pubsub", "subscriptions", "create", "alba-nury-chat-events-sub",
        "--topic=alba-nury-chat-events",
    ] in issued_args
    assert [
        "iam", "service-accounts", "create", "alba-nury-chat-gw-sa",
        "--display-name=Hermes Google Chat gateway alba-nury",
    ] in issued_args
    assert [
        "pubsub", "topics", "add-iam-policy-binding", "alba-nury-chat-events",
        "--member=serviceAccount:chat-api-push@system.gserviceaccount.com",
        "--role=roles/pubsub.publisher",
        "--quiet",
    ] in issued_args
    assert [
        "pubsub", "subscriptions", "add-iam-policy-binding", "alba-nury-chat-events-sub",
        "--member=serviceAccount:alba-nury-chat-gw-sa@ghosty-agent-chat.iam.gserviceaccount.com",
        "--role=roles/pubsub.subscriber",
        "--quiet",
    ] in issued_args
    assert [
        "pubsub", "subscriptions", "add-iam-policy-binding", "alba-nury-chat-events-sub",
        "--member=serviceAccount:alba-nury-chat-gw-sa@ghosty-agent-chat.iam.gserviceaccount.com",
        "--role=roles/pubsub.viewer",
        "--quiet",
    ] in issued_args
    assert [
        "iam", "service-accounts", "keys", "create",
        str(resources.local_key_path),
        "--iam-account=alba-nury-chat-gw-sa@ghosty-agent-chat.iam.gserviceaccount.com",
    ] in issued_args
    assert [
        "compute", "ssh", "ghosty-alba-nury",
        "--zone=us-central1-a",
        "--tunnel-through-iap",
        "--command=mkdir -p ~/.config/hermes && chmod 700 ~/.config/hermes",
        "--quiet",
    ] in issued_args
    assert ("proj", [
        "compute", "ssh", "ghosty-alba-nury",
        "--zone=us-central1-a",
        "--tunnel-through-iap",
        "--command=mkdir -p ~/.config/hermes && chmod 700 ~/.config/hermes",
        "--quiet",
    ]) in issued_projects
    assert [
        "compute", "scp", str(resources.local_key_path),
        "ghosty-alba-nury:~/.config/hermes/google-chat-service-account.json",
        "--zone=us-central1-a",
        "--tunnel-through-iap",
        "--quiet",
    ] in issued_args


def test_google_chat_setup_creates_missing_project_and_links_billing(monkeypatch, cfg):
    issued = []

    def fake_run_json(_cfg, args, **_kw):
        if args[:3] == ["projects", "describe", "proj"]:
            return {"parent": {"type": "folder", "id": "folders-123"}}
        if args[:3] == ["billing", "projects", "describe"]:
            return {"billingEnabled": False}
        if args[:2] == ["services", "list"]:
            return [
                {"config": {"name": "chat.googleapis.com"}},
                {"config": {"name": "pubsub.googleapis.com"}},
                {"config": {"name": "iam.googleapis.com"}},
            ]
        raise AssertionError(args)

    def fake_exists(_cfg, args, **_kw):
        if args[:2] == ["projects", "describe"]:
            return False
        if args[:3] in (
            ["pubsub", "topics", "describe"],
            ["pubsub", "subscriptions", "describe"],
            ["iam", "service-accounts", "describe"],
        ):
            return True
        return False

    monkeypatch.setattr(gcloud, "run_json", fake_run_json)
    monkeypatch.setattr(gcloud, "exists", fake_exists)
    monkeypatch.setattr(gcloud, "run", lambda _cfg, args, **kw: issued.append((args, kw)) or _completed())
    monkeypatch.setattr(bootstrap, "upload_google_chat_key", lambda _cfg, _resources: None)

    bootstrap.ensure_google_chat_gateway(cfg, "alba-nury", chat_project="ghosty-agent-chat")

    assert ([
        "projects", "create", "ghosty-agent-chat",
        "--name=ghosty-agent-chat",
        "--folder=folders-123",
    ], {"no_project": True}) in issued
    assert ([
        "billing", "projects", "link", "ghosty-agent-chat",
        "--billing-account=billing",
    ], {"no_project": True}) in issued


def test_google_chat_setup_skips_existing_local_key(monkeypatch, tmp_path, cfg):
    monkeypatch.setenv("GHOSTY_CONFIG_DIR", str(tmp_path))
    resources = bootstrap.google_chat_resources(cfg, "alba-nury", "ghosty-agent-chat")
    resources.local_key_path.parent.mkdir(parents=True)
    resources.local_key_path.write_text("{}", encoding="utf-8")
    issued = []

    monkeypatch.setattr(gcloud, "run_json", lambda _cfg, args, **_kw: [{"config": {"name": args[2]}}] if args[:2] == ["services", "list"] else [])
    monkeypatch.setattr(gcloud, "exists", lambda _cfg, _args, **_kw: True)
    monkeypatch.setattr(gcloud, "run", lambda _cfg, args, **kw: issued.append((args, kw)) or _completed())

    bootstrap.ensure_google_chat_gateway(cfg, "alba-nury", chat_project="ghosty-agent-chat")

    assert not any(args[:4] == ["iam", "service-accounts", "keys", "create"] for args, _kw in issued)


def test_google_chat_status_reports_resource_state(monkeypatch, tmp_path, cfg):
    monkeypatch.setenv("GHOSTY_CONFIG_DIR", str(tmp_path))
    resources = bootstrap.google_chat_resources(cfg, "alba-nury", "ghosty-agent-chat")
    resources.local_key_path.parent.mkdir(parents=True)
    resources.local_key_path.write_text("{}", encoding="utf-8")

    def fake_run_json(_cfg, args, **_kw):
        if args[:2] == ["services", "list"]:
            return [
                {"config": {"name": "chat.googleapis.com"}},
                {"config": {"name": "pubsub.googleapis.com"}},
            ]
        if args[:4] == ["pubsub", "topics", "get-iam-policy", "alba-nury-chat-events"]:
            return {
                "bindings": [{
                    "role": "roles/pubsub.publisher",
                    "members": ["serviceAccount:chat-api-push@system.gserviceaccount.com"],
                }]
            }
        if args[:4] == ["pubsub", "subscriptions", "get-iam-policy", "alba-nury-chat-events-sub"]:
            return {
                "bindings": [
                    {
                        "role": "roles/pubsub.subscriber",
                        "members": ["serviceAccount:alba-nury-chat-gw-sa@ghosty-agent-chat.iam.gserviceaccount.com"],
                    },
                    {
                        "role": "roles/pubsub.viewer",
                        "members": ["serviceAccount:alba-nury-chat-gw-sa@ghosty-agent-chat.iam.gserviceaccount.com"],
                    },
                ]
            }
        raise AssertionError(args)

    monkeypatch.setattr(gcloud, "run_json", fake_run_json)
    monkeypatch.setattr(gcloud, "exists", lambda _cfg, _args, **_kw: True)

    status = bootstrap.google_chat_status(cfg, "alba-nury", chat_project="ghosty-agent-chat")

    assert status.chat_project_exists
    assert status.chat_api_enabled
    assert status.pubsub_api_enabled
    assert status.topic_exists
    assert status.subscription_exists
    assert status.service_account_exists
    assert status.topic_publisher_bound
    assert status.subscription_subscriber_bound
    assert status.subscription_viewer_bound
    assert status.local_key_exists


def test_google_chat_status_treats_missing_project_as_disabled(monkeypatch, cfg):
    monkeypatch.setattr(
        gcloud,
        "exists",
        lambda _cfg, args, **_kw: False
        if args[:2] == ["projects", "describe"]
        else pytest.fail(args),
    )
    monkeypatch.setattr(gcloud, "run_json", lambda _cfg, args, **_kw: pytest.fail(args))

    status = bootstrap.google_chat_status(cfg, "alba-nury", chat_project="ghosty-agent-chat")

    assert not status.chat_project_exists
    assert not status.chat_api_enabled
    assert not status.pubsub_api_enabled
    assert not status.topic_exists
    assert not status.subscription_exists
    assert not status.service_account_exists
    assert not status.topic_publisher_bound
    assert not status.subscription_subscriber_bound
    assert not status.subscription_viewer_bound


def test_destroy_google_chat_gateway_removes_pubsub_not_service_account(monkeypatch, cfg):
    issued = []
    monkeypatch.setattr(gcloud, "exists", lambda _cfg, _args, **_kw: True)
    monkeypatch.setattr(gcloud, "run", lambda _cfg, args, **kw: issued.append((args, kw)) or _completed())
    cfg.google_chat_projects["alba-nury"] = "ghosty-agent-chat"

    bootstrap.destroy_google_chat_gateway(cfg, "alba-nury", chat_project="ghosty-agent-chat")

    issued_args = [args for args, _kw in issued]
    assert [
        "pubsub", "topics", "remove-iam-policy-binding", "alba-nury-chat-events",
        "--member=serviceAccount:chat-api-push@system.gserviceaccount.com",
        "--role=roles/pubsub.publisher",
        "--quiet",
    ] in issued_args
    assert [
        "pubsub", "subscriptions", "remove-iam-policy-binding", "alba-nury-chat-events-sub",
        "--member=serviceAccount:alba-nury-chat-gw-sa@ghosty-agent-chat.iam.gserviceaccount.com",
        "--role=roles/pubsub.subscriber",
        "--quiet",
    ] in issued_args
    assert [
        "pubsub", "subscriptions", "remove-iam-policy-binding", "alba-nury-chat-events-sub",
        "--member=serviceAccount:alba-nury-chat-gw-sa@ghosty-agent-chat.iam.gserviceaccount.com",
        "--role=roles/pubsub.viewer",
        "--quiet",
    ] in issued_args
    assert ["pubsub", "subscriptions", "delete", "alba-nury-chat-events-sub", "--quiet"] in issued_args
    assert ["pubsub", "topics", "delete", "alba-nury-chat-events", "--quiet"] in issued_args
    assert not any(args[:3] == ["iam", "service-accounts", "delete"] for args in issued_args)
    assert not any(args[:4] == ["iam", "service-accounts", "keys", "delete"] for args in issued_args)
    assert cfg.google_chat_projects == {}


def test_webhook_resource_names(cfg):
    resources = bootstrap.webhook_resources(cfg, "Alba Nury", "GitHub")

    assert resources.slug == "alba-nury"
    assert resources.name_slug == "github"
    assert resources.provider == "generic"
    assert resources.service_name == "ghosty-alba-nury-webhook-github"
    assert resources.topic == "alba-nury-webhook-github-events"
    assert resources.subscription == "alba-nury-webhook-github-events-sub"
    assert resources.full_topic == "projects/proj/topics/alba-nury-webhook-github-events"
    assert resources.full_subscription == "projects/proj/subscriptions/alba-nury-webhook-github-events-sub"
    assert resources.run_service_account_id == "alba-nury-github-wh-run-sa"
    assert resources.run_service_account_email == "alba-nury-github-wh-run-sa@proj.iam.gserviceaccount.com"
    assert resources.agent_service_account_email == "ghosty-alba-nury-sa@proj.iam.gserviceaccount.com"
    assert resources.env_path == "~/.config/hermes/webhooks/github.env"

    long_service = bootstrap.webhook_service_name("a-very-long-agent-name-that-exceeds-limits", "a-very-long-provider-name")
    long_sa = bootstrap.webhook_run_service_account_id("a-very-long-agent-name-that-exceeds-limits", "provider")
    assert len(long_service) <= 63
    assert len(long_sa) <= 30


def test_cloud_run_source_build_iam_grants_compute_default_sa(monkeypatch, cfg):
    issued = []

    def fake_run(_cfg, args, **kw):
        issued.append((args, kw))
        if args[:3] == ["projects", "describe", "proj"]:
            return _completed("275874631169\n")
        return _completed()

    monkeypatch.setattr(gcloud, "run", fake_run)

    service_account = bootstrap.ensure_cloud_run_source_build_iam(cfg)

    assert service_account == "275874631169-compute@developer.gserviceaccount.com"
    assert issued == [
        ([
            "projects", "describe", "proj", "--format=value(projectNumber)",
        ], {"no_project": True}),
        ([
            "projects", "add-iam-policy-binding", "proj",
            "--member=serviceAccount:275874631169-compute@developer.gserviceaccount.com",
            "--role=roles/run.builder",
            "--condition=None",
            "--quiet",
        ], {"no_project": True}),
    ]


def test_webhook_setup_creates_resources_deploys_and_uploads_env(monkeypatch, cfg):
    enabled = set()
    state = {"topic": False, "subscription": False, "sa": False, "service": False}
    issued = []
    agent = SimpleNamespace(name="alba-nury", status="RUNNING")

    def fake_run_json(_cfg, args, **_kw):
        if args[:2] == ["services", "list"]:
            return [{"config": {"name": service}} for service in sorted(enabled)]
        if args[:3] == ["run", "services", "describe"]:
            return {"status": {"url": "https://hook.example.run.app"}}
        raise AssertionError(args)

    def fake_exists(_cfg, args, **_kw):
        if args[:3] == ["pubsub", "topics", "describe"]:
            return state["topic"]
        if args[:3] == ["pubsub", "subscriptions", "describe"]:
            return state["subscription"]
        if args[:3] == ["iam", "service-accounts", "describe"]:
            return state["sa"]
        if args[:3] == ["run", "services", "describe"]:
            return state["service"]
        return False

    def fake_run(_cfg, args, **kw):
        issued.append((args, kw))
        if args[:3] == ["projects", "describe", "proj"]:
            return _completed("275874631169\n")
        if args[:2] == ["services", "enable"]:
            enabled.add(args[2])
        if args[:3] == ["pubsub", "topics", "create"]:
            state["topic"] = True
        if args[:3] == ["pubsub", "subscriptions", "create"]:
            state["subscription"] = True
        if args[:3] == ["iam", "service-accounts", "create"]:
            state["sa"] = True
        if args[:2] == ["run", "deploy"]:
            state["service"] = True
        return _completed()

    monkeypatch.setattr(gcloud, "run_json", fake_run_json)
    monkeypatch.setattr(gcloud, "exists", fake_exists)
    monkeypatch.setattr(gcloud, "run", fake_run)

    result = bootstrap.ensure_webhook_gateway(
        cfg,
        agent,
        name="github",
        secret="topsecret",
    )

    issued_args = [args for args, _kw in issued]
    assert result.service_url == "https://hook.example.run.app"
    assert result.secret == "topsecret"
    assert cfg.webhook_gateways["alba-nury"]["github"]["provider"] == "generic"
    assert cfg.webhook_gateways["alba-nury"]["github"]["secret"] == "topsecret"
    assert ["services", "enable", "run.googleapis.com"] in issued_args
    assert ["services", "enable", "artifactregistry.googleapis.com"] in issued_args
    assert ["services", "enable", "cloudbuild.googleapis.com"] in issued_args
    assert ["services", "enable", "pubsub.googleapis.com"] in issued_args
    assert ["services", "enable", "iam.googleapis.com"] in issued_args
    assert ["pubsub", "topics", "create", "alba-nury-webhook-github-events"] in issued_args
    assert [
        "pubsub", "subscriptions", "create", "alba-nury-webhook-github-events-sub",
        "--topic=alba-nury-webhook-github-events",
    ] in issued_args
    assert [
        "iam", "service-accounts", "create", "alba-nury-github-wh-run-sa",
        "--display-name=Ghosty webhook receiver alba-nury/github",
    ] in issued_args
    assert [
        "pubsub", "topics", "add-iam-policy-binding", "alba-nury-webhook-github-events",
        "--member=serviceAccount:alba-nury-github-wh-run-sa@proj.iam.gserviceaccount.com",
        "--role=roles/pubsub.publisher",
        "--quiet",
    ] in issued_args
    assert [
        "pubsub", "subscriptions", "add-iam-policy-binding", "alba-nury-webhook-github-events-sub",
        "--member=serviceAccount:ghosty-alba-nury-sa@proj.iam.gserviceaccount.com",
        "--role=roles/pubsub.subscriber",
        "--quiet",
    ] in issued_args
    assert [
        "pubsub", "subscriptions", "add-iam-policy-binding", "alba-nury-webhook-github-events-sub",
        "--member=serviceAccount:ghosty-alba-nury-sa@proj.iam.gserviceaccount.com",
        "--role=roles/pubsub.viewer",
        "--quiet",
    ] in issued_args
    assert [
        "projects", "add-iam-policy-binding", "proj",
        "--member=serviceAccount:275874631169-compute@developer.gserviceaccount.com",
        "--role=roles/run.builder",
        "--condition=None",
        "--quiet",
    ] in issued_args

    deploy = next(args for args in issued_args if args[:2] == ["run", "deploy"])
    assert deploy[2] == "ghosty-alba-nury-webhook-github"
    assert "--region=us-east1" in deploy
    assert "--service-account=alba-nury-github-wh-run-sa@proj.iam.gserviceaccount.com" in deploy
    assert "--allow-unauthenticated" in deploy
    assert any(arg.startswith("--source=") and "webhook_receiver" in arg for arg in deploy)
    assert any("GHOSTY_WEBHOOK_SECRET=topsecret" in arg for arg in deploy)

    ssh = next(args for args in issued_args if args[:2] == ["compute", "ssh"])
    assert "ghosty-alba-nury" in ssh
    command = next(arg for arg in ssh if arg.startswith("--command="))
    assert "GHOSTY_WEBHOOK_SUBSCRIPTION=projects/proj/subscriptions/alba-nury-webhook-github-events-sub" in command
    assert "GOOGLE_CLOUD_PROJECT=proj" in command


def test_webhook_setup_reuses_existing_resources_and_generates_secret(monkeypatch, cfg):
    issued = []
    agent = SimpleNamespace(name="alba-nury", status="RUNNING")

    monkeypatch.setattr(gcloud, "run_json", lambda _cfg, args, **_kw: (
        [{"config": {"name": args[2]}}] if args[:2] == ["services", "list"]
        else {"status": {"url": "https://hook.example.run.app"}}
        if args[:3] == ["run", "services", "describe"]
        else pytest.fail(args)
    ))
    monkeypatch.setattr(gcloud, "exists", lambda _cfg, _args, **_kw: True)

    def fake_run(_cfg, args, **kw):
        issued.append((args, kw))
        if args[:3] == ["projects", "describe", "proj"]:
            return _completed("275874631169\n")
        return _completed()

    monkeypatch.setattr(gcloud, "run", fake_run)

    result = bootstrap.ensure_webhook_gateway(cfg, agent, name="generic", provider="generic", generate_secret=True)

    issued_args = [args for args, _kw in issued]
    assert result.secret
    assert not any(args[:3] == ["pubsub", "topics", "create"] for args in issued_args)
    assert not any(args[:3] == ["pubsub", "subscriptions", "create"] for args in issued_args)
    assert not any(args[:3] == ["iam", "service-accounts", "create"] for args in issued_args)
    assert any(args[:2] == ["run", "deploy"] for args in issued_args)


def test_webhook_status_reports_complete_and_missing_states(monkeypatch, cfg):
    agent = SimpleNamespace(name="alba-nury", status="RUNNING")
    cfg.webhook_gateways["alba-nury"] = {"github": {"provider": "generic", "secret": "topsecret"}}

    def fake_run_json(_cfg, args, **_kw):
        if args[:2] == ["services", "list"]:
            return [
                {"config": {"name": "run.googleapis.com"}},
                {"config": {"name": "artifactregistry.googleapis.com"}},
                {"config": {"name": "cloudbuild.googleapis.com"}},
                {"config": {"name": "pubsub.googleapis.com"}},
                {"config": {"name": "iam.googleapis.com"}},
            ]
        if args[:3] == ["run", "services", "describe"]:
            return {"status": {"url": "https://hook.example.run.app"}}
        if args[:4] == ["pubsub", "topics", "get-iam-policy", "alba-nury-webhook-github-events"]:
            return {
                "bindings": [{
                    "role": "roles/pubsub.publisher",
                    "members": ["serviceAccount:alba-nury-github-wh-run-sa@proj.iam.gserviceaccount.com"],
                }]
            }
        if args[:4] == ["pubsub", "subscriptions", "get-iam-policy", "alba-nury-webhook-github-events-sub"]:
            return {
                "bindings": [
                    {
                        "role": "roles/pubsub.subscriber",
                        "members": ["serviceAccount:ghosty-alba-nury-sa@proj.iam.gserviceaccount.com"],
                    },
                    {
                        "role": "roles/pubsub.viewer",
                        "members": ["serviceAccount:ghosty-alba-nury-sa@proj.iam.gserviceaccount.com"],
                    },
                ]
            }
        raise AssertionError(args)

    monkeypatch.setattr(gcloud, "run_json", fake_run_json)
    monkeypatch.setattr(gcloud, "exists", lambda _cfg, _args, **_kw: True)
    monkeypatch.setattr(gcloud, "run", lambda _cfg, _args, **_kw: _completed())

    status = bootstrap.webhook_status(cfg, agent, name="github")
    assert status.run_api_enabled
    assert status.artifactregistry_api_enabled
    assert status.cloudbuild_api_enabled
    assert status.pubsub_api_enabled
    assert status.iam_api_enabled
    assert status.topic_exists
    assert status.subscription_exists
    assert status.run_service_account_exists
    assert status.service_exists
    assert status.publisher_bound
    assert status.subscriber_bound
    assert status.viewer_bound
    assert status.vm_env_exists
    assert status.secret_configured
    assert status.service_url == "https://hook.example.run.app"

    monkeypatch.setattr(gcloud, "run_json", lambda _cfg, args, **_kw: [] if args[:2] == ["services", "list"] else pytest.fail(args))
    monkeypatch.setattr(gcloud, "exists", lambda _cfg, _args, **_kw: False)
    monkeypatch.setattr(gcloud, "run", lambda _cfg, _args, **_kw: _completed(returncode=1))
    cfg.webhook_gateways = {}

    status = bootstrap.webhook_status(cfg, agent, name="missing")
    assert not status.run_api_enabled
    assert not status.topic_exists
    assert not status.service_exists
    assert status.vm_env_exists is False
    assert not status.secret_configured


def test_webhook_sync_reapplies_iam_and_env_without_redeploy(monkeypatch, cfg):
    cfg.webhook_gateways["alba-nury"] = {"github": {"provider": "generic", "secret": "topsecret", "url": "https://old.example"}}
    agent = SimpleNamespace(name="alba-nury", status="RUNNING")
    issued = []

    monkeypatch.setattr(gcloud, "run_json", lambda _cfg, args, **_kw: (
        {"status": {"url": "https://hook.example.run.app"}}
        if args[:3] == ["run", "services", "describe"]
        else pytest.fail(args)
    ))
    monkeypatch.setattr(gcloud, "exists", lambda _cfg, _args, **_kw: True)
    monkeypatch.setattr(gcloud, "run", lambda _cfg, args, **kw: issued.append((args, kw)) or _completed())

    result = bootstrap.sync_webhook_gateway(cfg, agent, name="github")

    issued_args = [args for args, _kw in issued]
    assert result.service_url == "https://hook.example.run.app"
    assert cfg.webhook_gateways["alba-nury"]["github"]["url"] == "https://hook.example.run.app"
    assert any(args[:3] == ["pubsub", "topics", "add-iam-policy-binding"] for args in issued_args)
    assert any(args[:2] == ["compute", "ssh"] for args in issued_args)
    assert not any(args[:2] == ["run", "deploy"] for args in issued_args)


def test_upload_webhook_env_retries_until_fresh_vm_accepts_ssh(monkeypatch, cfg):
    resources = bootstrap.webhook_resources(cfg, "alba-nury", "github")
    sleeps = []
    calls = {"n": 0}

    def fake_run(_cfg, args, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(stdout="", stderr="Connection refused", returncode=255)
        return _completed()

    monkeypatch.setattr(gcloud, "run", fake_run)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda seconds: sleeps.append(seconds))

    updated, message = bootstrap.upload_webhook_env_to_agent(
        cfg,
        SimpleNamespace(name="alba-nury", status="RUNNING"),
        resources,
        service_url="https://hook.example.run.app",
        attempts=2,
        delay=0.25,
    )

    assert updated
    assert message == ""
    assert calls["n"] == 2
    assert sleeps == [0.25]


def test_webhook_consumer_script_saves_acknowledges_then_invokes_hermes(cfg):
    resources = bootstrap.webhook_resources(cfg, "Alba Nury", "GitHub")

    script = bootstrap.webhook_consumer_script(resources)

    assert 'ENV_PATH = Path("~/.config/hermes/webhooks/github.env").expanduser()' in script
    assert 'EVENT_DIR = HOME / ".config/hermes/inbox/events/github"' in script
    assert 'subscription = env["GHOSTY_WEBHOOK_SUBSCRIPTION"]' in script
    assert "duplicate-skipped" in script
    assert 'timeout=HERMES_TIMEOUT' in script

    write_index = script.index("event_path.write_text")
    ack_index = script.index('acknowledge(subscription, received["ackId"])')
    hermes_index = script.index("subprocess.run(")
    assert write_index < ack_index < hermes_index


def test_install_webhook_consumer_writes_script_service_and_starts(monkeypatch, cfg):
    resources = bootstrap.webhook_resources(cfg, "alba-nury", "github")
    agent = SimpleNamespace(name="alba-nury", status="RUNNING")
    issued = []

    def fake_run(_cfg, args, **kw):
        issued.append((args, kw))
        return _completed()

    monkeypatch.setattr(gcloud, "run", fake_run)

    result = bootstrap.install_webhook_consumer(cfg, agent, resources)

    assert result.installed
    assert result.active
    assert result.script_path == "~/.local/bin/ghosty-github-consumer"
    assert result.service_name == "ghosty-github-consumer.service"
    args, kwargs = issued[0]
    assert args[:3] == ["compute", "ssh", "ghosty-alba-nury"]
    assert "--tunnel-through-iap" in args
    assert kwargs == {"check": False}
    command = next(arg for arg in args if arg.startswith("--command="))
    assert "cat > \"$script_path\" <<'GHOSTY_CONSUMER'" in command
    assert "cat > \"$service_path\" <<'GHOSTY_SERVICE'" in command
    assert "systemctl --user enable --now ghosty-github-consumer.service" in command
    assert 'loginctl enable-linger "$USER"' in command


def test_webhook_status_reports_consumer_state(monkeypatch, cfg):
    agent = SimpleNamespace(name="alba-nury", status="RUNNING")
    cfg.webhook_gateways["alba-nury"] = {"github": {"provider": "generic", "secret": "topsecret"}}

    def fake_run_json(_cfg, args, **_kw):
        if args[:2] == ["services", "list"]:
            return [
                {"config": {"name": "run.googleapis.com"}},
                {"config": {"name": "artifactregistry.googleapis.com"}},
                {"config": {"name": "cloudbuild.googleapis.com"}},
                {"config": {"name": "pubsub.googleapis.com"}},
                {"config": {"name": "iam.googleapis.com"}},
            ]
        if args[:3] == ["run", "services", "describe"]:
            return {"status": {"url": "https://hook.example.run.app"}}
        if args[:4] == ["pubsub", "topics", "get-iam-policy", "alba-nury-webhook-github-events"]:
            return {
                "bindings": [{
                    "role": "roles/pubsub.publisher",
                    "members": ["serviceAccount:alba-nury-github-wh-run-sa@proj.iam.gserviceaccount.com"],
                }]
            }
        if args[:4] == ["pubsub", "subscriptions", "get-iam-policy", "alba-nury-webhook-github-events-sub"]:
            return {
                "bindings": [
                    {
                        "role": "roles/pubsub.subscriber",
                        "members": ["serviceAccount:ghosty-alba-nury-sa@proj.iam.gserviceaccount.com"],
                    },
                    {
                        "role": "roles/pubsub.viewer",
                        "members": ["serviceAccount:ghosty-alba-nury-sa@proj.iam.gserviceaccount.com"],
                    },
                ]
            }
        raise AssertionError(args)

    def fake_run(_cfg, args, **_kw):
        command = next((arg for arg in args if arg.startswith("--command=")), "")
        if 'printf "%s %s\\n" "$installed" "$active"' in command:
            return _completed("1 1\n")
        return _completed()

    monkeypatch.setattr(gcloud, "run_json", fake_run_json)
    monkeypatch.setattr(gcloud, "exists", lambda _cfg, _args, **_kw: True)
    monkeypatch.setattr(gcloud, "run", fake_run)

    status = bootstrap.webhook_status(cfg, agent, name="github")

    assert status.consumer_installed is True
    assert status.consumer_active is True


def test_destroy_webhook_gateway_removes_run_pubsub_and_service_account(monkeypatch, cfg):
    cfg.webhook_gateways["alba-nury"] = {"github": {"provider": "generic", "secret": "topsecret"}}
    issued = []
    monkeypatch.setattr(gcloud, "exists", lambda _cfg, _args, **_kw: True)
    monkeypatch.setattr(gcloud, "run", lambda _cfg, args, **kw: issued.append((args, kw)) or _completed())

    bootstrap.destroy_webhook_gateway(cfg, "alba-nury", name="github")

    issued_args = [args for args, _kw in issued]
    assert [
        "run", "services", "delete", "ghosty-alba-nury-webhook-github",
        "--region=us-east1",
        "--quiet",
    ] in issued_args
    assert [
        "pubsub", "topics", "remove-iam-policy-binding", "alba-nury-webhook-github-events",
        "--member=serviceAccount:alba-nury-github-wh-run-sa@proj.iam.gserviceaccount.com",
        "--role=roles/pubsub.publisher",
        "--quiet",
    ] in issued_args
    assert ["pubsub", "subscriptions", "delete", "alba-nury-webhook-github-events-sub", "--quiet"] in issued_args
    assert ["pubsub", "topics", "delete", "alba-nury-webhook-github-events", "--quiet"] in issued_args
    assert [
        "iam", "service-accounts", "delete",
        "alba-nury-github-wh-run-sa@proj.iam.gserviceaccount.com",
        "--quiet",
    ] in issued_args
    assert cfg.webhook_gateways == {}


def test_storage_bucket_defaults(cfg):
    assert bootstrap.storage_bucket_name(cfg) == "proj-ghosty-agent-storage"
    assert bootstrap.storage_public_bucket_name(cfg) == "proj-ghosty-agent-public"
    assert bootstrap.storage_location(cfg) == "us-east1"
    assert bootstrap.storage_public_location(cfg) == "us-east1"
    assert bootstrap.storage_agent_prefix("Alba Nury") == "agents/alba-nury/"
    assert bootstrap.storage_agent_folder_uri(cfg, "Alba Nury") == "gs://proj-ghosty-agent-storage/agents/alba-nury/"
    assert bootstrap.storage_agent_public_folder_uri(cfg, "Alba Nury") == "gs://proj-ghosty-agent-public/agents/alba-nury/"

    cfg.storage_bucket = "custom-bucket"
    cfg.storage_public_bucket = "custom-public"
    cfg.storage_location = "us"
    cfg.storage_public_location = "nam4"
    assert bootstrap.storage_bucket_name(cfg) == "custom-bucket"
    assert bootstrap.storage_public_bucket_name(cfg) == "custom-public"
    assert bootstrap.storage_bucket_name(cfg, "override") == "override"
    assert bootstrap.storage_public_bucket_name(cfg, "public-override") == "public-override"
    assert bootstrap.storage_location(cfg) == "us"
    assert bootstrap.storage_public_location(cfg) == "nam4"
    assert bootstrap.storage_location(cfg, "us-east4") == "us-east4"
    assert bootstrap.storage_public_location(cfg, "us-east4") == "us-east4"


def test_cleanup_storage_for_agent_removes_iam_env_and_managed_folders(monkeypatch, cfg):
    cfg.storage_bucket = "agent-bucket"
    cfg.storage_public_enabled = True
    cfg.storage_public_bucket = "public-bucket"
    cfg.storage_signed_urls_enabled = True
    agent = SimpleNamespace(name="worker-1", status="RUNNING")
    issued = []

    def fake_exists(_cfg, args, **_kw):
        if args[:3] == ["storage", "buckets", "describe"]:
            return args[3] in {"gs://agent-bucket", "gs://public-bucket"}
        if args[:3] == ["storage", "managed-folders", "describe"]:
            return args[3] in {
                "gs://agent-bucket/agents/worker-1/",
                "gs://public-bucket/agents/worker-1/",
            }
        return False

    monkeypatch.setattr(gcloud, "exists", fake_exists)
    monkeypatch.setattr(gcloud, "run", lambda _cfg, args, **kw: issued.append((args, kw)) or _completed())

    result = bootstrap.cleanup_storage_for_agent(cfg, agent)

    issued_args = [args for args, _kw in issued]
    assert [
        "storage", "managed-folders", "remove-iam-policy-binding",
        "gs://agent-bucket/agents/worker-1/",
        "--member=serviceAccount:ghosty-worker-1-sa@proj.iam.gserviceaccount.com",
        "--role=roles/storage.objectUser",
        "--condition=None",
        "--quiet",
    ] in issued_args
    assert ["storage", "managed-folders", "delete", "gs://agent-bucket/agents/worker-1/", "--quiet"] in issued_args
    assert ["storage", "managed-folders", "delete", "gs://public-bucket/agents/worker-1/", "--quiet"] in issued_args
    assert any(args[:3] == ["iam", "service-accounts", "remove-iam-policy-binding"] for args in issued_args)
    assert any(args[:2] == ["compute", "ssh"] for args in issued_args)
    assert result.private_folder_uri == "gs://agent-bucket/agents/worker-1/"
    assert result.public_folder_uri == "gs://public-bucket/agents/worker-1/"
    assert result.private_folder_iam_updated
    assert result.public_folder_iam_updated
    assert result.signed_url_iam_updated


def test_setup_storage_creates_private_public_folders_and_signed_urls(monkeypatch, cfg):
    enabled = set()
    state = {"buckets": set(), "folders": set()}
    issued = []
    agent = SimpleNamespace(name="worker-1", status="RUNNING")

    def fake_run_json(_cfg, args, **_kw):
        if args[:2] == ["services", "list"]:
            return [{"config": {"name": service}} for service in sorted(enabled)]
        raise AssertionError(args)

    def fake_exists(_cfg, args, **_kw):
        if args[:3] == ["storage", "buckets", "describe"]:
            return args[3].removeprefix("gs://") in state["buckets"]
        if args[:3] == ["storage", "managed-folders", "describe"]:
            return args[3] in state["folders"]
        return False

    def fake_run(_cfg, args, **kw):
        issued.append((args, kw))
        if args[:2] == ["services", "enable"]:
            enabled.add(args[2])
        if args[:3] == ["storage", "buckets", "create"]:
            state["buckets"].add(args[3].removeprefix("gs://"))
        if args[:3] == ["storage", "managed-folders", "create"]:
            state["folders"].add(args[3])
        return _completed()

    monkeypatch.setattr(gcloud, "run_json", fake_run_json)
    monkeypatch.setattr(gcloud, "exists", fake_exists)
    monkeypatch.setattr(gcloud, "run", fake_run)

    result = bootstrap.setup_storage(
        cfg,
        with_public=True,
        with_signed_urls=True,
        agent_items=[agent],
    )

    issued_args = [args for args, _kw in issued]
    assert cfg.storage_enabled
    assert cfg.storage_public_enabled
    assert cfg.storage_signed_urls_enabled
    assert cfg.storage_bucket == "proj-ghosty-agent-storage"
    assert cfg.storage_public_bucket == "proj-ghosty-agent-public"
    assert cfg.storage_location == "us-east1"
    assert result.bucket_uri == "gs://proj-ghosty-agent-storage"
    assert result.public_bucket_uri == "gs://proj-ghosty-agent-public"
    assert ["services", "enable", "storage.googleapis.com"] in issued_args
    assert ["services", "enable", "storage-api.googleapis.com"] in issued_args
    assert ["services", "enable", "iamcredentials.googleapis.com"] in issued_args
    assert [
        "storage", "buckets", "create", "gs://proj-ghosty-agent-storage",
        "--location=us-east1",
        "--default-storage-class=STANDARD",
        "--uniform-bucket-level-access",
        "--public-access-prevention",
    ] in issued_args
    assert [
        "storage", "buckets", "create", "gs://proj-ghosty-agent-public",
        "--location=us-east1",
        "--default-storage-class=STANDARD",
        "--uniform-bucket-level-access",
        "--no-public-access-prevention",
    ] in issued_args
    assert [
        "storage", "managed-folders", "create",
        "gs://proj-ghosty-agent-storage/agents/worker-1/",
    ] in issued_args
    assert [
        "storage", "managed-folders", "create",
        "gs://proj-ghosty-agent-public/agents/worker-1/",
    ] in issued_args
    assert [
        "storage", "managed-folders", "add-iam-policy-binding",
        "gs://proj-ghosty-agent-storage/agents/worker-1/",
        "--member=serviceAccount:ghosty-worker-1-sa@proj.iam.gserviceaccount.com",
        "--role=roles/storage.objectUser",
        "--condition=None",
        "--quiet",
    ] in issued_args
    assert [
        "storage", "managed-folders", "add-iam-policy-binding",
        "gs://proj-ghosty-agent-public/agents/worker-1/",
        "--member=allUsers",
        "--role=roles/storage.objectViewer",
        "--condition=None",
        "--quiet",
    ] in issued_args
    assert [
        "storage", "buckets", "remove-iam-policy-binding", "gs://proj-ghosty-agent-storage",
        "--member=serviceAccount:ghosty-worker-1-sa@proj.iam.gserviceaccount.com",
        "--role=roles/storage.objectUser",
        "--quiet",
    ] in issued_args
    assert [
        "iam", "service-accounts", "add-iam-policy-binding",
        "ghosty-worker-1-sa@proj.iam.gserviceaccount.com",
        "--member=serviceAccount:ghosty-worker-1-sa@proj.iam.gserviceaccount.com",
        "--role=roles/iam.serviceAccountTokenCreator",
        "--quiet",
    ] in issued_args
    ssh = [args for args in issued_args if args[:3] == ["compute", "ssh", "ghosty-worker-1"]]
    assert len(ssh) == 1
    assert "--tunnel-through-iap" in ssh[0]
    remote = " ".join(ssh[0])
    assert "GHOSTY_BUCKET_URI=gs://proj-ghosty-agent-storage/agents/worker-1/" in remote
    assert "GHOSTY_PUBLIC_BUCKET_URI=gs://proj-ghosty-agent-public/agents/worker-1/" in remote
    assert "GHOSTY_SIGNING_SERVICE_ACCOUNT=ghosty-worker-1-sa@proj.iam.gserviceaccount.com" in remote
    assert result.agents[0].vm_env_updated


def test_setup_storage_skips_existing_folder_creation_but_syncs(monkeypatch, cfg):
    issued = []
    cfg.storage_bucket = "existing-bucket"

    def fake_run_json(_cfg, args, **_kw):
        if args[:2] == ["services", "list"]:
            return [
                {"config": {"name": "storage.googleapis.com"}},
                {"config": {"name": "storage-api.googleapis.com"}},
            ]
        raise AssertionError(args)

    monkeypatch.setattr(gcloud, "run_json", fake_run_json)
    monkeypatch.setattr(
        gcloud,
        "exists",
        lambda _cfg, args, **_kw: args[:3] in (
            ["storage", "buckets", "describe"],
            ["storage", "managed-folders", "describe"],
        ),
    )
    monkeypatch.setattr(gcloud, "run", lambda _cfg, args, **kw: issued.append((args, kw)) or _completed())

    bootstrap.setup_storage(
        cfg,
        agent_items=[SimpleNamespace(name="worker-1", status="RUNNING")],
    )

    issued_args = [args for args, _kw in issued]
    assert not any(args[:3] == ["storage", "buckets", "create"] for args in issued_args)
    assert not any(args[:3] == ["storage", "managed-folders", "create"] for args in issued_args)
    assert any(args[:3] == ["storage", "managed-folders", "add-iam-policy-binding"] for args in issued_args)
    assert any(args[:3] == ["compute", "ssh", "ghosty-worker-1"] for args in issued_args)


def test_storage_status_reports_private_public_signing_iam_and_vm_env(monkeypatch, cfg):
    cfg.storage_enabled = True
    cfg.storage_bucket = "agent-bucket"
    cfg.storage_public_enabled = True
    cfg.storage_public_bucket = "agent-public"
    cfg.storage_signed_urls_enabled = True
    issued = []

    def fake_run_json(_cfg, args, **_kw):
        if args[:2] == ["services", "list"]:
            return [
                {"config": {"name": "storage.googleapis.com"}},
                {"config": {"name": "storage-api.googleapis.com"}},
                {"config": {"name": "iamcredentials.googleapis.com"}},
            ]
        if args[:3] == ["storage", "buckets", "get-iam-policy"]:
            return {"bindings": [{
                "role": "roles/storage.objectUser",
                "members": ["serviceAccount:ghosty-worker-1-sa@proj.iam.gserviceaccount.com"],
            }]} if args[3] == "gs://agent-bucket" else {"bindings": []}
        if args[:3] == ["storage", "managed-folders", "get-iam-policy"]:
            return {
                "bindings": [
                    {
                        "role": "roles/storage.objectUser",
                        "members": ["serviceAccount:ghosty-worker-1-sa@proj.iam.gserviceaccount.com"],
                    },
                    {
                        "role": "roles/storage.objectViewer",
                        "members": ["allUsers"],
                    },
                ]
            }
        if args[:3] == ["iam", "service-accounts", "get-iam-policy"]:
            return {
                "bindings": [{
                    "role": "roles/iam.serviceAccountTokenCreator",
                    "members": ["serviceAccount:ghosty-worker-1-sa@proj.iam.gserviceaccount.com"],
                }]
            }
        raise AssertionError(args)

    monkeypatch.setattr(gcloud, "run_json", fake_run_json)
    monkeypatch.setattr(
        gcloud,
        "exists",
        lambda _cfg, args, **_kw: args[:3] in (
            ["storage", "buckets", "describe"],
            ["storage", "managed-folders", "describe"],
        ),
    )
    monkeypatch.setattr(gcloud, "run", lambda _cfg, args, **kw: issued.append((args, kw)) or _completed(returncode=0))

    status = bootstrap.storage_status(
        cfg,
        agent_items=[SimpleNamespace(name="worker-1", status="RUNNING")],
    )

    assert status.storage_api_enabled
    assert status.storage_json_api_enabled
    assert status.signing_api_enabled
    assert status.auto_grant_enabled
    assert status.public_enabled
    assert status.signed_urls_enabled
    assert status.bucket_exists
    assert status.public_bucket_exists
    assert status.bucket_uri == "gs://agent-bucket"
    assert status.public_bucket_uri == "gs://agent-public"
    assert status.agents[0].private_folder_exists
    assert status.agents[0].has_private_folder_role
    assert status.agents[0].public_folder_exists
    assert status.agents[0].has_public_folder_role
    assert status.agents[0].public_folder_is_public
    assert status.agents[0].has_signed_url_iam
    assert status.agents[0].has_legacy_bucket_role
    assert status.agents[0].vm_env_exists is True
    assert any(args[:3] == ["compute", "ssh", "ghosty-worker-1"] for args, _kw in issued)


def test_sync_storage_updates_managed_folder_iam_and_env(monkeypatch, cfg):
    cfg.storage_bucket = "agent-bucket"
    issued = []

    monkeypatch.setattr(gcloud, "exists", lambda _cfg, args, **_kw: args[:3] == ["storage", "managed-folders", "describe"])
    monkeypatch.setattr(gcloud, "run", lambda _cfg, args, **kw: issued.append((args, kw)) or _completed())

    result = bootstrap.sync_storage(
        cfg,
        agent_items=[SimpleNamespace(name="worker-1", status="RUNNING")],
    )

    issued_args = [args for args, _kw in issued]
    assert result.agents[0].vm_env_updated
    assert result.agents[0].private_folder_iam_updated
    assert [
        "storage", "managed-folders", "add-iam-policy-binding",
        "gs://agent-bucket/agents/worker-1/",
        "--member=serviceAccount:ghosty-worker-1-sa@proj.iam.gserviceaccount.com",
        "--role=roles/storage.objectUser",
        "--condition=None",
        "--quiet",
    ] in issued_args
    assert any(args[:3] == ["storage", "buckets", "remove-iam-policy-binding"] for args in issued_args)
    assert any(args[:3] == ["compute", "ssh", "ghosty-worker-1"] for args in issued_args)


def test_upload_storage_env_retries_until_fresh_vm_accepts_ssh(monkeypatch, cfg):
    cfg.storage_bucket = "agent-bucket"
    sleeps = []
    calls = {"n": 0}

    def fake_run(_cfg, args, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(stdout="", stderr="Connection refused", returncode=255)
        return _completed()

    monkeypatch.setattr(gcloud, "run", fake_run)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = bootstrap.upload_storage_env_to_agent(
        cfg,
        SimpleNamespace(name="worker-1", status="RUNNING"),
        attempts=2,
        delay=0.25,
    )

    assert result.vm_env_updated
    assert calls["n"] == 2
    assert sleeps == [0.25]


def test_sync_storage_reports_stopped_vm_as_partial_failure(monkeypatch, cfg):
    cfg.storage_bucket = "agent-bucket"
    issued = []

    monkeypatch.setattr(gcloud, "exists", lambda _cfg, args, **_kw: args[:3] == ["storage", "managed-folders", "describe"])
    monkeypatch.setattr(gcloud, "run", lambda _cfg, args, **kw: issued.append((args, kw)) or _completed())

    result = bootstrap.sync_storage(
        cfg,
        agent_items=[SimpleNamespace(name="worker-1", status="TERMINATED")],
    )

    issued_args = [args for args, _kw in issued]
    assert not result.agents[0].vm_env_updated
    assert "TERMINATED" in result.agents[0].message
    assert any(args[:3] == ["storage", "managed-folders", "add-iam-policy-binding"] for args in issued_args)
    assert not any(args[:3] == ["compute", "ssh", "ghosty-worker-1"] for args in issued_args)


def test_disable_storage_removes_folder_public_signing_iam_env_and_clears_config(monkeypatch, cfg):
    cfg.storage_enabled = True
    cfg.storage_bucket = "agent-bucket"
    cfg.storage_location = "us-east1"
    cfg.storage_public_enabled = True
    cfg.storage_public_bucket = "agent-public"
    cfg.storage_public_location = "us-east1"
    cfg.storage_signed_urls_enabled = True
    issued = []

    monkeypatch.setattr(gcloud, "exists", lambda _cfg, args, **_kw: args[:3] == ["storage", "buckets", "describe"])
    monkeypatch.setattr(gcloud, "run", lambda _cfg, args, **kw: issued.append((args, kw)) or _completed())

    result = bootstrap.disable_storage(
        cfg,
        agent_items=[SimpleNamespace(name="worker-1", status="RUNNING")],
    )

    issued_args = [args for args, _kw in issued]
    assert not cfg.storage_enabled
    assert cfg.storage_bucket == ""
    assert cfg.storage_location == ""
    assert not cfg.storage_public_enabled
    assert cfg.storage_public_bucket == ""
    assert not cfg.storage_signed_urls_enabled
    assert result.bucket_uri == "gs://agent-bucket"
    assert result.public_bucket_uri == "gs://agent-public"
    assert [
        "storage", "managed-folders", "remove-iam-policy-binding",
        "gs://agent-bucket/agents/worker-1/",
        "--member=serviceAccount:ghosty-worker-1-sa@proj.iam.gserviceaccount.com",
        "--role=roles/storage.objectUser",
        "--condition=None",
        "--quiet",
    ] in issued_args
    assert [
        "storage", "managed-folders", "remove-iam-policy-binding",
        "gs://agent-public/agents/worker-1/",
        "--member=allUsers",
        "--role=roles/storage.objectViewer",
        "--condition=None",
        "--quiet",
    ] in issued_args
    assert [
        "iam", "service-accounts", "remove-iam-policy-binding",
        "ghosty-worker-1-sa@proj.iam.gserviceaccount.com",
        "--member=serviceAccount:ghosty-worker-1-sa@proj.iam.gserviceaccount.com",
        "--role=roles/iam.serviceAccountTokenCreator",
        "--quiet",
    ] in issued_args
    assert any(args[:3] == ["compute", "ssh", "ghosty-worker-1"] and 'rm -f "$env_path"' in " ".join(args) for args in issued_args)
    assert not any(args[:3] == ["storage", "buckets", "delete"] for args in issued_args)
