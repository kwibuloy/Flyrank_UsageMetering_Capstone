"""Usage-based billing: Pro usage reported to a Stripe Billing Meter, exactly, once.

The Stripe API is faked: the worker gets a fake `send`, and the subscription fetch is faked.
"""

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import stripe
from sqlalchemy import text

from app.db.session import engine
from app.jobs import worker
from app.services import usage_billing
from tests.integration.conftest import checkout_obj, post_webhook, stripe_event, subscription_obj

E1 = {
    "input_tokens": 10_000,
    "cached_input_tokens": 4_000,
    "output_tokens": 2_000,
    "reasoning_tokens": 500,
}
E2 = {"reasoning_tokens": 1_000}
E3 = {"input_tokens": 8_000, "cached_input_tokens": 8_000}

FLAT = {"price": {"id": "price_pro", "recurring": {"usage_type": "licensed"}}}
METERED = {"price": {"id": "price_usage", "recurring": {"usage_type": "metered"}}}


def fetch_with(*items):
    def _fetch(subscription_id):
        return {
            "id": subscription_id,
            "status": "active",
            "customer": "cus_test_1",
            "items": {"data": [{**item, "current_period_end": 1_800_000_000} for item in items]},
        }

    return _fetch


def subscribe(client, tenant, *items):
    """Run a checkout.session.completed webhook through the real worker path."""
    post_webhook(client, stripe_event("checkout.session.completed", checkout_obj(tenant.id)))
    while worker.run_once(fetch_subscription=fetch_with(*items)) is not None:
        pass


def gen(client, api_key, key, body):
    return client.post(
        "/generate", json=body, headers={"X-API-Key": api_key, "Idempotency-Key": key}
    )


def reports(tenant_id):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT idempotency_key, identifier, value_picousd, stripe_customer_id, status, "
                "attempts FROM usage_reports WHERE tenant_id = :t ORDER BY id"
            ),
            {"t": tenant_id},
        ).mappings()
        return [dict(r) for r in rows]


class FakeSend:
    def __init__(self, fail_times=0):
        self.fail_times = fail_times
        self.sent = []

    def __call__(self, report):
        if self.fail_times:
            self.fail_times -= 1
            raise stripe.APIConnectionError("stripe unreachable")
        self.sent.append(report.identifier)


def test_metered_pro_request_queues_its_exact_cost(client, make_tenant):
    tenant, api_key = make_tenant("free")
    subscribe(client, tenant, FLAT, METERED)

    r = gen(client, api_key, "k1", E1)

    assert r.status_code == 200
    assert reports(tenant.id) == [
        {
            "idempotency_key": "k1",
            "identifier": usage_billing.identifier(tenant.id, "k1"),
            "value_picousd": 34_250_000_000,  # 33,750 µ$ tokens + 500 µ$ call, unrounded
            "stripe_customer_id": "cus_test_1",
            "status": "pending",
            "attempts": 0,
        }
    ]


def test_replays_report_nothing(client, make_tenant):
    tenant, api_key = make_tenant("free")
    subscribe(client, tenant, FLAT, METERED)
    gen(client, api_key, "k1", E1)
    gen(client, api_key, "k1", E1)  # replay
    assert [r["idempotency_key"] for r in reports(tenant.id)] == ["k1"]


def test_rejected_requests_report_nothing(client, make_tenant, preload_usage):
    tenant, api_key = make_tenant("free")
    preload_usage(tenant.id, "api_calls", 50_000)  # Pro quota exhausted once upgraded
    subscribe(client, tenant, FLAT, METERED)
    rejected = gen(client, api_key, "over", E1)
    assert rejected.status_code == 429
    assert reports(tenant.id) == []


def test_flat_fee_and_free_tenants_report_nothing(client, make_tenant):
    flat, flat_key = make_tenant("free")
    subscribe(client, flat, FLAT)  # Pro without the usage price
    free, free_key = make_tenant("free")
    comped, comped_key = make_tenant("pro")  # seeded Pro, no subscription

    for key in (flat_key, free_key, comped_key):
        assert gen(client, key, "k", E1).status_code == 200
    assert reports(flat.id) == reports(free.id) == reports(comped.id) == []


def test_removing_the_usage_price_stops_reporting(client, make_tenant):
    tenant, api_key = make_tenant("free")
    subscribe(client, tenant, FLAT, METERED)
    updated = {**subscription_obj(tenant.id), "items": {"data": [FLAT]}}
    post_webhook(client, stripe_event("customer.subscription.updated", updated))
    while worker.run_once(fetch_subscription=fetch_with()) is not None:
        pass

    gen(client, api_key, "after", E1)
    assert reports(tenant.id) == []


def test_worker_sends_each_report_once_and_usage_shows_it(client, make_tenant):
    tenant, api_key = make_tenant("free")
    subscribe(client, tenant, FLAT, METERED)
    for key, body in (("e1", E1), ("e2", E2), ("e3", E3)):
        gen(client, api_key, key, body)
    send = FakeSend()

    while worker.run_usage_reports_once(send=send) is not None:
        pass

    assert send.sent == [usage_billing.identifier(tenant.id, k) for k in ("e1", "e2", "e3")]
    assert {r["status"] for r in reports(tenant.id)} == {"sent"}
    usage = client.get("/usage", headers={"X-API-Key": api_key}).json()
    # What Stripe will bill (exact values summed, rounded once) == the /usage cost.
    assert usage["usage_billing"] == {
        "enabled": True,
        "reports_sent": 3,
        "reports_pending": 0,
        "reports_dead": 0,
        "reported_microusd": 47_750,
    }
    assert usage["cost"]["total_microusd"] == 47_750


def test_failed_report_is_retried_with_backoff(client, make_tenant):
    tenant, api_key = make_tenant("free")
    subscribe(client, tenant, FLAT, METERED)
    gen(client, api_key, "k", E1)
    send = FakeSend(fail_times=1)
    now = datetime.now(UTC)

    assert worker.run_usage_reports_once(send=send, now=now) == "retry"
    assert worker.run_usage_reports_once(send=send, now=now) is None
    later = now + timedelta(seconds=worker.backoff_seconds(1) + 1)
    assert worker.run_usage_reports_once(send=send, now=later) == "sent"
    assert reports(tenant.id)[0]["attempts"] == 2


def test_permanently_failing_report_goes_dead_and_alerts(client, make_tenant, caplog):
    tenant, api_key = make_tenant("free")
    subscribe(client, tenant, FLAT, METERED)
    gen(client, api_key, "k", E1)
    send = FakeSend(fail_times=99)
    now = datetime.now(UTC)
    results = []
    with caplog.at_level(logging.WARNING, logger="app.jobs.worker"):
        for _ in range(worker.MAX_ATTEMPTS):
            now += timedelta(seconds=worker.MAX_BACKOFF_SECONDS + 1)
            results.append(worker.run_usage_reports_once(send=send, now=now))

    assert results == ["retry"] * (worker.MAX_ATTEMPTS - 1) + ["dead"]
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "ALERT usage_report_dead" in errors[0]


def test_send_creates_a_meter_event_with_the_identifier(monkeypatch):
    calls = []
    monkeypatch.setattr(stripe.billing.MeterEvent, "create", lambda **kw: calls.append(kw))
    report = SimpleNamespace(
        identifier="usage_abc",
        stripe_customer_id="cus_1",
        value_picousd=34_250_000_000,
        occurred_at=datetime(2026, 11, 14, 12, 0, tzinfo=UTC),
    )

    usage_billing.send(report)

    (call,) = calls
    assert call["event_name"] == "usage_cost_picousd"
    assert call["identifier"] == "usage_abc"
    assert call["payload"] == {"stripe_customer_id": "cus_1", "value": "34250000000"}
    assert call["timestamp"] == int(report.occurred_at.timestamp())
    assert call["api_key"] == "sk_test_dummy_for_tests"
