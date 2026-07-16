"""Tests for naming + config helpers (no GCP)."""

import pytest

from ghosty.models import (
    Config,
    agent_name_from_instance,
    agent_sa_id,
    instance_name,
    sanitize_agent_name,
)


def test_sanitize_agent_name_basic():
    assert sanitize_agent_name("Worker 1") == "worker-1"
    assert sanitize_agent_name("  My__Agent!! ") == "my-agent"


def test_sanitize_prefixes_digit_start():
    assert sanitize_agent_name("1st").startswith("a")


def test_sanitize_rejects_empty():
    with pytest.raises(ValueError):
        sanitize_agent_name("!!!")


def test_instance_and_sa_names():
    assert instance_name("worker-1") == "ghosty-worker-1"
    assert agent_sa_id("worker-1") == "ghosty-worker-1-sa"


def test_sa_id_truncates_to_30():
    long = "a-very-long-agent-name-that-exceeds-limits"
    assert len(agent_sa_id(long)) <= 30


def test_agent_name_from_instance_roundtrip():
    assert agent_name_from_instance(instance_name("worker-1")) == "worker-1"


def test_config_missing_required():
    cfg = Config()
    assert set(cfg.missing_required()) == {"project_id", "billing_account_id", "account"}
    cfg.project_id = "p"
    cfg.billing_account_id = "b"
    cfg.account = "a@example.com"
    assert cfg.missing_required() == []


def test_sa_email():
    cfg = Config(project_id="proj")
    assert cfg.sa_email("worker-1") == "ghosty-worker-1-sa@proj.iam.gserviceaccount.com"
