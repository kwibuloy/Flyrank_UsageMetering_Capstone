"""Usage alerts at 80% and 100% of a monthly quota. Spec: docs/background-jobs.md#usage-alerts.

`crossed_thresholds` is pure. `enqueue_crossed` runs inside the metering transaction, while
the tenant's counter rows are locked, so `used_before` is exact and two concurrent requests
can never both "cross" the same threshold; the unique constraint is the backstop.
"""

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.repositories import alerts as alerts_repo
from app.services.metrics import CHECK_ORDER, LABELS
from app.services.periods import period_bounds

THRESHOLDS = (80, 100)


def crossed_thresholds(used_before: int, used_after: int, limit: int) -> list[int]:
    """Thresholds reached by moving from used_before to used_after (integer math only)."""
    if limit <= 0 or used_after <= used_before:
        return []
    return [t for t in THRESHOLDS if used_before * 100 < limit * t <= used_after * 100]


def enqueue_crossed(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    plan_id: str,
    period_start: date,
    limits: dict[str, int],
    used_before: dict[str, int],
    used_after: dict[str, int],
) -> list[tuple[str, int]]:
    """Queue one alert per newly crossed (metric, threshold). Returns what was queued."""
    queued = []
    for metric in CHECK_ORDER:
        before, after = used_before.get(metric, 0), used_after.get(metric, 0)
        for threshold in crossed_thresholds(before, after, limits[metric]):
            if alerts_repo.insert_if_new(
                session,
                tenant_id=tenant_id,
                period_start=period_start,
                metric=metric,
                threshold_percent=threshold,
                plan_id=plan_id,
                quota_limit=limits[metric],
                used_at_trigger=after,
            ):
                queued.append((metric, threshold))
    return queued


def message(
    tenant_name: str, plan_id: str, metric: str, threshold: int, used: int, limit: int
) -> str:
    label = LABELS[metric]
    if threshold >= 100:
        fix = (
            "Requests are blocked until the next month. Upgrade to Pro to continue now."
            if plan_id == "free"
            else "Requests are rejected with 429 until the next month."
        )
        return (
            f"{tenant_name} has used all of its monthly {label} quota "
            f"({used:,} of {limit:,}). {fix}"
        )
    return (
        f"{tenant_name} has used {threshold}% of its monthly {label} quota "
        f"({used:,} of {limit:,}) on the {plan_id.capitalize()} plan."
    )


def payload(alert: Any, tenant_name: str) -> dict[str, Any]:
    """Body sent to the alert webhook. `alert_id` lets receivers de-duplicate (at-least-once)."""
    return {
        "type": "usage.threshold_reached",
        "alert_id": alert.id,
        "tenant": tenant_name,
        "tenant_id": str(alert.tenant_id),
        "plan": alert.plan_id,
        "metric": alert.metric,
        "threshold_percent": alert.threshold_percent,
        "used": alert.used_at_trigger,
        "limit": alert.quota_limit,
        "period_start": alert.period_start.isoformat(),
        "message": message(
            tenant_name,
            alert.plan_id,
            alert.metric,
            alert.threshold_percent,
            alert.used_at_trigger,
            alert.quota_limit,
        ),
    }


def to_json(alert: Any) -> dict[str, Any]:
    """Tenant-facing view for GET /alerts (no internals like last_error)."""
    return {
        "metric": alert.metric,
        "threshold_percent": alert.threshold_percent,
        "plan": alert.plan_id,
        "used": alert.used_at_trigger,
        "limit": alert.quota_limit,
        "status": alert.status,
        "triggered_at": alert.created_at.isoformat().replace("+00:00", "Z"),
        "sent_at": alert.sent_at.isoformat().replace("+00:00", "Z") if alert.sent_at else None,
    }


def current_period(
    session: Session, tenant_id: uuid.UUID, now: datetime | None = None
) -> tuple[datetime, datetime, list[dict[str, Any]]]:
    """This tenant's alerts for the current billing period, tenant-facing JSON."""
    start, end = period_bounds(now or datetime.now(UTC))
    rows = alerts_repo.list_for_period(session, tenant_id, start.date())
    return start, end, [to_json(row) for row in rows]
