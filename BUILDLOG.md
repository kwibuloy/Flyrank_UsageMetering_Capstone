# BUILDLOG 

Where AI helped, where it was wrong, what I changed. 

## 2026-11-14 · Phase 1 · Project scaffolding and design docs
- **AI helped:** Claude Code drafted the docs in `docs/` (design, data model, API, idempotency, quotas, pricing, Stripe, jobs, ADRs) from the capstone brief.

## 2026-11-14 · Phase 2 · Idempotent metering and quotas
- **AI helped:** Claude Code wrote the Docker setup, migration 0001, metering transaction (`app/services/metering.py`), pure quota decision (`app/services/quota.py`), API layer, seed and probe scripts, and 70 tests.
- **AI was wrong / I changed:** first version stored replay responses in a `JSONB` column. The test "10 retries → identical responses" failed: Postgres JSONB reorders object keys, so the replay was equal JSON but not the same bytes. Switched the column to `JSON`, which keeps the text as written, and made the P1 test and probe compare raw bytes.

