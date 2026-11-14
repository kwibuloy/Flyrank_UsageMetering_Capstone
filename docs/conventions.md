# Conventions

## Python
- Python 3.12, FastAPI, SQLAlchemy 2.x (typed `Mapped[]`, sync engine + psycopg 3), Alembic, Pydantic v2, pydantic-settings.
- Format/lint: `ruff format` + `ruff check`. Type hints on all public functions.
- Names: services are verbs (`record_usage`, `decide`, `compute_cost`); repositories are data verbs (`get_…`, `insert_…`, `lock_counters`).

## Money
- `int` micro-USD everywhere; variable names end in `_microusd`. No `float`, no `round()` on money, no `/` on money (use `//` or the helper in `pricing.py`).
## Validation
- Pydantic models at the HTTP boundary with `extra="forbid"`, `ge=0`, cross-field validators (`cached_input_tokens <= input_tokens`).
- Any bad input → 4xx via the error envelope ([api.md](api.md)). A 500 caused by input is a bug: add a test.

## Secrets and logging
- Config only from env via `app/config.py`; `SecretStr` for keys. `.env` is git-ignored; `.env.example` lists every var with placeholders.
- Never log: API keys, `Stripe-Signature`, webhook payload bodies, Stripe secrets. Log ids instead (`tenant_id`, `event_id`, `request_id`).
- Tenant API keys stored as SHA-256 hashes only.

## Transactions
- One request = one session = one transaction, opened in `api/deps.py`. Services never commit.
- The **route** calls `db.commit()` right before returning, so a response is never sent for work that was not committed. The dependency only rolls back and closes (anything uncommitted is discarded).
- Quota rejections are a normal return value (not an exception), so the stored rejection on the idempotency row is committed like any other response.

## Testing
- `pytest`; unit tests have no DB; integration tests use the compose Postgres (`TEST_DATABASE_URL`), schema via `alembic upgrade head`, each test in a rolled-back transaction or truncated tables.
- Every acceptance probe (P1–P5) has at least one test. Concurrency tests use threads with separate sessions.
- Command: `docker compose run --rm api pytest -q` (this is `capstone.yaml` `test:`).

## Git
- One public repo; small commits per phase step; message style `phase2: idempotent metering transaction`.
- No `Co-Authored-By` trailers, no "Generated with" lines, no added collaborators.
