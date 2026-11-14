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

## Planned stretch job: usage alerts (80% / 100%)
After a request is metered, if a metric crosses 80% or 100% of its limit for the period, enqueue an alert job (same queue pattern, new `jobs` table). The worker "notifies" (structured log / outbox row) exactly once per `(tenant, period, metric, threshold)`, enforced by a unique constraint. Built only after every core requirement is green.
