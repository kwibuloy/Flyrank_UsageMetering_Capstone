"""Stripe Checkout (test mode). Plan changes happen only via verified webhooks, never here."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.api import pages
from app.api.deps import CurrentTenant, DbSession, OptionalIdempotencyKey
from app.config import get_settings
from app.repositories import plans as plans_repo
from app.services import billing

router = APIRouter(prefix="/billing", tags=["billing"])


def _summary(db: DbSession, plan_id: str) -> pages.PlanSummary | None:
    plan = plans_repo.get(db, plan_id)
    if plan is None:
        return None
    return pages.PlanSummary(plan.name, plan.api_calls_limit, plan.ai_tokens_limit)


@router.post("/checkout")
def create_checkout(tenant: CurrentTenant, idempotency_key: OptionalIdempotencyKey) -> dict:
    return billing.create_checkout_session(tenant, idempotency_key)


@router.get("/success", response_class=HTMLResponse)
def checkout_success(db: DbSession, session_id: str | None = None) -> str:
    # The redirect proves nothing about payment; the signed webhook is the source of truth,
    # so this page shows "Activating" and never touches the tenant's plan.
    return pages.success_page(_summary(db, "pro"), session_id, get_settings().public_base_url)


@router.get("/cancel", response_class=HTMLResponse)
def checkout_cancel(db: DbSession) -> str:
    return pages.cancel_page(_summary(db, "free"), get_settings().public_base_url)
