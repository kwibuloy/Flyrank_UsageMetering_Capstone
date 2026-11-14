"""Cost math. Pure: no I/O except loading the pinned price book. Spec: docs/pricing.md.

Money is integer micro-USD everywhere. Token prices are per 1,000,000 tokens, so each
category contributes an exact integer numerator; the numerators are summed and divided
by 1,000,000 exactly once, rounding half-up. No floats, no Decimal.
"""

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

TOKENS_PER_PRICE_UNIT = 1_000_000
MICROUSD_PER_CENT = 10_000
REPO_ROOT = Path(__file__).resolve().parents[2]


class PricingConfigError(ValueError):
    pass


@dataclass(frozen=True)
class PriceBook:
    version: str
    per_call: int
    input_per_million: int
    cached_input_per_million: int
    output_per_million: int


@dataclass(frozen=True)
class TokenUsage:
    """`input` includes `cached`; `reasoning` is separate from `output`."""

    input: int = 0
    cached: int = 0
    output: int = 0
    reasoning: int = 0

    def __post_init__(self) -> None:
        for name in ("input", "cached", "output", "reasoning"):
            value = getattr(self, name)
            if not _is_int(value) or value < 0:
                raise ValueError(f"{name} must be a non-negative int")
        if self.cached > self.input:
            raise ValueError("cached tokens are a subset of input tokens")


@dataclass(frozen=True)
class Line:
    quantity: int
    rate_microusd: int
    rate_unit: str
    amount_microusd: int

    def to_json(self) -> dict[str, Any]:
        return {
            "quantity": self.quantity,
            "rate_microusd": self.rate_microusd,
            "rate_unit": self.rate_unit,
            "amount_microusd": self.amount_microusd,
        }


@dataclass(frozen=True)
class Cost:
    pricing_version: str
    lines: dict[str, Line]
    tokens_microusd: int
    rounding_adjustment_microusd: int
    total_microusd: int

    def to_json(self) -> dict[str, Any]:
        return {
            "pricing_version": self.pricing_version,
            "currency": "USD",
            "lines": {name: line.to_json() for name, line in self.lines.items()},
            "rounding_adjustment_microusd": self.rounding_adjustment_microusd,
            "total_microusd": self.total_microusd,
            "total_display": format_usd(self.total_microusd),
        }


def half_up_div(numerator: int, denominator: int) -> int:
    """Integer division rounding .5 up. Only defined for numerator >= 0, denominator > 0."""
    if not (_is_int(numerator) and _is_int(denominator)) or numerator < 0 or denominator <= 0:
        raise ValueError("half_up_div needs int numerator >= 0 and int denominator > 0")
    return (2 * numerator + denominator) // (2 * denominator)


def format_usd(microusd: int) -> str:
    """Display only: round to cents once, half-up."""
    cents = half_up_div(microusd, MICROUSD_PER_CENT)
    return f"${cents // 100:,}.{cents % 100:02d}"


def token_numerators(tokens: TokenUsage, prices: PriceBook) -> dict[str, int]:
    """Exact per-category cost x 1,000,000. Cached is billed once, at its own rate."""
    return {
        "input_tokens": (tokens.input - tokens.cached) * prices.input_per_million,
        "cached_input_tokens": tokens.cached * prices.cached_input_per_million,
        "output_tokens": (tokens.output + tokens.reasoning) * prices.output_per_million,
    }


def quote(api_calls: int, tokens: TokenUsage, prices: PriceBook) -> Cost:
    """Cost of `api_calls` calls plus `tokens`, for one request or a whole period.

    The token subtotal divides the summed numerators once. Each line is also rounded for
    display; `rounding_adjustment_microusd` makes lines + adjustment == total exactly.
    """
    if not _is_int(api_calls) or api_calls < 0:
        raise ValueError("api_calls must be a non-negative int")
    numerators = token_numerators(tokens, prices)
    quantities = {
        "input_tokens": tokens.input - tokens.cached,
        "cached_input_tokens": tokens.cached,
        "output_tokens": tokens.output + tokens.reasoning,
    }
    rates = {
        "input_tokens": prices.input_per_million,
        "cached_input_tokens": prices.cached_input_per_million,
        "output_tokens": prices.output_per_million,
    }
    lines = {"api_calls": Line(api_calls, prices.per_call, "per_call", api_calls * prices.per_call)}
    for name, numerator in numerators.items():
        lines[name] = Line(
            quantities[name],
            rates[name],
            "per_million_tokens",
            half_up_div(numerator, TOKENS_PER_PRICE_UNIT),
        )
    tokens_microusd = half_up_div(sum(numerators.values()), TOKENS_PER_PRICE_UNIT)
    token_lines = sum(lines[name].amount_microusd for name in numerators)
    return Cost(
        pricing_version=prices.version,
        lines=lines,
        tokens_microusd=tokens_microusd,
        rounding_adjustment_microusd=tokens_microusd - token_lines,
        total_microusd=lines["api_calls"].amount_microusd + tokens_microusd,
    )


def parse_price_book(raw: dict[str, Any]) -> PriceBook:
    try:
        version = raw["version"]
        calls = raw["api_calls"]
        tokens = raw["ai_tokens"]
        values = {
            "per_call": calls["price_per_call_microusd"],
            "input_per_million": tokens["input_per_million_microusd"],
            "cached_input_per_million": tokens["cached_input_per_million_microusd"],
            "output_per_million": tokens["output_per_million_microusd"],
        }
    except (KeyError, TypeError) as exc:
        raise PricingConfigError(f"pricing config is missing {exc}") from exc
    if not isinstance(version, str) or not version:
        raise PricingConfigError("version must be a non-empty string")
    for name, value in values.items():
        if not _is_int(value) or value < 0:
            raise PricingConfigError(f"{name} must be a non-negative integer (micro-USD)")
    if values["cached_input_per_million"] > values["input_per_million"]:
        raise PricingConfigError("cached input must not cost more than fresh input")
    return PriceBook(version=version, **values)


def load_price_book(path: Path) -> PriceBook:
    with path.open("rb") as f:
        return parse_price_book(tomllib.load(f))


@lru_cache
def get_price_book() -> PriceBook:
    from app.config import get_settings

    path = Path(get_settings().pricing_config_path)
    return load_price_book(path if path.is_absolute() else REPO_ROOT / path)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
