"""Integration fixtures: real Postgres (the compose test database), schema via Alembic."""

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import SessionLocal, engine
from app.main import create_app
from app.services import tenancy
from app.services.periods import period_bounds
from app.services.tenancy import TenantContext

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def migrated() -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def clean_db(migrated: None) -> Iterator[None]:
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE usage_events, usage_counters, idempotency_keys, tenants "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def make_tenant() -> Callable[..., tuple[TenantContext, str]]:
    def _make(plan_id: str = "free") -> tuple[TenantContext, str]:
        with SessionLocal() as session:
            name = f"t-{uuid.uuid4().hex[:10]}"
            api_key = tenancy.create_or_update_tenant(session, name=name, plan_id=plan_id)
            ctx = tenancy.authenticate(session, api_key)
            session.commit()
        return ctx, api_key

    return _make


@pytest.fixture
def preload_usage() -> Callable[[uuid.UUID, str, int], None]:
    """Put a tenant at `used` for a metric this period, keeping counters == SUM(events)."""

    def _preload(tenant_id: uuid.UUID, metric: str, used: int) -> None:
        period_start = period_bounds(datetime.now(UTC))[0].date()
        key = f"preload-{metric}"
        params = {"t": tenant_id, "k": key, "m": metric, "q": used, "p": period_start}
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO idempotency_keys (tenant_id, key, request_hash, status_code) "
                    "VALUES (:t, :k, 'preload', 200)"
                ),
                params,
            )
            conn.execute(
                text(
                    "INSERT INTO usage_events (tenant_id, idempotency_key, metric, quantity, "
                    "period_start) VALUES (:t, :k, :m, :q, :p)"
                ),
                params,
            )
            conn.execute(
                text(
                    "INSERT INTO usage_counters (tenant_id, period_start, metric, used) "
                    "VALUES (:t, :p, :m, :q)"
                ),
                params,
            )

    return _preload


def event_count(tenant_id: uuid.UUID, metric: str | None = None) -> int:
    sql = "SELECT count(*) FROM usage_events WHERE tenant_id = :t"
    if metric:
        sql += " AND metric = :m"
    with engine.connect() as conn:
        return conn.execute(text(sql), {"t": tenant_id, "m": metric}).scalar_one()


def counters_match_events(tenant_id: uuid.UUID) -> bool:
    with engine.connect() as conn:
        mismatches = conn.execute(
            text(
                "SELECT count(*) FROM usage_counters c "
                "LEFT JOIN (SELECT metric, period_start, sum(quantity) AS total FROM usage_events "
                "           WHERE tenant_id = :t GROUP BY metric, period_start) e "
                "  ON e.metric = c.metric AND e.period_start = c.period_start "
                "WHERE c.tenant_id = :t AND c.used <> coalesce(e.total, 0)"
            ),
            {"t": tenant_id},
        ).scalar_one()
    return mismatches == 0
