# Docs index

The source of truth for *how this system works and why*.

| Doc | Read it when |
|---|---|
| [brief.md](brief.md) | You need the grading contract: requirements checklist, acceptance probes, required files |
| [design.md](design.md) | Phase 1 gate — the one-page design doc (problem, data model, API, layers, non-goal) |
| [architecture.md](architecture.md) | Deciding where code goes; layer rules; folder layout; request flows |
| [data-model.md](data-model.md) | Touching tables, migrations, indexes, tenant isolation |
| [api.md](api.md) | Adding/changing an endpoint, status code, header, or error shape |
| [idempotency.md](idempotency.md) | Anything on the metering write path or webhook dedupe |
| [quotas.md](quotas.md) | Limits, the boundary rule, 429 vs 402, billing periods |
| [pricing.md](pricing.md) | Cost math, token rules, pricing constants, rounding |
| [stripe.md](stripe.md) | Checkout, webhooks, signature verification, Stripe CLI |
| [background-jobs.md](background-jobs.md) | The worker, job queues, retries, failure alerts, 80%/100% usage alerts |
| [conventions.md](conventions.md) | Code style, money types, validation, logging, testing |
| [workflow.md](workflow.md) | Phases and gates, commits, EVIDENCE.md / BUILDLOG.md discipline |
| [adr/](adr/) | Why a decision was made (one file per decision) |

Rule for editing docs: when code and a doc disagree, fix one of them in the same change. A new decision with trade-offs gets an ADR.
