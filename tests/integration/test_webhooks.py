"""Webhook intake (S2, P4): signature verification and dedupe. Spec: docs/stripe.md."""

import json

from tests.integration.conftest import (
    checkout_obj,
    post_webhook,
    stripe_event,
    stripe_event_count,
    stripe_event_row,
    subscription_obj,
)


def test_forged_signature_is_400_and_nothing_is_stored(client, make_tenant):
    tenant, _ = make_tenant()
    event = stripe_event("checkout.session.completed", checkout_obj(tenant.id))

    r = post_webhook(client, event, secret="whsec_attacker_guess")

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_signature"
    assert stripe_event_count() == 0


def test_missing_signature_is_400(client):
    r = client.post("/webhooks/stripe", content=b"{}")
    assert r.status_code == 400
    assert stripe_event_count() == 0


def test_tampered_body_is_400(client, make_tenant):
    tenant, _ = make_tenant()
    event = stripe_event("customer.subscription.updated", subscription_obj(tenant.id))
    good = post_webhook(client, event)  # sanity: the untampered version is accepted
    assert good.status_code == 200

    payload = json.dumps(event).encode()
    from tests.integration.conftest import sign

    header = sign(payload)
    tampered = payload.replace(b'"active"', b'"canceled"')
    r = client.post("/webhooks/stripe", content=tampered, headers={"Stripe-Signature": header})
    assert r.status_code == 400


def test_valid_event_is_queued_once_and_replay_is_duplicate(client, make_tenant):
    tenant, _ = make_tenant()
    event = stripe_event("customer.subscription.updated", subscription_obj(tenant.id))

    first = post_webhook(client, event)
    second = post_webhook(client, event)

    assert first.status_code == second.status_code == 200
    assert first.json() == {"status": "queued"}
    assert second.json() == {"status": "duplicate"}
    assert stripe_event_count() == 1
    assert stripe_event_row(event["id"])["status"] == "pending"


def test_unhandled_event_type_is_recorded_as_ignored(client):
    event = stripe_event("invoice.created", {"id": "in_1", "object": "invoice"})
    r = post_webhook(client, event)
    assert r.json() == {"status": "ignored"}
    assert stripe_event_row(event["id"])["status"] == "ignored"
