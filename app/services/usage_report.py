"""Read path: current-period usage per tenant (GET /usage)."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.repositories import usage as usage_repo
from app.services.metrics import CHECK_ORDER
from app.services.periods import iso_z, period_bounds
from app.services.tenancy import TenantContext


def metrics_snapshot(limits: dict[str, int], used: dict[str, int]) -> dict[str, Any]:
    return {
        metric: {
            "used": used.get(metric, 0),
            "limit": limits[metric],
            "remaining": max(0, limits[metric] - used.get(metric, 0)),
        }
        for metric in CHECK_ORDER
    }


def period_json(start: datetime, end: datetime) -> dict[str, str]:
    return {"start": iso_z(start), "end": iso_z(end)}


def current_usage(
    session: Session, tenant: TenantContext, now: datetime | None = None
) -> dict[str, Any]:
    start, end = period_bounds(now or datetime.now(UTC))
    used = usage_repo.rollup(session, tenant.id, start.date())
    return {
        "tenant": tenant.name,
        "plan": tenant.plan_id,
        "subscription_status": tenant.subscription_status,
        "period": period_json(start, end),
        "metrics": metrics_snapshot(tenant.limits, used),
    }
