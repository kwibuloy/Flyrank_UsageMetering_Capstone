"""Pure threshold math and alert wording (stretch: usage alerts)."""

import pytest

from app.services.alerts import crossed_thresholds, message


@pytest.mark.parametrize(
    "before,after,limit,expected",
    [
        (799, 800, 1_000, [80]),  # exactly 80% fires
        (800, 801, 1_000, []),  # already past 80%: never again
        (798, 799, 1_000, []),  # 79.9% is not 80%
        (999, 1_000, 1_000, [100]),  # the last allowed call
        (0, 1_000, 1_000, [80, 100]),  # one request can cross both
        (0, 100_000, 100_000, [80, 100]),
        (79_999, 80_000, 100_000, [80]),
        (3, 4, 5, [80]),  # 80% of 5 is exactly 4: integer math, no float rounding
        (0, 3, 4, []),  # 80% of 4 is 3.2: 3 calls is not there yet
        (3, 4, 4, [80, 100]),
        (500, 500, 1_000, []),  # no change
        (0, 10, 0, []),  # zero limit never alerts
    ],
)
def test_crossed_thresholds(before, after, limit, expected):
    assert crossed_thresholds(before, after, limit) == expected


def test_messages_say_what_happened_and_what_to_do():
    assert message("acme", "free", "api_calls", 80, 800, 1_000) == (
        "acme has used 80% of its monthly API call quota (800 of 1,000) on the Free plan."
    )
    free_full = message("acme", "free", "ai_tokens", 100, 100_000, 100_000)
    assert "all of its monthly AI token quota (100,000 of 100,000)" in free_full
    assert "Upgrade to Pro" in free_full
    assert "429" in message("globex", "pro", "api_calls", 100, 50_000, 50_000)
