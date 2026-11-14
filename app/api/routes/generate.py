"""POST /generate — the dummy billable action. Contract: docs/api.md."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.deps import CurrentTenant, DbSession, IdempotencyKeyHeader
from app.api.schemas import GenerateRequest
from app.services import metering
from app.services.fingerprint import request_fingerprint

router = APIRouter(tags=["metering"])


@router.post("/generate")
def generate(
    payload: GenerateRequest,
    tenant: CurrentTenant,
    idempotency_key: IdempotencyKeyHeader,
    db: DbSession,
) -> JSONResponse:
    body = payload.model_dump()
    result = metering.record_usage(
        db,
        tenant=tenant,
        idempotency_key=idempotency_key,
        request_hash=request_fingerprint("POST", "/generate", body),
        usage=metering.UsageRequest(**body),
    )
    db.commit()  # commit before responding: never acknowledge uncommitted usage
    return to_response(result)


def to_response(result: metering.MeterResult) -> JSONResponse:
    headers = {}
    if result.replayed:
        headers["Idempotent-Replayed"] = "true"
    retry_after = result.body.get("error", {}).get("details", {}).get("retry_after_seconds")
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(result.body, status_code=result.status_code, headers=headers)
