# BUILDLOG 

Where AI helped, where it was wrong, what I changed. 

## 2026-11-14 · Phase 1 · Project scaffolding and design docs
- **AI helped:** Claude Code drafted the docs in `docs/` (design, data model, API, idempotency, quotas, pricing, Stripe, jobs, ADRs) from the capstone brief.

## 2026-11-14 · Phase 2 · Idempotent metering and quotas
- **AI helped:** Claude Code wrote the Docker setup, migration 0001, metering transaction (`app/services/metering.py`), pure quota decision (`app/services/quota.py`), API layer, seed and probe scripts, and 70 tests.
- **AI was wrong / I changed:** first version stored replay responses in a `JSONB` column. The test "10 retries → identical responses" failed: Postgres JSONB reorders object keys, so the replay was equal JSON but not the same bytes. Switched the column to `JSON`, which keeps the text as written, and made the P1 test and probe compare raw bytes.

## 2026-11-14 · Phase 3 · Stripe Checkout, webhooks and the background worker
- **AI helped:** Claude Code wrote migration 0002 (`subscriptions`, `stripe_events`), the checkout and webhook routes, the event-to-plan sync (`app/services/stripe_sync.py`), the retrying worker (`app/jobs/worker.py`), the Stripe CLI compose service, probes P3/P4, and 26 more tests. It also restyled the Checkout success/cancel pages after I asked for something that looks like a real product.
- **AI was wrong / I changed:**
  - It assumed `StripeObject` behaves like a dict; in stripe-python 16 it does not, so `fetch_subscription` now returns `.to_dict()`.
  - The Stripe CLI 1.55 `listen` command refused to start without an `--events` list; added the three handled types to `docker-compose.yml`.
  - The first live P3 run timed out after 15 minutes because no payment event arrived in the window; the rerun passed. While checking, the listener log showed `Session expired, reconnecting...`, and events sent during that gap are not forwarded, so `docs/stripe.md` now says to check for it and use `stripe events resend`.
  - First design had a `processing` status plus a "reset stuck rows" sweep. Holding the row lock (`FOR UPDATE SKIP LOCKED`) while applying the event and marking it `done` in the same transaction removes both; the docs were updated to match.

## 2026-11-14 · Phase 4 · Cost math, README and submission files
- **AI helped:** Claude Code wrote `config/pricing.toml`, the pure integer pricing module (`app/services/pricing.py`), the monthly cost rollup in `/usage` and per-request cost in `/generate`, 38 more tests, probe P5, the README with its Mermaid diagram, and `capstone.yaml`.
- **Design decision:** showing per-category lines means rounding each line, but the rule is "divide once per period" (three requests of 1 fresh input token are 3.75 µ$, which rounds to 4, not 1+1+1). So the total comes from the summed numerators, and the response reports a `rounding_adjustment_microusd` so lines + adjustment always equal the total, as on an invoice. A test pins this case.
- **AI was wrong / I changed:** editing `scripts/probes.py` from Windows wrote the `µ` symbol as Windows-1252, and the probe crashed in the container with a non-UTF-8 error. Re-encoded the file as UTF-8 and checked every changed file.

## 2026-11-14 · Phase 5 (stretch) · Usage alerts at 80% / 100%
- **AI helped:** Claude Code wrote migration 0003 (`usage_alerts`), the pure threshold check and the enqueue step inside the metering transaction, the signed webhook / log delivery, the second worker queue, `GET /alerts`, 25 tests, probe A1 and ADR 0005.
- **Design change from the plan:** the Phase 1 docs planned a generic `jobs` table. Building it showed one `usage_alerts` table can be both the "already fired" record and the queue, with tighter constraints, so the generic table was dropped (ADR 0005).
- **AI was wrong / I changed:** one unit-test case expected 3 of 4 calls (75%) to trigger the 80% alert, then special-cased the test instead of fixing the expectation. Replaced with the correct expectation (75% fires nothing; 4 of 4 fires both 80% and 100%).
