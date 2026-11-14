from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.services.periods import iso_z, period_bounds, seconds_until


@pytest.mark.parametrize(
    ("now", "start", "end"),
    [
        (
            datetime(2026, 11, 14, 10, 0, tzinfo=UTC),
            datetime(2026, 11, 1, tzinfo=UTC),
            datetime(2026, 12, 1, tzinfo=UTC),
        ),
        (
            datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC),
            datetime(2026, 12, 1, tzinfo=UTC),
            datetime(2027, 1, 1, tzinfo=UTC),
        ),
        (
            datetime(2028, 2, 29, 12, tzinfo=UTC),
            datetime(2028, 2, 1, tzinfo=UTC),
            datetime(2028, 3, 1, tzinfo=UTC),
        ),
        (
            datetime(2026, 11, 1, 0, 0, tzinfo=UTC),
            datetime(2026, 11, 1, tzinfo=UTC),
            datetime(2026, 12, 1, tzinfo=UTC),
        ),
    ],
)
def test_period_bounds_are_utc_calendar_months(now, start, end):
    assert period_bounds(now) == (start, end)


def test_period_uses_utc_not_local_time():
    # 2026-12-01 07:00 in UTC+8 is still November 30 in UTC.
    manila = timezone(timedelta(hours=8))
    start, _ = period_bounds(datetime(2026, 12, 1, 7, 0, tzinfo=manila))
    assert start == datetime(2026, 11, 1, tzinfo=UTC)


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        period_bounds(datetime(2026, 11, 14))


def test_seconds_until_rounds_up_and_is_at_least_one():
    end = datetime(2026, 12, 1, tzinfo=UTC)
    assert seconds_until(end, end - timedelta(seconds=10, milliseconds=1)) == 11
    assert seconds_until(end, end) == 1
    assert seconds_until(end, end + timedelta(seconds=5)) == 1


def test_iso_z():
    assert iso_z(datetime(2026, 12, 1, tzinfo=UTC)) == "2026-12-01T00:00:00Z"
