# Design doc (Phase 1 gate)

## Problem
A SaaS needs to answer, per tenant and per month: **how much was used, what does it cost, and is the tenant over its limit?** Every answer must stay correct under client retries, duplicate webhooks, concurrent requests, and requests landing exactly on a quota boundary.

## Data model (detail: [data-model.md](data-model.md))
- `plans` — `free`, `pro`; monthly limits per metric; Stripe price id for Pro.
- `tenants` — name, hashed API key, current `plan_id`, Stripe customer id.
- `subscriptions` — mirror of the tenant's Stripe subscription (status, period, last event time).
- `idempotency_keys` — `(tenant_id, key)` unique; request hash + stored response, so a retry returns the original answer.
- `usage_events` — append-only; one row per metric per accepted request; token breakdown on the `ai_tokens` row.
- `usage_counters` — `(tenant_id, period_start, metric)` running total, row-locked for atomic quota checks.
- `stripe_events` — Stripe event id as primary key → webhook dedupe; processing status for the worker.

## API surface (detail: [api.md](api.md))
| Method | Path | Purpose |
|---|---|---|
| POST | `/generate` | Dummy billable action: meter 1 API call + simulated tokens, quota-checked |
| GET | `/usage` | Current-period `{used, limit, remaining, cost}` per metric |
| POST | `/billing/checkout` | Create a Stripe Checkout Session for Pro |
| POST | `/webhooks/stripe` | Signed Stripe events |
| GET | `/health` | Liveness + DB check |

Tenant identity comes only from `X-API-Key`. Billable writes require `Idempotency-Key`.

## Idempotency strategy (detail: [idempotency.md](idempotency.md))
One DB transaction per billable request: insert `idempotency_keys` row (unique constraint serializes concurrent retries) → lock `usage_counters` → quota check → insert `usage_events` → store response. A repeated key with the same body returns the stored response byte-for-byte; with a different body → `409`.

## Quota rule (detail: [quotas.md](quotas.md))
Allow iff `used + requested <= limit` for **every** metric the request touches; otherwise reject the whole request and record nothing. `402` when paying fixes it (Free exhausted, or subscription unpaid); `429` + `Retry-After` when only waiting fixes it (Pro exhausted).

## Layers (detail: [architecture.md](architecture.md))
```
HTTP  app/api/        routers, Pydantic schemas, error mapping — no SQL, no business rules
Logic app/services/   metering, quota, pricing, billing, webhook processing — no FastAPI imports
Data  app/repositories/ + app/db/   SQLAlchemy models and queries — every query scoped by tenant_id
Jobs  app/jobs/       worker process: Stripe event processing (retries + alert)
```

## Explicit non-goal
**No invoicing, proration, or overage billing.** Usage beyond the limit is rejected, never charged. Plan price is whatever Stripe charges; our "cost" figure is usage cost computed from pinned constants.
