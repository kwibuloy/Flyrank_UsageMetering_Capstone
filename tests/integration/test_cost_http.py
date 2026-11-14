"""Cost in /generate and the monthly rollup in /usage (C1, C2, P5) against real Postgres."""

E1 = {
    "input_tokens": 10_000,
    "cached_input_tokens": 4_000,
    "output_tokens": 2_000,
    "reasoning_tokens": 500,
}
E2 = {"reasoning_tokens": 1_000}
E3 = {"input_tokens": 8_000, "cached_input_tokens": 8_000}


def gen(client, api_key, key, body):
    return client.post(
        "/generate", json=body, headers={"X-API-Key": api_key, "Idempotency-Key": key}
    )


def usage_cost(client, api_key):
    return client.get("/usage", headers={"X-API-Key": api_key}).json()["cost"]


def test_generate_returns_the_exact_request_cost(client, make_tenant):
    _, api_key = make_tenant("free")
    cost = gen(client, api_key, "e1", E1).json()["cost"]
    assert cost["total_microusd"] == 33_750 + 500  # E1 tokens + one API call
    assert cost["pricing_version"] == "2026-11-01"
    assert cost["lines"]["cached_input_tokens"] == {
        "quantity": 4_000,
        "rate_microusd": 312_500,
        "rate_unit": "per_million_tokens",
        "amount_microusd": 1_250,
    }


def test_usage_rolls_up_the_month_and_matches_requests(client, make_tenant):
    _, api_key = make_tenant("free")
    per_request = [
        gen(client, api_key, k, b).json()["cost"]["total_microusd"]
        for k, b in (("e1", E1), ("e2", E2), ("e3", E3))
    ]

    cost = usage_cost(client, api_key)
    assert per_request == [34_250, 10_500, 3_000]
    assert cost["total_microusd"] == 47_750 == sum(per_request)
    assert cost["total_display"] == "$0.05"
    assert {k: v["quantity"] for k, v in cost["lines"].items()} == {
        "api_calls": 3,
        "input_tokens": 6_000,
        "cached_input_tokens": 12_000,
        "output_tokens": 3_500,
    }


def test_replays_add_no_cost(client, make_tenant):
    _, api_key = make_tenant("free")
    gen(client, api_key, "same", E1)
    gen(client, api_key, "same", E1)  # replay
    assert usage_cost(client, api_key)["total_microusd"] == 34_250


def test_rejected_requests_add_no_cost(client, make_tenant, preload_usage):
    tenant, api_key = make_tenant("free")
    preload_usage(tenant.id, "api_calls", 1_000)  # quota exhausted
    before = usage_cost(client, api_key)["total_microusd"]
    rejected = gen(client, api_key, "over", E1)
    assert rejected.status_code == 402 and "cost" not in rejected.json()
    assert usage_cost(client, api_key)["total_microusd"] == before == 1_000 * 500


def test_empty_month_costs_zero(client, make_tenant):
    _, api_key = make_tenant("free")
    cost = usage_cost(client, api_key)
    assert cost["total_microusd"] == 0 and cost["total_display"] == "$0.00"


def test_cost_is_isolated_per_tenant(client, make_tenant):
    _, a = make_tenant("free")
    _, b = make_tenant("pro")
    gen(client, a, "k", E1)
    assert usage_cost(client, b)["total_microusd"] == 0
