"""Maps every error to the JSON envelope in docs/api.md: {"error": {code, message, details}}."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.errors import (
    AppError,
    IdempotencyKeyRequired,
    IdempotencyKeyReuse,
    InvalidIdempotencyKey,
    Unauthorized,
)

logger = logging.getLogger(__name__)

STATUS_BY_ERROR: dict[type[AppError], int] = {
    Unauthorized: 401,
    IdempotencyKeyRequired: 400,
    InvalidIdempotencyKey: 400,
    IdempotencyKeyReuse: 409,
}


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        status = next((s for cls, s in STATUS_BY_ERROR.items() if isinstance(exc, cls)), 500)
        headers = {"WWW-Authenticate": "ApiKey"} if status == 401 else None
        return JSONResponse(
            error_body(exc.code, exc.message, exc.details), status_code=status, headers=headers
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(p) for p in err["loc"] if p != "body") or "body",
                "message": err["msg"],
                "type": err["type"],
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            error_body(
                "validation_error", "Request is invalid; see details.fields.", {"fields": fields}
            ),
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(
            error_body(code, str(exc.detail)),
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error method=%s path=%s", request.method, request.url.path)
        return JSONResponse(error_body("internal_error", "Internal server error."), status_code=500)
