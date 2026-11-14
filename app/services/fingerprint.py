"""Request fingerprint used to detect an idempotency key reused with a different request."""

import hashlib
import json
from typing import Any


def request_fingerprint(method: str, path: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{method.upper()} {path}\n{canonical}".encode()).hexdigest()
