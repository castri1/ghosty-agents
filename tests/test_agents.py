"""Tests for agents.py with the gcloud layer mocked."""

import json
from types import SimpleNamespace

import pytest

from ghosty import agents, bootstrap, gcloud
from ghosty.models import Agent, Config


@pytest.fixture
def cfg():
    return Config(project_id="proj", account="me@example.com", billing_account_id="b")


def _completed(stdout="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


def test_list_agents_parses_and_filters(monkeypatch, cfg):
    payload = [
        {
            "name": "ghosty-worker-1",
            "status": "RUNNING",
            "zone": "https://x/zones/us-central1-a",
            "machineType": "https://x/machineTypes/e2-small",
            "creationTimestamp": "2026-06-16T22:00:00.000-07:00",
            "labels": {"managed-by": "ghosty-agents", "ghosty-agent": "worker-1"},
            "networkInterfaces": [{"networkIP": "10.10.0.2"}],
        }
    ]

    def fake_run(config, args, **kw):
        assert args[:3] == ["compute", "instances", "list"]
        # filter must scope to the ghosty label
        assert any("labels.managed-by=ghosty-agents" in a for a in args)
        return _completed(json.dumps(payload))

    monkeypatch.setattr(gcloud, "run", fake_run)
    result = agents.list_agents(cfg)
    assert len(result) == 1
    a = result[0]
    assert a.name == "worker-1"
    assert a.instance == "ghosty-worker-1"
    assert a.status == "RUNNING"
    assert a.zone == "us-central1-a"
    assert a.machine_type == "e2-small"
    assert a.internal_ip == "10.10.0.2"


def test_create_agent_skips_when_exists(monkeypatch, cfg):
    calls = []

    def fake_exists(config, args, **kw):
        # the existence check for the instance
        return args[:3] == ["compute", "instances", "describe"]

    def fake_run(config, args, **kw):
        calls.append(args)
        if args[:3] == ["compute", "instances", "describe"]:
            return _completed(json.dumps({
                "name": "ghosty-worker-1", "status": "RUNNING",
                "zone": "z/zones/us-central1-a",
                "machineType": "m/machineTypes/e2-small",
                "labels": {"ghosty-agent": "worker-1"},
                "networkInterfaces": [{"networkIP": "10.0.0.2"}],
            }))
        return _completed("")

    monkeypatch.setattr(gcloud, "exists", fake_exists)
    monkeypatch.setattr(gcloud, "run", fake_run)

    a = agents.create_agent(cfg, "worker-1")
    assert a.name == "worker-1"
    # must NOT have issued an instances create
    assert not any(c[:3] == ["compute", "instances", "create"] for c in calls)


def test_create_agent_creates_when_absent(monkeypatch, cfg):
    monkeypatch.setattr(agents.time, "sleep", lambda *_: None)
    state = {"instance": False, "sa": False}
    issued = []

    def fake_exists(config, args, **kw):
        if args[:3] == ["compute", "instances", "describe"]:
            return state["instance"]
        if args[:3] == ["iam", "service-accounts", "describe"]:
            return state["sa"]
        return False

    def fake_run(config, args, **kw):
        issued.append(args)
        if args[:3] == ["iam", "service-accounts", "create"]:
            state["sa"] = True
            return _completed("")
        if args[:3] == ["compute", "instances", "create"]:
            state["instance"] = True
            return _completed("")
        if args[:3] == ["compute", "instances", "describe"]:
            return _completed(json.dumps({
                "name": "ghosty-worker-1", "status": "RUNNING",
                "zone": "z/zones/us-central1-a",
                "machineType": "m/machineTypes/e2-small",
                "labels": {"ghosty-agent": "worker-1"},
                "networkInterfaces": [{"networkIP": "10.0.0.2"}],
            }))
        return _completed("")

    monkeypatch.setattr(gcloud, "exists", fake_exists)
    monkeypatch.setattr(gcloud, "run", fake_run)

    a = agents.create_agent(cfg, "worker-1")
    assert a.status == "RUNNING"
    create = [c for c in issued if c[:3] == ["compute", "instances", "create"]]
    assert len(create) == 1
    flat = " ".join(create[0])
    # hardening assertions
    assert "--no-address" in create[0]
    assert "--shielded-secure-boot" in create[0]
    assert "enable-oslogin=TRUE" in flat
    assert "managed-by=ghosty-agents" in flat
    assert "ghosty-agent=worker-1" in flat


def test_create_agent_leaves_storage_sync_to_callers(monkeypatch, cfg):
    cfg.storage_enabled = True
    cfg.storage_bucket = "agent-bucket"
    synced = []

    created = Agent(
        name="worker-1",
        instance="ghosty-worker-1",
        status="RUNNING",
        zone="us-central1-a",
        machine_type="e2-small",
    )

    monkeypatch.setattr(agents, "agent_exists", lambda _cfg, _agent: False)
    monkeypatch.setattr(agents, "_ensure_agent_sa", lambda _cfg, _agent: cfg.sa_email("worker-1"))
    monkeypatch.setattr(agents, "get_agent", lambda _cfg, _agent: created)
    monkeypatch.setattr(gcloud, "run", lambda _cfg, _args, **_kw: _completed())
    monkeypatch.setattr(
        bootstrap,
        "sync_storage_for_agent",
        lambda config, agent: synced.append((config, agent)),
    )

    result = agents.create_agent(cfg, "worker-1")

    assert result == created
    assert synced == []


def test_ensure_sa_retries_on_propagation(monkeypatch, cfg):
    monkeypatch.setattr(agents.time, "sleep", lambda *_: None)
    # SA doesn't exist initially; after "create" it does.
    state = {"sa": False}

    def fake_exists(config, args, **kw):
        if args[:3] == ["iam", "service-accounts", "describe"]:
            return state["sa"]
        return False

    calls = {"binding": 0}

    def fake_run(config, args, **kw):
        if args[:3] == ["iam", "service-accounts", "create"]:
            state["sa"] = True
            return _completed("")
        if args[1:3] == ["add-iam-policy-binding"] or args[:3] == ["iam", "service-accounts", "add-iam-policy-binding"]:
            calls["binding"] += 1
            if calls["binding"] == 1:
                raise gcloud.GcloudError(args, 1, "Service account ... does not exist.")
            return _completed("")
        return _completed("")

    monkeypatch.setattr(gcloud, "exists", fake_exists)
    monkeypatch.setattr(gcloud, "run", fake_run)

    email = agents._ensure_agent_sa(cfg, "worker-1")
    assert email == cfg.sa_email("worker-1")
    # first binding failed (propagation) then retried -> at least 2 binding calls
    assert calls["binding"] >= 2


def test_ensure_sa_grants_google_ai_when_enabled(monkeypatch, cfg):
    monkeypatch.setattr(agents.time, "sleep", lambda *_: None)
    cfg.google_ai_enabled = True
    state = {"sa": False}
    google_ai_grants = []

    def fake_exists(config, args, **kw):
        if args[:3] == ["iam", "service-accounts", "describe"]:
            return state["sa"]
        return False

    def fake_run(config, args, **kw):
        if args[:3] == ["iam", "service-accounts", "create"]:
            state["sa"] = True
        return _completed("")

    monkeypatch.setattr(gcloud, "exists", fake_exists)
    monkeypatch.setattr(gcloud, "run", fake_run)
    monkeypatch.setattr(
        bootstrap,
        "grant_google_ai_to_agent",
        lambda config, agent: google_ai_grants.append((config, agent)),
    )

    agents._ensure_agent_sa(cfg, "worker-1")

    assert google_ai_grants == [(cfg, "worker-1")]


def test_retry_propagation_reraises_other_errors(monkeypatch, cfg):
    monkeypatch.setattr(agents.time, "sleep", lambda *_: None)

    def boom():
        raise gcloud.GcloudError(["x"], 1, "PERMISSION_DENIED: nope")

    with pytest.raises(gcloud.GcloudError):
        agents._retry_propagation(boom, attempts=3)


def test_retry_transient_recovers(monkeypatch):
    monkeypatch.setattr(agents.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise gcloud.GcloudError(["x"], 1, "Permission denied on 'locations/us-east1-a'")
        return "ok"

    assert agents._retry_transient(flaky, attempts=3) == "ok"
    assert calls["n"] == 2


def test_retry_transient_reraises_non_transient(monkeypatch):
    monkeypatch.setattr(agents.time, "sleep", lambda *_: None)

    def boom():
        raise gcloud.GcloudError(["x"], 1, "Quota 'CPUS' exceeded")

    with pytest.raises(gcloud.GcloudError):
        agents._retry_transient(boom, attempts=3)


def test_destroy_agent_deletes_vm_and_sa(monkeypatch, cfg):
    issued = []
    monkeypatch.setattr(gcloud, "exists", lambda c, a, **k: True)
    monkeypatch.setattr(gcloud, "run", lambda c, a, **k: issued.append(a) or _completed(""))

    agents.destroy_agent(cfg, "worker-1")
    vm_delete = next(i for i, a in enumerate(issued) if a[:3] == ["compute", "instances", "delete"])
    logging_remove = next(i for i, a in enumerate(issued) if a[:3] == ["projects", "remove-iam-policy-binding", "proj"] and "--role=roles/logging.logWriter" in a)
    monitoring_remove = next(i for i, a in enumerate(issued) if a[:3] == ["projects", "remove-iam-policy-binding", "proj"] and "--role=roles/monitoring.metricWriter" in a)
    sa_delete = next(i for i, a in enumerate(issued) if a[:3] == ["iam", "service-accounts", "delete"])
    assert vm_delete < logging_remove < sa_delete
    assert vm_delete < monitoring_remove < sa_delete
