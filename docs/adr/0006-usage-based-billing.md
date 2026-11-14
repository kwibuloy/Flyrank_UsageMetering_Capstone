# ADR 0006 — Usage on the Stripe invoice: exact pico-USD meter events from an outbox

Status: accepted · 2026-11-14

## Context
The cost in `GET /usage` was computed but never charged. Option chosen (over "true overage", which would have changed the graded 402/429 contract): keep quotas as they are and put each Pro request's cost on the Stripe invoice via a Billing Meter.

## Decision
- Report the **unrounded** cost per request in pico-USD (the integer numerator the pricing module already computes); price the meter at $10⁻¹² per unit. Stripe sums exact values and rounds once.
- Insert a `usage_reports` outbox row in the metering transaction; the worker sends it. The meter-event `identifier` is derived from (tenant, idempotency key).
- Bill only subscriptions that contain a metered price (`subscriptions.usage_billing`), detected from Stripe's subscription items. Find the usage price by lookup key.

## Why
- One rounding, same as `/usage`: the invoice and the API agree to the cent.
- Outbox + deterministic identifier: no report without committed usage, no lost report after a crash, and Stripe-side de-duplication makes retries safe.
- Item-based detection: existing flat-fee subscriptions and comped tenants are never billed by surprise.

## Consequences
- + Invoice == `/usage`, proven against a real test-mode invoice preview (probe B1).
- − One Stripe API call per Pro request. Fine at this scale; a high-volume system would batch per customer per minute (needs a batch identifier scheme).
- − Unit counts on the invoice are large numbers (34,250,000,000 units = $0.03425) — correct but unusual to read.
- − Stripe identifier uniqueness is guaranteed for 24 h; reports older than that which failed and are retried later could in theory double-bill. The worker gives up after 5 attempts (≤ ~10 minutes), well inside that window.
