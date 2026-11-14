"""Stripe API calls (test mode): Checkout Session creation and subscription lookup."""

import logging
from typing import Any

import stripe

from app.config import get_settings
from app.services.errors import AlreadySubscribed, StripeNotConfigured, StripeUpstreamError
from app.services.tenancy import TenantContext

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = frozenset({"active", "trialing"})


def _secret_key() -> str:
    key = get_settings().stripe_secret_key.get_secret_value()
    if not key:
        raise StripeNotConfigured("STRIPE_SECRET_KEY is not set.")
    if key.startswith(("sk_live_", "rk_live_")):
        raise StripeNotConfigured("Live Stripe keys are refused; use a test-mode key.")
    return key


def create_checkout_session(
    tenant: TenantContext, idempotency_key: str | None = None
) -> dict[str, str]:
    if tenant.plan_id == "pro" and tenant.subscription_status in ACTIVE_STATUSES:
        raise AlreadySubscribed("This tenant already has an active Pro subscription.")
    settings = get_settings()
    key = _secret_key()
    if not settings.stripe_pro_price_id:
        raise StripeNotConfigured("STRIPE_PRO_PRICE_ID is not set.")

    base = settings.public_base_url.rstrip("/")
    tenant_ref = str(tenant.id)
    params: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": settings.stripe_pro_price_id, "quantity": 1}],
        "client_reference_id": tenant_ref,
        "metadata": {"tenant_id": tenant_ref},
        # Copied onto the subscription, so subscription events identify the tenant too.
        "subscription_data": {"metadata": {"tenant_id": tenant_ref}},
        "success_url": f"{base}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{base}/billing/cancel",
    }
    if tenant.stripe_customer_id:
        params["customer"] = tenant.stripe_customer_id
    options: dict[str, Any] = {"api_key": key}
    if idempotency_key:
        options["idempotency_key"] = f"checkout-{tenant_ref}-{idempotency_key}"

    try:
        session = stripe.checkout.Session.create(**options, **params)
    except stripe.StripeError as exc:
        logger.warning("stripe_checkout_failed tenant=%s error=%s", tenant.id, type(exc).__name__)
        raise StripeUpstreamError(
            "Stripe could not create the checkout session. Try again shortly."
        ) from exc
    logger.info("stripe_checkout_created tenant=%s session=%s", tenant.id, session.id)
    return {"checkout_url": session.url, "session_id": session.id}


def fetch_subscription(subscription_id: str) -> dict[str, Any]:
    subscription = stripe.Subscription.retrieve(subscription_id, api_key=_secret_key())
    return subscription.to_dict()
