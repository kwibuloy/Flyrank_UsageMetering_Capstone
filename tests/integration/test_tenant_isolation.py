"""Tenants never see or affect each other's usage (D1)."""


def test_usage_is_isolated_per_tenant(client, make_tenant):
    _, key_a = make_tenant()
    _, key_b = make_tenant()

    for i in range(3):
        client.post(
            "/generate",
            json={"output_tokens": 100},
            headers={"X-API-Key": key_a, "Idempotency-Key": f"a-{i}"},
        )

    a = client.get("/usage", headers={"X-API-Key": key_a}).json()
    b = client.get("/usage", headers={"X-API-Key": key_b}).json()

    assert a["metrics"]["api_calls"]["used"] == 3
    assert a["metrics"]["ai_tokens"]["used"] == 300
    assert b["metrics"]["api_calls"]["used"] == 0
    assert b["metrics"]["ai_tokens"]["used"] == 0
    assert a["tenant"] != b["tenant"]
