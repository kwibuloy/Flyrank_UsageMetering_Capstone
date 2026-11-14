"""Acceptance probes P1-P5, plus stretch probe A1, against a running system.
Spec: docs/brief.md#acceptance-probes (A1: docs/background-jobs.md#usage-alerts).

Run inside the api container, which has DB access to create fresh probe tenants:
    docker compose exec api python scripts/probes.py [--only P1,P2]

Each probe prints PASS/FAIL with the evidence it saw; exit code 1 if any probe fails.
"""

import argparse
import hashlib
import hmac
import json
import sys
import time
import uuid
from collections.abc import Callable

import httpx
from sqlalchemy import text

from app.config import get_settings
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


def stripe_event_row(event_id: str) -> dict | None:
    with SessionLocal() as session:
        row = (
            session.execute(
                text("SELECT status, attempts, outcome FROM stripe_events WHERE id = :i"),
                {"i": event_id},
            )
            .mappings()
            .first()
        )
    return dict(row) if row else None


def stripe_event_count() -> int:
    with SessionLocal() as session:
        return session.execute(text("SELECT count(*) FROM stripe_events")).scalar_one()


def signed_post(client: httpx.Client, event: dict, secret: str) -> httpx.Response:
    payload = json.dumps(event).encode()
    ts = int(time.time())
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/stripe",
        content=payload,
        headers={"Stripe-Signature": f"t={ts},v1={digest}", "Content-Type": "application/json"},
    )


def wait_for(predicate: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.5)
    return predicate()


def probe_p3(client: httpx.Client) -> str:
    """Real test-mode Checkout: you pay with 4242 4242 4242 4242 in a browser."""
    api_key = fresh_tenant("free")
    r = client.post("/billing/checkout", headers={"X-API-Key": api_key})
    check(r.status_code == 200, f"checkout: {r.status_code} {r.text}")
    print(f"    Open and pay with 4242 4242 4242 4242:\n    {r.json()['checkout_url']}", flush=True)
    upgraded = wait_for(lambda: usage(client, api_key)["plan"] == "pro", seconds=900)
    check(upgraded, "tenant still on Free after 15 minutes (is the stripe listener running?)")
    after = usage(client, api_key)
    limits = {m: v["limit"] for m, v in after["metrics"].items()}
    return f"Free -> {after['plan']} ({after['subscription_status']}) via webhook; limits {limits}"


def probe_p4(client: httpx.Client) -> str:
    """Forged webhook -> 400 + no change; a signed event sent twice -> processed once."""
    secret = get_settings().stripe_webhook_secret.get_secret_value()
    check(bool(secret), "STRIPE_WEBHOOK_SECRET is not set")
    api_key = fresh_tenant("free")
    with SessionLocal() as session:
        tenant_id = tenancy.authenticate(session, api_key).id

    before = stripe_event_count()
    forged = signed_post(client, {"id": "evt_forged", "type": "x"}, secret="whsec_wrong")
    check(forged.status_code == 400, f"forged: {forged.status_code}, expected 400")
    check(stripe_event_count() == before, "forged webhook was stored")

    event = {
        "id": f"evt_probe_{uuid.uuid4().hex[:16]}",
        "object": "event",
        "type": "customer.subscription.updated",
        "created": int(time.time()),
        "livemode": False,
        "data": {
            "object": {
                "id": f"sub_probe_{uuid.uuid4().hex[:12]}",
                "object": "subscription",
                "status": "active",
                "customer": None,
                "metadata": {"tenant_id": str(tenant_id)},
            }
        },
    }
    first = signed_post(client, event, secret)
    second = signed_post(client, event, secret)
    check(first.json() == {"status": "queued"}, f"first delivery: {first.text}")
    check(second.json() == {"status": "duplicate"}, f"replay: {second.text}")
    processed = wait_for(lambda: (stripe_event_row(event["id"]) or {}).get("status") == "done", 15)
    check(processed, f"worker did not process the event: {stripe_event_row(event['id'])}")
    row = stripe_event_row(event["id"])
    check(row["attempts"] == 1, f"attempts = {row['attempts']}, expected 1")
    check(usage(client, api_key)["plan"] == "pro", "plan did not change")
    return (
        f"forged -> 400, 0 rows stored; replay -> queued then duplicate; "
        f"worker applied once (attempts={row['attempts']}, outcome '{row['outcome']}')"
    )


def probe_p5(client: httpx.Client) -> str:
    """Pinned prices -> exact totals for cached input and reasoning; /usage matches."""
    api_key = fresh_tenant("free")
    requests = [  # docs/pricing.md worked examples E1-E3, each + one $0.0005 API call
        (
            {
                "input_tokens": 10_000,
                "cached_input_tokens": 4_000,
                "output_tokens": 2_000,
                "reasoning_tokens": 500,
            },
            34_250,
        ),
        ({"reasoning_tokens": 1_000}, 10_500),
        ({"input_tokens": 8_000, "cached_input_tokens": 8_000}, 3_000),
    ]
    for i, (body, expected) in enumerate(requests, start=1):
        r = generate(client, api_key, f"p5-{i}", body)
        check(r.status_code == 200, f"request {i}: {r.status_code} {r.text}")
        got = r.json()["cost"]["total_microusd"]
        check(got == expected, f"request {i}: cost {got} µ$, expected {expected}")
    cost = usage(client, api_key)["cost"]
    check(cost["total_microusd"] == 47_750, f"/usage total {cost['total_microusd']} µ$")
    check(cost["total_display"] == "$0.05", f"/usage display {cost['total_display']}")
    lines = {k: v["amount_microusd"] for k, v in cost["lines"].items()}
    return (
        f"E1 34,250 + E2 10,500 + E3 3,000 µ$ per request; /usage total "
        f"{cost['total_microusd']:,} µ$ ({cost['total_display']}) lines {lines}"
    )


def alerts_for(client: httpx.Client, api_key: str) -> list[dict]:
    return client.get("/alerts", headers={"X-API-Key": api_key}).json()["alerts"]


def probe_a1(client: httpx.Client) -> str:
    """Stretch: 80% and 100% alerts fire exactly once each and the worker delivers them."""
    api_key = fresh_tenant("free")
    for i in range(1, 800):
        r = generate(client, api_key, f"a1-{i}")
        check(r.status_code == 200, f"request {i}: {r.status_code}")
    check(alerts_for(client, api_key) == [], "an alert fired before 80%")
    at_80 = generate(client, api_key, "a1-800").json()["alerts_triggered"]
    check(at_80 == [{"metric": "api_calls", "threshold_percent": 80}], f"#800: {at_80}")
    for i in range(801, 1000):
        r = generate(client, api_key, f"a1-{i}")
        check(r.json()["alerts_triggered"] == [], f"request {i} re-fired an alert")
    at_100 = generate(client, api_key, "a1-1000").json()["alerts_triggered"]
    check(at_100 == [{"metric": "api_calls", "threshold_percent": 100}], f"#1000: {at_100}")
    over = generate(client, api_key, "a1-1001")
    check(over.status_code == 402, f"#1001: {over.status_code}")
    sent = wait_for(
        lambda: [a["status"] for a in alerts_for(client, api_key)] == ["sent", "sent"], 15
    )
    found = alerts_for(client, api_key)
    check(sent, f"worker did not deliver both alerts: {found}")
    return (
        "#799 -> no alert; #800 -> 80% alert; #801-#999 -> none; #1000 -> 100% alert; "
        f"#1001 -> 402; worker delivered {len(found)} of {len(found)} "
        f"(used at trigger {[a['used'] for a in found]})"
    )


def probe_b1(client: httpx.Client) -> str:
    """Optional feature: Pro usage reaches the Stripe invoice, matching /usage to the cent.

    Interactive like P3 (pay in a browser). Needs scripts/stripe_setup_metering.py run once.
    """
    from app.services import usage_billing

    api_key = fresh_tenant("free")
    r = client.post("/billing/checkout", headers={"X-API-Key": api_key})
    check(r.status_code == 200, f"checkout: {r.status_code} {r.text}")
    print("    Open and pay with 4242 4242 4242 4242:", flush=True)
    print(f"    {r.json()['checkout_url']}", flush=True)
    check(
        wait_for(lambda: usage(client, api_key)["usage_billing"]["enabled"], seconds=900),
        "tenant not on a metered Pro subscription after 15 minutes",
    )
    requests = [
        {
            "input_tokens": 10_000,
            "cached_input_tokens": 4_000,
            "output_tokens": 2_000,
            "reasoning_tokens": 500,
        },
        {"reasoning_tokens": 1_000},
        {"input_tokens": 8_000, "cached_input_tokens": 8_000},
    ] + [{"output_tokens": 1_000_000}] * 4
    for i, body in enumerate(requests, start=1):
        g = generate(client, api_key, f"b1-{i}", body)
        check(g.status_code == 200, f"request {i}: {g.status_code} {g.text}")
    check(
        wait_for(lambda: usage(client, api_key)["usage_billing"]["reports_sent"] == 7, 60),
        f"worker did not send 7 reports: {usage(client, api_key)['usage_billing']}",
    )
    after = usage(client, api_key)
    expected_micro = after["cost"]["total_microusd"]
    check(after["usage_billing"]["reported_microusd"] == expected_micro, "reported != /usage")
    expected_cents = pricing_cents(expected_micro)

    with SessionLocal() as session:
        tenant = tenancy.authenticate(session, api_key)
    cents = wait_for_invoice_usage(tenant.id, expected_cents)
    ident = usage_billing.identifier(tenant.id, "b1-1")
    return (
        f"7 Pro requests -> 7 meter events (identifier {ident[:14]}...); "
        f"/usage {expected_micro:,} µ$ ({after['cost']['total_display']}) == Stripe upcoming "
        f"invoice usage line {cents} cents"
    )


def invoice_usage_cents(tenant_id) -> int | None:
    """The usage line on the tenant's upcoming Stripe invoice, in cents (None = not yet)."""
    import stripe

    from app.services import billing

    with SessionLocal() as session:
        customer, subscription = session.execute(
            text(
                "SELECT t.stripe_customer_id, s.stripe_subscription_id FROM tenants t "
                "JOIN subscriptions s ON s.tenant_id = t.id WHERE t.id = :t"
            ),
            {"t": tenant_id},
        ).one()
    key = billing.secret_key()
    usage_price = billing.usage_price_id(key)
    preview = stripe.Invoice.create_preview(
        api_key=key, customer=customer, subscription=subscription
    )
    for item in preview.lines.auto_paging_iter():
        line = item.to_dict()
        details = (line.get("pricing") or {}).get("price_details") or {}
        price = details.get("price") or (line.get("price") or {}).get("id")
        if price == usage_price and line.get("amount"):
            return line["amount"]
    return None


def wait_for_invoice_usage(tenant_id, expected_cents: int, seconds: int = 600) -> int | None:
    """Poll the invoice preview every 10 s; Stripe aggregates meter events asynchronously."""
    print("    Waiting for Stripe to aggregate the meter (can take a few minutes)...", flush=True)
    deadline = time.monotonic() + seconds
    cents = None
    while time.monotonic() < deadline:
        cents = invoice_usage_cents(tenant_id)
        if cents == expected_cents:
            return cents
        time.sleep(10)
    check(False, f"invoice usage line {cents} cents != expected {expected_cents} cents")
    return cents


def pricing_cents(microusd: int) -> int:
    from app.services.pricing import half_up_div

    return half_up_div(microusd, 10_000)


PROBES: dict[str, Callable[[httpx.Client], str]] = {
    "P1": probe_p1,
    "P2": probe_p2,
    "P3": probe_p3,
    "P4": probe_p4,
    "P5": probe_p5,
    "A1": probe_a1,  # stretch goal: usage alerts
    "B1": probe_b1,  # optional: usage on the Stripe invoice (interactive)
}
INTERACTIVE = {"P3", "B1"}
NOT_YET: dict[str, str] = {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--only", default="", help="comma-separated, e.g. P1,P2")
    args = parser.parse_args()
    chosen = [p.strip().upper() for p in args.only.split(",") if p.strip()]
    selected = chosen or [*PROBES, *NOT_YET]

    failed = False
    with httpx.Client(base_url=args.base_url, timeout=10) as client:
        for name in selected:
            if name in NOT_YET:
                print(f"{name} SKIP  not built yet: {NOT_YET[name]}")
                continue
            if name in INTERACTIVE and not chosen:
                print(f"{name} SKIP  needs a browser: run with --only {name}")
                continue
            try:
                print(f"{name} PASS  {PROBES[name](client)}")
            except (ProbeFailure, httpx.HTTPError) as exc:
                failed = True
                print(f"{name} FAIL  {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
