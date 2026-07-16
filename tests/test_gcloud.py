"""Tests for the gcloud wrapper flag injection (subprocess mocked)."""

from types import SimpleNamespace

import pytest

from ghosty import gcloud
from ghosty.models import Config


@pytest.fixture(autouse=True)
def _fake_gcloud_on_path(monkeypatch):
    monkeypatch.setattr(gcloud.shutil, "which", lambda _: "/usr/bin/gcloud")


def _capture(monkeypatch):
    captured = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        return SimpleNamespace(stdout="[]", stderr="", returncode=0)

    monkeypatch.setattr(gcloud.subprocess, "run", fake_run)
    return captured


def test_global_flags_injected(monkeypatch):
    captured = _capture(monkeypatch)
    cfg = Config(project_id="proj", account="me@x.com", gcloud_config_name="ghosty-agents")
    gcloud.run(cfg, ["compute", "instances", "list"])
    cmd = captured["cmd"]
    assert cmd[0] == "gcloud"
    assert "--configuration" in cmd and "ghosty-agents" in cmd
    assert "--account" in cmd and "me@x.com" in cmd
    assert "--project" in cmd and "proj" in cmd


def test_no_project_omits_project(monkeypatch):
    captured = _capture(monkeypatch)
    cfg = Config(project_id="proj", account="me@x.com")
    gcloud.run(cfg, ["projects", "create", "proj"], no_project=True)
    assert "--project" not in captured["cmd"]


def test_error_raises(monkeypatch):
    monkeypatch.setattr(gcloud.shutil, "which", lambda _: "/usr/bin/gcloud")

    def fake_run(cmd, capture_output, text):
        return SimpleNamespace(stdout="", stderr="boom", returncode=2)

    monkeypatch.setattr(gcloud.subprocess, "run", fake_run)
    cfg = Config(project_id="proj")
    with pytest.raises(gcloud.GcloudError) as exc:
        gcloud.run(cfg, ["compute", "instances", "list"])
    assert exc.value.returncode == 2
    assert "boom" in exc.value.stderr


def test_missing_gcloud_raises(monkeypatch):
    monkeypatch.setattr(gcloud.shutil, "which", lambda _: None)
    with pytest.raises(gcloud.GcloudNotFound):
        gcloud.run(Config(), ["version"])
