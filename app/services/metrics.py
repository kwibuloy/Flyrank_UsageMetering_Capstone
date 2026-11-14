"""The two metered usage types. Order matters: it is the lock order and the check order."""

API_CALLS = "api_calls"
AI_TOKENS = "ai_tokens"

METRICS: tuple[str, ...] = (AI_TOKENS, API_CALLS)
CHECK_ORDER: tuple[str, ...] = (API_CALLS, AI_TOKENS)

LABELS = {API_CALLS: "API call", AI_TOKENS: "AI token"}
