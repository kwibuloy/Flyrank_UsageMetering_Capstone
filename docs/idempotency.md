# Idempotency (the heart of the capstone)

Decision record: [adr/0002-idempotency-strategy.md](adr/0002-idempotency-strategy.md).

## Guarantee
For a given `(tenant, Idempotency-Key)`: at most one set of usage events exists, and every response after the first is the stored first response. Holds under sequential retries **and** concurrent duplicates.

## Algorithm — `MeterService.record` (single transaction, READ COMMITTED)
1. `request_hash = sha256(method + path + canonical_json(body))` (sorted keys, no whitespace).
2. `INSERT INTO idempotency_keys (tenant_id, key, request_hash) VALUES (…) ON CONFLICT DO NOTHING RETURNING key`.
   - Concurrent duplicate blocks here on the unique index until the first transaction commits, then sees the conflict. **This is why the whole flow is one transaction.**
3. If no row returned (key exists): load it.
   - `request_hash` differs → `409 idempotency_key_reuse`.
   - same → return `status_code` + `response_body` verbatim with `Idempotent-Replayed: true`. No new events.
4. Lock counters: `SELECT … FROM usage_counters WHERE tenant_id=… AND period_start=… AND metric IN (…) ORDER BY metric FOR UPDATE` (upsert missing rows first; fixed lock order prevents deadlocks).
5. `quota.decide(...)` (pure). Rejected → write the error response onto the idempotency row, commit, return it.
6. Insert `usage_events` rows; `UPDATE usage_counters SET used = used + qty`.
7. Write the 200 response onto the idempotency row. Commit.

Defence in depth: `usage_events` unique `(tenant_id, idempotency_key, metric)`.

## Webhook dedupe
`stripe_events.id` (Stripe event id) is the primary key; `INSERT … ON CONFLICT DO NOTHING`. A conflict means "already received" → `200 duplicate`, no reprocessing. Processing is in the worker ([background-jobs.md](background-jobs.md)) and is itself idempotent: applying the same event twice yields the same tenant state.

## Tests that must exist (and feed EVIDENCE.md M1/M2, P1, P4)
- same key + same body twice → 1 event, identical bodies, second has replay header
- same key + different body → 409, still 1 event
- 20 concurrent requests, same key → exactly 1 event, counter == SUM(events)
- rejected request retried → same 402/429 body, 0 events
- same Stripe event id posted twice → processed once
