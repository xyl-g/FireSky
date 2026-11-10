"""Calendar helpers for user-facing forecast dates."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from firesky import rust_astronomy


CHINA_TIME_ZONE = ZoneInfo("Asia/Shanghai")


def china_today(now: datetime | None = None) -> date:
    """Return the current calendar date used by the China-facing API and CLI."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if rust_astronomy.enabled():
        return date.fromisoformat(rust_astronomy.time_operation(
            "china_date", time=rust_astronomy._time(current),
        )["value"])
    return current.astimezone(CHINA_TIME_ZONE).date()
