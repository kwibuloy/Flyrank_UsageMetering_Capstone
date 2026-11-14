"""Exactly-once metering over HTTP (M1, M2, P1). Spec: docs/idempotency.md."""

from tests.integration.conftest import counters_match_events, event_count

BODY = {
    "input_tokens": 10_000,
    "cached_input_tokens": 4_000,
    "output_tokens": 2_000,
    "reasoning_tokens": 500,
}


def post(client, api_key, key, body=BODY):
    return client.post(
        "/generate", json=body, headers={"X-API-Key": api_key, "Idempotency-Key": key}
    )


def test_same_request_twice_records_one_event_and_mirrors_response(client, make_tenant):
    tenant, api_key = make_tenant()

    first = post(client, api_key, "req-001")
    second = post(client, api_key, "req-001")

    assert first.status_code == second.status_code == 200
    assert second.content == first.content  # byte-identical, not just equal JSON
    assert "Idempotent-Replayed" not in first.headers
    assert second.headers["Idempotent-Replayed"] == "true"
    assert event_count(tenant.id, "api_calls") == 1
    assert event_count(tenant.id, "ai_tokens") == 1

    usage = client.get("/usage", headers={"X-API-Key": api_key}).json()["metrics"]
    assert usage["api_calls"]["used"] == 1
    assert usage["ai_tokens"]["used"] == 12_500
    assert counters_match_events(tenant.id)


def test_many_retries_still_one_event(client, make_tenant):
    tenant, api_key = make_tenant()
    responses = [post(client, api_key, "retry-me") for _ in range(10)]
    assert len({r.text for r in responses}) == 1
    assert event_count(tenant.id) == 2  # one api_calls row + one ai_tokens row


def test_same_key_different_body_is_409_and_records_nothing_new(client, make_tenant):
    tenant, api_key = make_tenant()
    post(client, api_key, "k1")

    conflict = post(client, api_key, "k1", {**BODY, "output_tokens": 9})

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_reuse"
    assert event_count(tenant.id) == 2


def test_equivalent_bodies_are_the_same_request(client, make_tenant):
    tenant, api_key = make_tenant()
    first = post(client, api_key, "k-eq", {})
    second = post(client, api_key, "k-eq", {"input_tokens": 0})
    assert second.status_code == 200 and second.json() == first.json()
    assert event_count(tenant.id) == 1  # zero tokens -> only the api_calls event


def test_keys_are_scoped_per_tenant(client, make_tenant):
    tenant_a, key_a = make_tenant()
    tenant_b, key_b = make_tenant()

    a = post(client, key_a, "shared-key")
    b = post(client, key_b, "shared-key")

    assert a.json()["request_id"] != b.json()["request_id"]
    assert event_count(tenant_a.id) == 2
    assert event_count(tenant_b.id) == 2


def test_distinct_keys_each_count(client, make_tenant):
    tenant, api_key = make_tenant()
    for i in range(5):
        assert post(client, api_key, f"k-{i}").status_code == 200
    assert event_count(tenant.id, "api_calls") == 5
    assert counters_match_events(tenant.id)
