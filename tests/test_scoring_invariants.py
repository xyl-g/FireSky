"""Scoring invariants of the firesky empirical layer."""

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from firesky.forecast_characteristics import fire_cloud_quality_level
from firesky.forward_field_scoring import (
    _normalized_active_weights,
    _unified_event_window_weights,
    calibrated_quality_response,
    duration_factor,
)
from firesky.scoring import estimated_cloud_base_m


def test_unified_window_short_single_run_keeps_the_active_mean():
    widths = np.asarray([3.0, 4.0, 5.0, 6.0])
    values = np.asarray([0.80, 0.80, 0.80, 0.10])
    active = np.ones(4, dtype=bool)

    weights, window_minutes = _unified_event_window_weights(values, active, widths)
    assert weights == pytest.approx(_normalized_active_weights(active, widths))
    assert window_minutes == pytest.approx(18.0)
    assert float(weights @ values) == pytest.approx(
        float(_normalized_active_weights(active, widths) @ values)
    )


def test_unified_window_scores_the_best_contiguous_twenty_minutes():
    widths = np.asarray([3.0, 4.0, 5.0, 6.0, 7.0])
    values = np.asarray([0.80, 0.80, 0.80, 0.80, 0.10])
    weights, window_minutes = _unified_event_window_weights(values, np.ones(5, dtype=bool), widths)
    assert window_minutes == pytest.approx(20.0)
    # Best window [0, 20]: four strong samples plus a partial 2-minute weak tail.
    assert float(weights @ values) == pytest.approx(0.73)


def test_fragmented_events_are_continuous_across_the_window_boundary():
    def pre_response(total_minutes: float) -> float:
        count = int(round(total_minutes / 5.0))
        active = np.zeros(20, dtype=bool)
        active[: count // 2] = True
        active[count // 2 + 1 : count // 2 + 1 + count - count // 2] = True
        widths = np.full(20, 5.0)
        values = np.where(active, 0.5, 0.0)
        weights, _ = _unified_event_window_weights(values, active, widths)
        duration = float(np.sum(np.where(active, widths, 0.0)))
        return float(weights @ values) * duration_factor(duration)

    below = pre_response(19.0)
    at = pre_response(20.0)
    above = pre_response(21.0)
    assert below == pytest.approx(at, abs=0.05)
    assert at == pytest.approx(above, abs=0.05)
    assert above >= below - 1e-9


def test_estimated_cloud_base_uses_surface_lcl_when_available():
    def feature(hpa=850, gh=1500.0, t=None, td=None, dist=30.0):
        return SimpleNamespace(
            pressure_hpa=hpa,
            geopotential_height_m=gh,
            surface_temperature_k=t,
            surface_dewpoint_k=td,
            distance_km=dist,
        )

    stratus = estimated_cloud_base_m(feature(t=290.0, td=288.5), terrain_asl_m=10.0)
    assert stratus == pytest.approx(10.0 + 125.0 * 1.5)
    assert estimated_cloud_base_m(feature(t=295.0, td=283.0), terrain_asl_m=10.0) == 1050.0
    assert estimated_cloud_base_m(feature(t=None, td=None), terrain_asl_m=10.0) == 1050.0
    assert estimated_cloud_base_m(feature(t=290.0, td=288.5, dist=400.0), terrain_asl_m=10.0) == 1050.0
    assert estimated_cloud_base_m(feature(t=290.0, td=288.5)) == 1050.0


def test_fire_cloud_quality_level_survives_nan():
    assert fire_cloud_quality_level(float("nan")) == "无明显火烧云"
    assert fire_cloud_quality_level(0.0) == "无明显火烧云"
    assert fire_cloud_quality_level(2.5) == "世纪大烧"


def test_quality_response_is_monotonic_and_saturates_at_max():
    values = np.asarray([0.0, 0.10, 0.40, 0.80, 2.50])
    response = calibrated_quality_response(values)
    assert response[0] == 0.0
    assert np.all(np.diff(response) >= 0.0)
    assert response[-1] == 2.5
