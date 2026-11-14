"""Usage alerts at 80% / 100%: exactly once, atomic with usage, delivered with retries."""

import hashlib
import hmac
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

from app.config import Settings
from app.db.session import SessionLocal, engine
from app.jobs import worker
from app.services import alert_delivery, alerts
from app.services.periods import period_bounds


def gen(client, api_key, key, body=None):
    return client.post(
        "/generate", json=body or {}, headers={"X-API-Key": api_key, "Idempotency-Key": key}
    )


def alert_rows(tenant_id=None):
    sql = "SELECT metric, threshold_percent, status, used_at_trigger, quota_limit, attempts"
    sql += " FROM usage_alerts"
    params = {}
    if tenant_id is not None:
        sql += " WHERE tenant_id = :t"
        params["t"] = tenant_id
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql + " ORDER BY id"), params).mappings()]


class FakeDeliver:
    def __init__(self, fail_times=0):
        self.fail_times = fail_times
        self.sent = []

    def __call__(self, body):
        if self.fail_times:
            self.fail_times -= 1
            raise ConnectionError("webhook unreachable")
        self.sent.append(body)
        return "fake 200"


def test_crossing_80_percent_queues_exactly_one_alert(client, make_tenant, preload_usage):
    tenant, api_key = make_tenant("free")
    preload_usage(tenant.id, "api_calls", 799)

    first = gen(client, api_key, "call-800")
    later = gen(client, api_key, "call-801")

    assert first.json()["alerts_triggered"] == [{"metric": "api_calls", "threshold_percent": 80}]
    assert later.json()["alerts_triggered"] == []
    assert alert_rows(tenant.id) == [
        {
            "metric": "api_calls",
            "threshold_percent": 80,
            "status": "pending",
            "used_at_trigger": 800,
            "quota_limit": 1_000,
            "attempts": 0,
        }
    ]


def test_one_request_can_cross_80_and_100(client, make_tenant):
    tenant, api_key = make_tenant("free")
    r = gen(client, api_key, "big", {"input_tokens": 100_000})
    assert r.json()["alerts_triggered"] == [
        {"metric": "ai_tokens", "threshold_percent": 80},
        {"metric": "ai_tokens", "threshold_percent": 100},
    ]
    assert [(a["metric"], a["threshold_percent"]) for a in alert_rows(tenant.id)] == [
        ("ai_tokens", 80),
        ("ai_tokens", 100),
    ]


def test_replay_and_rejection_queue_nothing(client, make_tenant, preload_usage):
    tenant, api_key = make_tenant("free")
    preload_usage(tenant.id, "api_calls", 999)
    last = gen(client, api_key, "last")
    replay = gen(client, api_key, "last")
    rejected = gen(client, api_key, "over")

    assert last.json()["alerts_triggered"] == [{"metric": "api_calls", "threshold_percent": 100}]
    assert replay.content == last.content  # replay mirrors the original, no new alert
    assert rejected.status_code == 402
    assert len(alert_rows(tenant.id)) == 1


def test_concurrent_requests_crossing_together_fire_once(client, make_tenant, preload_usage):
    tenant, api_key = make_tenant("free")
    preload_usage(tenant.id, "api_calls", 790)

    with ThreadPoolExecutor(max_workers=20) as pool:
        statuses = list(pool.map(lambda i: gen(client, api_key, f"c{i}").status_code, range(20)))

    assert statuses == [200] * 20
    assert [(a["threshold_percent"], a["used_at_trigger"]) for a in alert_rows(tenant.id)] == [
        (80, 800)
    ]


def test_new_limit_rearms_thresholds_same_month(make_tenant):
    """Upgrading Free -> Pro mid-month: Pro's 80% is a new alert, not a duplicate."""
    tenant, _ = make_tenant("free")
    period_start = period_bounds(datetime.now(UTC))[0].date()
    common = {"tenant_id": tenant.id, "period_start": period_start}
    with SessionLocal() as session:
        free = alerts.enqueue_crossed(
            session,
            plan_id="free",
            limits={"api_calls": 1_000, "ai_tokens": 100_000},
            used_before={"api_calls": 799},
            used_after={"api_calls": 800},
            **common,
        )
        again = alerts.enqueue_crossed(
            session,
            plan_id="free",
            limits={"api_calls": 1_000, "ai_tokens": 100_000},
            used_before={"api_calls": 799},
            used_after={"api_calls": 800},
            **common,
        )
        pro = alerts.enqueue_crossed(
            session,
            plan_id="pro",
            limits={"api_calls": 50_000, "ai_tokens": 5_000_000},
            used_before={"api_calls": 39_999},
            used_after={"api_calls": 40_000},
            **common,
        )
        session.commit()
    assert (free, again, pro) == ([("api_calls", 80)], [], [("api_calls", 80)])


def test_worker_delivers_each_alert_once(client, make_tenant, preload_usage):
    tenant, api_key = make_tenant("free")
    preload_usage(tenant.id, "api_calls", 799)
    gen(client, api_key, "k")
    deliver = FakeDeliver()

    assert worker.run_alerts_once(deliver=deliver) == "sent"
    assert worker.run_alerts_once(deliver=deliver) is None

    (body,) = deliver.sent
    assert body["type"] == "usage.threshold_reached"
    assert (body["metric"], body["threshold_percent"], body["used"], body["limit"]) == (
        "api_calls",
        80,
        800,
        1_000,
    )
    assert body["tenant"] == tenant.name and isinstance(body["alert_id"], int)
    row = alert_rows(tenant.id)[0]
    assert row["status"] == "sent" and row["attempts"] == 1


def test_failed_delivery_is_retried_with_backoff(client, make_tenant, preload_usage):
    tenant, api_key = make_tenant("free")
    preload_usage(tenant.id, "api_calls", 799)
    gen(client, api_key, "k")
    deliver = FakeDeliver(fail_times=1)
    now = datetime.now(UTC)

    assert worker.run_alerts_once(deliver=deliver, now=now) == "retry"
    assert worker.run_alerts_once(deliver=deliver, now=now) is None  # backing off
    later = now + timedelta(seconds=worker.backoff_seconds(1) + 1)
    assert worker.run_alerts_once(deliver=deliver, now=later) == "sent"
    assert len(deliver.sent) == 1
    assert alert_rows(tenant.id)[0]["attempts"] == 2


def test_permanently_failing_alert_goes_dead_and_alerts_ops(
    client, make_tenant, preload_usage, caplog
):
    tenant, api_key = make_tenant("free")
    preload_usage(tenant.id, "api_calls", 799)
    gen(client, api_key, "k")
    deliver = FakeDeliver(fail_times=99)
    now = datetime.now(UTC)
    results = []
    with caplog.at_level(logging.WARNING, logger="app.jobs.worker"):
        for _ in range(worker.MAX_ATTEMPTS):
            now += timedelta(seconds=worker.MAX_BACKOFF_SECONDS + 1)
            results.append(worker.run_alerts_once(deliver=deliver, now=now))

    assert results == ["retry"] * (worker.MAX_ATTEMPTS - 1) + ["dead"]
    assert alert_rows(tenant.id)[0]["status"] == "dead"
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "ALERT usage_alert_dead" in errors[0]


def test_get_alerts_lists_only_this_tenants_alerts(client, make_tenant, preload_usage):
    a, a_key = make_tenant("free")
    _, b_key = make_tenant("free")
    preload_usage(a.id, "api_calls", 799)
    gen(client, a_key, "k")
    worker.run_alerts_once(deliver=FakeDeliver())

    mine = client.get("/alerts", headers={"X-API-Key": a_key}).json()
    theirs = client.get("/alerts", headers={"X-API-Key": b_key}).json()

    assert theirs["alerts"] == []
    (alert,) = mine["alerts"]
    assert alert["metric"] == "api_calls" and alert["threshold_percent"] == 80
    assert alert["status"] == "sent" and alert["sent_at"] is not None
    assert "last_error" not in alert
    assert client.get("/alerts").status_code == 401


# --- the real delivery function ---------------------------------------------------------

BODY = {
    "alert_id": 7,
    "tenant": "acme",
    "metric": "api_calls",
    "threshold_percent": 80,
    "message": "acme has used 80% of its monthly API call quota (800 of 1,000) on the Free plan.",
}


def use_settings(monkeypatch, **values):
    monkeypatch.setattr(alert_delivery, "get_settings", lambda: Settings(**values))


def test_without_a_url_alerts_go_to_the_log(monkeypatch, caplog):
    use_settings(monkeypatch, alert_webhook_url="")
    with caplog.at_level(logging.WARNING, logger="app.alerts"):
        assert alert_delivery.deliver(BODY) == "log"
    assert "USAGE_ALERT alert_id=7" in caplog.text


def test_webhook_delivery_is_signed_and_slack_compatible(monkeypatch):
    use_settings(
        monkeypatch, alert_webhook_url="https://hooks.example.test/x", alert_webhook_secret="s3"
    )
    captured = {}

    def fake_post(url, content, headers, timeout):
        captured.update(url=url, content=content, headers=headers)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(alert_delivery.httpx, "post", fake_post)
    assert alert_delivery.deliver(BODY) == "webhook 200"

    sent = json.loads(captured["content"])
    assert sent["text"] == BODY["message"] and sent["alert_id"] == 7
    ts, sig = (
        part.split("=", 1)[1] for part in captured["headers"]["X-Alert-Signature"].split(",")
    )
    expected = hmac.new(b"s3", f"{ts}.".encode() + captured["content"], hashlib.sha256)
    assert hmac.compare_digest(sig, expected.hexdigest())


def test_webhook_error_status_raises_so_the_worker_retries(monkeypatch):
    use_settings(monkeypatch, alert_webhook_url="https://hooks.example.test/x")
    monkeypatch.setattr(
        alert_delivery.httpx,
        "post",
        lambda url, **kw: httpx.Response(503, request=httpx.Request("POST", url)),
    )
    with pytest.raises(httpx.HTTPStatusError):
        alert_delivery.deliver(BODY)
