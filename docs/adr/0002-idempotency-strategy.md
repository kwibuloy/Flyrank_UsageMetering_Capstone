# ADR 0002 — Idempotency via a keys table inside the metering transaction

Status: accepted · 2026-11-14

## Context
Same request + same key must produce one usage event and a mirrored response (P1), including concurrent duplicates.

## Options
1. Unique constraint on `usage_events` only — prevents duplicate rows but cannot replay the original response or rejections.
2. Redis `SETNX` lock + cache — extra infra; lock/DB not atomic together.
3. **`idempotency_keys` table written in the same transaction as events and counters** — unique PK serializes duplicates; stored response replays exactly.

## Decision
Option 3, plus option 1's unique constraint as a backstop. Key scoped per tenant. Request hash detects key reuse with a different body → 409. Rejections are stored too, so retries are stable.

## Consequences
- + Atomic: either key+events+counters+response all commit or none do.
- + Concurrent duplicates block on the index, then replay — no race window.
- − Keys table grows; 30-day retention is documented, pruning is a stretch job.
