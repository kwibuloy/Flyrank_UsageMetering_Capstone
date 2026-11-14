"""Usage-based billing: report each accepted request's exact cost to a Stripe Billing Meter.

Spec: docs/stripe.md#usage-based-billing-metered. Quotas and 402/429 are unchanged; this
only puts the usage cost on the Pro invoice, next to the flat monthly fee.

- `enqueue` runs inside the metering transaction: the report exists iff the usage committed.
- `send` runs in the worker. Stripe de-duplicates meter events on `identifier`, which is
  derived from (tenant, idempotency key), so a retry after a timeout never double-bills.
"""

import hashlib
import uuid
from datetime import datetime
from typing import Any

import stripe
from sqlalchemy.orm import Session

from app.config import get_settings
from app.repositories import usage_reports as reports_repo
from app.services import billing
from app.services.tenancy import TenantContext

REPORTABLE_STATUSES = frozenset({"active", "trialing"})


def should_report(tenant: TenantContext) -> bool:
    return (
        tenant.plan_id == "pro"
        and tenant.usage_billing
        and tenant.subscription_status in REPORTABLE_STATUSES
        and bool(tenant.stripe_customer_id)
    )


def identifier(tenant_id: uuid.UUID, idempotency_key: str) -> str:
    """Stable per billable request; Stripe keeps identifiers unique for at least 24 hours."""
    digest = hashlib.sha256(f"{tenant_id}:{idempotency_key}".encode()).hexdigest()
    return f"usage_{digest[:40]}"


def enqueue(
    session: Session,
    *,
    tenant: TenantContext,
    idempotency_key: str,
    value_picousd: int,
    occurred_at: datetime,
) -> bool:
    if value_picousd <= 0 or not should_report(tenant):
        return False
    assert tenant.stripe_customer_id is not None  # guaranteed by should_report
    return reports_repo.insert_if_new(
        session,
        tenant_id=tenant.id,
        idempotency_key=idempotency_key,
        stripe_customer_id=tenant.stripe_customer_id,
        identifier=identifier(tenant.id, idempotency_key),
        value_picousd=value_picousd,
        occurred_at=occurred_at,
    )


def send(report: Any) -> None:
    """Create one meter event. Raises on any Stripe error so the worker retries."""
    stripe.billing.MeterEvent.create(
        api_key=billing.secret_key(),
        event_name=get_settings().stripe_meter_event_name,
        identifier=report.identifier,
        timestamp=int(report.occurred_at.timestamp()),
        payload={
            "stripe_customer_id": report.stripe_customer_id,
            "value": str(report.value_picousd),
        },
    )
