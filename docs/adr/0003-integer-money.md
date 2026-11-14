# ADR 0003 — Money as integer micro-USD, prices per million tokens

Status: accepted · 2026-11-14

## Context
Brief: store money as integers, never floats. Token prices are fractions of a cent per token.

## Decision
- Unit: micro-USD (`int`, `BIGINT`). Prices per 1,000,000 tokens in µ$.
- Multiply token counts by per-million rates, sum the numerators, divide by 1e6 **once** with half-up rounding.
- Cents appear only in `total_display`, rounded once, half-up.

## Consequences
- + Exact, reproducible totals; worked examples in pricing.md are asserted by tests.
- + No `Decimal` context pitfalls.
- − Display rounding means `$0.03375` shows as `$0.03`; µ$ value is always returned alongside.
