import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import Plan, Tenant


def get_with_plan_by_key_hash(session: Session, key_hash: str) -> tuple[Tenant, Plan] | None:
    row = session.execute(
        select(Tenant, Plan)
        .join(Plan, Plan.id == Tenant.plan_id)
        .where(Tenant.api_key_hash == key_hash)
    ).first()
    return (row[0], row[1]) if row else None


def upsert(session: Session, *, name: str, plan_id: str, api_key_hash: str) -> uuid.UUID:
    stmt = pg_insert(Tenant).values(
        id=uuid.uuid4(), name=name, plan_id=plan_id, api_key_hash=api_key_hash
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Tenant.name],
        set_={"plan_id": stmt.excluded.plan_id, "api_key_hash": stmt.excluded.api_key_hash},
    ).returning(Tenant.id)
    return session.execute(stmt).scalar_one()
