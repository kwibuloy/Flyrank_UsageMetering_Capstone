# Architecture

Stack and rationale: [adr/0001-stack.md](adr/0001-stack.md).

## Folder layout
```
app/
  main.py                 app factory, routers, exception handlers
  config.py               pydantic-settings (env only)
  api/
    deps.py               DB session, tenant-from-API-key, Idempotency-Key header
    errors.py             AppError → JSON error envelope; validation → 422
    schemas.py            request/response models
    pages.py              HTML for the Checkout success/cancel redirect pages
    routes/               generate.py, usage.py, billing.py, webhooks.py, health.py
  services/
    metering.py           record_usage(...) — the exactly-once path
    quota.py              pure decision: allow / 402 / 429
    pricing.py            pure integer cost math
    billing.py            Stripe Checkout session creation
    webhooks.py           verify signature, store/dedupe stripe_events
    usage_report.py       GET /usage: rollup + cost
    alerts.py             80%/100% threshold crossing (pure) + enqueue in the metering txn
    alert_delivery.py     signed webhook or log sink for usage alerts
    stripe_sync.py        apply a verified Stripe event to tenant/subscription
    periods.py            billing-period boundaries (UTC calendar month)
  repositories/           alerts.py, health.py, tenants.py, plans.py, usage.py, idempotency.py, subscriptions.py, stripe_events.py
  db/
    base.py, models.py, session.py
  jobs/
    worker.py             polling loop over two queues (stripe_events, usage_alerts); FOR UPDATE SKIP LOCKED
config/
  pricing.toml            pinned pricing constants (versioned)
migrations/               Alembic
scripts/
  seed.py                 demo tenants + API keys
  probes.py               runs acceptance probes P1–P5 against base_url
tests/
  unit/                   pricing, quota decisions, periods (no DB)
  integration/            real Postgres: idempotency, concurrency, webhooks, isolation
docker-compose.yml        db, api, worker, stripe (optional profile)
```

## Layer rules
| Layer | May import | Must not |
|---|---|---|
| `api/` | services, schemas, deps | run SQL, compute money, decide quotas |
| `services/` | repositories, other services, config | import FastAPI / Request / HTTPException |
| `repositories/` | db models, SQLAlchemy | contain business rules; omit `tenant_id` in tenant-owned queries |
| `jobs/` | services, repositories | duplicate service logic |

`services/quota.py` and `services/pricing.py` are **pure functions** (no I/O) so they are trivially unit-testable.

Errors: services raise typed `AppError` subclasses (`QuotaExceeded`, `PaymentRequired`, `IdempotencyConflict`, …); `api/errors.py` maps them to status codes. No `HTTPException` below the HTTP layer.

## Flows
```
POST /generate
  deps: tenant ← X-API-Key ; key ← Idempotency-Key ; body validated (422)
  metering.record_usage(tenant, request, key)        [one transaction]
    ├─ idempotency_keys INSERT … ON CONFLICT → existing? same hash → return stored response
    │                                                  different hash → 409
    ├─ usage_counters SELECT … FOR UPDATE (both metrics)
    ├─ quota.decide(...) → reject? store rejection response, return 402/429
    ├─ usage_events INSERT (api_calls, ai_tokens) ; counters += qty
    └─ store 200 response on idempotency row ; COMMIT

GET /usage  → SUM(usage_events) per metric + SUM of token columns → pricing.quote(...) → {used, limit, remaining, cost}

POST /billing/checkout → Stripe Checkout Session (client_reference_id = tenant id) → {url}

POST /webhooks/stripe
  ├─ verify signature on RAW body → 400 if forged
  ├─ stripe_events INSERT … ON CONFLICT DO NOTHING → duplicate? 200 {"status":"duplicate"}
  └─ 200 {"status":"queued"}
worker: claim pending stripe_events → stripe_sync.apply(event) → mark done | retry w/ backoff | dead + alert
```
The architecture diagram (Mermaid) is in the [README](../README.md#architecture).
