# EVIDENCE

One pasted proof per requirement (test name + output, curl transcript, or log line). Claims without evidence score as not done. Each entry: **How** (command to reproduce), **Date**, **Output** (verbatim, secrets redacted), **Why this proves it** (one sentence).

## Metering
- [x] **M1** A billable action creates exactly one usage event, even under retries.
  - **How:** same request sent twice with one `Idempotency-Key` (demo tenant `acme`, after `scripts/seed.py`).
  - **Date:** 2026-11-14
  - **Output:**
    ```
    --- send 1
    HTTP/1.1 200 OK
    {"request_id":"bbfb1e87-8f43-4b97-850e-0a360cb81fe8","idempotency_key":"demo-retry-1","metered":{"api_calls":1,"ai_tokens":12500},"usage":{"api_calls":{"used":1,"limit":1000,"remaining":999},"ai_tokens":{"used":12500,"limit":100000,"remaining":87500}},...}
    --- send 2
    HTTP/1.1 200 OK
    idempotent-replayed: true
    {"request_id":"bbfb1e87-8f43-4b97-850e-0a360cb81fe8", ... identical body ...}
    --- events in DB
      metric   | quantity | idempotency_key
     ai_tokens |    12500 | demo-retry-1
     api_calls |        1 | demo-retry-1
    (2 rows)
    ```
  - **Why this proves it:** two sends, one row per metric, same `request_id`; the retry is served from the stored response.
- [x] **M2** Proof that double-counting cannot happen.
  - **How:** `docker compose run --rm api pytest -q tests/integration/test_idempotency.py tests/integration/test_concurrency.py`
  - **Date:** 2026-11-14
  - **Output (full suite):** `70 passed in 6.45s`
  - **Why this proves it:** covers 10 sequential retries → 1 event, 20 threads racing on one key against real Postgres → exactly 1 event, same key + different body → 409 with no new event, and `usage_counters == SUM(usage_events)` after every scenario. Backstop: unique constraint `(tenant_id, idempotency_key, metric)` in migration 0001.

## Quotas
- [x] **Q1** Usage checked against the tenant's plan; over-limit requests rejected.
  - **How:** `docker compose exec api python scripts/probes.py --only P2` and `pytest tests/integration/test_quota_http.py tests/integration/test_concurrency.py::test_concurrent_requests_never_oversell_the_quota`
  - **Date:** 2026-11-14
  - **Output:** `P2 PASS  #1000 -> 200, #1001 -> 402 ...; tokens 100,000 -> 200, +1 -> 402` · tests: `70 passed`
  - **Why this proves it:** the 1,000th call is allowed and the 1,001st is rejected; 20 concurrent requests with 5 calls left → exactly 5 accepted, 15 rejected.
- [x] **Q2** Correct 429 / 402 with an explanatory message.
  - **How:** probe P2 (Free) and `tests/integration/test_quota_http.py::test_pro_tenant_over_limit_is_429_with_retry_after` (Pro).
  - **Date:** 2026-11-14
  - **Output:** `402 'Monthly API call quota exceeded on the Free plan: 1,000 of 1,000 used, this request needs 1. Upgrade to Pro to continue.'` · Pro: `429 quota_exceeded`, `Retry-After` header == `details.retry_after_seconds`
  - **Why this proves it:** 402 when upgrading fixes it, 429 + Retry-After when only waiting fixes it (docs/adr/0004); messages name the metric, the numbers and the fix.

## Cost calculation
- [ ] **C1** Monthly usage rolls up into a cost figure per tenant.
- [ ] **C2** Token pricing handles cached input, reasoning, and output correctly.
- [ ] **C3** Pricing constants pinned in config; totals proven.

## Stripe integration
- [ ] **S1** Subscription Checkout works end-to-end in test mode.
- [ ] **S2** Webhooks verify signatures, ignore duplicates, update plan/status.

## Data model, tests & documentation
- [ ] **D1** Tenants, plans, subscriptions, usage events; tenant isolation.
- [ ] **D2** README + architecture diagram + setup; required files present.

## Acceptance probes
- [x] **P1** Same request twice → one event, mirrored response.
  - **How:** `docker compose exec api python scripts/probes.py --only P1` · **Date:** 2026-11-14
  - **Output:** `P1 PASS  2 sends -> 1 event (api_calls used=1), byte-identical replay of dff61711-48df-4407-83fd-47ff44012565`
- [x] **P2** Exact quota boundary → documented behaviour; next → 429/402.
  - **How:** `docker compose exec api python scripts/probes.py --only P2` · **Date:** 2026-11-14
  - **Output:** `P2 PASS  #1000 -> 200, #1001 -> 402 'Monthly API call quota exceeded on the Free plan: 1,000 of 1,000 used, this request needs 1. Upgrade to Pro to continue.'; tokens 100,000 -> 200, +1 -> 402`
- [ ] **P3** Test Checkout → Free → Pro; `/usage` shows new limits.
- [ ] **P4** Forged webhook → 400; replayed event processed once.
- [ ] **P5** Pricing rules → exact expected totals; `/usage` matches.
