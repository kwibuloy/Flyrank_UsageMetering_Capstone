# Data model

PostgreSQL 16 (Docker). Schema changes **only** via Alembic migrations in `migrations/` — never `create_all` outside tests. Money columns are `BIGINT` micro-USD ([adr/0003-integer-money.md](adr/0003-integer-money.md)).

## Tables
### plans
| column | type | notes |
|---|---|---|
| id | text PK | `free`, `pro` |
| name | text | |
| api_calls_limit | bigint | monthly |
| ai_tokens_limit | bigint | monthly |
| stripe_price_id | text null | Pro only; from env at seed time |

### tenants
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| name | text | |
| api_key_hash | text unique | SHA-256 of the key; raw key shown once at seed |
| plan_id | text FK plans | default `free` |
| stripe_customer_id | text unique null | |
| created_at | timestamptz | |

### subscriptions
| column | type | notes |
|---|---|---|
| id | uuid PK | |
| tenant_id | uuid FK unique | one active mirror per tenant |
| stripe_subscription_id | text unique | |
| status | text | Stripe status verbatim (`active`, `past_due`, `unpaid`, `canceled`, …) |
| current_period_end | timestamptz | |
| last_event_created | bigint | Stripe `event.created`; older events are ignored (ordering guard) |

### idempotency_keys
| column | type | notes |
|---|---|---|
| tenant_id, key | PK (composite) | key scoped per tenant |
| request_hash | text | SHA-256 of canonical JSON body + method + path |
| status_code | int null | null while in-flight inside the transaction |
| response_body | json null | replayed verbatim (`json`, not `jsonb`: jsonb reorders keys) |
| created_at | timestamptz | retention: 30 days (documented, not enforced in core) |

### usage_events (append-only)
| column | type | notes |
|---|---|---|
| id | bigserial PK | |
| tenant_id | uuid FK | |
| idempotency_key | text | |
| metric | text | `api_calls` \| `ai_tokens` |
| quantity | bigint | > 0 |
| input_tokens, cached_input_tokens, output_tokens, reasoning_tokens | bigint null | set on `ai_tokens` rows |
| period_start | date | first day of UTC month |
| created_at | timestamptz | |
Unique: `(tenant_id, idempotency_key, metric)` — the last line of defence against double counting.
Index: `(tenant_id, period_start, metric)` for rollups.

### usage_counters
| column | type | notes |
|---|---|---|
| tenant_id, period_start, metric | PK | row locked `FOR UPDATE` during quota check |
| used | bigint | must equal `SUM(usage_events.quantity)` — integration test asserts it |

### stripe_events
| column | type | notes |
|---|---|---|
| id | text PK | Stripe `evt_…` id → dedupe |
| type | text | |
| payload | jsonb | verified event |
| status | text | `pending` \| `processing` \| `done` \| `dead` \| `ignored` |
| attempts | int | |
| next_attempt_at | timestamptz | backoff |
| last_error | text null | never contains secrets |
Index: `(status, next_attempt_at)` for the worker claim query.

## Tenant isolation
- Tenant is resolved **only** from `X-API-Key`; no tenant id in URLs or bodies.
- Every repository function on tenant-owned tables takes `tenant_id` as a required argument and filters by it.
- Integration test: tenant B's key can never read or affect tenant A's usage.
