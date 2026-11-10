from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from firesky import rust_astronomy


ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ZONED_ISO_DATETIME_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)


def parse_iso_date(value: Any, field: str) -> date:
    if rust_astronomy.enabled():
        return rust_astronomy.parse(value, field, "parse_date")
    if not isinstance(value, str) or ISO_DATE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be an ISO calendar date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO calendar date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must be an ISO calendar date")
    return parsed


def parse_zoned_iso_datetime(value: Any, field: str) -> datetime:
    if rust_astronomy.enabled():
        return rust_astronomy.parse(value, field, "parse_zoned")
    if (
        not isinstance(value, str)
        or ZONED_ISO_DATETIME_PATTERN.fullmatch(value) is None
    ):
        raise ValueError(f"{field} must be a timezone-aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a timezone-aware ISO timestamp") from exc
    offset = parsed.utcoffset()
    if (
        parsed.tzinfo is None
        or offset is None
        or abs(offset) > timedelta(hours=14)
    ):
        raise ValueError(f"{field} must be a timezone-aware ISO timestamp")
    return parsed


def parse_utc_iso_datetime(value: Any, field: str) -> datetime:
    if rust_astronomy.enabled():
        return rust_astronomy.parse(value, field, "parse_utc")
    parsed = parse_zoned_iso_datetime(value, field)
    if not value.endswith(("Z", "+00:00")) or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must use UTC offset Z or +00:00")
    return parsed
