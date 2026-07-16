"""Tests for the browser-based harness view."""

from __future__ import annotations

import json
from urllib.request import urlopen

from ghosty import harness, harness_browser
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


def _snapshot():
    agent = _agent()
    return harness.HarnessSnapshot(
        agent=agent,
        capabilities=[
            harness.HarnessCapability("Connect", harness.READY, "ready", detail="ready to connect"),
            harness.HarnessCapability(
                "Chat",
                harness.ATTACHED,
                "attached",
                detail="project ghosty-agent-chat",
                advanced=[("topic", "projects/ghosty-agent-chat/topics/alba-nury-chat-events")],
            ),
            harness.HarnessCapability("Notifications", harness.ATTACHED, "1 path", advanced=[("crm url", "https://hook.example")]),
            harness.HarnessCapability("Storage", harness.ATTACHED, "ready", advanced=[("private files", "gs://agent-bucket")]),
            harness.HarnessCapability("Models", harness.READY, "ready", advanced=[("api", "aiplatform.googleapis.com")]),
            harness.HarnessCapability("Internet", harness.OFF, "off", shared=True),
        ],
        advanced=[("instance", agent.instance), ("private IP", agent.internal_ip)],
    )


def test_snapshot_to_dict_serializes_browser_contract():
    payload = harness.snapshot_to_dict(_snapshot(), refresh_interval=3.0, generated_at=123.0)

    assert payload["generated_at"] == 123.0
    assert payload["refresh_interval"] == 3.0
    assert payload["agent"]["name"] == "alba-nury"
    assert payload["agent"]["status"] == "RUNNING"
    assert payload["agent"]["advanced"] == [
        {"label": "instance", "value": "ghosty-alba-nury"},
        {"label": "private IP", "value": "10.10.0.2"},
    ]
    chat = next(cap for cap in payload["capabilities"] if cap["name"] == "Chat")
    assert chat["state"] == harness.ATTACHED
    assert chat["summary"] == "attached"
    assert chat["advanced"] == [{"label": "topic", "value": "projects/ghosty-agent-chat/topics/alba-nury-chat-events"}]
    internet = next(cap for cap in payload["capabilities"] if cap["name"] == "Internet")
    assert internet["shared"] is True


def test_render_app_html_contains_svg_app_without_external_dependencies():
    html = harness_browser.render_app_html(refresh_interval=2.0)

    assert "<svg" in html
    assert "Animated Ghosty agent harness diagram" in html
    assert "/snapshot" in html
    for label in ("Connect", "Chat", "Notifications", "Storage", "Models", "Internet"):
        assert label in html
        assert f"id=\"icon-{label.lower()}\"" in html
    assert "https://cdn" not in html
    assert "Pub/Sub" not in html
    assert "Cloud Run" not in html
    assert "SSH" not in html


def test_render_app_html_uses_anchored_card_nodes_not_generic_badges():
    html = harness_browser.render_app_html(refresh_interval=2.0)

    assert "const NODE_DEFS" in html
    assert "const AGENT_NODE" in html
    assert "function anchorPoint" in html
    assert "agentAnchor" in html
    assert "nodeDef.anchor" in html
    assert "class=\"node-card\"" in html
    assert "class=\"icon-plate\"" in html
    assert "class=\"node-icon\"" in html
    assert "class=\"status-dot\"" in html
    assert "class=\"badge\"" not in html
    assert "const ICONS" not in html
    assert "circle class=\"outer\"" not in html


def test_render_app_html_has_render_app_smoke_hooks():
    html = harness_browser.render_app_html(refresh_interval=2.0)

    for hook in ("renderLinks", "renderAgent", "renderNodes", "renderDetails"):
        assert f"{hook}();" in html
    assert "function renderApp(snapshot)" in html
    assert "renderApp(snapshot)" in html
    for element_id in ("links", "packets", "agent-core", "nodes", "details-title", "meaning", "enables", "advanced"):
        assert f'id="{element_id}"' in html


def test_render_app_html_contains_friendly_node_descriptions():
    html = harness_browser.render_app_html(refresh_interval=2.0)

    assert "const NODE_DESCRIPTIONS" in html
    assert "Current state" in html
    assert "What this means" in html
    assert "What this enables" in html
    assert "Advanced values" in html
    assert html.index("What this enables") < html.index("Advanced values")
    assert "NODE_DESCRIPTIONS.Agent" in html
    assert "NODE_DESCRIPTIONS[cap.name]" in html
    for copy in (
        "The private worker where this agent runs.",
        "A protected way for you to enter the agent's machine when you need to guide or inspect it.",
        "A conversation doorway between people and the agent.",
        "A safe receiving point for outside systems to tell the agent something happened.",
        "A private file space assigned to the agent.",
        "Access to Google AI models through the agent's cloud identity.",
        "Shared outbound access for private agents.",
    ):
        assert copy in html


def test_server_serves_index_health_and_snapshot(monkeypatch):
    cfg = Config(project_id="proj", account="me@example.com", billing_account_id="billing")
    agent = _agent()
    calls = []

    def fake_collect(_cfg, selected):
        calls.append(selected.name)
        return _snapshot()

    monkeypatch.setattr(harness_browser.harness, "collect_harness", fake_collect)
    server = harness_browser.start_server(cfg, agent, refresh_interval=1.5)

    try:
        assert server.url.startswith("http://127.0.0.1:")
        with urlopen(server.url, timeout=5) as response:
            index = response.read().decode("utf-8")
        with urlopen(server.url + "health", timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
        with urlopen(server.url + "snapshot", timeout=5) as response:
            snapshot = json.loads(response.read().decode("utf-8"))
    finally:
        server.stop()

    assert "<svg" in index
    assert health == {"ok": True}
    assert snapshot["agent"]["name"] == "alba-nury"
    assert snapshot["refresh_interval"] == 1.5
    assert calls == ["alba-nury"]
