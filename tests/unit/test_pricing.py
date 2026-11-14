"""Exact integer cost math (C2, C3, P5). Numbers mirror docs/pricing.md worked examples."""

import itertools

import pytest

from app.services import pricing
from app.services.pricing import PriceBook, TokenUsage, format_usd, half_up_div, quote

PRICES = PriceBook(
    version="2026-11-01",
    per_call=500,
    input_per_million=1_250_000,
    cached_input_per_million=312_500,
    output_per_million=10_000_000,
)
NO_TOKENS = TokenUsage()


def test_pinned_config_file_matches_documented_constants():
    """C3: the shipped config/pricing.toml is exactly the price book the docs describe."""
    pricing.get_price_book.cache_clear()
    assert pricing.get_price_book() == PRICES


def test_e1_cached_input_reasoning_and_output():
    cost = quote(0, TokenUsage(input=10_000, cached=4_000, output=2_000, reasoning=500), PRICES)
    lines = {name: line.amount_microusd for name, line in cost.lines.items()}
    assert lines == {
        "api_calls": 0,
        "input_tokens": 7_500,  # 6,000 fresh x $1.25/M
        "cached_input_tokens": 1_250,  # 4,000 cached x $0.3125/M
        "output_tokens": 25_000,  # (2,000 + 500 reasoning) x $10/M
    }
    assert cost.total_microusd == 33_750
    assert cost.rounding_adjustment_microusd == 0
    assert format_usd(cost.total_microusd) == "$0.03"


def test_e1_rejects_the_two_classic_mistakes():
    tokens = TokenUsage(input=10_000, cached=4_000, output=2_000, reasoning=500)
    correct = quote(0, tokens, PRICES).total_microusd
    naive_one_rate = half_up_div((10_000 + 4_000 + 2_000 + 500) * 1_250_000, 1_000_000)
    cached_counted_twice = half_up_div(
        10_000 * 1_250_000 + 4_000 * 312_500 + 2_500 * 10_000_000, 1_000_000
    )
    assert (naive_one_rate, cached_counted_twice) == (20_625, 38_750)
    assert correct not in (naive_one_rate, cached_counted_twice)


def test_e2_reasoning_only_is_billed_at_output_rate():
    assert quote(0, TokenUsage(reasoning=1_000), PRICES).total_microusd == 10_000


def test_e3_all_cached_input():
    cost = quote(0, TokenUsage(input=8_000, cached=8_000), PRICES)
    assert cost.lines["input_tokens"].quantity == 0
    assert cost.total_microusd == 2_500


def test_e4_api_calls():
    cost = quote(1_000, NO_TOKENS, PRICES)
    assert cost.total_microusd == 500_000
    assert format_usd(cost.total_microusd) == "$0.50"


def test_period_rounds_once_not_per_request():
    """1 fresh input token costs 1.25 µ$. Three requests: per-request rounding would give
    1+1+1 = 3; the period total divides the summed numerators once: 3.75 -> 4."""
    one = quote(0, TokenUsage(input=1), PRICES).total_microusd
    period = quote(0, TokenUsage(input=3), PRICES).total_microusd
    assert (one, 3 * one, period) == (1, 3, 4)


def test_lines_plus_adjustment_always_equal_total():
    values = (0, 1, 3, 7, 999, 1_000_001)
    for inp, cached, out, reasoning in itertools.product(values, repeat=4):
        if cached > inp:
            continue
        cost = quote(5, TokenUsage(inp, cached, out, reasoning), PRICES)
        lines = sum(line.amount_microusd for line in cost.lines.values())
        assert lines + cost.rounding_adjustment_microusd == cost.total_microusd
        assert abs(cost.rounding_adjustment_microusd) <= 1


def test_large_period_stays_exact():
    """Python ints do not overflow; 5M tokens of each kind is still exact to the µ$."""
    tokens = TokenUsage(input=5_000_000, cached=2_000_000, output=5_000_000, reasoning=5_000_000)
    assert quote(50_000, tokens, PRICES).total_microusd == (
        50_000 * 500
        + 3_000_000 * 1_250_000 // 1_000_000
        + 2_000_000 * 312_500 // 1_000_000
        + 10_000_000 * 10
    )


@pytest.mark.parametrize(
    "n,d,expected",
    [
        (0, 10, 0),
        (4, 10, 0),
        (5, 10, 1),
        (6, 10, 1),
        (15, 10, 2),
        (25, 10, 3),
        (3_750_000, 10**6, 4),
    ],
)
def test_half_up_div(n, d, expected):
    assert half_up_div(n, d) == expected


@pytest.mark.parametrize("bad", [(-1, 10), (1, 0), (1.5, 10), (True, 10)])
def test_half_up_div_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        half_up_div(*bad)


@pytest.mark.parametrize(
    "micro,display",
    [
        (0, "$0.00"),
        (4_999, "$0.00"),
        (5_000, "$0.01"),
        (33_750, "$0.03"),
        (47_750, "$0.05"),
        (1_234_567_890, "$1,234.57"),
    ],
)
def test_format_usd_rounds_to_cents_once(micro, display):
    assert format_usd(micro) == display


def test_token_usage_rejects_cached_above_input_and_floats():
    with pytest.raises(ValueError):
        TokenUsage(input=1, cached=2)
    with pytest.raises(ValueError):
        TokenUsage(input=1.0)


def _raw(**overrides):
    raw = {
        "version": "v1",
        "api_calls": {"price_per_call_microusd": 500},
        "ai_tokens": {
            "input_per_million_microusd": 1_250_000,
            "cached_input_per_million_microusd": 312_500,
            "output_per_million_microusd": 10_000_000,
        },
    }
    for key, value in overrides.items():
        section, field = key.split("__")
        raw[section][field] = value
    return raw


@pytest.mark.parametrize(
    "overrides",
    [
        {"api_calls__price_per_call_microusd": 0.0005},  # float money
        {"api_calls__price_per_call_microusd": -1},
        {"ai_tokens__cached_input_per_million_microusd": 2_000_000},  # cached > input
        {"ai_tokens__output_per_million_microusd": "10"},
    ],
)
def test_invalid_price_books_are_rejected(overrides):
    with pytest.raises(pricing.PricingConfigError):
        pricing.parse_price_book(_raw(**overrides))


def test_missing_key_is_rejected():
    raw = _raw()
    del raw["ai_tokens"]["output_per_million_microusd"]
    with pytest.raises(pricing.PricingConfigError):
        pricing.parse_price_book(raw)
