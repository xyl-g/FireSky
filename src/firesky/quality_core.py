"""Shared, explainable quality corrections used by point and regional scoring."""

from __future__ import annotations

from firesky.analysis_constants import DEFAULT_ANALYSIS_RADIUS_KM


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def duration_quality_factor(duration_minutes: float) -> float:
    """Return a bounded, signed-quality factor for the burn window."""
    duration = max(0.0, float(duration_minutes))
    if duration < 2.0:
        return 0.50 + 0.25 * duration / 2.0
    if duration < 5.0:
        return 0.75 + 0.15 * (duration - 2.0) / 3.0
    if duration < 12.0:
        return 0.90 + 0.10 * (duration - 5.0) / 7.0
    return clamp(1.0 + 0.08 * (duration - 12.0) / 12.0, high=1.12)


__all__ = [
    "DEFAULT_ANALYSIS_RADIUS_KM",
    "duration_quality_factor",
]
