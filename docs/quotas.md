# Quotas

Decision record: [adr/0004-quota-boundary-and-status-codes.md](adr/0004-quota-boundary-and-status-codes.md).

## Plans (README must repeat this table)
| Plan | api_calls / month | ai_tokens / month |
|---|---|---|
| Free | 1,000 | 100,000 |
| Pro | 50,000 | 5,000,000 |

Change Pro numbers here, in the seed, and in the README together.

## Billing period
UTC calendar month: `[first day 00:00Z, first day of next month 00:00Z)`. Logic in `app/services/periods.py`. No rollover of unused quota.

## What a request consumes
`POST /generate` consumes `api_calls: 1` and `ai_tokens: input_tokens + output_tokens + reasoning_tokens` (cached input is a subset of input, so it is **not** added again — see [pricing.md](pricing.md)).

## The boundary rule (exact)
Allow iff, for **every** metric touched, `used + requested <= limit`.
- 999 used, request 1 → allowed (becomes 1,000).
- 1,000 used, request 1 → rejected.
- 99,000 tokens used, request 2,500 → rejected **whole**; no partial grant, nothing recorded (not even the API call).
- Rejections never create usage events.

## 402 vs 429 — "what fixes it?"
| Situation | Status | code | Fix |
|---|---|---|---|
| Subscription `past_due` / `unpaid` / `incomplete` | 402 | `payment_required` | pay |
| Free plan, limit would be exceeded | 402 | `payment_required` | upgrade to Pro (`upgrade_url` in details) |
| Pro plan, limit would be exceeded | 429 | `quota_exceeded` | wait — `Retry-After` = seconds until period end |
Check order: payment status first, then limits (api_calls before ai_tokens; the first failing metric is reported).

Message examples (must name the metric, the numbers, and the fix):
- "Monthly AI token quota exceeded on the Free plan: 99,000 of 100,000 used, this request needs 2,500. Upgrade to Pro to continue."
- "Monthly API call quota reached on the Pro plan (50,000 of 50,000). Quota resets 2026-12-01T00:00:00Z."

## Implementation
`app/services/quota.py::decide(plan, status, used_by_metric, requested_by_metric, period_end) -> Allow | Reject(status, code, message, details)` — pure; exhaustively unit-tested at limit-1, limit, limit+1. Locking lives in metering ([idempotency.md](idempotency.md)).
