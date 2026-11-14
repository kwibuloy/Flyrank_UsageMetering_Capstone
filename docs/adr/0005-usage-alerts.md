# ADR 0005 — Usage alerts: one table as dedupe record and queue, at-least-once delivery

Status: accepted · 2026-11-14

## Context
Stretch goal: notify at 80% and 100% of a monthly quota, exactly once per threshold, without new infrastructure.

## Decision
- One table, `usage_alerts`, is both the "already fired" record (unique key per tenant, period, metric, threshold, limit) and the job queue (status, attempts, backoff). No generic `jobs` table.
- Insert the alert inside the metering transaction, while the counter rows are locked.
- Deliver from the existing worker with the Stripe-event retry policy. Sink: an optional signed webhook (one operator URL, Slack-compatible), otherwise the log.

## Why
- Inserting with the usage makes the alert exist if and only if the usage committed: no lost alert after a crash, no alert for a rolled-back request.
- The counter lock already serializes a tenant's requests, so threshold crossing is decided on exact numbers; the unique key turns any remaining race into a no-op.
- A per-type table keeps constraints specific (`threshold_percent IN (80, 100)`) and the tenant-facing `GET /alerts` a single indexed query.

## Consequences
- + Exactly-once *queueing*; proven under 20 concurrent requests.
- − Delivery is at-least-once (POST then commit). Receivers de-duplicate on `alert_id`.
- − One webhook URL for all tenants (an operator channel). Per-tenant endpoints would need a settings API; out of scope.
- − A request rejected while usage is below 100% (e.g. 99,000 tokens + a 5,000-token request) never reaches the 100% alert; the 402/429 response itself tells the client.
