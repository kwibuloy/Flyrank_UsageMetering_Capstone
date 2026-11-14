"""Quota decision. Pure function: no I/O, no clock, no database.

Rules (docs/quotas.md, docs/adr/0004):
- allow iff used + requested <= limit for every metric (inclusive boundary, all-or-nothing)
- 402 when paying fixes it (unpaid subscription, or Free plan exhausted)
- 429 + Retry-After when only waiting fixes it (Pro plan exhausted)
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.services.metrics import CHECK_ORDER, LABELS
from app.services.periods import iso_z, seconds_until

FREE_PLAN_ID = "free"
PAYMENT_BLOCKING_STATUSES = frozenset({"past_due", "unpaid", "incomplete"})
UPGRADE_PATH = "/billing/checkout"


@dataclass(frozen=True)
class Allow:
    pass


@dataclass(frozen=True)
class Reject:
    status_code: int
    code: str
    message: str
    details: dict[str, Any]


Decision = Allow | Reject


def decide(
    *,
    plan_id: str,
    limits: dict[str, int],
    used: dict[str, int],
    requested: dict[str, int],
    subscription_status: str | None,
    period_end: datetime,
    now: datetime,
) -> Decision:
    if subscription_status in PAYMENT_BLOCKING_STATUSES:
        return Reject(
            status_code=402,
            code="payment_required",
            message=(
                f"Your subscription is {subscription_status}. Update your payment method "
                "to continue; usage is paused until payment succeeds."
            ),
            details={"plan": plan_id, "subscription_status": subscription_status},
        )

    for metric in CHECK_ORDER:
        current = used.get(metric, 0)
        wanted = requested.get(metric, 0)
        limit = limits[metric]
        if current + wanted <= limit:
            continue

        label = LABELS[metric]
        plan_name = plan_id.title()
        numbers = f"{current:,} of {limit:,} used, this request needs {wanted:,}"
        details: dict[str, Any] = {
            "metric": metric,
            "used": current,
            "limit": limit,
            "requested": wanted,
            "plan": plan_id,
            "period_end": iso_z(period_end),
        }
        if plan_id == FREE_PLAN_ID:
            details["upgrade_url"] = UPGRADE_PATH
            return Reject(
                status_code=402,
                code="payment_required",
                message=(
                    f"Monthly {label} quota exceeded on the {plan_name} plan: {numbers}. "
                    "Upgrade to Pro to continue."
                ),
                details=details,
            )
        details["retry_after_seconds"] = seconds_until(period_end, now)
        return Reject(
            status_code=429,
            code="quota_exceeded",
            message=(
                f"Monthly {label} quota exceeded on the {plan_name} plan: {numbers}. "
                f"Quota resets at {iso_z(period_end)}."
            ),
            details=details,
        )

    return Allow()
