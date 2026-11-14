# ADR 0001 — Python + FastAPI + Postgres + SQLAlchemy/Alembic

Status: accepted · 2026-11-14

## Context
Brief allows Node+Express or Python+FastAPI; Postgres via Docker or SQLite. Correctness under concurrency (row locks, unique constraints, `SKIP LOCKED`) is central.

## Decision
Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (sync, psycopg 3), Alembic, PostgreSQL 16 in Docker Compose, pytest, ruff, `stripe` Python SDK.

## Consequences
- + Pydantic gives boundary validation and clean 422s for free; Python `int` is arbitrary precision for money math.
- + Postgres provides real row locking and `ON CONFLICT`, which SQLite cannot model faithfully.
- − Sync SQLAlchemy in async FastAPI: routes are plain `def` (threadpool), which is simpler and adequate at this scale.
- Rejected: SQLite (no `FOR UPDATE`/`SKIP LOCKED`), Redis queue (extra infra for no grading benefit).
