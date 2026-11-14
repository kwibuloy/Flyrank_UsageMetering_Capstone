"""Typed errors raised below the HTTP layer. app/api/errors.py maps them to status codes."""

from typing import Any


class AppError(Exception):
    code = "app_error"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class Unauthorized(AppError):
    code = "unauthorized"


class IdempotencyKeyRequired(AppError):
    code = "idempotency_key_required"


class InvalidIdempotencyKey(AppError):
    code = "invalid_idempotency_key"


class IdempotencyKeyReuse(AppError):
    code = "idempotency_key_reuse"


class InvalidSignature(AppError):
    code = "invalid_signature"


class AlreadySubscribed(AppError):
    code = "already_subscribed"


class StripeNotConfigured(AppError):
    code = "stripe_not_configured"


class StripeUpstreamError(AppError):
    code = "stripe_error"
