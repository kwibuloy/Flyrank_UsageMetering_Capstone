"""Billing periods: UTC calendar months, [start, end). See docs/quotas.md."""

import math
from datetime import UTC, datetime


def period_bounds(now: datetime) -> tuple[datetime, datetime]:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=UTC)
    return start, end


def seconds_until(end: datetime, now: datetime) -> int:
    """Whole seconds until `end`, rounded up, never below 1 (a valid Retry-After)."""
    return max(1, math.ceil((end - now).total_seconds()))


def iso_z(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
