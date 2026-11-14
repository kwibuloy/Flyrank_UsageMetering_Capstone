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


class FakeStripeObject(dict):
    def to_dict(self):
        return dict(self)


def fake_session(tenant_id, session_id="cs_test_a1B2c3D4e5F6g7H8"):
    return FakeStripeObject(
        id=session_id,
        livemode=False,
        currency="usd",
        amount_total=2000,
        client_reference_id=str(tenant_id),
        customer_details={"email": "dev@example.test"},
        line_items={
            "data": [
                {
                    "description": "Pro",
                    "amount_total": 2000,
                    "price": {"recurring": {"interval": "month", "usage_type": "licensed"}},
                },
                {
                    "description": "Pro",
                    "amount_total": 0,
                    "price": {
                        "recurring": {"interval": "month", "usage_type": "metered"},
                        "unit_amount_decimal": "0.0000000001",
                    },
                },
            ]
        },
    )


@pytest.fixture
def stripe_session(monkeypatch):
    """Serve one fake Checkout Session and account name instead of calling Stripe."""
    holder = {}
    monkeypatch.setattr(
        stripe.checkout.Session, "retrieve", lambda session_id, **kw: holder["session"]
    )
    monkeypatch.setattr(
        stripe.Account,
        "retrieve",
        lambda **kw: FakeStripeObject(settings={"dashboard": {"display_name": "Kwibie sandbox"}}),
    )
    return holder


def test_success_page_shows_the_order_and_changes_nothing(client, make_tenant, stripe_session):
    tenant, api_key = make_tenant("free")
    stripe_session["session"] = fake_session(tenant.id)

    r = client.get("/billing/success", params={"session_id": "cs_test_a1B2c3D4e5F6g7H8"})

    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    html = r.text
    for expected in (
        "Payment successful",
        "Kwibie sandbox",  # merchant name, as on Checkout
        "Sandbox",
        "$20.00",
        "Billed monthly based on usage",
        "Price varies",
        "$0.000000000001 per unit",
        "dev@example.test",
        "50,000 API calls · 5,000,000 AI tokens / mo",  # Pro limits from the plans table
        "Order summary",
    ):
        assert expected in html, expected
    # Display only: the plan changes when the webhook is processed, not here.
    assert client.get("/usage", headers={"X-API-Key": api_key}).json()["plan"] == "free"


def test_pending_page_polls_status_and_active_page_does_not(client, make_tenant, stripe_session):
    tenant, _ = make_tenant("free")
    stripe_session["session"] = fake_session(tenant.id)
    params = {"session_id": "cs_test_a1B2c3D4e5F6g7H8"}

    pending = client.get("/billing/success", params=params).text
    assert 'data-state="pending"' in pending
    assert 'data-status-url="/billing/status?session_id=cs_test_a1B2c3D4e5F6g7H8"' in pending
    assert "fetch(url" in pending and "<noscript>" in pending

    pro, _ = make_tenant("pro")
    stripe_session["session"] = fake_session(pro.id)
    active = client.get("/billing/success", params=params).text
    assert 'data-state="active"' in active and "fetch(url" not in active


def test_status_endpoint_flips_when_the_webhook_lands(client, make_tenant, stripe_session):
    from app.services import billing as billing_service

    billing_service._session_tenants.clear()
    tenant, _ = make_tenant("free")
    stripe_session["session"] = fake_session(tenant.id)
    params = {"session_id": "cs_test_a1B2c3D4e5F6g7H8"}

    assert client.get("/billing/status", params=params).json() == {"status": "pending"}
    post_webhook(client, stripe_event("checkout.session.completed", checkout_obj(tenant.id)))
    from app.jobs import worker

    def fetch(sub_id):
        return {"id": sub_id, "status": "active", "customer": "cus_test_1", "items": {"data": []}}

    while worker.run_once(fetch_subscription=fetch) is not None:
        pass
    assert client.get("/billing/status", params=params).json() == {"status": "active"}


def test_status_endpoint_rejects_invalid_ids(client):
    for bad in ("", "nope", "<script>", "cs_test_short"):
        assert client.get("/billing/status", params={"session_id": bad}).json() == {
            "status": "unknown"
        }


def test_success_page_without_stripe_still_renders(client):
    r = client.get("/billing/success", params={"session_id": "cs_test_a1B2c3D4e5F6g7H8"})
    assert r.status_code == 200
    assert "Payment successful" in r.text and "50,000 API calls" in r.text
    assert "fetch(url" not in r.text  # nothing to poll for


def test_success_page_never_reflects_an_invalid_session_id(client, monkeypatch):
    def must_not_call(*args, **kwargs):
        raise AssertionError("invalid ids must not reach Stripe")

    monkeypatch.setattr(stripe.checkout.Session, "retrieve", must_not_call)
    r = client.get("/billing/success", params={"session_id": "<script>alert(1)</script>"})
    assert r.status_code == 200
    assert "<script>alert" not in r.text and "alert(1)" not in r.text


def test_cancel_page(client):
    r = client.get("/billing/cancel")
    assert r.status_code == 200
    assert "Checkout canceled" in r.text and "weren't charged" in r.text
    assert "1,000 API calls" in r.text and "50,000 API calls" in r.text


def test_checkout_adds_the_usage_price_when_it_is_set_up(
    client, make_tenant, fake_stripe_checkout, no_stripe_price_lookup
):
    no_stripe_price_lookup.append(SimpleNamespace(id="price_usage_metered"))
    _, api_key = make_tenant("free")

    assert client.post("/billing/checkout", headers={"X-API-Key": api_key}).status_code == 200
    (call,) = fake_stripe_checkout
    assert call["line_items"] == [
        {"price": "price_test_pro", "quantity": 1},
        {"price": "price_usage_metered"},  # metered: no quantity
    ]


def test_checkout_still_sells_pro_if_the_usage_lookup_fails(
    client, make_tenant, fake_stripe_checkout, monkeypatch
):
    def boom(**kwargs):
        raise stripe.APIConnectionError("down")

    monkeypatch.setattr(stripe.Price, "list", boom)
    _, api_key = make_tenant("free")

    assert client.post("/billing/checkout", headers={"X-API-Key": api_key}).status_code == 200
    (call,) = fake_stripe_checkout
    assert call["line_items"] == [{"price": "price_test_pro", "quantity": 1}]
