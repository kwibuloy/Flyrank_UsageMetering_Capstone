import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import IdempotencyKey


def try_claim(session: Session, tenant_id: uuid.UUID, key: str, request_hash: str) -> bool:
    """Insert the key. True if we own it now; False if it already existed.

    The primary key (tenant_id, key) makes a concurrent duplicate wait here until the
    first transaction commits, then see the conflict.
    """
    stmt = (
        pg_insert(IdempotencyKey)
        .values(tenant_id=tenant_id, key=key, request_hash=request_hash)
        .on_conflict_do_nothing(index_elements=[IdempotencyKey.tenant_id, IdempotencyKey.key])
        .returning(IdempotencyKey.key)
    )
    return session.execute(stmt).first() is not None


def get(session: Session, tenant_id: uuid.UUID, key: str) -> IdempotencyKey | None:
    return session.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.tenant_id == tenant_id, IdempotencyKey.key == key
        )
    ).scalar_one_or_none()


def save_response(
    session: Session, tenant_id: uuid.UUID, key: str, status_code: int, body: dict[str, Any]
) -> None:
    session.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.tenant_id == tenant_id, IdempotencyKey.key == key)
        .values(status_code=status_code, response_body=body)
    )
