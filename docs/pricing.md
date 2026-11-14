# Pricing and cost math

Decision record: [adr/0003-integer-money.md](adr/0003-integer-money.md).

## Units
- All money is **integer micro-USD** (`1 USD = 1_000_000 µ$`). Python `int`, Postgres `BIGINT`. Never `float`, never `Decimal` built from a float.
- Prices are stored **per 1,000,000 tokens** in µ$ so fractional per-token prices stay exact.
- Display (`total_display`) rounds µ$ to cents **once**, half-up, at the very end.

## Pinned constants — `config/pricing.toml`
```toml
version = "2026-11-01"
[api_calls]
price_per_call_microusd = 500            # $0.0005 per call
[ai_tokens]                              # µ$ per 1,000,000 tokens
input_per_million_microusd        = 1_250_000   # $1.25 / M
cached_input_per_million_microusd =   312_500   # $0.3125 / M (25% of input)
output_per_million_microusd       = 10_000_000  # $10.00 / M (reasoning billed here)
```
Path from `PRICING_CONFIG_PATH` (default `config/pricing.toml`, relative to the repo root). Loaded once at startup and validated (all non-negative ints, never floats; cached ≤ input); an invalid file stops the app from starting. Changing a constant = bump `version` + update the worked examples below + the exact integers in `tests/unit/test_pricing.py` + EVIDENCE C3.

## Token rules
Request fields: `input_tokens` (total prompt, **includes** cached), `cached_input_tokens` (subset of input), `output_tokens` (visible), `reasoning_tokens` (separate from output).
| Category | Billable tokens | Rate |
|---|---|---|
| fresh input | `input_tokens - cached_input_tokens` | input |
| cached input | `cached_input_tokens` | cached_input |
| output | `output_tokens + reasoning_tokens` | output |
Why categories "can't simply be added": cached is *inside* input (adding it again double-bills) and each category has its own rate (summing tokens then applying one rate is wrong).

## Formula (exact, integer)
```
numerator           = fresh*input_rate + cached*cached_rate + (output + reasoning)*output_rate
token_cost_microusd = half_up_div(numerator, 1_000_000)      # half_up_div(n, d) = (2*n + d) // (2*d)
```
Policy: sum numerators across the period, divide by 1,000,000 **once** (half-up to whole µ$). Per-request `cost_microusd` applies the same function to that one request. Implementation: `app/services/pricing.py` (pure, no I/O).

`api_calls_cost_microusd = calls * price_per_call_microusd`. `total_microusd = api_calls_cost + token_cost`.

Because cost is linear in token counts, "sum the numerators over the period" equals "sum each token column over the period's `ai_tokens` events, then price once" — that is what `GET /usage` does (`usage_repo.token_sums`). Consequence: the month total can exceed the sum of per-request `cost` values by rounding (e.g. three requests of 1 fresh input token: 1+1+1 = 3 µ$ per request, 4 µ$ for the month, since 3.75 rounds to 4). Tests pin this.

### Response lines and the rounding adjustment
Each response lists one line per category (quantity, rate, amount). Line amounts are rounded individually for readability, so `rounding_adjustment_microusd = token_cost - sum(token line amounts)` is reported, and lines + adjustment always equal the total. Shape: [api.md](api.md#the-cost-object-both-generate-and-usage).

## Worked examples (P5 / C2 / C3 — tests assert these exact numbers)
**E1** input 10,000 (cached 4,000), output 2,000, reasoning 500:
- fresh 6,000 × 1,250,000 = 7,500,000,000
- cached 4,000 × 312,500 = 1,250,000,000
- output 2,500 × 10,000,000 = 25,000,000,000
- sum 33,750,000,000 / 1,000,000 = **33,750 µ$ = $0.03375 → display $0.03**
- Wrong (naive: all 16,500 tokens at input rate) = 20,625 µ$. Wrong (cached added twice) = 38,750 µ$.

**E2** reasoning only: input 0, output 0, reasoning 1,000 → 1,000 × 10,000,000 / 1e6 = **10,000 µ$**.

**E3** all cached: input 8,000, cached 8,000, output 0 → 8,000 × 312,500 / 1e6 = **2,500 µ$**.

**E4** 1,000 API calls → 1,000 × 500 = **500,000 µ$ = $0.50**.

**P5** (probe and `tests/integration/test_cost_http.py`): E1, E2, E3 sent as three requests to a fresh tenant → per-request totals 34,250 / 10,500 / 3,000 µ$ (tokens + 500 µ$ per call); `GET /usage` total **47,750 µ$ = $0.05** (fresh 6,000, cached 12,000, output+reasoning 3,500, 3 calls).
