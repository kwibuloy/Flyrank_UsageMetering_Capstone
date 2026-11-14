# Background jobs

Satisfies shared requirement 3 (≥1 background job, retries, failure alert) without extra infrastructure: a Postgres-backed queue and one worker process.

## The job: Stripe event processing
- Producer: webhook handler inserts verified events into `stripe_events` with `status='pending'`.
- Consumer: `python -m app.jobs.worker` (its own `worker` service in docker compose).

## Claim loop
```sql
UPDATE stripe_events SET status='processing', attempts = attempts + 1
WHERE id = (
  SELECT id FROM stripe_events
  WHERE status='pending' AND next_attempt_at <= now()
  ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING *;
```
Poll interval 1s (keeps probe P3 fast). `SKIP LOCKED` makes multiple workers safe.

## Retry and alert policy
- Success → `done`.
- Failure → `pending`, `next_attempt_at = now() + 2^attempts seconds` (cap 5 min), `last_error` = exception class + message (no payload, no secrets).
- After 5 attempts → `dead` and an **alert**: `logger.error("ALERT stripe_event_dead", extra={event_id, type, attempts})`. The alert sink is the structured log (documented limitation; swap for email/Slack later).
- Stuck `processing` rows older than 5 min are reset to `pending` on worker start.

## Testing
Integration tests call `worker.run_once()` directly — deterministic, no sleeps. Cover: success, transient failure then success, 5 failures → dead + alert log captured with `caplog`.

## Planned stretch job: usage alerts (80% / 100%)
After a request is metered, if a metric crosses 80% or 100% of its limit for the period, enqueue an alert job (same queue pattern, new `jobs` table). The worker "notifies" (structured log / outbox row) exactly once per `(tenant, period, metric, threshold)`, enforced by a unique constraint. Built only after every core requirement is green.
