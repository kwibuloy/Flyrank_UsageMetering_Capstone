"""Webhook intake: verify, dedupe, queue. No tenant state changes here (the worker does that).

Spec: docs/stripe.md, docs/idempotency.md#webhook-dedupe.
"""

import json
import logging

import stripe
from sqlalchemy.orm import Session

from app.config import get_settings
from app.repositories import stripe_events as events_repo
from app.services.errors import InvalidSignature, StripeNotConfigured
from app.services.stripe_sync import HANDLED_EVENT_TYPES

logger = logging.getLogger(__name__)


def receive(session: Session, raw_body: bytes, signature_header: str | None) -> str:
    """Returns "queued", "duplicate" or "ignored". Raises InvalidSignature for forgeries."""
    secret = get_settings().stripe_webhook_secret.get_secret_value()
    if not secret:
        raise StripeNotConfigured("STRIPE_WEBHOOK_SECRET is not set.")
    if not signature_header:
        raise InvalidSignature("Missing Stripe-Signature header.")
    try:
        # Verified against the RAW bytes: re-serialised JSON would not match the signature.
        stripe.Webhook.construct_event(raw_body, signature_header, secret)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        logger.warning("stripe_webhook_rejected reason=%s", type(exc).__name__)
        raise InvalidSignature("Webhook signature verification failed.") from exc

    payload = json.loads(raw_body)
    handled = payload["type"] in HANDLED_EVENT_TYPES
    is_new = events_repo.insert_if_new(
        session,
        event_id=payload["id"],
        event_type=payload["type"],
        payload=payload,
        status="pending" if handled else "ignored",
    )
    outcome = "duplicate" if not is_new else ("queued" if handled else "ignored")
    logger.info("stripe_webhook id=%s type=%s result=%s", payload["id"], payload["type"], outcome)
    return outcome
