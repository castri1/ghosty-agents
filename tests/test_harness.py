"""Tests for the live harness snapshot and renderer."""

from io import StringIO

from rich.console import Console

from ghosty import bootstrap, gcloud, harness
from ghosty.models import Agent, Config


def _agent():
    return Agent(
        name="alba-nury",
        instance="ghosty-alba-nury",
        status="RUNNING",
        zone="us-east1-b",
        machine_type="e2-small",
        internal_ip="10.10.0.2",
    )


def _storage_status(cfg):
    return bootstrap.StorageStatus(
        storage_api_enabled=True,
        storage_json_api_enabled=True,
        signing_api_enabled=True,
        auto_grant_enabled=True,
        public_enabled=True,
        signed_urls_enabled=True,
        bucket="agent-bucket",
        bucket_uri="gs://agent-bucket",
        public_bucket="public-bucket",
        public_bucket_uri="gs://public-bucket",
        location="us-east1",
        public_location="us-east1",
        storage_class=cfg.storage_class,
        public_storage_class=cfg.storage_public_class,
        role=cfg.storage_role,
        public_viewer_role=cfg.storage_public_viewer_role,
        bucket_exists=True,
        public_bucket_exists=True,
        agents=[
            bootstrap.StorageStatusAgent(
                agent="alba-nury",
                service_account="ghosty-alba-nury-sa@proj.iam.gserviceaccount.com",
                private_folder_uri="gs://agent-bucket/agents/alba-nury/",
                private_folder_exists=True,
                has_private_folder_role=True,
                public_folder_uri="gs://public-bucket/agents/alba-nury/",
                public_folder_exists=True,
                has_public_folder_role=True,
                public_folder_is_public=True,
                has_signed_url_iam=True,
                has_legacy_bucket_role=False,
                vm_env_path="~/.config/hermes/storage.env",
                vm_env_exists=None,
            )
        ],
    )


def test_collect_harness_reports_attached_capabilities(monkeypatch):
    cfg = Config(
        project_id="proj",
        account="me@example.com",
        billing_account_id="billing",
        google_ai_enabled=True,
        google_chat_projects={"alba-nury": "ghosty-agent-chat"},
        webhook_gateways={"alba-nury": {"crm": {"url": "https://hook.example.run.app"}}},
        storage_enabled=True,
        storage_bucket="agent-bucket",
        storage_public_enabled=True,
        storage_public_bucket="public-bucket",
        storage_signed_urls_enabled=True,
    )
    agent = _agent()
    storage_calls = []

    monkeypatch.setattr(harness.agents, "get_agent", lambda _cfg, _name: agent)
    monkeypatch.setattr(
        harness.bootstrap,
        "storage_status",
        lambda _cfg, agent_items, check_vm_env=True: storage_calls.append((agent_items, check_vm_env)) or _storage_status(cfg),
    )
    monkeypatch.setattr(
        harness.bootstrap,
        "google_ai_status",
        lambda _cfg, agent_names=None: bootstrap.GoogleAiStatus(
            api_enabled=True,
            auto_grant_enabled=True,
            api="aiplatform.googleapis.com",
            role="roles/aiplatform.user",
            agents=[
                bootstrap.GoogleAiAgentStatus(
                    agent="alba-nury",
                    service_account="ghosty-alba-nury-sa@proj.iam.gserviceaccount.com",
                    has_role=True,
                )
            ],
        ),
    )
    monkeypatch.setattr(
        harness.bootstrap,
        "nat_status",
        lambda _cfg: bootstrap.NatStatus(
            router_exists=True,
            nat_exists=True,
            router="ghosty-router",
            nat="ghosty-nat",
            region="us-east1",
            network="ghosty-vpc",
            subnet="ghosty-subnet",
        ),
    )

    snapshot = harness.collect_harness(cfg, agent)
    caps = {cap.name: cap for cap in snapshot.capabilities}

    assert caps["Connect"].state == harness.READY
    assert caps["Chat"].state == harness.ATTACHED
    assert caps["Notifications"].state == harness.ATTACHED
    assert caps["Storage"].state == harness.ATTACHED
    assert caps["Models"].state == harness.READY
    assert caps["Internet"].state == harness.READY
    assert ("crm url", "https://hook.example.run.app") in caps["Notifications"].advanced
    assert storage_calls == [([agent], False)]


def test_collect_harness_detects_chat_from_local_key_file(monkeypatch, tmp_path):
    monkeypatch.setenv("GHOSTY_CONFIG_DIR", str(tmp_path))
    key_dir = tmp_path / "google-chat" / "alba-nury"
    key_dir.mkdir(parents=True)
    (key_dir / "ghosty-agent-chat-service-account.json").write_text("{}")

    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    agent = _agent()

    monkeypatch.setattr(harness.agents, "get_agent", lambda _cfg, _name: agent)
    monkeypatch.setattr(
        harness.bootstrap,
        "google_ai_status",
        lambda _cfg, agent_names=None: bootstrap.GoogleAiStatus(
            api_enabled=False,
            auto_grant_enabled=False,
            api="aiplatform.googleapis.com",
            role="roles/aiplatform.user",
            agents=[],
        ),
    )
    monkeypatch.setattr(
        harness.bootstrap,
        "nat_status",
        lambda _cfg: bootstrap.NatStatus(
            router_exists=False,
            nat_exists=False,
            router="ghosty-router",
            nat="ghosty-nat",
            region="us-east1",
            network="ghosty-vpc",
            subnet="ghosty-subnet",
        ),
    )

    snapshot = harness.collect_harness(cfg, agent)
    chat = next(cap for cap in snapshot.capabilities if cap.name == "Chat")

    assert chat.state == harness.ATTACHED
    assert ("project", "ghosty-agent-chat") in chat.advanced
    assert ("detected from", "local key file") in chat.advanced


def test_collect_harness_marks_one_failed_capability_unknown(monkeypatch):
    cfg = Config(
        project_id="proj",
        account="me@example.com",
        billing_account_id="billing",
        storage_enabled=True,
        storage_bucket="agent-bucket",
    )
    agent = _agent()

    monkeypatch.setattr(harness.agents, "get_agent", lambda _cfg, _name: agent)
    monkeypatch.setattr(
        harness.bootstrap,
        "storage_status",
        lambda *_args, **_kw: (_ for _ in ()).throw(gcloud.GcloudError(["storage", "buckets", "describe"], 1, "boom")),
    )
    monkeypatch.setattr(
        harness.bootstrap,
        "google_ai_status",
        lambda _cfg, agent_names=None: bootstrap.GoogleAiStatus(
            api_enabled=False,
            auto_grant_enabled=False,
            api="aiplatform.googleapis.com",
            role="roles/aiplatform.user",
            agents=[],
        ),
    )
    monkeypatch.setattr(
        harness.bootstrap,
        "nat_status",
        lambda _cfg: bootstrap.NatStatus(
            router_exists=False,
            nat_exists=False,
            router="ghosty-router",
            nat="ghosty-nat",
            region="us-east1",
            network="ghosty-vpc",
            subnet="ghosty-subnet",
        ),
    )

    snapshot = harness.collect_harness(cfg, agent)
    caps = {cap.name: cap for cap in snapshot.capabilities}

    assert caps["Storage"].state == harness.UNKNOWN
    assert caps["Storage"].summary == "unknown"
    assert caps["Connect"].state == harness.READY
    assert caps["Internet"].state == harness.OFF


def test_render_harness_uses_friendly_primary_labels():
    agent = _agent()
    snapshot = harness.HarnessSnapshot(
        agent=agent,
        capabilities=[
            harness.HarnessCapability("Connect", harness.READY, "ready"),
            harness.HarnessCapability("Chat", harness.ATTACHED, "attached", advanced=[("topic", "projects/proj/topics/chat")]),
            harness.HarnessCapability("Notifications", harness.ATTACHED, "1 path"),
            harness.HarnessCapability("Storage", harness.ATTACHED, "ready"),
            harness.HarnessCapability("Models", harness.READY, "ready"),
            harness.HarnessCapability("Internet", harness.OFF, "off", shared=True),
        ],
        advanced=[("instance", agent.instance)],
    )

    stream = StringIO()
    console = Console(file=stream, width=120, force_terminal=False, color_system=None)
    console.print(harness.render_harness(snapshot, frame=1))
    output = stream.getvalue()

    for label in ("Connect", "Chat", "Notifications", "Storage", "Models", "Internet"):
        assert label in output
    assert "VOXEL HARNESS" in output
    assert "Beacons" in output
    assert "@ alba-nury" in output
    assert "ADVANCED VALUES" in output
    for capability in snapshot.capabilities:
        assert "Pub/Sub" not in capability.name
        assert "Cloud Run" not in capability.name
        assert "IAM" not in capability.name
        assert "SSH" not in capability.name
