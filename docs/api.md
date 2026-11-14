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
| 200 | Accepted and metered. Body: `{request_id, idempotency_key, metered: {api_calls: 1, ai_tokens: N}, usage: {metric: {used, limit, remaining}}, period: {start, end}, cost, alerts_triggered}` — `cost` is this request's price (shape below); `alerts_triggered` lists any 80%/100% thresholds it crossed |
| 200 (replay) | Same key + same body → stored body verbatim, header `Idempotent-Replayed: true` |
| 402 `payment_required` | Free plan quota exhausted (upgrade) or subscription `past_due`/`unpaid` |
| 429 `quota_exceeded` | Pro quota exhausted; `Retry-After` = seconds to period end |
| 409 `idempotency_key_reuse` | Same key, different body |
| 400 / 401 / 422 | header / auth / validation |
Rejections are also stored under the idempotency key, so a retried rejected request returns the same rejection.
Quota error `details`: `{metric, used, limit, requested, period_end, plan, upgrade_url?}`. Rules: [quotas.md](quotas.md).

### GET /usage
`used` is the SUM of this period's `usage_events` (the rollup); `usage_counters` are only for locking, and tests assert both agree. `cost` prices the same events for the whole month (token numerators summed, divided once). `usage_billing` says whether this tenant's usage is billed through Stripe and how much has been reported: `{enabled, reports_sent, reports_pending, reports_dead, reported_microusd}` ([stripe.md](stripe.md#usage-based-billing-metered)).
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

### GET /alerts
This tenant's usage alerts for the current month (stretch goal; [background-jobs.md](background-jobs.md#usage-alerts-stretch-goal-built)).
```json
{
  "tenant": "acme",
  "period": { "start": "2026-11-01T00:00:00Z", "end": "2026-12-01T00:00:00Z" },
  "thresholds_percent": [80, 100],
  "alerts": [
    { "metric": "api_calls", "threshold_percent": 80, "plan": "free", "used": 800, "limit": 1000,
      "status": "sent", "triggered_at": "2026-11-14T13:22:01Z", "sent_at": "2026-11-14T13:22:02Z" }
  ]
}
```
`status`: `pending` (queued), `sent`, `dead` (delivery failed 5 times). `POST /generate` also returns `alerts_triggered: [{metric, threshold_percent}]` for the thresholds that request crossed (usually empty).

### POST /billing/checkout
→ `200 {"checkout_url": "https://checkout.stripe.com/…", "session_id": "cs_test_…"}`. Already Pro and active → `409 already_subscribed`. Stripe error → `502 stripe_error`; Stripe env vars missing → `503 stripe_not_configured`.

### GET /billing/success · GET /billing/cancel
HTML pages Stripe redirects to, styled like Stripe's payment pages (one centered card, entrance and checkmark animations, `prefers-reduced-motion` respected). The success page repeats the order read back from the Checkout Session (merchant name, line items, amounts; `session_id` must match `cs_(test|live)_…` or it is ignored) and shows whether the webhook has activated Pro: a small inline script polls `GET /billing/status` every 1.5 s (up to ~1 minute) and crossfades "Activating" to "Active" in place, with a `<noscript>` refresh fallback.

### GET /billing/status?session_id=cs_…
`200 {"status": "active" | "pending" | "unknown"}`: whether the tenant behind that Checkout Session is on Pro yet. Display only; the session-to-tenant lookup is cached so polling calls Stripe once. If Stripe is unreachable the page still renders from the plans table. They change nothing: the plan changes only when the verified webhook is processed.

### POST /webhooks/stripe
Raw body, `Stripe-Signature` checked first. → `400 invalid_signature` (nothing stored); `200 {"status":"queued"}` (new handled event; the worker applies it); `200 {"status":"duplicate"}` (event id already seen); `200 {"status":"ignored"}` (unhandled type, stored as `ignored`).

### GET /health
`200 {"status":"ok","db":"ok"}`; DB down → `503`.
