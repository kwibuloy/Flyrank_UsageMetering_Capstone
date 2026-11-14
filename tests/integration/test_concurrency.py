"""Concurrent requests against real Postgres row locks. Each thread uses its own session."""

import threading
from concurrent.futures import ThreadPoolExecutor

from app.db.session import SessionLocal
from app.services.metering import MeterResult, UsageRequest, record_usage
from tests.integration.conftest import counters_match_events, event_count

THREADS = 20


def run_concurrently(fn, n=THREADS) -> list[MeterResult]:
    barrier = threading.Barrier(n)

    def worker(i: int) -> MeterResult:
        with SessionLocal() as session:
            barrier.wait()  # release all threads at the same moment
            result = fn(session, i)
            session.commit()
            return result

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(worker, range(n)))


def test_concurrent_duplicates_record_exactly_once(make_tenant):
    tenant, _ = make_tenant()
    usage = UsageRequest(input_tokens=1_000, output_tokens=100)

    results = run_concurrently(
        lambda s, i: record_usage(
            s, tenant=tenant, idempotency_key="same", request_hash="h", usage=usage
        )
    )

    assert sum(not r.replayed for r in results) == 1
    assert {r.body["request_id"] for r in results} == {results[0].body["request_id"]}
    assert event_count(tenant.id, "api_calls") == 1
    assert event_count(tenant.id, "ai_tokens") == 1
    assert counters_match_events(tenant.id)


def test_concurrent_requests_never_oversell_the_quota(make_tenant, preload_usage):
    tenant, _ = make_tenant("free")
    preload_usage(tenant.id, "api_calls", 995)  # 5 calls left, 20 racing

    results = run_concurrently(
        lambda s, i: record_usage(
            s, tenant=tenant, idempotency_key=f"k-{i}", request_hash=f"h-{i}", usage=UsageRequest()
        )
    )

    assert sorted(r.status_code for r in results) == [200] * 5 + [402] * 15
    assert event_count(tenant.id, "api_calls") == 6  # preload + exactly 5
    assert counters_match_events(tenant.id)
