"""Deliver one usage alert. Called by the worker; any exception means "retry later".

With ALERT_WEBHOOK_URL set, POSTs the JSON payload (also carrying a Slack-compatible `text`
field), signed with HMAC-SHA256 when ALERT_WEBHOOK_SECRET is set:
    X-Alert-Signature: t=<unix>,v1=<hex hmac of "<t>.<raw body>">
Without a URL the alert is written to the log, so the feature works with zero setup.
"""

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger("app.alerts")

Deliver = Callable[[dict[str, Any]], str]
TIMEOUT_SECONDS = 5.0


def sign(secret: str, timestamp: int, raw_body: bytes) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256)
    return f"t={timestamp},v1={digest.hexdigest()}"


def deliver(body: dict[str, Any]) -> str:
    settings = get_settings()
    if not settings.alert_webhook_url:
        logger.warning(
            "USAGE_ALERT alert_id=%s tenant=%s metric=%s threshold=%s%% %s",
            body["alert_id"],
            body["tenant"],
            body["metric"],
            body["threshold_percent"],
            body["message"],
        )
        return "log"

    raw = json.dumps({**body, "text": body["message"]}, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "metering-billing-alerts/1"}
    secret = settings.alert_webhook_secret.get_secret_value()
    if secret:
        headers["X-Alert-Signature"] = sign(secret, int(time.time()), raw)
    response = httpx.post(
        settings.alert_webhook_url, content=raw, headers=headers, timeout=TIMEOUT_SECONDS
    )
    response.raise_for_status()  # 4xx/5xx -> exception -> worker retries with backoff
    return f"webhook {response.status_code}"
