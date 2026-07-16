from __future__ import annotations

import hmac
import json
import os
import time
import uuid

from flask import Flask, jsonify, request
from google.cloud import pubsub_v1


app = Flask(__name__)
publisher = pubsub_v1.PublisherClient()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _secret() -> str:
    return _env("GHOSTY_WEBHOOK_SECRET")


def _provider() -> str:
    return "generic"


def _topic() -> str:
    return _env("GHOSTY_WEBHOOK_TOPIC")


def _constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _verify_generic() -> bool:
    header = _env("GHOSTY_WEBHOOK_SECRET_HEADER", "X-Ghosty-Webhook-Secret")
    return bool(_secret()) and _constant_time_equal(request.headers.get(header, ""), _secret())


def _authorized(body: bytes) -> bool:
    return _verify_generic()


def _safe_headers() -> dict[str, str]:
    blocked = {
        "authorization",
        "cookie",
        _env("GHOSTY_WEBHOOK_SECRET_HEADER", "X-Ghosty-Webhook-Secret").lower(),
    }
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in blocked
    }


def _event(body: bytes) -> dict:
    text = body.decode("utf-8", errors="replace")
    parsed = None
    try:
        parsed = json.loads(text) if text else None
    except json.JSONDecodeError:
        parsed = None
    return {
        "version": _env("GHOSTY_WEBHOOK_EVENT_FORMAT", "ghosty.webhook.v1"),
        "id": str(uuid.uuid4()),
        "received_at": int(time.time()),
        "provider": _provider(),
        "webhook_name": _env("GHOSTY_WEBHOOK_NAME"),
        "agent": _env("GHOSTY_WEBHOOK_AGENT"),
        "method": request.method,
        "path": request.path,
        "query_string": request.query_string.decode("utf-8", errors="replace"),
        "headers": _safe_headers(),
        "body": text,
        "json": parsed,
    }


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.post("/")
@app.post("/<path:_path>")
def receive(_path: str = ""):
    topic = _topic()
    if not topic:
        return jsonify({"error": "missing Pub/Sub topic configuration"}), 500

    body = request.get_data(cache=False)
    if not _authorized(body):
        return jsonify({"error": "unauthorized"}), 401

    payload = json.dumps(_event(body), separators=(",", ":")).encode("utf-8")
    future = publisher.publish(
        topic,
        payload,
        provider=_provider(),
        webhook=_env("GHOSTY_WEBHOOK_NAME"),
        format=_env("GHOSTY_WEBHOOK_EVENT_FORMAT", "ghosty.webhook.v1"),
    )
    message_id = future.result(timeout=30)
    return jsonify({"ok": True, "message_id": message_id})
