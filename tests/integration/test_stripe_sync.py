"""Worker + subscription sync (S2, P3, P4, background job). Stripe API calls are faked."""

import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.jobs import worker
from tests.integration.conftest import (
    checkout_obj,
    post_webhook,
    stripe_event,
    stripe_event_row,
    subscription_obj,
)


def fake_fetch(status="active", sub_id="sub_test_1"):
    calls = []

    def _fetch(subscription_id):
        calls.append(subscription_id)
        return {
            "id": sub_id,
            "status": status,
            "customer": "cus_test_1",
            "items": {"data": [{"current_period_end": 1_800_000_000}]},
        }

    _fetch.calls = calls
    return _fetch


def drain(fetch=None, now=None):
    results = []
    while (
        result := worker.run_once(fetch_subscription=fetch or fake_fetch(), now=now)
    ) is not None:
        results.append(result)
    return results


def usage(client, api_key):
    return client.get("/usage", headers={"X-API-Key": api_key}).json()


def test_checkout_completed_flips_free_to_pro(client, make_tenant):
    tenant, api_key = make_tenant("free")
    assert usage(client, api_key)["plan"] == "free"

    post_webhook(client, stripe_event("checkout.session.completed", checkout_obj(tenant.id)))
    fetch = fake_fetch("active")
    assert drain(fetch) == ["done"]

    after = usage(client, api_key)
    assert after["plan"] == "pro"
    assert after["subscription_status"] == "active"
    assert after["metrics"]["api_calls"]["limit"] == 50_000
    assert after["metrics"]["ai_tokens"]["limit"] == 5_000_000
    assert fetch.calls == ["sub_test_1"]


def test_replayed_event_is_processed_once(client, make_tenant):
    tenant, _ = make_tenant("free")
    event = stripe_event("checkout.session.completed", checkout_obj(tenant.id))
    fetch = fake_fetch()

    post_webhook(client, event)
    post_webhook(client, event)  # Stripe retry / `stripe events resend`
    results = drain(fetch)

    assert results == ["done"]
    assert len(fetch.calls) == 1
    assert stripe_event_row(event["id"])["attempts"] == 1


def test_subscription_deleted_downgrades_to_free(client, make_tenant):
    tenant, api_key = make_tenant("free")
    post_webhook(client, stripe_event("checkout.session.completed", checkout_obj(tenant.id)))
    drain()

    deleted = subscription_obj(tenant.id, status="canceled")
    post_webhook(client, stripe_event("customer.subscription.deleted", deleted))
    drain()

    after = usage(client, api_key)
    assert after["plan"] == "free"
    assert after["subscription_status"] == "canceled"
    assert after["metrics"]["api_calls"]["limit"] == 1_000


def test_past_due_keeps_pro_limits_but_blocks_usage_with_402(client, make_tenant):
    tenant, api_key = make_tenant("free")
    post_webhook(client, stripe_event("checkout.session.completed", checkout_obj(tenant.id)))
    drain()
    post_webhook(
        client,
        stripe_event(
            "customer.subscription.updated", subscription_obj(tenant.id, status="past_due")
        ),
    )
    drain()

    r = client.post("/generate", json={}, headers={"X-API-Key": api_key, "Idempotency-Key": "k"})

    assert usage(client, api_key)["plan"] == "pro"
    assert r.status_code == 402
    assert r.json()["error"]["details"]["subscription_status"] == "past_due"


def test_older_event_arriving_late_is_ignored(client, make_tenant):
    tenant, api_key = make_tenant("free")
    newer = stripe_event(
        "customer.subscription.updated", subscription_obj(tenant.id, status="active"), created=2_000
    )
    older = stripe_event(
        "customer.subscription.updated",
        subscription_obj(tenant.id, status="canceled"),
        created=1_000,
    )
    post_webhook(client, newer)
    drain()
    post_webhook(client, older)
    drain()

    assert usage(client, api_key)["plan"] == "pro"
    assert stripe_event_row(older["id"])["outcome"] == "ignored_stale"


def test_subscription_event_resolves_tenant_by_customer_id(client, make_tenant):
    tenant, api_key = make_tenant("free")
    post_webhook(client, stripe_event("checkout.session.completed", checkout_obj(tenant.id)))
    drain()
    no_metadata = {**subscription_obj(tenant.id, status="unpaid"), "metadata": {}}
    post_webhook(client, stripe_event("customer.subscription.updated", no_metadata))
    drain()
    assert usage(client, api_key)["subscription_status"] == "unpaid"
    assert usage(client, api_key)["plan"] == "free"


def test_transient_failure_is_retried_then_succeeds(client, make_tenant):
    tenant, api_key = make_tenant("free")
    event = stripe_event("checkout.session.completed", checkout_obj(tenant.id))
    post_webhook(client, event)

    def flaky(_subscription_id):
        raise ConnectionError("stripe unreachable")

    now = datetime.now(UTC)
    assert worker.run_once(fetch_subscription=flaky, now=now) == "retry"
    row = stripe_event_row(event["id"])
    assert row["status"] == "pending" and row["attempts"] == 1
    assert "ConnectionError" in row["last_error"]
    assert usage(client, api_key)["plan"] == "free"  # failed attempt changed nothing

    # Not due yet (backoff), then due.
    assert worker.run_once(fetch_subscription=fake_fetch(), now=now) is None
    later = now + timedelta(seconds=worker.backoff_seconds(1) + 1)
    assert worker.run_once(fetch_subscription=fake_fetch(), now=later) == "done"
    assert usage(client, api_key)["plan"] == "pro"


def test_permanent_failure_goes_dead_and_alerts(client, caplog):
    unknown = stripe_event(
        "customer.subscription.updated",
        {"id": "sub_x", "status": "active", "customer": "cus_unknown", "metadata": {}},
    )
    post_webhook(client, unknown)

    results = []
    now = datetime.now(UTC)
    with caplog.at_level(logging.WARNING, logger="app.jobs.worker"):
        for _ in range(worker.MAX_ATTEMPTS):
            now += timedelta(seconds=worker.MAX_BACKOFF_SECONDS + 1)
            results.append(worker.run_once(fetch_subscription=fake_fetch(), now=now))

    assert results == ["retry"] * (worker.MAX_ATTEMPTS - 1) + ["dead"]
    row = stripe_event_row(unknown["id"])
    assert row["status"] == "dead" and row["attempts"] == worker.MAX_ATTEMPTS
    alerts = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(alerts) == 1 and "ALERT stripe_event_dead" in alerts[0].getMessage()


@pytest.mark.parametrize("attempts,expected", [(1, 2), (3, 8), (8, 256), (9, 300), (20, 300)])
def test_backoff_is_exponential_and_capped(attempts, expected):
    assert worker.backoff_seconds(attempts) == expected
