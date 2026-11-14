"""GET /alerts: this tenant's usage alerts for the current billing period."""

from typing import Any

from fastapi import APIRouter

from app.api.deps import CurrentTenant, DbSession
from app.services import alerts
from app.services.usage_report import period_json

router = APIRouter(tags=["metering"])


@router.get("/alerts")
def list_alerts(tenant: CurrentTenant, db: DbSession) -> dict[str, Any]:
    start, end, rows = alerts.current_period(db, tenant.id)
    return {
        "tenant": tenant.name,
        "period": period_json(start, end),
        "thresholds_percent": list(alerts.THRESHOLDS),
        "alerts": rows,
    }
