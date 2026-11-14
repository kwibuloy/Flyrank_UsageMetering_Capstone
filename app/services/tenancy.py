"""Tenant identity: API keys are stored only as SHA-256 hashes."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.repositories import plans as plans_repo
from app.repositories import tenants as tenants_repo
from app.services.errors import Unauthorized
from app.services.metrics import AI_TOKENS, API_CALLS


@dataclass(frozen=True)
class TenantContext:
    id: uuid.UUID
    name: str
    plan_id: str
    limits: dict[str, int]
    subscription_status: str | None = None
    stripe_customer_id: str | None = None
    usage_billing: bool = False  # subscription includes the metered usage price


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    return "mk_" + secrets.token_urlsafe(24)


def authenticate(session: Session, api_key: str | None) -> TenantContext:
    if not api_key:
        raise Unauthorized("Missing X-API-Key header.")
    found = tenants_repo.get_with_plan_by_key_hash(session, hash_api_key(api_key))
    if found is None:
        raise Unauthorized("Unknown API key.")
    tenant, plan, subscription = found
    return TenantContext(
        id=tenant.id,
        name=tenant.name,
        plan_id=plan.id,
        limits={API_CALLS: plan.api_calls_limit, AI_TOKENS: plan.ai_tokens_limit},
        subscription_status=subscription.status if subscription else None,
        stripe_customer_id=tenant.stripe_customer_id,
        usage_billing=bool(subscription and subscription.usage_billing),
    )


def create_or_update_tenant(
    session: Session, *, name: str, plan_id: str, api_key: str | None = None
) -> str:
    """Upsert a tenant by name and return its (raw) API key. Used by seed, probes, tests."""
    api_key = api_key or generate_api_key()
    tenants_repo.upsert(session, name=name, plan_id=plan_id, api_key_hash=hash_api_key(api_key))
    return api_key


@dataclass(frozen=True)
class PlanLimits:
    plan_id: str
    name: str
    api_calls: int
    ai_tokens: int


def plan_limits(session: Session, plan_id: str) -> PlanLimits | None:
    plan = plans_repo.get(session, plan_id)
    if plan is None:
        return None
    return PlanLimits(plan.id, plan.name, plan.api_calls_limit, plan.ai_tokens_limit)


def current_plan(session: Session, tenant_id: uuid.UUID) -> str | None:
    tenant = tenants_repo.get_by_id(session, tenant_id)
    return tenant.plan_id if tenant else None
