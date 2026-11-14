"""POST /webhooks/stripe — verify, dedupe, queue. Contract: docs/api.md, docs/stripe.md."""

from typing import Annotated

from fastapi import APIRouter, Header

from app.api.deps import DbSession, RawBody
from app.services import webhooks

router = APIRouter(tags=["stripe"])


@router.post("/webhooks/stripe")
def stripe_webhook(
    body: RawBody,
    db: DbSession,
    stripe_signature: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    result = webhooks.receive(db, body, stripe_signature)
    db.commit()  # the event row is durable before Stripe gets its 200
    return {"status": result}
