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
