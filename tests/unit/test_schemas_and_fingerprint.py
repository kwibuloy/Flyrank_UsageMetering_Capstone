import pytest
from pydantic import ValidationError

from app.api.schemas import GenerateRequest
from app.services.fingerprint import request_fingerprint
from app.services.metering import UsageRequest


@pytest.mark.parametrize(
    "body",
    [
        {"input_tokens": -1},
        {"input_tokens": "5"},
        {"input_tokens": 5.0},
        {"input_tokens": True},
        {"input_tokens": 1_000_001},
        {"input_tokens": 10, "cached_input_tokens": 11},
        {"input_tokens": 600_000, "output_tokens": 400_001},
        {"unknown_field": 1},
    ],
)
def test_invalid_bodies_rejected(body):
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate(body)


def test_defaults_are_zero():
    assert GenerateRequest.model_validate({}).model_dump() == {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
    }


def test_cached_tokens_are_not_counted_twice_toward_quota():
    usage = UsageRequest(
        input_tokens=10_000, cached_input_tokens=4_000, output_tokens=2_000, reasoning_tokens=500
    )
    assert usage.quantities() == {"api_calls": 1, "ai_tokens": 12_500}


def test_fingerprint_ignores_key_order_but_not_values():
    a = request_fingerprint("POST", "/generate", {"input_tokens": 1, "output_tokens": 2})
    b = request_fingerprint("post", "/generate", {"output_tokens": 2, "input_tokens": 1})
    c = request_fingerprint("POST", "/generate", {"input_tokens": 1, "output_tokens": 3})
    assert a == b
    assert a != c
