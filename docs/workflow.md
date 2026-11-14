# Workflow

## Phases (do them in order; each ends with a gate — see [brief.md](brief.md))
1. **Design** — docs/design.md + docs in this folder. Gate: committed.
2. **Core billing** — compose + DB + migrations + seed; `/generate`, `/usage` counters; idempotency; quotas. Gate: P1 + P2 pass.
3. **Stripe** — checkout, webhook verify, `stripe_events`, worker, sync. Gate: P3 + P4 pass.
4. **Cost & finalize** — pricing.toml, cost rollup in `/usage`, README, diagram, capstone.yaml. Gate: P5 passes; every EVIDENCE box filled.
5. **Stretch** — only after a full green self-check.

A gate passes only on real output (tests or `scripts/probes.py`), never on reading code.

## Definition of done for any change
- Code in the right layer ([architecture.md](architecture.md)).
- Tests for the behaviour; `pytest` green.
- Docs updated if behaviour/contract changed.
- If it satisfies a requirement → `EVIDENCE.md` entry with real captured output.
- If AI helped meaningfully or was wrong → `BUILDLOG.md` entry.

## Required root files
| File | Must contain |
|---|---|
| `README.md` | What it does, architecture diagram, exact run + seed steps, plans table, endpoints, how to test, honest limitations |
| `capstone.yaml` | `run`, `seed`, `test`, `base_url`, `endpoints` — commands identical to the README |
| `EVIDENCE.md` | One real proof per checkbox |
| `BUILDLOG.md` | Honest AI-usage log per phase |
| `.env.example` | Every env var the app reads, placeholders only |

Final check: clone the repo into a fresh folder, follow only the README, and confirm `/health`, seed, and tests work.

## Commits
Small commits per phase step, message style `phase2: idempotent metering transaction`. No co-author trailers. Never stage `.env`.
