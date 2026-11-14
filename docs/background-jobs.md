# Background jobs

Satisfies shared requirement 3 (≥1 background job, retries, failure alert) without extra infrastructure: a Postgres-backed queue and one worker process.

## The job: Stripe event processing
- Producer: webhook handler inserts verified events into `stripe_events` with `status='pending'`.
- Consumer: `python -m app.jobs.worker` (its own `worker` service in docker compose).

## Claim loop (`app/jobs/worker.py::run_once`)
One transaction per event:
```sql
SELECT * FROM stripe_events
WHERE status='pending' AND next_attempt_at <= now()
ORDER BY received_at LIMIT 1 FOR UPDATE SKIP LOCKED;
-- apply the event (stripe_sync.apply), then in the SAME transaction:
UPDATE stripe_events SET status='done', outcome=..., attempts=attempts+1, processed_at=now() WHERE id=...;
COMMIT;
```
The row lock is held while the event is applied, so there is no `processing` status and nothing can get stuck in one: if the worker dies, the transaction rolls back and the row is `pending` again. The plan change and `done` commit together, so an event is applied exactly once. Poll interval `WORKER_POLL_SECONDS` (default 1s). `SKIP LOCKED` makes multiple workers safe.

## Retry and alert policy
- Success → `done`.
- Failure → the apply transaction rolls back (no partial state), then a second transaction sets `pending`, `next_attempt_at = now() + 2^attempts seconds` (cap 5 min), `last_error` = exception class + message (no payload, no secrets).
- After 5 attempts → `dead` and an **alert**: `logger.error("ALERT stripe_event_dead id=... type=... attempts=... error=...")`. The alert sink is the log (documented limitation; swap for email/Slack later).
- What fails and retries: Stripe API errors while fetching the subscription, and `TenantNotFound` (the event's tenant cannot be resolved yet; a later event may link the customer).

## Testing
Integration tests call `worker.run_once()` directly — deterministic, no sleeps. Cover: success, transient failure then success, 5 failures → dead + alert log captured with `caplog`.

## Queues at a glance
| Queue (table) | Producer | Sends | Dead-letter alert |
|---|---|---|---|
| `stripe_events` | webhook handler | applies plan/subscription changes | `ALERT stripe_event_dead` |
| `usage_alerts` | metering transaction | 80% / 100% alerts (webhook or log) | `ALERT usage_alert_dead` |
| `usage_reports` | metering transaction | Stripe meter events (usage on the invoice, [stripe.md](stripe.md#usage-based-billing-metered)) | `ALERT usage_report_dead` |
All three use the same claim (`FOR UPDATE SKIP LOCKED`), backoff (`2^attempts` s, cap 5 min) and 5-attempt limit. The loop drains all three and sleeps only when all are empty.

## Usage alerts (stretch goal, built)
Tell a tenant when a metric reaches **80%** and **100%** of its monthly limit — once per threshold, never duplicated, never lost. Decision record: [adr/0005-usage-alerts.md](adr/0005-usage-alerts.md).

**Trigger (in the metering transaction).** After an accepted request bumps the counters, `alerts.enqueue_crossed` checks each metric with integer math: threshold `t` is crossed when `used_before*100 < limit*t <= used_after*100`. One request can cross both (e.g. 0 → 100,000 tokens). Each crossing inserts a `usage_alerts` row with `ON CONFLICT DO NOTHING`.
- **Exactly once:** the counter rows are locked `FOR UPDATE`, so `used_before` is exact and two concurrent requests can never both cross the same threshold; the unique key `(tenant_id, period_start, metric, threshold_percent, quota_limit)` is the backstop.
- **Atomic with usage:** the alert commits with the usage that caused it, or not at all. Replays and rejected requests change no usage, so they queue nothing.
- **Re-armed by a new limit:** `quota_limit` is in the key, so after an upgrade to Pro the Pro thresholds fire for the rest of the month.
- The `/generate` response lists what this request triggered in `alerts_triggered`.

**Delivery (in the worker).** `worker.run_alerts_once` uses the same claim loop as Stripe events (`FOR UPDATE SKIP LOCKED`, oldest first), the same backoff (`2^attempts` s, cap 5 min) and the same dead-letter rule (5 attempts → `dead` + `ALERT usage_alert_dead` log). The worker loop drains both queues and sleeps only when both are empty.
- **Sink:** `ALERT_WEBHOOK_URL` set → HTTP POST of the JSON payload (with a Slack-compatible `text` field), signed when `ALERT_WEBHOOK_SECRET` is set: `X-Alert-Signature: t=<unix>,v1=<hex HMAC-SHA256 of "<t>.<raw body>">`. Non-2xx or a network error → retry. No URL → a `USAGE_ALERT …` log line (works with zero setup).
- **At-least-once:** the POST happens while the row is locked and `sent` commits right after, so a crash between them can re-send. The payload carries `alert_id` for receivers to de-duplicate.
- Tenants read their alerts with `GET /alerts` ([api.md](api.md)).

**Testing:** `tests/unit/test_alerts.py` (threshold math, wording) and `tests/integration/test_alerts.py` (exactly one alert under 20 concurrent requests, replay/rejection queue nothing, re-arm on a new limit, delivery once, retry with backoff, dead + alert, webhook signing, tenant isolation). Live: probe `A1`.
