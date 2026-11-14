# Stripe integration (test mode only)

Hard rules: test mode keys only (`sk_test_…`, `whsec_…` from `stripe listen`); keys live in `.env` only; never `sk_live_`. Payment truth lives at Stripe; our DB changes **only** from verified webhook events — never from the Checkout redirect.

## Env vars (see `.env.example`)
`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRO_PRICE_ID`, `PUBLIC_BASE_URL` (for success/cancel URLs).

## One-time setup in the Stripe dashboard (test mode)
1. Create product "Pro" with a recurring monthly price → copy `price_…` into `STRIPE_PRO_PRICE_ID`.
2. Copy the test secret key into `STRIPE_SECRET_KEY`.

## Checkout
`POST /billing/checkout` → `stripe.checkout.Session.create(mode="subscription", line_items=[{price: PRO, quantity: 1}], client_reference_id=tenant.id, metadata={"tenant_id": …}, subscription_data={"metadata": {"tenant_id": …}}, customer=tenant.stripe_customer_id or omitted, success_url=…/billing/success, cancel_url=…/billing/cancel)`. `subscription_data.metadata` copies the tenant id onto the subscription itself, so later `customer.subscription.*` events resolve the tenant directly. An optional `Idempotency-Key` header is passed to Stripe as `checkout-{tenant}-{key}`. Live keys are refused; Stripe errors → `502 stripe_error`; missing config → `503 stripe_not_configured`. Pay with `4242 4242 4242 4242`, any future expiry, any CVC.

## Webhook handler — `POST /webhooks/stripe`
1. Read the **raw** body (`await request.body()`); never parse JSON first — re-serialization breaks the signature.
2. `stripe.Webhook.construct_event(raw, sig_header, STRIPE_WEBHOOK_SECRET)`; `SignatureVerificationError`/`ValueError` → `400 invalid_signature`. Nothing is written.
3. Insert into `stripe_events` (`ON CONFLICT DO NOTHING`) → duplicate → `200`.
4. Return `200` fast; the worker applies it.

## Event → state mapping (`app/services/stripe_sync.py`)
| Event | Effect |
|---|---|
| `checkout.session.completed` | tenant from `client_reference_id` (or `metadata.tenant_id`); save `stripe_customer_id`; **fetch the subscription from Stripe** (the session carries no status); upsert it; plan = `pro` if status in {active, trialing, past_due}* else `free`. Non-subscription sessions → outcome `skipped_not_subscription` |
| `customer.subscription.updated` | tenant from `metadata.tenant_id`, else by customer id; update status/period; plan = `pro` if status in {active, trialing, past_due}* else `free` |
| `customer.subscription.deleted` | status `canceled`; plan → `free` |
| anything else | stored as `ignored` |
\* `past_due` keeps Pro limits but quota returns 402 until paid ([quotas.md](quotas.md)).

Ordering guard: skip an event if `event.created < subscriptions.last_event_created` (outcome `ignored_stale`); the checkout path skips the guard because it just fetched fresh state. A cancel for a different subscription than the one we track → `ignored_other_subscription`. `current_period_end` is read from the subscription, falling back to its first item (API version 2026-10-28 moved it there). Unknown tenant → `TenantNotFound` → retried with backoff, then `dead` + alert (never guess).

## Local loop (Stripe CLI in Docker, no tunnel, no host install)
The CLI runs as the optional compose service `stripe` (image `stripe/stripe-cli`, pinned). CLI 1.55+ needs an explicit `--events` list; compose passes the three handled types. It authenticates with `STRIPE_SECRET_KEY` from `.env` (passed as `STRIPE_API_KEY`), so no `stripe login` is needed, and forwards to `http://api:8000/webhooks/stripe` over the compose network.
```bash
docker compose run --rm stripe listen --print-secret                  # once: prints whsec_… → .env STRIPE_WEBHOOK_SECRET
docker compose up -d --force-recreate api                             # reload .env
docker compose --profile stripe up -d                                 # start forwarding
docker compose logs -f stripe                                         # see delivered events + status codes
docker compose run --rm stripe trigger checkout.session.completed
docker compose run --rm stripe events resend evt_…                    # replay → must be processed once
```
Forged-signature probe: `curl -X POST localhost:8000/webhooks/stripe -H "Stripe-Signature: t=1,v1=bad" -d '{}'` → 400.
If a delivery seems missing, check `docker compose logs stripe` for `Session expired, reconnecting...`: events sent while the CLI reconnects are not forwarded; `stripe events resend evt_…` redelivers them safely.
A host-installed Stripe CLI works too: `stripe listen --forward-to localhost:8000/webhooks/stripe`.
