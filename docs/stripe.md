# Stripe integration (test mode only)

Hard rules: test mode keys only (`sk_test_…`, `whsec_…` from `stripe listen`); keys live in `.env` only; never `sk_live_`. Payment truth lives at Stripe; our DB changes **only** from verified webhook events — never from the Checkout redirect.

## Env vars (see `.env.example`)
`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRO_PRICE_ID`, `PUBLIC_BASE_URL` (for success/cancel URLs).

## One-time setup in the Stripe dashboard (test mode)
1. Create product "Pro" with a recurring monthly price → copy `price_…` into `STRIPE_PRO_PRICE_ID`.
2. Copy the test secret key into `STRIPE_SECRET_KEY`.

## Checkout
`POST /billing/checkout` → `stripe.checkout.Session.create(mode="subscription", line_items=[{price: PRO, quantity: 1}], client_reference_id=tenant.id, metadata={"tenant_id": …}, customer=tenant.stripe_customer_id or omitted, success_url=…, cancel_url=…)`. Pay with `4242 4242 4242 4242`, any future expiry, any CVC.

## Webhook handler — `POST /webhooks/stripe`
1. Read the **raw** body (`await request.body()`); never parse JSON first — re-serialization breaks the signature.
2. `stripe.Webhook.construct_event(raw, sig_header, STRIPE_WEBHOOK_SECRET)`; `SignatureVerificationError`/`ValueError` → `400 invalid_signature`. Nothing is written.
3. Insert into `stripe_events` (`ON CONFLICT DO NOTHING`) → duplicate → `200`.
4. Return `200` fast; the worker applies it.

## Event → state mapping (`app/services/stripe_sync.py`)
| Event | Effect |
|---|---|
| `checkout.session.completed` | tenant from `client_reference_id`; save `stripe_customer_id`; upsert subscription (status from the subscription); plan → `pro` if status active/trialing |
| `customer.subscription.updated` | find tenant by customer id; update status/period; plan = `pro` if status in {active, trialing, past_due}* else `free` |
| `customer.subscription.deleted` | status `canceled`; plan → `free` |
| anything else | stored as `ignored` |
\* `past_due` keeps Pro limits but quota returns 402 until paid ([quotas.md](quotas.md)).

Ordering guard: skip an event if `event.created < subscriptions.last_event_created`. Unknown tenant → mark `dead` + alert (never guess).

## Local loop (Stripe CLI, no tunnel)
```bash
stripe login
stripe listen --forward-to localhost:8000/webhooks/stripe      # prints whsec_… → .env
stripe trigger checkout.session.completed
stripe events resend evt_…                                     # replay → must be processed once
```
Forged-signature probe: `curl -X POST localhost:8000/webhooks/stripe -H "Stripe-Signature: t=1,v1=bad" -d '{}'` → 400.
Install on Windows: `scoop bucket add stripe https://github.com/stripe/scoop-stripe-cli.git` then `scoop install stripe`, or the release zip from github.com/stripe/stripe-cli.
