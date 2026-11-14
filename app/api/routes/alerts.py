"""GET /alerts: this tenant's usage alerts for the current billing period."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from app.api.deps import CurrentTenant, DbSession
from app.repositories import alerts as alerts_repo
from app.services import alerts
from app.services.periods import period_bounds
from app.services.usage_report import period_json

router = APIRouter(tags=["metering"])


@router.get("/alerts")
def list_alerts(tenant: CurrentTenant, db: DbSession) -> dict[str, Any]:
    start, end = period_bounds(datetime.now(UTC))
    rows = alerts_repo.list_for_period(db, tenant.id, start.date())
    return {
        "tenant": tenant.name,
        "period": period_json(start, end),
        "thresholds_percent": list(alerts.THRESHOLDS),
        "alerts": [alerts.to_json(row) for row in rows],
    }
