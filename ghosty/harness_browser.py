"""Browser-based animated harness viewer."""

from __future__ import annotations

import json
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import urlparse

from ghosty import harness, ui
from ghosty.models import Agent, Config


DEFAULT_REFRESH_INTERVAL = 2.0


@dataclass
class HarnessBrowserServer:
    """A running localhost harness viewer."""

    server: ThreadingHTTPServer
    thread: threading.Thread
    url: str

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def snapshot_payload(config: Config, agent: Agent, *, refresh_interval: float = DEFAULT_REFRESH_INTERVAL) -> dict:
    """Collect and serialize the latest read-only harness state."""
    snapshot = harness.collect_harness(config, agent)
    return harness.snapshot_to_dict(snapshot, refresh_interval=refresh_interval)


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _make_handler(
    config: Config,
    agent: Agent,
    *,
    refresh_interval: float,
    html_factory: Callable[[float], str],
) -> type[BaseHTTPRequestHandler]:
    class HarnessRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args) -> None:
            return

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: dict) -> None:
            self._send(status, _json_bytes(payload), "application/json; charset=utf-8")

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in {"", "/"}:
                body = html_factory(refresh_interval).encode("utf-8")
                self._send(200, body, "text/html; charset=utf-8")
                return
            if path == "/health":
                self._send_json(200, {"ok": True})
                return
            if path == "/snapshot":
                try:
                    self._send_json(200, snapshot_payload(config, agent, refresh_interval=refresh_interval))
                except Exception as exc:  # pragma: no cover - defensive boundary for browser callers
                    self._send_json(500, {"error": str(exc)})
                return
            if path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
                return
            self._send_json(404, {"error": "not found"})

    return HarnessRequestHandler


class _LocalHarnessHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_server(
    config: Config,
    agent: Agent,
    *,
    refresh_interval: float = DEFAULT_REFRESH_INTERVAL,
) -> HarnessBrowserServer:
    """Start the read-only localhost harness server."""
    handler = _make_handler(
        config,
        agent,
        refresh_interval=refresh_interval,
        html_factory=render_app_html,
    )
    server = _LocalHarnessHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, name=f"ghosty-harness-{agent.name}", daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return HarnessBrowserServer(server=server, thread=thread, url=f"http://{host}:{port}/")


def live_harness(
    config: Config,
    agent: Agent,
    *,
    refresh_interval: float = DEFAULT_REFRESH_INTERVAL,
    open_browser: bool = True,
) -> None:
    """Open the animated browser harness and keep its server alive."""
    server = start_server(config, agent, refresh_interval=refresh_interval)
    try:
        ui.info(f"Harness view: {server.url}")
        ui.info("Press Ctrl+C here to close the harness view and return.")
        if open_browser and not webbrowser.open(server.url):
            ui.warn("Could not open the browser automatically. Open the harness URL manually.")
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        ui.skip("returned to agent menu")
    finally:
        server.stop()


def render_app_html(refresh_interval: float = DEFAULT_REFRESH_INTERVAL) -> str:
    refresh_ms = max(750, int(refresh_interval * 1000))
    return _APP_HTML.replace("__REFRESH_MS__", str(refresh_ms))


_APP_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ghosty Harness</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0b0f0e;
      --surface: #101614;
      --surface-2: #151d1a;
      --panel: rgba(16, 22, 20, 0.92);
      --panel-strong: rgba(22, 31, 27, 0.98);
      --line: rgba(220, 229, 222, 0.12);
      --line-strong: rgba(220, 229, 222, 0.22);
      --text: #eef4f0;
      --muted: #99a59e;
      --soft: #c9d3cc;
      --ready: #7fd99b;
      --attention: #e7bd63;
      --off: #5d6862;
      --unknown: #8ebeda;
      --agent: #f3f5f2;
      --shadow: rgba(0, 0, 0, 0.42);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      overflow: hidden;
      font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at 25% 18%, rgba(127, 217, 155, 0.10), transparent 34%),
        radial-gradient(circle at 74% 78%, rgba(142, 190, 218, 0.08), transparent 30%),
        var(--bg);
    }

    .shell {
      display: grid;
      grid-template-columns: minmax(720px, 1fr) 380px;
      gap: 16px;
      height: 100vh;
      padding: 16px;
    }

    .stage, .details {
      position: relative;
      min-height: 0;
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: 0 24px 80px var(--shadow), inset 0 1px 0 rgba(255,255,255,0.04);
      backdrop-filter: blur(18px);
      border-radius: 8px;
      overflow: hidden;
    }

    .stage {
      padding: 22px;
    }

    .topbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      position: relative;
      z-index: 2;
    }

    h1 {
      margin: 0;
      font-size: 17px;
      letter-spacing: 0;
      font-weight: 720;
    }

    .subtitle {
      color: var(--muted);
      font-size: 13px;
      margin-top: 3px;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 0 10px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--muted);
      background: rgba(255,255,255,0.03);
      white-space: nowrap;
      font-size: 12px;
    }

    .pill.ready { color: var(--ready); }
    .pill.stale { color: var(--attention); }

    svg {
      width: 100%;
      height: calc(100% - 62px);
      min-height: 560px;
      display: block;
      margin-top: 12px;
    }

    .map-grid line {
      stroke: rgba(220, 229, 222, 0.045);
      stroke-width: 1;
    }

    .backplane {
      fill: rgba(255,255,255,0.018);
      stroke: rgba(220, 229, 222, 0.07);
      stroke-width: 1;
    }

    .link {
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-dasharray: 7 12;
      opacity: 0.78;
      animation: dash-flow 2.3s linear infinite;
    }

    @keyframes dash-flow {
      from { stroke-dashoffset: 0; }
      to { stroke-dashoffset: -38; }
    }

    .link.ready, .link.attached { stroke: var(--ready); color: var(--ready); }
    .link.attention { stroke: var(--attention); color: var(--attention); }
    .link.unknown { stroke: var(--unknown); color: var(--unknown); }
    .link.off {
      stroke: var(--off);
      color: var(--off);
      opacity: 0.28;
      animation: none;
    }

    .packet {
      opacity: 0.9;
    }

    .packet.ready, .packet.attached { fill: var(--ready); color: var(--ready); }
    .packet.attention { fill: var(--attention); color: var(--attention); }
    .packet.unknown { fill: var(--unknown); color: var(--unknown); }
    .packet.off { opacity: 0; }

    .node {
      cursor: pointer;
    }

    .node-card,
    .agent-card {
      fill: rgba(19, 27, 24, 0.92);
      stroke: var(--line);
      stroke-width: 1.2;
      transition: fill 160ms ease, stroke 160ms ease;
    }

    .node:hover .node-card,
    .node.selected .node-card {
      fill: rgba(27, 38, 33, 0.96);
      stroke: var(--line-strong);
    }

    .node.ready .node-card,
    .node.attached .node-card { stroke: rgba(127, 217, 155, 0.38); }
    .node.attention .node-card { stroke: rgba(231, 189, 99, 0.45); }
    .node.unknown .node-card { stroke: rgba(142, 190, 218, 0.45); }
    .node.off .node-card { stroke: rgba(93, 104, 98, 0.45); }

    .icon-plate {
      fill: rgba(255,255,255,0.035);
      stroke: rgba(220, 229, 222, 0.10);
      stroke-width: 1;
    }

    .node-icon {
      fill: none;
      stroke: var(--soft);
      stroke-width: 1.8;
      stroke-linecap: round;
      stroke-linejoin: round;
    }

    .node.ready .node-icon,
    .node.attached .node-icon { stroke: var(--ready); }
    .node.attention .node-icon { stroke: var(--attention); }
    .node.unknown .node-icon { stroke: var(--unknown); }
    .node.off .node-icon { stroke: var(--off); }

    .node .title {
      fill: var(--text);
      font-weight: 720;
      font-size: 15px;
      letter-spacing: 0;
    }

    .node .summary {
      fill: var(--muted);
      font-size: 12px;
    }

    .status-dot {
      stroke: rgba(11, 15, 14, 0.95);
      stroke-width: 2;
    }

    .node.ready .status-dot,
    .node.attached .status-dot { fill: var(--ready); }
    .node.attention .status-dot { fill: var(--attention); }
    .node.unknown .status-dot { fill: var(--unknown); }
    .node.off .status-dot { fill: var(--off); }

    .agent-core {
      cursor: pointer;
    }

    .agent-card {
      fill: rgba(24, 33, 29, 0.98);
      stroke: rgba(238, 244, 240, 0.32);
    }

    .agent-icon-shell {
      fill: rgba(255,255,255,0.04);
      stroke: rgba(238, 244, 240, 0.16);
    }

    .agent-core .name {
      fill: #ffffff;
      font-weight: 760;
      font-size: 18px;
      letter-spacing: 0;
    }

    .agent-core .meta {
      fill: var(--muted);
      font-size: 12px;
    }

    .agent-icon {
      fill: none;
      stroke: var(--agent);
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }

    .details {
      padding: 18px;
      overflow-y: auto;
    }

    .details h2 {
      margin: 0 0 4px;
      font-size: 20px;
      letter-spacing: 0;
    }

    .details .summary {
      margin: 0 0 18px;
      color: var(--muted);
    }

    .section {
      border-top: 1px solid var(--line);
      padding-top: 14px;
      margin-top: 14px;
    }

    .section-title {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0;
      margin-bottom: 10px;
    }

    .description-copy {
      color: var(--soft);
      font-size: 13px;
      line-height: 1.55;
      max-width: 34rem;
    }

    .kv {
      display: grid;
      grid-template-columns: 120px minmax(0, 1fr);
      gap: 8px 12px;
      align-items: start;
      margin-bottom: 8px;
      word-break: break-word;
    }

    .kv .key {
      color: var(--muted);
    }

    .kv .value {
      color: var(--text);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      font-size: 12px;
    }

    .state-ready, .state-attached { color: var(--ready); }
    .state-attention { color: var(--attention); }
    .state-unknown { color: var(--unknown); }
    .state-off { color: var(--off); }

    @media (max-width: 980px) {
      body { overflow: auto; }
      .shell {
        grid-template-columns: 1fr;
        height: auto;
        min-height: 100vh;
      }
      svg { min-height: 520px; }
      .details { max-height: none; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="stage">
      <div class="topbar">
        <div>
          <h1 id="title">Ghosty harness</h1>
          <div id="subtitle" class="subtitle">Loading agent map...</div>
        </div>
        <div id="freshness" class="pill">loading</div>
      </div>
      <svg id="diagram" viewBox="0 0 1000 650" role="img" aria-label="Animated Ghosty agent harness diagram">
        <defs>
          <symbol id="icon-connect" viewBox="0 0 32 32">
            <rect x="5" y="8" width="22" height="15" rx="3"></rect>
            <path d="M9 13l4 3-4 3"></path>
            <path d="M15 20h7"></path>
            <path d="M20 6l4 2v4c0 3-1.7 5-4 6-2.3-1-4-3-4-6V8l4-2z"></path>
          </symbol>
          <symbol id="icon-chat" viewBox="0 0 32 32">
            <path d="M7 9h14a5 5 0 0 1 5 5v2a5 5 0 0 1-5 5h-6l-6 4v-4H7a5 5 0 0 1-5-5v-2a5 5 0 0 1 5-5z"></path>
            <path d="M10 14h9"></path>
            <path d="M10 18h5"></path>
            <path d="M21 7c3 .4 5 2.3 5.5 5"></path>
          </symbol>
          <symbol id="icon-notifications" viewBox="0 0 32 32">
            <path d="M16 5v5"></path>
            <path d="M6 13h7l3 4 3-8 3 4h4"></path>
            <path d="M7 21h18"></path>
            <path d="M10 25h12"></path>
            <path d="M5 9l3-3"></path>
            <path d="M27 9l-3-3"></path>
          </symbol>
          <symbol id="icon-storage" viewBox="0 0 32 32">
            <path d="M6 11h20l-2 15H8L6 11z"></path>
            <path d="M10 11l2-5h8l2 5"></path>
            <path d="M10 17h12"></path>
            <path d="M11 22h10"></path>
          </symbol>
          <symbol id="icon-models" viewBox="0 0 32 32">
            <rect x="9" y="9" width="14" height="14" rx="3"></rect>
            <path d="M13 9V5"></path>
            <path d="M19 9V5"></path>
            <path d="M13 27v-4"></path>
            <path d="M19 27v-4"></path>
            <path d="M5 13h4"></path>
            <path d="M5 19h4"></path>
            <path d="M23 13h4"></path>
            <path d="M23 19h4"></path>
            <circle cx="16" cy="16" r="3"></circle>
          </symbol>
          <symbol id="icon-internet" viewBox="0 0 32 32">
            <circle cx="16" cy="16" r="11"></circle>
            <path d="M5 16h22"></path>
            <path d="M16 5c3 3 4.5 6.7 4.5 11S19 24 16 27"></path>
            <path d="M16 5c-3 3-4.5 6.7-4.5 11S13 24 16 27"></path>
          </symbol>
          <symbol id="icon-agent" viewBox="0 0 40 40">
            <rect x="9" y="10" width="22" height="18" rx="4"></rect>
            <path d="M14 16h12"></path>
            <path d="M14 21h8"></path>
            <path d="M16 28v5"></path>
            <path d="M24 28v5"></path>
            <path d="M20 7v3"></path>
          </symbol>
        </defs>
        <g class="map-grid" aria-hidden="true">
          <line x1="80" y1="150" x2="920" y2="150"></line>
          <line x1="80" y1="330" x2="920" y2="330"></line>
          <line x1="80" y1="510" x2="920" y2="510"></line>
          <line x1="250" y1="80" x2="250" y2="580"></line>
          <line x1="500" y1="80" x2="500" y2="580"></line>
          <line x1="750" y1="80" x2="750" y2="580"></line>
        </g>
        <rect class="backplane" x="360" y="242" width="280" height="176" rx="24"></rect>
        <g id="links"></g>
        <g id="packets"></g>
        <g id="agent-core" class="agent-core"></g>
        <g id="nodes"></g>
      </svg>
    </section>
    <aside class="details">
      <h2 id="details-title">Harness</h2>
      <p id="details-summary" class="summary">Select a node to inspect it.</p>
      <div class="section">
        <div class="section-title">Current state</div>
        <div id="friendly"></div>
      </div>
      <div class="section">
        <div class="section-title">What this means</div>
        <div id="meaning" class="description-copy"></div>
      </div>
      <div class="section">
        <div class="section-title">What this enables</div>
        <div id="enables" class="description-copy"></div>
      </div>
      <div class="section">
        <div class="section-title">Advanced values</div>
        <div id="advanced"></div>
      </div>
    </aside>
  </main>

  <script>
    const REFRESH_MS = __REFRESH_MS__;
    const AGENT_NODE = {
      x: 390,
      y: 282,
      width: 220,
      height: 96,
      anchors: {
        left: "left",
        right: "right",
        topLeft: "top",
        topRight: "top",
        bottomLeft: "bottom",
        bottomRight: "bottom",
      },
    };
    const NODE_DEFS = {
      Connect: { x: 96, y: 276, width: 196, height: 108, icon: "icon-connect", anchor: "right", agentAnchor: "left", title: "Connect" },
      Chat: { x: 180, y: 94, width: 196, height: 108, icon: "icon-chat", anchor: "right", agentAnchor: "topLeft", title: "Chat" },
      Notifications: { x: 708, y: 276, width: 208, height: 108, icon: "icon-notifications", anchor: "left", agentAnchor: "right", title: "Notifications" },
      Storage: { x: 180, y: 458, width: 196, height: 108, icon: "icon-storage", anchor: "right", agentAnchor: "bottomLeft", title: "Storage" },
      Models: { x: 624, y: 94, width: 196, height: 108, icon: "icon-models", anchor: "left", agentAnchor: "topRight", title: "Models" },
      Internet: { x: 624, y: 458, width: 196, height: 108, icon: "icon-internet", anchor: "left", agentAnchor: "bottomRight", title: "Internet" },
    };
    const NODE_DESCRIPTIONS = {
      Agent: {
        meaning: "The private worker where this agent runs.",
        enables: "A protected place for the agent to think, use tools, and connect to added capabilities.",
      },
      Connect: {
        meaning: "A protected way for you to enter the agent's machine when you need to guide or inspect it.",
        enables: "Hands-on access without giving the agent a public address.",
      },
      Chat: {
        meaning: "A conversation doorway between people and the agent.",
        enables: "The agent can receive Google Chat messages and respond as part of a workspace flow.",
      },
      Notifications: {
        meaning: "A safe receiving point for outside systems to tell the agent something happened.",
        enables: "The agent can react to events without being exposed directly to the internet.",
      },
      Storage: {
        meaning: "A private file space assigned to the agent.",
        enables: "The agent can save, read, and share approved files without mixing data with other agents.",
      },
      Models: {
        meaning: "Access to Google AI models through the agent's cloud identity.",
        enables: "The agent can use approved model capabilities without relying on personal credentials.",
      },
      Internet: {
        meaning: "Shared outbound access for private agents.",
        enables: "The agent can reach external services while still keeping inbound access closed.",
      },
    };

    let latest = null;
    let selected = "agent";

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;",
      }[char]));
    }

    function stateClass(state) {
      return ["ready", "attached", "attention", "unknown", "off"].includes(state) ? state : "unknown";
    }

    function anchorPoint(box, side) {
      const midX = box.x + box.width / 2;
      const midY = box.y + box.height / 2;
      const inset = 28;
      const anchors = {
        left: { x: box.x, y: midY },
        right: { x: box.x + box.width, y: midY },
        top: { x: midX, y: box.y },
        bottom: { x: midX, y: box.y + box.height },
        topLeft: { x: box.x + inset, y: box.y },
        topRight: { x: box.x + box.width - inset, y: box.y },
        bottomLeft: { x: box.x + inset, y: box.y + box.height },
        bottomRight: { x: box.x + box.width - inset, y: box.y + box.height },
      };
      return anchors[side] || anchors.right;
    }

    function pathFor(nodeDef) {
      const start = anchorPoint(AGENT_NODE, nodeDef.agentAnchor);
      const end = anchorPoint(nodeDef, nodeDef.anchor);
      const dx = end.x - start.x;
      const c1 = { x: start.x + dx * 0.44, y: start.y };
      const c2 = { x: end.x - dx * 0.44, y: end.y };
      return `M ${start.x} ${start.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${end.x} ${end.y}`;
    }

    function capabilityByName(name) {
      return latest?.capabilities?.find((cap) => cap.name === name);
    }

    function renderLinks() {
      const links = document.getElementById("links");
      const packets = document.getElementById("packets");
      links.innerHTML = "";
      packets.innerHTML = "";

      for (const capability of latest.capabilities) {
        const nodeDef = NODE_DEFS[capability.name];
        if (!nodeDef) continue;
        const cls = stateClass(capability.state);
        const id = `path-${capability.name.replace(/[^a-z0-9]/gi, "-")}`;
        links.insertAdjacentHTML("beforeend", `<path id="${id}" class="link ${cls}" d="${pathFor(nodeDef)}"></path>`);
        const speed = capability.state === "attention" ? "2.3s" : "3.2s";
        packets.insertAdjacentHTML("beforeend", `
          <circle class="packet ${cls}" r="4">
            <animateMotion dur="${speed}" repeatCount="indefinite" rotate="auto">
              <mpath href="#${id}"></mpath>
            </animateMotion>
          </circle>
        `);
      }
    }

    function renderAgent() {
      const agent = latest.agent;
      document.getElementById("agent-core").innerHTML = `
        <title>${escapeHtml(agent.name)} agent core</title>
        <rect class="agent-card" x="${AGENT_NODE.x}" y="${AGENT_NODE.y}" width="${AGENT_NODE.width}" height="${AGENT_NODE.height}" rx="18"></rect>
        <rect class="agent-icon-shell" x="${AGENT_NODE.x + 18}" y="${AGENT_NODE.y + 22}" width="52" height="52" rx="14"></rect>
        <use class="agent-icon" href="#icon-agent" x="${AGENT_NODE.x + 24}" y="${AGENT_NODE.y + 28}" width="40" height="40"></use>
        <text class="name" x="${AGENT_NODE.x + 86}" y="${AGENT_NODE.y + 41}">${escapeHtml(agent.name)}</text>
        <text class="meta" x="${AGENT_NODE.x + 86}" y="${AGENT_NODE.y + 62}">${escapeHtml(agent.status || "unknown")} · ${escapeHtml(agent.machine_type || "-")}</text>
        <text class="meta" x="${AGENT_NODE.x + 86}" y="${AGENT_NODE.y + 80}">${escapeHtml(agent.zone || "-")}</text>
      `;
    }

    function renderNodes() {
      const nodes = document.getElementById("nodes");
      nodes.innerHTML = "";
      for (const capability of latest.capabilities) {
        const nodeDef = NODE_DEFS[capability.name];
        if (!nodeDef) continue;
        const cls = stateClass(capability.state);
        const selectedClass = selected === capability.name ? " selected" : "";
        nodes.insertAdjacentHTML("beforeend", `
          <g class="node ${cls}${selectedClass}" data-name="${escapeHtml(capability.name)}" role="button" aria-label="${escapeHtml(capability.name)} ${escapeHtml(capability.summary || "")}">
            <title>${escapeHtml(capability.name)} - ${escapeHtml(capability.summary || "")}</title>
            <rect class="node-card" x="${nodeDef.x}" y="${nodeDef.y}" width="${nodeDef.width}" height="${nodeDef.height}" rx="16"></rect>
            <rect class="icon-plate" x="${nodeDef.x + 16}" y="${nodeDef.y + 22}" width="48" height="48" rx="13"></rect>
            <use class="node-icon" href="#${nodeDef.icon}" x="${nodeDef.x + 24}" y="${nodeDef.y + 30}" width="32" height="32"></use>
            <text class="title" x="${nodeDef.x + 78}" y="${nodeDef.y + 43}">${escapeHtml(nodeDef.title)}</text>
            <text class="summary" x="${nodeDef.x + 78}" y="${nodeDef.y + 64}">${escapeHtml(capability.summary || "")}</text>
            <circle class="status-dot" cx="${nodeDef.x + nodeDef.width - 22}" cy="${nodeDef.y + 24}" r="6"></circle>
          </g>
        `);
      }
    }

    function kv(label, value) {
      return `<div class="kv"><div class="key">${escapeHtml(label)}</div><div class="value">${escapeHtml(value || "-")}</div></div>`;
    }

    function renderDetails() {
      const title = document.getElementById("details-title");
      const summary = document.getElementById("details-summary");
      const friendly = document.getElementById("friendly");
      const meaning = document.getElementById("meaning");
      const enables = document.getElementById("enables");
      const advanced = document.getElementById("advanced");

      if (selected === "agent") {
        const agent = latest.agent;
        const description = NODE_DESCRIPTIONS.Agent;
        title.textContent = agent.name;
        summary.innerHTML = `<span class="state-${agent.status === "RUNNING" ? "ready" : "attention"}">${escapeHtml(agent.status || "unknown")}</span>`;
        friendly.innerHTML =
          kv("State", agent.status || "unknown") +
          kv("Machine", agent.machine_type || "-") +
          kv("Zone", agent.zone || "-");
        meaning.textContent = description.meaning;
        enables.textContent = description.enables;
        advanced.innerHTML =
          kv("Instance", agent.instance || "-") +
          kv("Private IP", agent.internal_ip || "-") +
          (agent.advanced || []).map((item) => kv(item.label, item.value)).join("");
        return;
      }

      const cap = capabilityByName(selected);
      if (!cap) {
        selected = "agent";
        renderDetails();
        return;
      }

      title.textContent = cap.name;
      const description = NODE_DESCRIPTIONS[cap.name] || {
        meaning: "This capability is attached to the agent.",
        enables: "The agent can use this added capability when its setup is complete.",
      };
      summary.innerHTML = `<span class="state-${stateClass(cap.state)}">${escapeHtml(cap.summary)}</span>`;
      friendly.innerHTML =
        kv("State", cap.state) +
        kv("Summary", cap.summary) +
        kv("Detail", cap.detail || "-") +
        kv("Shared", cap.shared ? "yes" : "no");
      meaning.textContent = description.meaning;
      enables.textContent = description.enables;
      advanced.innerHTML = (cap.advanced && cap.advanced.length)
        ? cap.advanced.map((item) => kv(item.label, item.value)).join("")
        : `<div class="value">No technical values for this node.</div>`;
    }

    function renderApp(snapshot) {
      latest = snapshot;
      if (selected !== "agent" && !capabilityByName(selected)) selected = "agent";
      document.getElementById("title").textContent = `${snapshot.agent.name} harness`;
      document.getElementById("subtitle").textContent = "Animated live map of attached capabilities";
      renderLinks();
      renderAgent();
      renderNodes();
      renderDetails();
    }

    function setFreshness(ok, error) {
      const pill = document.getElementById("freshness");
      pill.className = ok ? "pill ready" : "pill stale";
      pill.textContent = ok ? "live" : `stale${error ? ": " + error : ""}`;
    }

    async function refresh() {
      try {
        const response = await fetch("/snapshot", { cache: "no-store" });
        if (!response.ok) throw new Error(`snapshot ${response.status}`);
        const snapshot = await response.json();
        renderApp(snapshot);
        setFreshness(true);
      } catch (error) {
        setFreshness(false, error.message);
      }
    }

    document.getElementById("nodes").addEventListener("click", (event) => {
      const node = event.target.closest(".node");
      if (!node) return;
      selected = node.dataset.name;
      renderApp(latest);
    });

    document.getElementById("agent-core").addEventListener("click", () => {
      selected = "agent";
      renderApp(latest);
    });

    refresh();
    setInterval(refresh, REFRESH_MS);
  </script>
</body>
</html>
"""
