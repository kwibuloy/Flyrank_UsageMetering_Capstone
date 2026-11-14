"""Multi-tenant load test with correctness checks. Not part of the test suite.

Run inside the api container (it creates its own throwaway tenants and removes them):
    docker compose exec api python scripts/loadtest.py \
        --tenants 100 --requests 5000 --concurrency 50

Every request is unique (its own idempotency key) and spread round-robin across tenants.
After the run it verifies, straight from Postgres, that nothing was lost or double-counted:
accepted requests == usage events, and usage_counters == SUM(usage_events) for every tenant.
"""

import argparse
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
from sqlalchemy import text

from app.db.session import SessionLocal
from app.services import tenancy

BODY = {"input_tokens": 1_000, "cached_input_tokens": 400, "output_tokens": 200}


def make_tenants(count: int, plan: str, run_id: str) -> list[tuple[str, str]]:
    keys = []
    with SessionLocal() as session:
        for i in range(count):
            name = f"load-{run_id}-{i:04d}"
            keys.append((name, tenancy.create_or_update_tenant(session, name=name, plan_id=plan)))
        session.commit()
    return keys


def verify(run_id: str) -> dict[str, int]:
    with SessionLocal() as session:
        events = session.execute(
            text(
                "SELECT count(*) FROM usage_events e JOIN tenants t ON t.id = e.tenant_id "
                "WHERE t.name LIKE :p AND e.metric = 'api_calls'"
            ),
            {"p": f"load-{run_id}-%"},
        ).scalar_one()
        mismatched = session.execute(
            text(
                """
                SELECT count(*) FROM usage_counters c JOIN tenants t ON t.id = c.tenant_id
                WHERE t.name LIKE :p AND c.used <> (
                    SELECT coalesce(sum(e.quantity), 0) FROM usage_events e
                    WHERE e.tenant_id = c.tenant_id AND e.period_start = c.period_start
                      AND e.metric = c.metric)
                """
            ),
            {"p": f"load-{run_id}-%"},
        ).scalar_one()
    return {"events": events, "mismatched_counters": mismatched}


def cleanup(run_id: str) -> None:
    with SessionLocal() as session:
        session.execute(text("DELETE FROM tenants WHERE name LIKE :p"), {"p": f"load-{run_id}-%"})
        session.commit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--tenants", type=int, default=100)
    parser.add_argument("--requests", type=int, default=5_000)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--plan", default="pro", choices=["free", "pro"])
    parser.add_argument("--keep", action="store_true", help="keep the load-test tenants")
    args = parser.parse_args()

    run_id = uuid.uuid4().hex[:6]
    tenants = make_tenants(args.tenants, args.plan, run_id)
    limits = httpx.Limits(
        max_connections=args.concurrency, max_keepalive_connections=args.concurrency
    )
    client = httpx.Client(base_url=args.base_url, timeout=30, limits=limits)

    def one(i: int) -> tuple[int, float]:
        _, api_key = tenants[i % len(tenants)]
        headers = {"X-API-Key": api_key, "Idempotency-Key": f"load-{run_id}-{i}"}
        start = time.perf_counter()
        try:
            status = client.post("/generate", json=BODY, headers=headers).status_code
        except httpx.HTTPError:
            status = 0
        return status, (time.perf_counter() - start) * 1000

    try:
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = list(pool.map(one, range(args.requests)))
        wall = time.perf_counter() - started

        latencies = sorted(ms for _, ms in results)
        statuses: dict[int, int] = {}
        for status, _ in results:
            statuses[status] = statuses.get(status, 0) + 1
        accepted = statuses.get(200, 0)
        checks = verify(run_id)

        def pct(p: float) -> float:
            return latencies[min(len(latencies) - 1, int(p * len(latencies)))]

        print(
            f"tenants={args.tenants} requests={args.requests} concurrency={args.concurrency} "
            f"plan={args.plan}"
        )
        print(f"statuses={dict(sorted(statuses.items()))}")
        print(
            f"throughput={args.requests / wall:.0f} req/s  wall={wall:.1f}s  "
            f"p50={statistics.median(latencies):.0f}ms  p95={pct(0.95):.0f}ms  "
            f"p99={pct(0.99):.0f}ms"
        )
        print(
            f"accepted={accepted} usage_events={checks['events']} "
            f"counters_mismatched={checks['mismatched_counters']}"
        )
        ok = (
            checks["events"] == accepted
            and checks["mismatched_counters"] == 0
            and not any(s == 0 or s >= 500 for s in statuses)
        )
        print("CORRECT" if ok else "INCORRECT")
        return 0 if ok else 1
    finally:
        client.close()
        if not args.keep:
            cleanup(run_id)


if __name__ == "__main__":
    sys.exit(main())
