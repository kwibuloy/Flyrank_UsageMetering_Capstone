"""Quota enforcement over HTTP (Q1, Q2, P2). Spec: docs/quotas.md."""

from tests.integration.conftest import counters_match_events, event_count


def post(client, api_key, key, body=None):
    return client.post(
        "/generate", json=body or {}, headers={"X-API-Key": api_key, "Idempotency-Key": key}
    )


def test_free_tenant_boundary_request_allowed_next_is_402(client, make_tenant, preload_usage):
    tenant, api_key = make_tenant("free")
    preload_usage(tenant.id, "api_calls", 999)

    at_boundary = post(client, api_key, "call-1000")
    over = post(client, api_key, "call-1001")

    assert at_boundary.status_code == 200
    assert at_boundary.json()["usage"]["api_calls"] == {"used": 1000, "limit": 1000, "remaining": 0}
    assert over.status_code == 402
    error = over.json()["error"]
    assert error["code"] == "payment_required"
    assert error["details"]["metric"] == "api_calls"
    assert error["details"]["used"] == 1000 and error["details"]["limit"] == 1000
    assert error["details"]["upgrade_url"] == "/billing/checkout"
    assert "Upgrade to Pro" in error["message"]
    assert "Retry-After" not in over.headers
    assert event_count(tenant.id, "api_calls") == 2  # preload + call-1000, nothing for 1001
    assert counters_match_events(tenant.id)


def test_pro_tenant_over_limit_is_429_with_retry_after(client, make_tenant, preload_usage):
    tenant, api_key = make_tenant("pro")
    preload_usage(tenant.id, "api_calls", 49_999)

    assert post(client, api_key, "last-ok").status_code == 200
    over = post(client, api_key, "too-many")

    assert over.status_code == 429
    error = over.json()["error"]
    assert error["code"] == "quota_exceeded"
    assert int(over.headers["Retry-After"]) == error["details"]["retry_after_seconds"] > 0
    assert "resets at" in error["message"]


def test_token_request_crossing_limit_rejected_whole(client, make_tenant, preload_usage):
    tenant, api_key = make_tenant("free")
    preload_usage(tenant.id, "ai_tokens", 99_000)

    rejected = post(client, api_key, "big", {"input_tokens": 2_500})

    assert rejected.status_code == 402
    assert rejected.json()["error"]["details"]["metric"] == "ai_tokens"
    assert event_count(tenant.id, "api_calls") == 0  # no partial grant, not even the call
    assert counters_match_events(tenant.id)


def test_token_request_landing_exactly_on_limit_allowed(client, make_tenant, preload_usage):
    tenant, api_key = make_tenant("free")
    preload_usage(tenant.id, "ai_tokens", 97_500)

    ok = post(client, api_key, "exact", {"input_tokens": 2_000, "output_tokens": 500})

    assert ok.status_code == 200
    assert ok.json()["usage"]["ai_tokens"] == {"used": 100_000, "limit": 100_000, "remaining": 0}


def test_rejected_request_replays_same_rejection(client, make_tenant, preload_usage):
    tenant, api_key = make_tenant("free")
    preload_usage(tenant.id, "api_calls", 1_000)

    first = post(client, api_key, "blocked")
    second = post(client, api_key, "blocked")

    assert first.status_code == second.status_code == 402
    assert first.json() == second.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    assert event_count(tenant.id, "api_calls") == 1  # only the preload


def test_usage_reports_limits_for_plan(client, make_tenant):
    _, free_key = make_tenant("free")
    _, pro_key = make_tenant("pro")
    free = client.get("/usage", headers={"X-API-Key": free_key}).json()
    pro = client.get("/usage", headers={"X-API-Key": pro_key}).json()
    assert free["metrics"]["api_calls"]["limit"] == 1_000
    assert free["metrics"]["ai_tokens"]["limit"] == 100_000
    assert pro["metrics"]["api_calls"]["limit"] == 50_000
    assert pro["metrics"]["ai_tokens"]["limit"] == 5_000_000
