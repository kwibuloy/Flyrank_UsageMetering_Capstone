"""Quota boundary rules (docs/quotas.md): limit-1, limit, limit+1, all-or-nothing, 402 vs 429."""

from datetime import UTC, datetime

import pytest

from app.services.quota import Allow, Reject, decide

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
PERIOD_END = datetime(2026, 12, 1, tzinfo=UTC)
FREE = {"api_calls": 1_000, "ai_tokens": 100_000}
PRO = {"api_calls": 50_000, "ai_tokens": 5_000_000}


def run(plan_id="free", limits=FREE, used=None, requested=None, status=None):
    return decide(
        plan_id=plan_id,
        limits=limits,
        used=used or {},
        requested=requested or {"api_calls": 1, "ai_tokens": 0},
        subscription_status=status,
        period_end=PERIOD_END,
        now=NOW,
    )


@pytest.mark.parametrize(("used_calls", "allowed"), [(998, True), (999, True), (1_000, False)])
def test_api_call_boundary_is_inclusive(used_calls, allowed):
    decision = run(used={"api_calls": used_calls})
    assert isinstance(decision, Allow) is allowed


@pytest.mark.parametrize(
    ("used_tokens", "requested_tokens", "allowed"),
    [
        (97_500, 2_500, True),
        (97_501, 2_500, False),
        (99_000, 2_500, False),
        (0, 100_000, True),
        (0, 100_001, False),
    ],
)
def test_token_boundary(used_tokens, requested_tokens, allowed):
    decision = run(
        used={"ai_tokens": used_tokens}, requested={"api_calls": 1, "ai_tokens": requested_tokens}
    )
    assert isinstance(decision, Allow) is allowed


def test_free_plan_over_limit_is_402_with_upgrade_hint():
    decision = run(used={"api_calls": 1_000})
    assert isinstance(decision, Reject)
    assert (decision.status_code, decision.code) == (402, "payment_required")
    assert decision.details == {
        "metric": "api_calls",
        "used": 1_000,
        "limit": 1_000,
        "requested": 1,
        "plan": "free",
        "period_end": "2026-12-01T00:00:00Z",
        "upgrade_url": "/billing/checkout",
    }
    assert "API call quota exceeded on the Free plan" in decision.message
    assert "1,000 of 1,000 used" in decision.message
    assert "Upgrade to Pro" in decision.message


def test_pro_plan_over_limit_is_429_with_retry_after():
    decision = run(plan_id="pro", limits=PRO, used={"api_calls": 50_000})
    assert isinstance(decision, Reject)
    assert (decision.status_code, decision.code) == (429, "quota_exceeded")
    seconds_to_reset = int((PERIOD_END - NOW).total_seconds())
    assert decision.details["retry_after_seconds"] == seconds_to_reset
    assert "upgrade_url" not in decision.details
    assert "resets at 2026-12-01T00:00:00Z" in decision.message


def test_all_or_nothing_reports_the_failing_metric():
    decision = run(
        used={"api_calls": 10, "ai_tokens": 99_000}, requested={"api_calls": 1, "ai_tokens": 2_500}
    )
    assert isinstance(decision, Reject)
    assert decision.details["metric"] == "ai_tokens"
    assert "AI token quota exceeded" in decision.message
    assert "99,000 of 100,000 used, this request needs 2,500" in decision.message


def test_api_calls_checked_before_tokens():
    decision = run(
        used={"api_calls": 1_000, "ai_tokens": 100_000}, requested={"api_calls": 1, "ai_tokens": 1}
    )
    assert isinstance(decision, Reject) and decision.details["metric"] == "api_calls"


@pytest.mark.parametrize("status", ["past_due", "unpaid", "incomplete"])
def test_unpaid_subscription_is_402_even_with_quota_left(status):
    decision = run(plan_id="pro", limits=PRO, used={}, status=status)
    assert isinstance(decision, Reject)
    assert (decision.status_code, decision.code) == (402, "payment_required")
    assert decision.details["subscription_status"] == status


@pytest.mark.parametrize("status", [None, "active", "trialing"])
def test_healthy_subscription_statuses_allow(status):
    assert isinstance(run(plan_id="pro", limits=PRO, status=status), Allow)


def test_zero_token_request_still_costs_one_api_call():
    assert isinstance(
        run(used={"ai_tokens": 100_000}, requested={"api_calls": 1, "ai_tokens": 0}), Allow
    )
