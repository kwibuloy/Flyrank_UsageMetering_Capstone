# The capstone contract (condensed from the FlyRank brief)

Source: *FlyRank Internship · Backend Track · Capstone — Usage Metering & Billing Engine*. This file is the checklist we build against; the original PDF is not in the repo.

## Scope (deliberately small)
- 2 plans: **Free** (1,000 API calls / month, 100,000 AI tokens / month) and **Pro** (our numbers, see [quotas.md](quotas.md)).
- 2 usage types: `api_calls`, `ai_tokens`.
- 1 dummy billable endpoint: `POST /generate` (simulated token counts; no model, no AI key).
- Stripe **test mode only**. Stripe CLI for local webhooks. $0, never a credit card.
- Not in core: invoicing, proration, overage billing (stretch goals only).

## Requirements checklist — Done = every box ticked + one proof each in `EVIDENCE.md`
| ID | Requirement | Primary doc |
|---|---|---|
| M1 | A billable action creates exactly one usage event, even under retries (idempotency key) | [idempotency.md](idempotency.md) |
| M2 | Proof double-counting cannot happen (test output or same-request-twice transcript) | [idempotency.md](idempotency.md) |
| Q1 | Usage checked against the tenant's plan; over-limit requests rejected | [quotas.md](quotas.md) |
| Q2 | Correct status codes (429 / 402) + a message explaining why | [quotas.md](quotas.md), [api.md](api.md) |
| C1 | Monthly usage rolls up into a cost figure per tenant | [pricing.md](pricing.md) |
| C2 | Token pricing handles cached input, reasoning, and output correctly | [pricing.md](pricing.md) |
| C3 | Pricing constants pinned in config; proof of correct totals | [pricing.md](pricing.md) |
| S1 | Subscription Checkout works end-to-end in test mode | [stripe.md](stripe.md) |
| S2 | Webhooks verify signatures, ignore duplicates, update tenant plan/status | [stripe.md](stripe.md) |
| D1 | DB has tenants, plans, subscriptions, usage events; tenant data isolated | [data-model.md](data-model.md) |
| D2 | README + architecture diagram + setup; required files present | [workflow.md](workflow.md) |

## Acceptance probes (run against the live system)
| Probe | Expectation |
|---|---|
| P1 | Same billable request twice, one idempotency key → exactly one usage event; second response mirrors the first |
| P2 | Drive a tenant to its exact quota → boundary request behaves per documented rule; next one → 429/402 with clear message |
| P3 | Complete a test Checkout → webhook flips tenant Free → Pro; `GET /usage` shows new limits |
| P4 | Forged webhook → 400, nothing changes. Real event replayed twice → processed once |
| P5 | Pinned pricing rules → exact expected totals for cached-input and reasoning tokens; `GET /usage` matches |

## Shared requirements (every capstone)
1. Layered architecture (data / logic / HTTP) → [architecture.md](architecture.md)
2. Validation at the boundary: bad input → clean 4xx, never 500 → [api.md](api.md)
3. ≥1 background job with retries + failure alert → [background-jobs.md](background-jobs.md)
4. Real persistence: migrations, right indexes, isolated tenants → [data-model.md](data-model.md)
5. Idempotency where it matters → [idempotency.md](idempotency.md)
6. Secrets clean: env only, never logged → [conventions.md](conventions.md)
7. Cost tracked if AI used (we simulate; cost is still attributed per call) → [pricing.md](pricing.md)

## Required files at submission
`README.md` · `capstone.yaml` (`run:`, `seed:`, `test:`, `base_url:`, endpoints) · `EVIDENCE.md` · `BUILDLOG.md` · `.env.example`. Details in [workflow.md](workflow.md).

## Phases and gates
| Phase | Gate |
|---|---|
| 1 Design | `docs/design.md` committed |
| 2 Core billing | Same request twice → one event; boundary returns 429/402 |
| 3 Stripe | Test Checkout flips a tenant Free → Pro via webhook |
| 4 Cost & finalize | `/usage` numbers match pinned pricing constants |
| Stretch | Only after every box above is green; one polished stretch beats three half ones |
