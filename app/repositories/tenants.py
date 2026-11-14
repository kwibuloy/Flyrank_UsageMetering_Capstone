import uuid

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import Plan, Subscription, Tenant


def get_with_plan_by_key_hash(
    session: Session, key_hash: str
) -> tuple[Tenant, Plan, Subscription | None] | None:
    row = session.execute(
        select(Tenant, Plan, Subscription)
        .join(Plan, Plan.id == Tenant.plan_id)
        .outerjoin(Subscription, Subscription.tenant_id == Tenant.id)
        .where(Tenant.api_key_hash == key_hash)
    ).first()
    return (row[0], row[1], row[2]) if row else None


def get_by_id(session: Session, tenant_id: uuid.UUID) -> Tenant | None:
    return session.get(Tenant, tenant_id)


def get_by_stripe_customer_id(session: Session, customer_id: str) -> Tenant | None:
    return session.execute(
        select(Tenant).where(Tenant.stripe_customer_id == customer_id)
    ).scalar_one_or_none()


def set_plan(session: Session, tenant_id: uuid.UUID, plan_id: str) -> None:
    session.execute(update(Tenant).where(Tenant.id == tenant_id).values(plan_id=plan_id))


def set_stripe_customer_id(session: Session, tenant_id: uuid.UUID, customer_id: str) -> None:
    session.execute(
        update(Tenant).where(Tenant.id == tenant_id).values(stripe_customer_id=customer_id)
    )


def upsert(session: Session, *, name: str, plan_id: str, api_key_hash: str) -> uuid.UUID:
    stmt = pg_insert(Tenant).values(
        id=uuid.uuid4(), name=name, plan_id=plan_id, api_key_hash=api_key_hash
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Tenant.name],
        set_={"plan_id": stmt.excluded.plan_id, "api_key_hash": stmt.excluded.api_key_hash},
    ).returning(Tenant.id)
    return session.execute(stmt).scalar_one()
