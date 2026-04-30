from __future__ import annotations

from datetime import UTC, datetime, timedelta

SCHEDULE_S = (15, 60, 300, 900, 3600)


def next_retry_at(attempt_n: int, *, now: datetime | None = None) -> str | None:
    """Return ISO8601 retry time for attempt_n (1-based), else None terminal."""
    if attempt_n >= len(SCHEDULE_S):
        return None
    base = now or datetime.now(UTC)
    return (base + timedelta(seconds=SCHEDULE_S[attempt_n])).isoformat()

