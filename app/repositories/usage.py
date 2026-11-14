import uuid
from datetime import date

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import UsageCounter, UsageEvent
from app.services.metrics import AI_TOKENS, API_CALLS


def lock_counters(
    session: Session, tenant_id: uuid.UUID, period_start: date, metrics: tuple[str, ...]
) -> dict[str, int]:
    """Ensure counter rows exist, then lock them FOR UPDATE in a fixed order."""
    ordered = sorted(metrics)
    session.execute(
        pg_insert(UsageCounter)
        .values(
            [
                {"tenant_id": tenant_id, "period_start": period_start, "metric": m, "used": 0}
                for m in ordered
            ]
        )
        .on_conflict_do_nothing()
    )
    rows = session.execute(
        select(UsageCounter.metric, UsageCounter.used)
        .where(
            UsageCounter.tenant_id == tenant_id,
            UsageCounter.period_start == period_start,
            UsageCounter.metric.in_(ordered),
        )
        .order_by(UsageCounter.metric)
        .with_for_update()
    ).all()
    return {metric: used for metric, used in rows}


def add_to_counters(
    session: Session, tenant_id: uuid.UUID, period_start: date, quantities: dict[str, int]
) -> None:
    for metric, qty in sorted(quantities.items()):
        if qty:
            session.execute(
                update(UsageCounter)
                .where(
                    UsageCounter.tenant_id == tenant_id,
                    UsageCounter.period_start == period_start,
                    UsageCounter.metric == metric,
                )
                .values(used=UsageCounter.used + qty)
            )


def insert_events(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    idempotency_key: str,
    period_start: date,
    quantities: dict[str, int],
    token_breakdown: dict[str, int],
) -> None:
    no_tokens = dict.fromkeys(token_breakdown)
    rows = []
    for metric in (API_CALLS, AI_TOKENS):
        qty = quantities.get(metric, 0)
        if qty <= 0:
            continue
        rows.append(
            {
                "tenant_id": tenant_id,
                "idempotency_key": idempotency_key,
                "metric": metric,
                "quantity": qty,
                "period_start": period_start,
                **(token_breakdown if metric == AI_TOKENS else no_tokens),
            }
        )
    if rows:
        session.execute(insert(UsageEvent), rows)


def rollup(session: Session, tenant_id: uuid.UUID, period_start: date) -> dict[str, int]:
    """SUM of usage events per metric for one tenant and period."""
    rows = session.execute(
        select(UsageEvent.metric, func.sum(UsageEvent.quantity))
        .where(UsageEvent.tenant_id == tenant_id, UsageEvent.period_start == period_start)
        .group_by(UsageEvent.metric)
    ).all()
    return {metric: int(total) for metric, total in rows}
