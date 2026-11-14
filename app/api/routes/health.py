from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.deps import DbSession
from app.repositories import health as health_repo

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: DbSession) -> JSONResponse:
    try:
        health_repo.ping(db)
    except Exception:
        return JSONResponse({"status": "degraded", "db": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok", "db": "ok"})
