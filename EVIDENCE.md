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
- [x] **C1** Monthly usage rolls up into a cost figure per tenant.
  - **How:** README demo request for tenant `acme`, then `curl localhost:8000/usage -H "X-API-Key: demo_free_acme_key"`
  - **Date:** 2026-11-14
  - **Output (excerpt):**
    ```
    "metrics": {"api_calls": {"used": 1, "limit": 1000, "remaining": 999},
                "ai_tokens": {"used": 12500, "limit": 100000, "remaining": 87500}},
    "cost": {"pricing_version": "2026-11-01", "currency": "USD",
      "lines": {"api_calls":           {"quantity": 1,    "rate_microusd": 500,      "amount_microusd": 500},
                "input_tokens":        {"quantity": 6000, "rate_microusd": 1250000,  "amount_microusd": 7500},
                "cached_input_tokens": {"quantity": 4000, "rate_microusd": 312500,   "amount_microusd": 1250},
                "output_tokens":       {"quantity": 2500, "rate_microusd": 10000000, "amount_microusd": 25000}},
      "rounding_adjustment_microusd": 0, "total_microusd": 34250, "total_display": "$0.03"}
    ```
  - **Why this proves it:** `/usage` prices the tenant's current-month events (the same rows `metrics` sums) into one integer total; `test_usage_rolls_up_the_month_and_matches_requests` checks a 3-request month equals 47,750 µ$, and `test_cost_is_isolated_per_tenant` checks another tenant stays at 0.
- [x] **C2** Token pricing handles cached input, reasoning, and output correctly.
  - **How:** `docker compose exec api pytest -q tests/unit/test_pricing.py tests/integration/test_cost_http.py tests/integration/test_tenant_isolation.py`
  - **Date:** 2026-11-14
  - **Output:** `39 passed in 2.19s`
  - **Why this proves it:** E1 (cached + reasoning + output) = exactly 33,750 µ$, and the two classic mistakes give different numbers (one rate for all tokens = 20,625; cached billed twice = 38,750); reasoning-only E2 = 10,000 µ$ at the output rate; all-cached E3 = 2,500 µ$; the month rounds once (3 × 1.25 µ$ → 4, not 3); lines + adjustment == total over 1,296 combinations.
- [x] **C3** Pricing constants pinned in config; totals proven.
  - **How:** constants in `config/pricing.toml` (version `2026-11-01`); `tests/unit/test_pricing.py::test_pinned_config_file_matches_documented_constants`; invalid files (float money, negative, cached > input, missing key) are rejected and stop the app at startup.
  - **Date:** 2026-11-14
  - **Output:** `config/pricing.toml` → `price_per_call_microusd = 500`, `input_per_million_microusd = 1_250_000`, `cached_input_per_million_microusd = 312_500`, `output_per_million_microusd = 10_000_000`; test suite `134 passed`.
  - **Why this proves it:** the shipped file is asserted equal to the documented price book, and every worked example in docs/pricing.md is an exact-integer test; changing a price fails the tests until docs and tests are updated together.

## Stripe integration
- [x] **S1** Subscription Checkout works end-to-end in test mode.
  - **How:** `docker compose --profile stripe up -d` then `docker compose exec api python -u scripts/probes.py --only P3`; open the printed Checkout URL and pay with `4242 4242 4242 4242`.
  - **Date:** 2026-11-14
  - **Output:**
    ```
    # Stripe CLI listener (docker compose logs stripe)
    2026-11-14 12:36:04   --> checkout.session.completed [evt_1UbgBcDlUAHamMxZ11fBjsOB]
    2026-11-14 12:36:04  <--  [200] POST http://api:8000/webhooks/stripe [evt_1UbgBcDlUAHamMxZ11fBjsOB]
    # worker
    12:36:05 INFO app.jobs.worker stripe_event_done id=evt_1UbgBcDlUAHamMxZ11fBjsOB type=checkout.session.completed outcome=plan=pro status=active
    # database
           name         | plan_id | status |    stripe_subscription_id    |   current_period_end
     probe-free-d95fac2b | pro     | active | sub_1UbgBaDlUAHamMxZ0oedHzHi | 2026-12-14 12:36:01+00
    # probe
    P3 PASS  Free -> pro (active) via webhook; limits {'api_calls': 50000, 'ai_tokens': 5000000}
    ```
  - **Why this proves it:** a real test-mode Checkout payment produced a real Stripe event, which reached our webhook, was applied by the worker, and moved the tenant to Pro with a real subscription id and period end. The success page itself changes nothing (`test_success_page_shows_real_limits_and_changes_nothing`).
- [x] **S2** Webhooks verify signatures, ignore duplicates, update plan/status.
  - **How:** `curl` with a forged signature; `stripe events resend` of a real event; `pytest tests/integration/test_webhooks.py tests/integration/test_stripe_sync.py`
  - **Date:** 2026-11-14
  - **Output:**
    ```
    $ curl -i -X POST localhost:8000/webhooks/stripe -H "Stripe-Signature: t=1,v1=bad" -d '{}'
    HTTP/1.1 400 Bad Request
    {"error":{"code":"invalid_signature","message":"Webhook signature verification failed.","details":{}}}

    # real event delivered twice (trigger, then `stripe events resend`), both 200
    2026-11-14 03:43:13  <--  [200] POST http://api:8000/webhooks/stripe [evt_1UbXrwDlUAHamMxZyxtObn0O]
    2026-11-14 03:43:33  <--  [200] POST http://api:8000/webhooks/stripe [evt_1UbXrwDlUAHamMxZyxtObn0O]
                  id              |            type            | status | attempts |         outcome
     evt_1UbXrwDlUAHamMxZyxtObn0O | checkout.session.completed | done   |        1 | skipped_not_subscription

    # full suite
    96 passed in 12.65s
    ```
  - **Why this proves it:** bad signatures are rejected before anything is stored; a redelivered event id is stored once and applied once (`attempts=1`); tests cover Free→Pro, cancel→Free, past_due→402, out-of-order events ignored, transient failure retried, and 5 failures → dead + `ALERT stripe_event_dead` log (the background job requirement).

## Data model, tests & documentation
- [x] **D1** Tenants, plans, subscriptions, usage events; tenant isolation.
  - **How:** `\dt` on the app database after `alembic upgrade head`; `tests/integration/test_tenant_isolation.py`, `test_cost_http.py::test_cost_is_isolated_per_tenant`
  - **Date:** 2026-11-14
  - **Output:** tables `idempotency_keys, plans, stripe_events, subscriptions, tenants, usage_counters, usage_events` (+ `alembic_version`); isolation tests pass (in the `39 passed` run above).
  - **Why this proves it:** migrations 0001–0002 create every required table with FKs, uniques and the rollup index; the tenant comes only from `X-API-Key`, every tenant query filters by `tenant_id`, and tests show tenant B can neither see nor change tenant A's usage or cost.
- [x] **D2** README + architecture diagram + setup; required files present.
  - **How:** `ls README.md capstone.yaml EVIDENCE.md BUILDLOG.md .env.example`; ran the README's run/seed/demo/test commands as written.
  - **Date:** 2026-11-14
  - **Output:** all five files present; `capstone.yaml` parses (`base_url, endpoints, run, seed, test, …`); seed prints the two demo tenants; demo `/generate` → 200 with `total_microusd: 34250`; `pytest -q` → `134 passed`.
  - **Why this proves it:** the README has the Mermaid architecture diagram, exact setup, plans, pricing, endpoints, tests and limitations, and its commands are identical to `capstone.yaml`.
  - **Fresh-clone check (docs/workflow.md final step):** `git clone` into an empty folder, own compose project (empty database), then only the README commands:
    ```
    $ cp .env.example .env
    $ docker compose up -d --build        -> db Healthy, api Started, worker Started
    $ docker compose exec api python scripts/seed.py   -> acme free / globex pro
    $ curl localhost:8000/health          -> {"status":"ok","db":"ok"}
    $ curl -X POST localhost:8000/generate ...   -> metered {'api_calls': 1, 'ai_tokens': 12500} total 34250 $0.03
    $ docker compose exec api pytest -q   -> 134 passed in 12.89s
    $ docker compose exec api python scripts/probes.py -> P1 PASS, P2 PASS, P3 SKIP (browser), P4 PASS, P5 PASS
    ```
    The clone contained no `.env`, `.claude/` or `CLAUDE.md`.

## Acceptance probes
- [x] **P1** Same request twice → one event, mirrored response.
  - **How:** `docker compose exec api python scripts/probes.py --only P1` · **Date:** 2026-11-14
  - **Output:** `P1 PASS  2 sends -> 1 event (api_calls used=1), byte-identical replay of dff61711-48df-4407-83fd-47ff44012565`
- [x] **P2** Exact quota boundary → documented behaviour; next → 429/402.
  - **How:** `docker compose exec api python scripts/probes.py --only P2` · **Date:** 2026-11-14
  - **Output:** `P2 PASS  #1000 -> 200, #1001 -> 402 'Monthly API call quota exceeded on the Free plan: 1,000 of 1,000 used, this request needs 1. Upgrade to Pro to continue.'; tokens 100,000 -> 200, +1 -> 402`
- [x] **P3** Test Checkout → Free → Pro; `/usage` shows new limits.
  - **How:** `docker compose exec api python -u scripts/probes.py --only P3` (pay in the browser) · **Date:** 2026-11-14
  - **Output:** `P3 PASS  Free -> pro (active) via webhook; limits {'api_calls': 50000, 'ai_tokens': 5000000}`
- [x] **P4** Forged webhook → 400; replayed event processed once.
  - **How:** `docker compose exec api python scripts/probes.py --only P4` · **Date:** 2026-11-14
  - **Output:** `P4 PASS  forged -> 400, 0 rows stored; replay -> queued then duplicate; worker applied once (attempts=1, outcome 'plan=pro status=active')`
- [x] **P5** Pricing rules → exact expected totals; `/usage` matches.
  - **How:** `docker compose exec api python scripts/probes.py` · **Date:** 2026-11-14
  - **Output:** `P5 PASS  E1 34,250 + E2 10,500 + E3 3,000 µ$ per request; /usage total 47,750 µ$ ($0.05) lines {'api_calls': 1500, 'input_tokens': 7500, 'cached_input_tokens': 3750, 'output_tokens': 35000}`
  - Same run: `P1 PASS`, `P2 PASS`, `P4 PASS` (P3 is interactive; its PASS is recorded above).
