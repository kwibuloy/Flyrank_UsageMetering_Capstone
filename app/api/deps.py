"""Request-scoped dependencies: DB session, tenant from X-API-Key, Idempotency-Key header."""

import re
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.services import tenancy
from app.services.errors import IdempotencyKeyRequired, InvalidIdempotencyKey
from app.services.tenancy import TenantContext

IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[\x21-\x7e]{1,255}$")  # visible ASCII, 1-255 chars


def get_db() -> Iterator[Session]:
    """One session per request. Routes commit explicitly *before* returning, so a response
    is never sent for work that was not committed. Anything uncommitted is rolled back."""
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]


def get_tenant(db: DbSession, x_api_key: Annotated[str | None, Header()] = None) -> TenantContext:
    return tenancy.authenticate(db, x_api_key)


def require_idempotency_key(idempotency_key: Annotated[str | None, Header()] = None) -> str:
    if idempotency_key is None:
        raise IdempotencyKeyRequired(
            "Billable requests need an Idempotency-Key header (for example a UUID) "
            "so a retry is never counted twice."
        )
    if not IDEMPOTENCY_KEY_PATTERN.match(idempotency_key):
        raise InvalidIdempotencyKey("Idempotency-Key must be 1-255 visible ASCII characters.")
    return idempotency_key


def optional_idempotency_key(
    idempotency_key: Annotated[str | None, Header()] = None,
) -> str | None:
    if idempotency_key is None:
        return None
    return require_idempotency_key(idempotency_key)


async def raw_body(request: Request) -> bytes:
    return await request.body()


CurrentTenant = Annotated[TenantContext, Depends(get_tenant)]
OptionalIdempotencyKey = Annotated[str | None, Depends(optional_idempotency_key)]
RawBody = Annotated[bytes, Depends(raw_body)]
IdempotencyKeyHeader = Annotated[str, Depends(require_idempotency_key)]
