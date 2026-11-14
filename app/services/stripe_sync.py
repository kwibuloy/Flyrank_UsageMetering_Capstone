"""Apply one verified Stripe event to our mirror of the tenant's subscription and plan.

Called by the worker inside a transaction. Must be idempotent: applying the same event
twice leaves the same state. Mapping table: docs/stripe.md.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.repositories import subscriptions as subscriptions_repo
from app.repositories import tenants as tenants_repo

HANDLED_EVENT_TYPES = frozenset(
    {
        "checkout.session.completed",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }
)
# past_due keeps Pro limits; the quota check returns 402 until it is paid (docs/quotas.md).
PRO_STATUSES = frozenset({"active", "trialing", "past_due"})

FetchSubscription = Callable[[str], dict[str, Any]]


class TenantNotFound(LookupError):
    """Raised so the worker retries: a later event may link the customer to a tenant."""


def apply(session: Session, event: dict[str, Any], fetch_subscription: FetchSubscription) -> str:
    event_type = event["type"]
    obj = event["data"]["object"]
    created = int(event["created"])

    if event_type == "checkout.session.completed":
        if obj.get("mode") != "subscription" or not obj.get("subscription"):
            return "skipped_not_subscription"
        tenant_id = _tenant_id_from(obj.get("client_reference_id"), obj.get("metadata"))
        if tenant_id is None or tenants_repo.get_by_id(session, tenant_id) is None:
            raise TenantNotFound(f"checkout session {obj.get('id')} has no known tenant")
        if obj.get("customer"):
            tenants_repo.set_stripe_customer_id(session, tenant_id, obj["customer"])
        # The session does not carry the subscription status, so read the current
        # subscription from Stripe. It is fresh state, so the ordering guard is skipped.
        subscription = fetch_subscription(obj["subscription"])
        return _apply_subscription(session, tenant_id, subscription, created, fresh=True)

    if event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        tenant_id = _resolve_tenant(session, obj)
        return _apply_subscription(session, tenant_id, obj, created, fresh=False)

    return "ignored_type"


def _apply_subscription(
    session: Session,
    tenant_id: uuid.UUID,
    subscription: dict[str, Any],
    created: int,
    *,
    fresh: bool,
) -> str:
    existing = subscriptions_repo.get_by_tenant(session, tenant_id)
    if existing is not None and not fresh:
        if existing.stripe_subscription_id != subscription["id"] and subscription["status"] in (
            "canceled",
            "incomplete_expired",
        ):
            return "ignored_other_subscription"
        if created < existing.last_event_created:
            return "ignored_stale"

    status = subscription["status"]
    plan_id = "pro" if status in PRO_STATUSES else "free"
    subscriptions_repo.upsert(
        session,
        tenant_id=tenant_id,
        stripe_subscription_id=subscription["id"],
        status=status,
        current_period_end=_period_end(subscription),
        event_created=created,
        usage_billing=has_metered_item(subscription),
    )
    tenants_repo.set_plan(session, tenant_id, plan_id)
    return f"plan={plan_id} status={status}"


def has_metered_item(subscription: dict[str, Any]) -> bool:
    """True if any subscription item uses a metered (usage-based) recurring price."""
    for item in (subscription.get("items") or {}).get("data") or []:
        recurring = ((item.get("price") or {}).get("recurring")) or {}
        if recurring.get("usage_type") == "metered":
            return True
    return False


def _resolve_tenant(session: Session, subscription: dict[str, Any]) -> uuid.UUID:
    tenant_id = _tenant_id_from(None, subscription.get("metadata"))
    if tenant_id is not None and tenants_repo.get_by_id(session, tenant_id) is not None:
        return tenant_id
    customer = subscription.get("customer")
    if customer:
        tenant = tenants_repo.get_by_stripe_customer_id(session, customer)
        if tenant is not None:
            return tenant.id
    raise TenantNotFound(f"subscription {subscription.get('id')} has no known tenant")


def _tenant_id_from(reference: str | None, metadata: dict[str, Any] | None) -> uuid.UUID | None:
    for candidate in (reference, (metadata or {}).get("tenant_id")):
        if candidate:
            try:
                return uuid.UUID(str(candidate))
            except ValueError:
                continue
    return None


def _period_end(subscription: dict[str, Any]) -> datetime | None:
    # Newer Stripe API versions moved current_period_end onto subscription items.
    ts = subscription.get("current_period_end")
    if ts is None:
        items = (subscription.get("items") or {}).get("data") or []
        ts = items[0].get("current_period_end") if items else None
    return datetime.fromtimestamp(ts, UTC) if ts else None
