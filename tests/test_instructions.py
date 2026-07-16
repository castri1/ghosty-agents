"""Tests for agent instruction prompt delivery."""

from __future__ import annotations

import subprocess

from ghosty import bootstrap, gcloud, instructions
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


def test_render_chat_instruction_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("GHOSTY_CONFIG_DIR", str(tmp_path))
    cfg = Config(
        project_id="ghosty-agents",
        account="me@example.com",
        billing_account_id="billing",
        google_chat_projects={"alba-nury": "ghosty-agent-chat"},
    )

    prompt = instructions.render_instruction_prompt(cfg, _agent(), "chat")

    assert "Configure Google Chat" in prompt
    assert "ghosty-agent-chat" in prompt
    assert "projects/ghosty-agent-chat/subscriptions/alba-nury-chat-events-sub" in prompt
    assert "~/.config/hermes/google-chat-service-account.json" in prompt


def test_render_notifications_instruction_prompt(monkeypatch):
    cfg = Config(
        project_id="proj",
        account="me@example.com",
        billing_account_id="billing",
        webhook_gateways={"alba-nury": {"crm": {"provider": "generic", "url": "https://hook.example"}}},
    )

    prompt = instructions.render_instruction_prompt(cfg, _agent(), "notifications", name="crm")

    assert "Configure Notifications" in prompt
    assert "https://hook.example" in prompt
    assert "projects/proj/subscriptions/alba-nury-webhook-crm-events-sub" in prompt
    assert "~/.config/hermes/webhooks/crm.env" in prompt
    assert "topsecret" not in prompt


def test_render_storage_and_models_instruction_prompts():
    cfg = Config(
        project_id="proj",
        account="me@example.com",
        billing_account_id="billing",
        storage_bucket="agent-bucket",
        storage_public_enabled=True,
        storage_public_bucket="public-bucket",
        storage_signed_urls_enabled=True,
        google_ai_enabled=True,
    )

    storage = instructions.render_instruction_prompt(cfg, _agent(), "storage")
    models = instructions.render_instruction_prompt(cfg, _agent(), "models")

    assert "gs://agent-bucket" in storage
    assert "gs://public-bucket" in storage
    assert "~/.config/hermes/storage.env" in storage
    assert "aiplatform.googleapis.com" in models
    assert "ghosty-alba-nury-sa@proj.iam.gserviceaccount.com" in models


def test_deliver_instruction_uploads_prompt_and_runs_hermes(monkeypatch, tmp_path):
    monkeypatch.setenv("GHOSTY_CONFIG_DIR", str(tmp_path))
    cfg = Config(
        project_id="proj",
        account="me@example.com",
        billing_account_id="billing",
        storage_bucket="agent-bucket",
    )
    issued = []

    def fake_run(_cfg, args, **kwargs):
        issued.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(instructions.gcloud, "run", fake_run)

    result = instructions.deliver_instruction(cfg, _agent(), "storage")

    assert result.delivered
    assert result.uploaded
    assert result.remote_prompt_path == "~/.config/hermes/inbox/storage-setup.md"
    assert result.local_prompt_path.is_file()
    assert issued[0][0][:3] == ["compute", "ssh", "ghosty-alba-nury"]
    assert "--tunnel-through-iap" in issued[0][0]
    assert issued[1][0][:2] == ["compute", "scp"]
    final_ssh = issued[2][0]
    command = next(arg for arg in final_ssh if arg.startswith("--command="))
    assert "GHOSTY_PROMPT_FILE" in command
    assert "GHOSTY_AGENT_NAME=alba-nury" in command
    assert "GHOSTY_SERVICE_NAME=storage" in command
    assert "timeout 600s bash -lc" in command
    assert '"$HOME/.local/bin/hermes" -z "$(cat "$GHOSTY_PROMPT_FILE")"' in command


def test_deliver_instruction_uses_configured_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv("GHOSTY_CONFIG_DIR", str(tmp_path))
    cfg = Config(
        project_id="proj",
        account="me@example.com",
        billing_account_id="billing",
        storage_bucket="agent-bucket",
        agent_instruction_timeout_seconds=45,
    )
    issued = []

    def fake_run(_cfg, args, **kwargs):
        issued.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(instructions.gcloud, "run", fake_run)

    result = instructions.deliver_instruction(cfg, _agent(), "storage")

    assert result.delivered
    command = next(arg for arg in issued[2] if arg.startswith("--command="))
    assert "timeout 45s bash -lc" in command


def test_deliver_instruction_keeps_setup_success_on_hermes_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("GHOSTY_CONFIG_DIR", str(tmp_path))
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing", storage_bucket="agent-bucket")
    calls = []

    def fake_run(_cfg, args, **kwargs):
        calls.append(args)
        if len(calls) == 3:
            return subprocess.CompletedProcess(args, 42, stdout="", stderr="hermes failed")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(instructions.gcloud, "run", fake_run)

    result = instructions.deliver_instruction(cfg, _agent(), "storage")

    assert result.uploaded
    assert not result.delivered
    assert "hermes failed" in result.message
    assert "SSH exited with return code 42" in result.message


def test_deliver_instruction_reports_timeout_clearly(monkeypatch, tmp_path):
    monkeypatch.setenv("GHOSTY_CONFIG_DIR", str(tmp_path))
    cfg = Config(
        project_id="proj",
        account="me@example.com",
        billing_account_id="billing",
        storage_bucket="agent-bucket",
        agent_instruction_timeout_seconds=12,
    )
    calls = []

    def fake_run(_cfg, args, **kwargs):
        calls.append(args)
        if len(calls) == 3:
            return subprocess.CompletedProcess(args, 124, stdout="", stderr="iap warning")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(instructions.gcloud, "run", fake_run)

    result = instructions.deliver_instruction(cfg, _agent(), "storage")

    assert not result.delivered
    assert "Hermes command timed out after 12 seconds" in result.message
    assert "iap warning" in result.message


def test_deliver_instruction_skips_stopped_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("GHOSTY_CONFIG_DIR", str(tmp_path))
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing", storage_bucket="agent-bucket")
    monkeypatch.setattr(
        instructions.gcloud,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(gcloud.GcloudError(["compute", "ssh"], 1, "should not run")),
    )

    result = instructions.deliver_instruction(cfg, _agent(status="TERMINATED"), "storage")

    assert not result.uploaded
    assert not result.delivered
    assert "TERMINATED" in result.message
