from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.deps import DbSession
from app.services import health

router = APIRouter(tags=["health"])


@router.get("/health")
def get_health(db: DbSession) -> JSONResponse:
    if not health.database_ok(db):
        return JSONResponse({"status": "degraded", "db": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok", "db": "ok"})
