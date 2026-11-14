"""Acceptance probes P1-P5 against a running system (docs/brief.md#acceptance-probes).

Run inside the api container, which has DB access to create fresh probe tenants:
    docker compose exec api python scripts/probes.py [--only P1,P2]

Each probe prints PASS/FAIL with the evidence it saw; exit code 1 if any probe fails.
"""

import argparse
import sys
import uuid
from collections.abc import Callable

import httpx

from app.db.session import SessionLocal
from app.services import tenancy


class ProbeFailure(AssertionError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeFailure(message)


def fresh_tenant(plan_id: str = "free") -> str:
    with SessionLocal() as session:
        api_key = tenancy.create_or_update_tenant(
            session, name=f"probe-{plan_id}-{uuid.uuid4().hex[:8]}", plan_id=plan_id
        )
        session.commit()
    return api_key


def generate(client: httpx.Client, api_key: str, key: str, body: dict | None = None):
    return client.post(
        "/generate", json=body or {}, headers={"X-API-Key": api_key, "Idempotency-Key": key}
    )


def usage(client: httpx.Client, api_key: str) -> dict:
    return client.get("/usage", headers={"X-API-Key": api_key}).json()


def probe_p1(client: httpx.Client) -> str:
    """Same request twice with one idempotency key -> one usage event, mirrored response."""
    api_key = fresh_tenant()
    body = {"input_tokens": 10_000, "cached_input_tokens": 4_000, "output_tokens": 2_000}
    first = generate(client, api_key, "p1-key", body)
    second = generate(client, api_key, "p1-key", body)
    check(first.status_code == 200, f"first request: {first.status_code} {first.text}")
    check(second.status_code == 200, f"second request: {second.status_code}")
    check(second.json() == first.json(), "second response body differs from the first")
    check(second.headers.get("Idempotent-Replayed") == "true", "missing Idempotent-Replayed")
    calls = usage(client, api_key)["metrics"]["api_calls"]["used"]
    check(calls == 1, f"api_calls used = {calls}, expected 1")
    check(second.content == first.content, "second response is not byte-identical")
    request_id = first.json()["request_id"]
    return f"2 sends -> 1 event (api_calls used={calls}), byte-identical replay of {request_id}"


def probe_p2(client: httpx.Client) -> str:
    """Drive a Free tenant to its exact quota: #1000 allowed, #1001 -> 402 with a reason."""
    api_key = fresh_tenant("free")
    for i in range(1, 1000):
        r = generate(client, api_key, f"p2-{i}")
        check(r.status_code == 200, f"request {i}: {r.status_code} {r.text}")
    boundary = generate(client, api_key, "p2-1000")
    over = generate(client, api_key, "p2-1001")
    check(boundary.status_code == 200, f"request 1000: {boundary.status_code} {boundary.text}")
    check(over.status_code == 402, f"request 1001: {over.status_code}, expected 402")
    error = over.json()["error"]
    check(error["code"] == "payment_required", f"code {error['code']}")

    tokens_key = fresh_tenant("free")
    exact = generate(client, tokens_key, "p2-tokens-exact", {"input_tokens": 100_000})
    one_more = generate(client, tokens_key, "p2-tokens-over", {"output_tokens": 1})
    check(exact.status_code == 200, f"100,000 tokens: {exact.status_code}")
    check(one_more.status_code == 402, f"100,001st token: {one_more.status_code}")
    return f"#1000 -> 200, #1001 -> 402 '{error['message']}'; tokens 100,000 -> 200, +1 -> 402"


PROBES: dict[str, Callable[[httpx.Client], str]] = {"P1": probe_p1, "P2": probe_p2}
NOT_YET = {"P3": "Phase 3 (Stripe checkout)", "P4": "Phase 3 (webhooks)", "P5": "Phase 4 (pricing)"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--only", default="", help="comma-separated, e.g. P1,P2")
    args = parser.parse_args()
    selected = [p.strip().upper() for p in args.only.split(",") if p.strip()] or [
        *PROBES,
        *NOT_YET,
    ]

    failed = False
    with httpx.Client(base_url=args.base_url, timeout=10) as client:
        for name in selected:
            if name in NOT_YET:
                print(f"{name} SKIP  not built yet: {NOT_YET[name]}")
                continue
            try:
                print(f"{name} PASS  {PROBES[name](client)}")
            except (ProbeFailure, httpx.HTTPError) as exc:
                failed = True
                print(f"{name} FAIL  {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
