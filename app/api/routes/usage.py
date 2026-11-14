from typing import Any

from fastapi import APIRouter

from app.api.deps import CurrentTenant, DbSession
from app.services import usage_report

router = APIRouter(tags=["metering"])


@router.get("/usage")
def get_usage(tenant: CurrentTenant, db: DbSession) -> dict[str, Any]:
    return usage_report.current_usage(db, tenant)
