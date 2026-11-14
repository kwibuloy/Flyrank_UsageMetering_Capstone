"""Request models: validation at the boundary. Bad input becomes a 422, never a 500."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_TOKENS_PER_REQUEST = 1_000_000

# strict=True: reject "5", 5.0 and true; only real JSON integers are token counts.
TokenCount = Annotated[int, Field(ge=0, le=MAX_TOKENS_PER_REQUEST, strict=True)]


class GenerateRequest(BaseModel):
    """Simulated AI usage for the dummy billable endpoint."""

    model_config = ConfigDict(extra="forbid")

    input_tokens: TokenCount = 0
    cached_input_tokens: TokenCount = 0
    output_tokens: TokenCount = 0
    reasoning_tokens: TokenCount = 0

    @model_validator(mode="after")
    def check_token_rules(self) -> "GenerateRequest":
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError(
                "cached_input_tokens cannot exceed input_tokens "
                "(cached tokens are a subset of input tokens)"
            )
        total = self.input_tokens + self.output_tokens + self.reasoning_tokens
        if total > MAX_TOKENS_PER_REQUEST:
            raise ValueError(f"total tokens per request cannot exceed {MAX_TOKENS_PER_REQUEST:,}")
        return self
