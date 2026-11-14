"""Stripe API calls (test mode): Checkout Session creation and subscription lookup."""

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

import stripe

from app.config import get_settings
from app.services.errors import AlreadySubscribed, StripeNotConfigured, StripeUpstreamError
from app.services.tenancy import TenantContext

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = frozenset({"active", "trialing"})


def secret_key() -> str:
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
    key = secret_key()
    if not settings.stripe_pro_price_id:
        raise StripeNotConfigured("STRIPE_PRO_PRICE_ID is not set.")

    base = settings.public_base_url.rstrip("/")
    tenant_ref = str(tenant.id)
    params: dict[str, Any] = {
        "mode": "subscription",
        # Flat Pro fee + (if set up) the metered usage price: usage shows on the invoice.
        "line_items": [{"price": settings.stripe_pro_price_id, "quantity": 1}]
        + _usage_line_items(key),
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


_usage_price_cache: dict[str, str] = {}


def usage_price_id(key: str) -> str | None:
    """The metered usage price, found by lookup key (scripts/stripe_setup_metering.py).

    Only a found price is cached, so running the setup script needs no restart.
    """
    lookup_key = get_settings().stripe_usage_price_lookup_key
    if lookup_key in _usage_price_cache:
        return _usage_price_cache[lookup_key]
    prices = stripe.Price.list(api_key=key, lookup_keys=[lookup_key], active=True, limit=1)
    if not prices.data:
        return None
    _usage_price_cache[lookup_key] = prices.data[0].id
    return prices.data[0].id


def _usage_line_items(key: str) -> list[dict[str, Any]]:
    try:
        price = usage_price_id(key)
    except stripe.StripeError as exc:
        # Selling Pro must not depend on the usage price; log and sell the flat plan.
        logger.warning("stripe_usage_price_lookup_failed error=%s", type(exc).__name__)
        return []
    return [{"price": price}] if price else []


def fetch_subscription(subscription_id: str) -> dict[str, Any]:
    subscription = stripe.Subscription.retrieve(subscription_id, api_key=secret_key())
    return subscription.to_dict()


# --- Checkout session summary for the success page (display only) -----------------------

SESSION_ID_PATTERN = re.compile(r"^cs_(test|live)_[A-Za-z0-9]{10,200}$")
_merchant_cache: dict[str, str] = {}


@dataclass(frozen=True)
class CheckoutLine:
    name: str
    detail: str
    amount_cents: int | None  # None for metered lines ("Price varies")
    unit_amount_decimal: str | None  # metered price per unit, in cents, as Stripe stores it


@dataclass(frozen=True)
class CheckoutSummary:
    session_id: str
    merchant: str
    livemode: bool
    currency: str
    amount_total: int
    recurring_cents: int  # the flat amount charged each period
    interval: str | None
    email: str | None
    tenant_id: uuid.UUID | None
    lines: list[CheckoutLine]


def _merchant_name(key: str) -> str:
    """The account's display name, as Checkout shows it."""
    if "name" not in _merchant_cache:
        account = stripe.Account.retrieve(api_key=key).to_dict()
        settings = account.get("settings") or {}
        profile = account.get("business_profile") or {}
        _merchant_cache["name"] = (
            (settings.get("dashboard") or {}).get("display_name")
            or profile.get("name")
            or "Metering & Billing"
        )
    return _merchant_cache["name"]


def checkout_summary(session_id: str | None) -> CheckoutSummary | None:
    """What the customer just bought, read back from Stripe. None if unknown or unreachable.

    Used only to render the success page; it never changes state (the webhook does).
    """
    if not session_id or not SESSION_ID_PATTERN.match(session_id):
        return None
    try:
        key = secret_key()
        session = stripe.checkout.Session.retrieve(
            session_id, api_key=key, expand=["line_items"]
        ).to_dict()
        merchant = _merchant_name(key)
    except (stripe.StripeError, StripeNotConfigured) as exc:
        logger.warning("checkout_summary_unavailable error=%s", type(exc).__name__)
        return None

    lines, recurring, interval = [], 0, None
    for item in (session.get("line_items") or {}).get("data") or []:
        price = item.get("price") or {}
        recurring_info = price.get("recurring") or {}
        interval = interval or recurring_info.get("interval")
        every = f"{recurring_info['interval']}ly" if recurring_info.get("interval") else "once"
        if recurring_info.get("usage_type") == "metered":
            lines.append(
                CheckoutLine(
                    name=item.get("description") or "Usage",
                    detail=f"Billed {every} based on usage",
                    amount_cents=None,
                    unit_amount_decimal=price.get("unit_amount_decimal"),
                )
            )
        else:
            amount = int(item.get("amount_total") or 0)
            recurring += amount
            lines.append(
                CheckoutLine(
                    name=item.get("description") or "Item",
                    detail=f"Billed {every}",
                    amount_cents=amount,
                    unit_amount_decimal=None,
                )
            )

    reference = session.get("client_reference_id")
    try:
        tenant_id = uuid.UUID(reference) if reference else None
    except ValueError:
        tenant_id = None
    if len(_session_tenants) >= _SESSION_CACHE_MAX:
        _session_tenants.clear()
    _session_tenants[session_id] = tenant_id  # lets status polling skip Stripe
    return CheckoutSummary(
        session_id=session_id,
        merchant=merchant,
        livemode=bool(session.get("livemode")),
        currency=session.get("currency") or "usd",
        amount_total=int(session.get("amount_total") or 0),
        recurring_cents=recurring,
        interval=interval,
        email=(session.get("customer_details") or {}).get("email"),
        tenant_id=tenant_id,
        lines=lines,
    )


def merchant_display_name() -> str | None:
    """The Stripe account's display name, or None if Stripe is unreachable or not set up."""
    try:
        return _merchant_name(secret_key())
    except (stripe.StripeError, StripeNotConfigured):
        return None


_session_tenants: dict[str, uuid.UUID | None] = {}
_SESSION_CACHE_MAX = 1_000


def session_tenant(session_id: str | None) -> uuid.UUID | None:
    """Tenant behind a Checkout Session, cached so status polling calls Stripe once."""
    if not session_id or not SESSION_ID_PATTERN.match(session_id):
        return None
    if session_id in _session_tenants:
        return _session_tenants[session_id]
    summary = checkout_summary(session_id)
    if summary is None:
        return None  # not cached: Stripe may be briefly unreachable
    if len(_session_tenants) >= _SESSION_CACHE_MAX:
        _session_tenants.clear()
    _session_tenants[session_id] = summary.tenant_id
    return summary.tenant_id
