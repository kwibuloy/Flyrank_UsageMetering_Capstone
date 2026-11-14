"""Integration fixtures: real Postgres (the compose test database), schema via Alembic."""

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import stripe
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import SessionLocal, engine
from app.main import create_app
from app.services import billing, tenancy
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
                "TRUNCATE usage_events, usage_counters, idempotency_keys, subscriptions, "
                "stripe_events, usage_alerts, usage_reports, tenants RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest.fixture(autouse=True)
def no_stripe_price_lookup(monkeypatch) -> Iterator[list]:
    """Tests never reach Stripe: the usage-price lookup returns "not set up" unless a test
    appends a fake price to the returned list."""
    found: list = []
    billing._usage_price_cache.clear()
    monkeypatch.setattr(
        stripe.Price, "list", lambda **kwargs: type("L", (), {"data": list(found)})()
    )
    yield found
    billing._usage_price_cache.clear()


@pytest.fixture(autouse=True)
def no_stripe_reads(monkeypatch) -> None:
    """Success/cancel pages read the Checkout Session and account name from Stripe; in tests
    those calls fail like an outage unless a test replaces them."""

    def offline(*args, **kwargs):
        raise stripe.APIConnectionError("tests never reach Stripe")

    billing._merchant_cache.clear()
    billing._session_tenants.clear()
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", offline)
    monkeypatch.setattr(stripe.Account, "retrieve", offline)


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


# --- Stripe helpers -----------------------------------------------------------------

TEST_WEBHOOK_SECRET = "whsec_test_only"


def stripe_event(
    event_type: str, obj: dict, event_id: str | None = None, created: int | None = None
):
    return {
        "id": event_id or f"evt_{uuid.uuid4().hex[:24]}",
        "object": "event",
        "type": event_type,
        "created": created or int(time.time()),
        "livemode": False,
        "data": {"object": obj},
    }


def sign(payload: bytes, secret: str = TEST_WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    """Build a Stripe-Signature header exactly the way Stripe does (HMAC-SHA256)."""
    ts = timestamp or int(time.time())
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def post_webhook(client, event: dict, secret: str = TEST_WEBHOOK_SECRET):
    payload = json.dumps(event).encode()
    return client.post(
        "/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": sign(payload, secret), "Content-Type": "application/json"},
    )


def subscription_obj(tenant_id, sub_id="sub_test_1", status="active", customer="cus_test_1"):
    return {
        "id": sub_id,
        "object": "subscription",
        "customer": customer,
        "status": status,
        "metadata": {"tenant_id": str(tenant_id)},
        "items": {"data": [{"current_period_end": 1_800_000_000}]},
    }


def checkout_obj(tenant_id, sub_id="sub_test_1", customer="cus_test_1"):
    return {
        "id": "cs_test_1",
        "object": "checkout.session",
        "mode": "subscription",
        "client_reference_id": str(tenant_id),
        "customer": customer,
        "subscription": sub_id,
        "metadata": {"tenant_id": str(tenant_id)},
    }


def stripe_event_row(event_id: str) -> dict | None:
    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT status, attempts, outcome, last_error FROM stripe_events WHERE id = :i"
                ),
                {"i": event_id},
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


def stripe_event_count() -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM stripe_events")).scalar_one()
