"""Who is reported to Stripe, and the de-duplication identifier (usage-based billing)."""

import uuid

import pytest

from app.services.stripe_sync import has_metered_item
from app.services.tenancy import TenantContext
from app.services.usage_billing import identifier, should_report

TID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def tenant(**overrides):
    values = {
        "id": TID,
        "name": "t",
        "plan_id": "pro",
        "limits": {"api_calls": 50_000, "ai_tokens": 5_000_000},
        "subscription_status": "active",
        "stripe_customer_id": "cus_1",
        "usage_billing": True,
    }
    return TenantContext(**{**values, **overrides})


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({}, True),
        ({"subscription_status": "trialing"}, True),
        ({"plan_id": "free"}, False),
        ({"usage_billing": False}, False),  # flat-fee subscription (no metered price)
        ({"subscription_status": "past_due"}, False),
        ({"subscription_status": None}, False),  # seeded/comped Pro, no subscription
        ({"stripe_customer_id": None}, False),
    ],
)
def test_should_report(overrides, expected):
    assert should_report(tenant(**overrides)) is expected


def test_identifier_is_stable_per_request_and_distinct_across_tenants():
    other = uuid.UUID("22222222-2222-2222-2222-222222222222")
    assert identifier(TID, "k1") == identifier(TID, "k1")
    assert len({identifier(TID, "k1"), identifier(TID, "k2"), identifier(other, "k1")}) == 3
    assert identifier(TID, "x" * 255).startswith("usage_") and len(identifier(TID, "k")) == 46


def test_has_metered_item():
    flat = {"items": {"data": [{"price": {"recurring": {"usage_type": "licensed"}}}]}}
    metered = {
        "items": {
            "data": [
                {"price": {"recurring": {"usage_type": "licensed"}}},
                {"price": {"recurring": {"usage_type": "metered"}}},
            ]
        }
    }
    assert has_metered_item(metered) is True
    assert has_metered_item(flat) is False
    assert has_metered_item({}) is False
