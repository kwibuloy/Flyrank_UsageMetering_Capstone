# ADR 0004 — Inclusive boundary, all-or-nothing, 402 vs 429 by "what fixes it"

Status: accepted · 2026-11-14

## Decision
- Allow iff `used + requested <= limit` for every metric touched; otherwise reject the whole request and record nothing.
- `402 payment_required` when paying/upgrading fixes it: unpaid subscription, or Free plan exhausted.
- `429 quota_exceeded` + `Retry-After` when only time fixes it: Pro plan exhausted.

## Why
- Inclusive `<=` means a plan of 1,000 really allows 1,000 calls.
- All-or-nothing avoids partial token grants that the client cannot reason about.
- MDN semantics: 429 is about rate/limits over time; 402 is about payment. Free users can always fix by upgrading, Pro users (no higher tier in core) can only wait.

## Consequences
- Probe P2 is deterministic: request #1000 → 200, #1001 → 402 (Free) / 429 (Pro).
- If overage billing (stretch) is added, Pro over-limit becomes "allowed + charged" and this ADR is superseded.
