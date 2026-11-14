"""Bad input at the HTTP boundary gives a clean 4xx envelope, never a 500."""

import pytest


@pytest.fixture
def api_key(make_tenant):
    return make_tenant()[1]


def headers(api_key, key="k"):
    return {"X-API-Key": api_key, "Idempotency-Key": key}


@pytest.mark.parametrize(
    "body",
    [
        {"input_tokens": -1},
        {"input_tokens": "100"},
        {"input_tokens": 1.5},
        {"input_tokens": 10**30},
        {"input_tokens": 5, "cached_input_tokens": 6},
        {"surprise": True},
        [],
        "text",
    ],
)
def test_invalid_body_is_422(client, api_key, body):
    r = client.post("/generate", json=body, headers=headers(api_key))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert r.json()["error"]["details"]["fields"]


def test_malformed_json_is_422(client, api_key):
    r = client.post(
        "/generate",
        content=b'{"input_tokens": ',
        headers={**headers(api_key), "Content-Type": "application/json"},
    )
    assert r.status_code == 422


def test_missing_api_key_is_401(client):
    r = client.post("/generate", json={}, headers={"Idempotency-Key": "k"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_unknown_api_key_is_401(client):
    r = client.get("/usage", headers={"X-API-Key": "nope"})
    assert r.status_code == 401


def test_missing_idempotency_key_is_400(client, api_key):
    r = client.post("/generate", json={}, headers={"X-API-Key": api_key})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "idempotency_key_required"


@pytest.mark.parametrize("bad_key", ["x" * 256, "has space", "tab\tkey"])
def test_invalid_idempotency_key_is_400(client, api_key, bad_key):
    r = client.post("/generate", json={}, headers=headers(api_key, bad_key))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_idempotency_key"


def test_unknown_route_and_method_use_envelope(client):
    assert client.get("/nope").json()["error"]["code"] == "not_found"
    r = client.get("/generate")
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed"


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "db": "ok"}
