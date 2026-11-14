import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import UsageReport


def insert_if_new(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    idempotency_key: str,
    stripe_customer_id: str,
    identifier: str,
    value_picousd: int,
    occurred_at: datetime,
) -> bool:
    stmt = (
        pg_insert(UsageReport)
        .values(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            stripe_customer_id=stripe_customer_id,
            identifier=identifier,
            value_picousd=value_picousd,
            occurred_at=occurred_at,
        )
        .on_conflict_do_nothing(constraint="uq_usage_reports_tenant_key")
        .returning(UsageReport.id)
    )
    return session.execute(stmt).first() is not None


def claim_next(session: Session, now: datetime) -> UsageReport | None:
    """Lock the oldest due pending report; SKIP LOCKED lets several workers run."""
    return session.execute(
        select(UsageReport)
        .where(UsageReport.status == "pending", UsageReport.next_attempt_at <= now)
        .order_by(UsageReport.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()


def mark_sent(session: Session, report_id: int) -> None:
    session.execute(
        update(UsageReport)
        .where(UsageReport.id == report_id)
        .values(
            status="sent", attempts=UsageReport.attempts + 1, last_error=None, sent_at=func.now()
        )
    )


def record_failure(
    session: Session,
    report_id: int,
    *,
    attempts: int,
    error: str,
    next_attempt_at: datetime,
    dead: bool,
) -> None:
    session.execute(
        update(UsageReport)
        .where(UsageReport.id == report_id)
        .values(
            status="dead" if dead else "pending",
            attempts=attempts,
            last_error=error,
            next_attempt_at=next_attempt_at,
        )
    )


def totals(session: Session, tenant_id: uuid.UUID) -> dict[str, int]:
    """Count and exact sum per status (used by GET /usage and the probe)."""
    rows = session.execute(
        select(
            UsageReport.status, func.count(), func.coalesce(func.sum(UsageReport.value_picousd), 0)
        )
        .where(UsageReport.tenant_id == tenant_id)
        .group_by(UsageReport.status)
    ).all()
    out: dict[str, int] = {}
    for status, count, total in rows:
        out[f"{status}_count"] = int(count)
        out[f"{status}_picousd"] = int(total)
    return out
