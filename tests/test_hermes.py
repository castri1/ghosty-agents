"""Tests for Hermes installation and configuration helpers."""

from types import SimpleNamespace

from ghosty import hermes
from ghosty.models import Agent, Config


def _agent(status="RUNNING"):
    return Agent(
        name="alba-nury",
        instance="ghosty-alba-nury",
        status=status,
        zone="us-east1-b",
        machine_type="e2-small",
        internal_ip="10.10.0.2",
    )


def _completed(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_install_command_uses_vendor_installer_and_gateway():
    command = hermes.install_command()

    assert "https://hermes-agent.nousresearch.com/install.sh" in command
    assert "curl -fsSL" in command
    assert "-o /tmp/ghosty-hermes-install.sh" in command
    assert "--skip-setup --non-interactive --branch main" in command
    assert '"$HOME/.local/bin/hermes" gateway --accept-hooks install' in command
    assert "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash" not in command


def test_install_command_accepts_branch_commit_and_skip_browser():
    command = hermes.install_command(branch="release", commit="abc123", skip_browser=True)

    assert "--branch release" in command
    assert "--commit abc123" in command
    assert "--skip-browser" in command


def test_install_hermes_runs_iap_ssh(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    issued = []

    def fake_run(_cfg, args, **kwargs):
        issued.append((args, kwargs))
        return _completed()

    monkeypatch.setattr(hermes.gcloud, "run", fake_run)

    result = hermes.install_hermes(cfg, _agent(), branch="main")

    assert result.installed
    assert result.gateway_started
    args, kwargs = issued[0]
    assert args[:3] == ["compute", "ssh", "ghosty-alba-nury"]
    assert "--tunnel-through-iap" in args
    assert kwargs == {"check": False}
    command = next(arg for arg in args if arg.startswith("--command="))
    assert "hermes-agent.nousresearch.com/install.sh" in command
    assert "gateway --accept-hooks install" in command


def test_install_hermes_retries_fresh_vm_ssh_race(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    calls = []
    sleeps = []

    def fake_run(_cfg, args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            return _completed(stderr="ERROR: [/usr/bin/ssh] exited with return code [255].", returncode=255)
        return _completed()

    monkeypatch.setattr(hermes.gcloud, "run", fake_run)
    monkeypatch.setattr(hermes.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = hermes.install_hermes(cfg, _agent(), attempts=2, delay=0.25)

    assert result.installed
    assert len(calls) == 2
    assert sleeps == [0.25]


def test_configure_command_writes_vertex_defaults():
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")

    command = hermes.configure_command(cfg)

    assert 'config set model.provider vertex' in command
    assert 'config set model.default google/gemini-3.1-pro-preview' in command
    assert 'config set vertex.project_id proj' in command
    assert 'config set vertex.region global' in command


def test_configure_hermes_reports_failure(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")

    monkeypatch.setattr(
        hermes.gcloud,
        "run",
        lambda _cfg, args, **kwargs: _completed(stderr="missing hermes", returncode=127),
    )

    result = hermes.configure_hermes(cfg, _agent())

    assert not result.configured
    assert result.provider == "vertex"
    assert result.model == "google/gemini-3.1-pro-preview"
    assert "missing hermes" in result.message


def test_hermes_status_parses_remote_json(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    payload = (
        '{"command_exists": true, "config_exists": true, "env_exists": true, '
        '"gateway_active": true, "installed": true, "model": "google/gemini", '
        '"provider": "vertex", "vertex_project": "proj", "vertex_region": "global", '
        '"version": "hermes 1.2.3"}\n'
    )

    monkeypatch.setattr(hermes.gcloud, "run", lambda _cfg, args, **kwargs: _completed(stdout=payload))

    status = hermes.hermes_status(cfg, _agent())

    assert status.installed
    assert status.command_exists
    assert status.env_exists
    assert status.config_exists
    assert status.gateway_active
    assert status.provider == "vertex"
    assert status.model == "google/gemini"
    assert status.vertex_project == "proj"
    assert status.vertex_region == "global"
    assert status.version == "hermes 1.2.3"


def test_hermes_status_handles_missing_or_stopped_agent(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")

    status = hermes.hermes_status(cfg, _agent(status="TERMINATED"))

    assert not status.installed
    assert "TERMINATED" in status.message
