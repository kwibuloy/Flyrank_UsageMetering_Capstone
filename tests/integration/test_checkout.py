"""POST /billing/checkout (S1). The Stripe SDK call is replaced; no network in tests."""

from types import SimpleNamespace

import pytest
import stripe

from tests.integration.conftest import checkout_obj, post_webhook, stripe_event


@pytest.fixture
def fake_stripe_checkout(monkeypatch):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            id="cs_test_123", url="https://checkout.stripe.com/c/pay/cs_test_123"
        )

    monkeypatch.setattr(stripe.checkout.Session, "create", create)
    return calls


def test_checkout_creates_subscription_session_for_tenant(
    client, make_tenant, fake_stripe_checkout
):
    tenant, api_key = make_tenant("free")

    r = client.post("/billing/checkout", headers={"X-API-Key": api_key, "Idempotency-Key": "co-1"})

    assert r.status_code == 200
    assert r.json() == {
        "checkout_url": "https://checkout.stripe.com/c/pay/cs_test_123",
        "session_id": "cs_test_123",
    }
    (call,) = fake_stripe_checkout
    assert call["mode"] == "subscription"
    assert call["line_items"] == [{"price": "price_test_pro", "quantity": 1}]
    assert call["client_reference_id"] == str(tenant.id)
    assert call["subscription_data"] == {"metadata": {"tenant_id": str(tenant.id)}}
    assert call["success_url"].startswith("http://testserver/billing/success")
    assert call["idempotency_key"] == f"checkout-{tenant.id}-co-1"
    assert call["api_key"] == "sk_test_dummy_for_tests"


def test_checkout_does_not_change_the_plan(client, make_tenant, fake_stripe_checkout):
    _, api_key = make_tenant("free")
    client.post("/billing/checkout", headers={"X-API-Key": api_key})
    assert client.get("/usage", headers={"X-API-Key": api_key}).json()["plan"] == "free"


def test_already_active_pro_is_409(client, make_tenant, fake_stripe_checkout):
    from tests.integration.test_stripe_sync import drain

    tenant, api_key = make_tenant("free")
    post_webhook(client, stripe_event("checkout.session.completed", checkout_obj(tenant.id)))
    drain()

    r = client.post("/billing/checkout", headers={"X-API-Key": api_key})

    assert r.status_code == 409
    assert r.json()["error"]["code"] == "already_subscribed"
    assert fake_stripe_checkout == []


def test_stripe_failure_is_502_not_500(client, make_tenant, monkeypatch):
    def boom(**kwargs):
        raise stripe.APIConnectionError("network down")

    monkeypatch.setattr(stripe.checkout.Session, "create", boom)
    _, api_key = make_tenant("free")

    r = client.post("/billing/checkout", headers={"X-API-Key": api_key})

    assert r.status_code == 502
    assert r.json()["error"]["code"] == "stripe_error"


def test_checkout_requires_api_key(client):
    assert client.post("/billing/checkout").status_code == 401


def test_success_page_shows_real_limits_and_changes_nothing(client, make_tenant):
    _, api_key = make_tenant("free")
    r = client.get("/billing/success?session_id=cs_test_123")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "webhook" in r.text and "cs_test_123" in r.text
    assert "50,000" in r.text and "5,000,000" in r.text  # read from the plans table
    assert client.get("/usage", headers={"X-API-Key": api_key}).json()["plan"] == "free"


def test_success_page_escapes_session_id(client):
    r = client.get("/billing/success", params={"session_id": "<script>alert(1)</script>"})
    assert "<script>alert" not in r.text and "&lt;script&gt;" in r.text


def test_cancel_page(client):
    r = client.get("/billing/cancel")
    assert r.status_code == 200 and "No payment was taken" in r.text and "1,000" in r.text
