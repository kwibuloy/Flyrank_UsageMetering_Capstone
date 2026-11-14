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
| usage_billing | bool default false | subscription has a metered (usage) price → usage is reported to Stripe |
| last_event_created | bigint default 0 | Stripe `event.created`; older events are ignored (ordering guard); upsert keeps `greatest()` |
| updated_at | timestamptz | |

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
| status | text (check) | `pending` \| `done` \| `dead` \| `ignored` (no `processing`: the row lock covers it, see [background-jobs.md](background-jobs.md)) |
| outcome | text null | what applying did, e.g. `plan=pro status=active`, `ignored_stale` |
| attempts | int | |
| next_attempt_at | timestamptz | backoff |
| last_error | text null | exception class + message; never payloads or secrets |
| received_at | timestamptz | worker processes oldest first |
| processed_at | timestamptz null | set when `done` |
Index: `(status, next_attempt_at)` for the worker claim query.

### usage_alerts (stretch: 80% / 100% alerts)
| column | type | notes |
|---|---|---|
| id | bigint identity PK | sent as `alert_id` so receivers can de-duplicate |
| tenant_id | uuid FK (cascade) | |
| period_start | date | |
| metric | text (check) | `api_calls` \| `ai_tokens` |
| threshold_percent | int (check) | `80` \| `100` |
| plan_id | text | plan when it fired |
| quota_limit | bigint (> 0) | limit when it fired; part of the unique key so a new limit re-arms |
| used_at_trigger | bigint | usage right after the crossing request |
| status | text (check) | `pending` \| `sent` \| `dead` |
| attempts, next_attempt_at, last_error | | same retry fields as `stripe_events` |
| delivery | text null | e.g. `webhook 204`, `log` |
| created_at, sent_at | timestamptz | |
Unique: `(tenant_id, period_start, metric, threshold_percent, quota_limit)` — the "once per threshold" guarantee. Indexes: `(status, next_attempt_at)` for the worker, `(tenant_id, period_start)` for `GET /alerts`.

### usage_reports (usage-based billing outbox)
| column | type | notes |
|---|---|---|
| id | bigint identity PK | worker claims oldest first |
| tenant_id, idempotency_key | unique together | one report per billable request; replays add nothing |
| stripe_customer_id | text | |
| identifier | text unique | sent to Stripe for de-duplication |
| value_picousd | bigint (> 0) | exact unrounded cost, 1 unit = 10⁻¹² USD |
| occurred_at | timestamptz | meter event timestamp |
| status, attempts, next_attempt_at, last_error, sent_at | | same retry fields as `stripe_events` |
Index: `(status, next_attempt_at)`.

## Tenant isolation
- Tenant is resolved **only** from `X-API-Key`; no tenant id in URLs or bodies.
- Every repository function on tenant-owned tables takes `tenant_id` as a required argument and filters by it.
- Integration test: tenant B's key can never read or affect tenant A's usage.
