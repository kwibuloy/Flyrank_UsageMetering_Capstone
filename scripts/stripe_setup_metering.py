"""One-time Stripe setup for usage-based billing (test mode). Idempotent: safe to re-run.

Creates, if missing:
  1. a Billing Meter that sums `value` per customer (event name from STRIPE_METER_EVENT_NAME,
     default `usage_cost_picousd`);
  2. a metered monthly price on the same product as STRIPE_PRO_PRICE_ID, priced at
     $0.000000000001 per unit (1 unit = 1 pico-USD), with lookup key
     STRIPE_USAGE_PRICE_LOOKUP_KEY (default `pro_usage_picousd`).

The app finds the price by lookup key, so no .env change is needed. New Pro checkouts then
include the usage price. Run:
    docker compose exec api python scripts/stripe_setup_metering.py

Spec: docs/stripe.md#usage-based-billing-metered.
"""

import sys

import stripe

from app.config import get_settings
from app.services import billing

# 1 unit = 1 pico-USD = 1e-12 USD = 1e-10 US cents. Stripe allows 12 decimal places.
UNIT_AMOUNT_CENTS = "0.0000000001"


def main() -> int:
    settings = get_settings()
    key = billing.secret_key()  # refuses live keys
    if not settings.stripe_pro_price_id:
        print("STRIPE_PRO_PRICE_ID is not set", file=sys.stderr)
        return 1

    event_name = settings.stripe_meter_event_name
    meter = next(
        (
            m
            for m in stripe.billing.Meter.list(api_key=key, limit=100).auto_paging_iter()
            if m.event_name == event_name and m.status == "active"
        ),
        None,
    )
    if meter is None:
        meter = stripe.billing.Meter.create(
            api_key=key,
            display_name="Metered usage (pico-USD)",
            event_name=event_name,
            default_aggregation={"formula": "sum"},
            customer_mapping={"event_payload_key": "stripe_customer_id", "type": "by_id"},
            value_settings={"event_payload_key": "value"},
        )
        print(f"created meter      {meter.id} event_name={event_name}")
    else:
        print(f"meter exists       {meter.id} event_name={event_name}")

    pro = stripe.Price.retrieve(settings.stripe_pro_price_id, api_key=key)
    lookup_key = settings.stripe_usage_price_lookup_key
    existing = stripe.Price.list(api_key=key, lookup_keys=[lookup_key], active=True, limit=1)
    if existing.data:
        price = existing.data[0]
        print(f"usage price exists {price.id} lookup_key={lookup_key}")
    else:
        price = stripe.Price.create(
            api_key=key,
            product=pro.product,
            currency=pro.currency,
            unit_amount_decimal=UNIT_AMOUNT_CENTS,
            recurring={"interval": "month", "usage_type": "metered", "meter": meter.id},
            lookup_key=lookup_key,
            nickname="Pro usage (1 unit = 1 pico-USD)",
        )
        print(f"created usage price {price.id} lookup_key={lookup_key}")

    if price.currency != pro.currency:
        print(f"currency mismatch: usage {price.currency} vs Pro {pro.currency}", file=sys.stderr)
        return 1
    print(f"ready: new Pro checkouts bill the flat fee + metered usage ({pro.currency.upper()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
