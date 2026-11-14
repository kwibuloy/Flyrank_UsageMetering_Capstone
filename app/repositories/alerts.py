import uuid
from datetime import date, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import Tenant, UsageAlert


def insert_if_new(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    period_start: date,
    metric: str,
    threshold_percent: int,
    plan_id: str,
    quota_limit: int,
    used_at_trigger: int,
) -> bool:
    """True if this threshold had not fired yet for this tenant, period and limit."""
    stmt = (
        pg_insert(UsageAlert)
        .values(
            tenant_id=tenant_id,
            period_start=period_start,
            metric=metric,
            threshold_percent=threshold_percent,
            plan_id=plan_id,
            quota_limit=quota_limit,
            used_at_trigger=used_at_trigger,
        )
        .on_conflict_do_nothing(constraint="uq_usage_alerts_once")
        .returning(UsageAlert.id)
    )
    return session.execute(stmt).first() is not None


def list_for_period(session: Session, tenant_id: uuid.UUID, period_start: date) -> list[UsageAlert]:
    return list(
        session.execute(
            select(UsageAlert)
            .where(UsageAlert.tenant_id == tenant_id, UsageAlert.period_start == period_start)
            .order_by(UsageAlert.created_at, UsageAlert.id)
        ).scalars()
    )


def claim_next(session: Session, now: datetime) -> tuple[UsageAlert, str] | None:
    """Lock the oldest due pending alert (SKIP LOCKED) with its tenant's name."""
    row = session.execute(
        select(UsageAlert, Tenant.name)
        .join(Tenant, Tenant.id == UsageAlert.tenant_id)
        .where(UsageAlert.status == "pending", UsageAlert.next_attempt_at <= now)
        .order_by(UsageAlert.created_at, UsageAlert.id)
        .limit(1)
        .with_for_update(of=UsageAlert, skip_locked=True)
    ).first()
    return (row[0], row[1]) if row else None


def mark_sent(session: Session, alert_id: int, delivery: str) -> None:
    session.execute(
        update(UsageAlert)
        .where(UsageAlert.id == alert_id)
        .values(
            status="sent",
            delivery=delivery,
            attempts=UsageAlert.attempts + 1,
            last_error=None,
            sent_at=func.now(),
        )
    )


def record_failure(
    session: Session,
    alert_id: int,
    *,
    attempts: int,
    error: str,
    next_attempt_at: datetime,
    dead: bool,
) -> None:
    session.execute(
        update(UsageAlert)
        .where(UsageAlert.id == alert_id)
        .values(
            status="dead" if dead else "pending",
            attempts=attempts,
            last_error=error,
            next_attempt_at=next_attempt_at,
        )
    )
