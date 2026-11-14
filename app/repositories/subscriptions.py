import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import Subscription


def get_by_tenant(session: Session, tenant_id: uuid.UUID) -> Subscription | None:
    return session.execute(
        select(Subscription).where(Subscription.tenant_id == tenant_id).with_for_update()
    ).scalar_one_or_none()


def upsert(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    stripe_subscription_id: str,
    status: str,
    current_period_end: datetime | None,
    event_created: int,
    usage_billing: bool = False,
) -> None:
    stmt = pg_insert(Subscription).values(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        stripe_subscription_id=stripe_subscription_id,
        status=status,
        current_period_end=current_period_end,
        last_event_created=event_created,
        usage_billing=usage_billing,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[Subscription.tenant_id],
        set_={
            "stripe_subscription_id": stmt.excluded.stripe_subscription_id,
            "status": stmt.excluded.status,
            "current_period_end": stmt.excluded.current_period_end,
            "usage_billing": stmt.excluded.usage_billing,
            "last_event_created": func.greatest(
                Subscription.last_event_created, stmt.excluded.last_event_created
            ),
            "updated_at": func.now(),
        },
    )
    session.execute(stmt)
