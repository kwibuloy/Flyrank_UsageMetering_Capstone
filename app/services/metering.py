"""The exactly-once metering path. Spec: docs/idempotency.md.

Everything below runs inside the caller's single database transaction. The caller (the
route) commits after this returns, so the key, the events, the counters and the stored
response are committed together or not at all.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.repositories import idempotency as idempotency_repo
from app.repositories import usage as usage_repo
from app.services import quota
from app.services.errors import IdempotencyKeyReuse
from app.services.metrics import AI_TOKENS, API_CALLS, METRICS
from app.services.periods import period_bounds
from app.services.tenancy import TenantContext
from app.services.usage_report import metrics_snapshot, period_json


@dataclass(frozen=True)
class UsageRequest:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    def quantities(self) -> dict[str, int]:
        # Cached input is a subset of input, so it is not added again (docs/quotas.md).
        tokens = self.input_tokens + self.output_tokens + self.reasoning_tokens
        return {API_CALLS: 1, AI_TOKENS: tokens}


@dataclass(frozen=True)
class MeterResult:
    status_code: int
    body: dict[str, Any]
    replayed: bool


def record_usage(
    session: Session,
    *,
    tenant: TenantContext,
    idempotency_key: str,
    request_hash: str,
    usage: UsageRequest,
    now: datetime | None = None,
) -> MeterResult:
    now = now or datetime.now(UTC)

    # 1. Claim the key. A concurrent duplicate blocks here until our transaction ends.
    if not idempotency_repo.try_claim(session, tenant.id, idempotency_key, request_hash):
        stored = idempotency_repo.get(session, tenant.id, idempotency_key)
        if stored is None or stored.status_code is None or stored.response_body is None:
            raise RuntimeError("idempotency row without a stored response")
        if stored.request_hash != request_hash:
            raise IdempotencyKeyReuse(
                "This Idempotency-Key was already used with a different request body. "
                "Use a new key for a new request.",
                {"idempotency_key": idempotency_key},
            )
        return MeterResult(stored.status_code, stored.response_body, replayed=True)

    # 2. Lock this tenant's counters for the period, then decide.
    start, end = period_bounds(now)
    period_start = start.date()
    requested = usage.quantities()
    used = usage_repo.lock_counters(session, tenant.id, period_start, METRICS)
    decision = quota.decide(
        plan_id=tenant.plan_id,
        limits=tenant.limits,
        used=used,
        requested=requested,
        subscription_status=tenant.subscription_status,
        period_end=end,
        now=now,
    )

    if isinstance(decision, quota.Reject):
        body = {
            "error": {
                "code": decision.code,
                "message": decision.message,
                "details": decision.details,
            }
        }
        idempotency_repo.save_response(
            session, tenant.id, idempotency_key, decision.status_code, body
        )
        return MeterResult(decision.status_code, body, replayed=False)

    # 3. Accepted: append events, bump counters, store the response for replays.
    usage_repo.insert_events(
        session,
        tenant_id=tenant.id,
        idempotency_key=idempotency_key,
        period_start=period_start,
        quantities=requested,
        token_breakdown={
            "input_tokens": usage.input_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "output_tokens": usage.output_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
        },
    )
    usage_repo.add_to_counters(session, tenant.id, period_start, requested)

    used_after = {m: used.get(m, 0) + requested.get(m, 0) for m in METRICS}
    body = {
        "request_id": str(uuid.uuid4()),
        "idempotency_key": idempotency_key,
        "metered": requested,
        "usage": metrics_snapshot(tenant.limits, used_after),
        "period": period_json(start, end),
    }
    idempotency_repo.save_response(session, tenant.id, idempotency_key, 200, body)
    return MeterResult(200, body, replayed=False)
