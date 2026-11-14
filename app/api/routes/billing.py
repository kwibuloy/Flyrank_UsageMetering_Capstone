"""Stripe Checkout (test mode). Plan changes happen only via verified webhooks, never here."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.api import pages
from app.api.deps import CurrentTenant, DbSession, OptionalIdempotencyKey
from app.config import get_settings
from app.services import billing, tenancy

router = APIRouter(prefix="/billing", tags=["billing"])


@router.post("/checkout")
def create_checkout(tenant: CurrentTenant, idempotency_key: OptionalIdempotencyKey) -> dict:
    return billing.create_checkout_session(tenant, idempotency_key)


@router.get("/success", response_class=HTMLResponse)
def checkout_success(db: DbSession, session_id: str | None = None) -> str:
    # The redirect proves nothing about payment; the signed webhook is the source of truth.
    # This page only reads: what was bought (from Stripe) and whether the webhook landed.
    summary = billing.checkout_summary(session_id)
    plan_id = tenancy.current_plan(db, summary.tenant_id) if summary and summary.tenant_id else None
    state = pages.SuccessState(active=plan_id == "pro", known=plan_id is not None)
    return pages.success_page(
        summary, tenancy.plan_limits(db, "pro"), state, get_settings().public_base_url
    )


@router.get("/status")
def checkout_status(db: DbSession, session_id: str | None = None) -> dict[str, str]:
    """Polled by the success page: has the webhook activated Pro for this Checkout Session?

    Display only. Returns "active", "pending" (paid session, webhook not processed yet) or
    "unknown" (invalid id, or Stripe unreachable).
    """
    tenant_id = billing.session_tenant(session_id)
    if tenant_id is None:
        return {"status": "unknown"}
    return {"status": "active" if tenancy.current_plan(db, tenant_id) == "pro" else "pending"}


@router.get("/cancel", response_class=HTMLResponse)
def checkout_cancel(db: DbSession) -> str:
    return pages.cancel_page(
        tenancy.plan_limits(db, "pro"),
        tenancy.plan_limits(db, "free"),
        billing.merchant_display_name(),
    )
