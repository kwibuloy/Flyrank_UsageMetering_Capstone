from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import StripeEvent


def insert_if_new(
    session: Session, *, event_id: str, event_type: str, payload: dict[str, Any], status: str
) -> bool:
    """True if this is the first time we see this Stripe event id."""
    stmt = (
        pg_insert(StripeEvent)
        .values(id=event_id, type=event_type, payload=payload, status=status)
        .on_conflict_do_nothing(index_elements=[StripeEvent.id])
        .returning(StripeEvent.id)
    )
    return session.execute(stmt).first() is not None


def claim_next(session: Session, now: datetime) -> StripeEvent | None:
    """Lock the oldest due pending event. SKIP LOCKED lets several workers run safely;
    the row stays locked until the caller commits or rolls back."""
    return session.execute(
        select(StripeEvent)
        .where(StripeEvent.status == "pending", StripeEvent.next_attempt_at <= now)
        .order_by(StripeEvent.received_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()


def mark_done(session: Session, event_id: str, outcome: str) -> None:
    session.execute(
        update(StripeEvent)
        .where(StripeEvent.id == event_id)
        .values(
            status="done",
            outcome=outcome,
            attempts=StripeEvent.attempts + 1,
            last_error=None,
            processed_at=func.now(),
        )
    )


def record_failure(
    session: Session,
    event_id: str,
    *,
    attempts: int,
    error: str,
    next_attempt_at: datetime,
    dead: bool,
) -> None:
    session.execute(
        update(StripeEvent)
        .where(StripeEvent.id == event_id)
        .values(
            status="dead" if dead else "pending",
            attempts=attempts,
            last_error=error,
            next_attempt_at=next_attempt_at,
        )
    )
