# API contract

Base URL local: `http://localhost:8000`. JSON in/out. OpenAPI at `/docs` (FastAPI).

## Auth and headers
| Header | Where | Rule |
|---|---|---|
| `X-API-Key` | all tenant endpoints | missing/unknown → `401 unauthorized` |
| `Idempotency-Key` | `POST /generate` (required), `POST /billing/checkout` (optional) | 1–255 chars, printable ASCII; missing → `400 idempotency_key_required` |
| `Stripe-Signature` | `/webhooks/stripe` | invalid → `400 invalid_signature` |

## Error envelope (every non-2xx)
```json
{ "error": { "code": "quota_exceeded", "message": "human sentence explaining why and what to do", "details": { } } }
```
Validation errors are `422` with `code: "validation_error"` and per-field `details`. Unhandled exceptions → `500 internal_error` with no stack or secrets in the body (should never happen on bad input).

## Endpoints
### POST /generate
Body (simulated usage):
```json
{ "input_tokens": 10000, "cached_input_tokens": 4000, "output_tokens": 2000, "reasoning_tokens": 500 }
```
Validation: all ints ≥ 0; `cached_input_tokens <= input_tokens`; total tokens ≤ 1,000,000 per request.
| Status | When |
|---|---|
| 200 | Accepted and metered. Body: `{request_id, idempotency_key, metered: {api_calls: 1, ai_tokens: N}, usage: {metric: {used, limit, remaining}}, period: {start, end}, cost}` — `cost` is this request's price (shape below) |
| 200 (replay) | Same key + same body → stored body verbatim, header `Idempotent-Replayed: true` |
| 402 `payment_required` | Free plan quota exhausted (upgrade) or subscription `past_due`/`unpaid` |
| 429 `quota_exceeded` | Pro quota exhausted; `Retry-After` = seconds to period end |
| 409 `idempotency_key_reuse` | Same key, different body |
| 400 / 401 / 422 | header / auth / validation |
Rejections are also stored under the idempotency key, so a retried rejected request returns the same rejection.
Quota error `details`: `{metric, used, limit, requested, period_end, plan, upgrade_url?}`. Rules: [quotas.md](quotas.md).

### GET /usage
`used` is the SUM of this period's `usage_events` (the rollup); `usage_counters` are only for locking, and tests assert both agree. `cost` prices the same events for the whole month (token numerators summed, divided once).
```json
{
  "tenant": "acme", "plan": "free", "subscription_status": null,
  "period": { "start": "2026-11-01T00:00:00Z", "end": "2026-12-01T00:00:00Z" },
  "metrics": {
    "api_calls": { "used": 12, "limit": 1000, "remaining": 988 },
    "ai_tokens": { "used": 150000, "limit": 100000, "remaining": 0 }
  },
  "cost": { "...": "same shape as below" }
}
```

### The `cost` object (both `/generate` and `/usage`)
All amounts are integer micro-USD (`1 USD = 1,000,000`). Example for request E1 plus its API call:
```json
{
  "pricing_version": "2026-11-01", "currency": "USD",
  "lines": {
    "api_calls":           { "quantity": 1,    "rate_microusd": 500,      "rate_unit": "per_call",           "amount_microusd": 500 },
    "input_tokens":        { "quantity": 6000, "rate_microusd": 1250000,  "rate_unit": "per_million_tokens", "amount_microusd": 7500 },
    "cached_input_tokens": { "quantity": 4000, "rate_microusd": 312500,   "rate_unit": "per_million_tokens", "amount_microusd": 1250 },
    "output_tokens":       { "quantity": 2500, "rate_microusd": 10000000, "rate_unit": "per_million_tokens", "amount_microusd": 25000 }
  },
  "rounding_adjustment_microusd": 0,
  "total_microusd": 34250,
  "total_display": "$0.03"
}
```
`lines` explain the bill; `total_microusd` is authoritative. Token lines are each rounded half-up for display, while the token subtotal divides the summed numerators once, so `sum(lines) + rounding_adjustment_microusd == total_microusd` always holds (the adjustment is at most ±1 µ$ per line). `quantity` for `input_tokens` is *fresh* input (input minus cached); `output_tokens` includes reasoning. Rules: [pricing.md](pricing.md).

### POST /billing/checkout
→ `200 {"checkout_url": "https://checkout.stripe.com/…", "session_id": "cs_test_…"}`. Already Pro and active → `409 already_subscribed`. Stripe error → `502 stripe_error`; Stripe env vars missing → `503 stripe_not_configured`.

### GET /billing/success · GET /billing/cancel
Branded HTML pages Stripe redirects to. They change nothing: the plan changes only when the verified webhook is processed.

### POST /webhooks/stripe
Raw body, `Stripe-Signature` checked first. → `400 invalid_signature` (nothing stored); `200 {"status":"queued"}` (new handled event; the worker applies it); `200 {"status":"duplicate"}` (event id already seen); `200 {"status":"ignored"}` (unhandled type, stored as `ignored`).

### GET /health
`200 {"status":"ok","db":"ok"}`; DB down → `503`.
