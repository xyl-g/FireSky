"""Contract and routing tests for the multi-scan native nowcast adjustment."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from firesky.geometry import GeoPoint
from firesky.models import ForecastRequest


TARGET_TIME = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)
DISPLAY_TIME = datetime(2026, 11, 7, 9, 5, tzinfo=timezone.utc)
EARLIER_TIME = datetime(2026, 11, 7, 8, 55, tzinfo=timezone.utc)


def _retrieval_context(valid_time: datetime) -> dict:
    return {
        "cycle_time_utc": datetime(2026, 11, 7, tzinfo=timezone.utc),
        "left_forecast_hour": 8 if valid_time < DISPLAY_TIME else 9,
        "right_forecast_hour": 9,
        "fraction": 0.0 if valid_time < DISPLAY_TIME else 0.0,
        "left_cache_path": Path("/tmp/firesky-left"),
        "right_cache_path": Path("/tmp/firesky-right"),
        "local_radius_km": 35.0,
        "domain_hash": "fixture-domain",
        "resolution": "0p25",
    }


def _native_frame(path: Path, valid_time: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        native_manifest_path=path,
        valid_time_utc=valid_time,
        bands={"B07": object(), "B13": object(), "B15": object()},
        source="fixture-hsd",
        range_bytes=4096,
        range_blocks=7,
    )


def _request() -> ForecastRequest:
    return ForecastRequest(
        target_date=TARGET_TIME.date(),
        timezone_name="Asia/Shanghai",
        point=GeoPoint(30.2741, 120.1551),
        radius_km=900.0,
        gfs_cycle_override=datetime(2026, 11, 7, tzinfo=timezone.utc),
    )


def _cloud_column(valid_time: datetime) -> dict:
    timestamp = valid_time.isoformat()
    return {
        "schema_version": 4,
        "algorithm": "ahi-calibrated-cloud-column-v4",
        "valid_time_utc": timestamp,
        "lat": 30.2741,
        "lon": 120.1551,
        "distance_km": 0.0,
        "bearing_deg": 25.0,
        "base_p10_asl_m": 1200.0,
        "base_p50_asl_m": 1300.0,
        "base_p90_asl_m": 1400.0,
        "base_p10_agl_m": 1192.0,
        "base_p50_agl_m": 1292.0,
        "base_p90_agl_m": 1392.0,
        "top_asl_m": 1800.0,
        "top_agl_m": 1792.0,
        "terrain_asl_m": 8.0,
        "phase": "water",
        "cloud_type": "low",
        "optical_depth": 1.5,
        "cloud_probability_by_height": {"1450.0": 0.6},
        "horizontal_cloud_probability": 0.7,
        "low_cloud_state": "detected",
        "retrieval_quality": "high",
        "source": "fixture-hsd",
        "vertical_prior_identity": "gfs-prior:fixture",
        "vertical_prior_source": "gfs-interpolated-vertical-prior",
        "base_source": "fixture",
        "motion_dx_px_per_hour": 3.5,
        "motion_dy_px_per_hour": -2.0,
    }


def _feature(valid_time: datetime) -> dict:
    del valid_time
    return {
        "lat": 30.2741,
        "lon": 120.1551,
        "distance_km": 0.0,
        "bearing_deg": 25.0,
        "pressure_hpa": 850,
        "geopotential_height_m": 1450.0,
        "rh_percent": 80.0,
        "temperature_k": 283.0,
        "omega_pa_s": None,
        "u_ms": None,
        "v_ms": None,
        "cloud_fraction_percent": 40.0,
        "horizontal_cloud_probability": 0.7,
        "cloud_probability_given_cloud": 0.8,
    }


def _nowcast_payload(valid_time: datetime = EARLIER_TIME) -> dict:
    timestamp = valid_time.isoformat()
    column_set = {
        "schema_version": 4,
        "valid_time_utc": timestamp,
        "source": "fixture-hsd",
        "vertical_prior_identity": "gfs-prior:fixture",
        "retrieval_quality": "high",
        "low_cloud_state": "detected",
        "columns": [_cloud_column(valid_time)],
        "motion_dx_px_per_hour": 3.5,
        "motion_dy_px_per_hour": -2.0,
    }
    return {
        "contract": "rust-desktop-nowcast-adjustment-v1",
        "runtime_python_used": False,
        "target_time_utc": TARGET_TIME.isoformat(),
        "nowcast_algorithm": "satellite-nowcast-2026.11.04.87-hsd-only-multi-observation-aerosol-ground-touchdown-coverage-gate-v4",
        "source": "noaa-himawari9-hsd-calibrated+gfs-prior",
        "image_valid_time_utc": DISPLAY_TIME.isoformat(),
        "image_valid_time_local": "2026-11-07T17:05:00+08:00",
        "image_channel": "hsd-b07+b13+b15",
        "image_area": "Himawari HSD segmented footprint",
        "near_cloud_ratio": 0.4,
        "far_cloud_ratio": 0.5,
        "trend_score": 0.5,
        "visibility_penalty": 0.2,
        "target_bonus": 0.8,
        "confidence": 0.7,
        "summary": ["fixture"],
        "motion_dx_px_per_hour": 3.5,
        "motion_dy_px_per_hour": -2.0,
        "motion_correlation": 0.8,
        "projection_hours": 0.0,
        "satellite_source_times_utc": [timestamp],
        "satellite_motion_age_minutes": 10.0,
        "satellite_motion_quality": "strong",
        "satellite_advection_horizon_hours": 0.0,
        "infrared_near_cloud_ratio": 0.4,
        "infrared_far_cloud_ratio": 0.5,
        "visible_near_cloud_ratio": None,
        "visible_far_cloud_ratio": None,
        "channel_fusion_weight_visible": 0.0,
        "local_cloud_ratio": 0.3,
        "horizon_cloud_ratio": 0.2,
        "thin_cloud_ratio": 0.1,
        "thick_cloud_ratio": 0.3,
        "cloud_texture_score": 0.25,
        "cloud_uniformity_score": 0.75,
        "cloud_blanket_score": 0.4,
        "horizon_clearance_score": 0.8,
        "composition_score": 0.6,
        "observed_features_by_offset": {"0": [_feature(valid_time)]},
        "observed_feature_times_by_offset": {"0": timestamp},
        "cloud_column_schema_version": 4,
        "cloud_column_sets": [column_set],
        "vertical_prior_identity": "gfs-prior:fixture",
        "vertical_prior_source": "gfs-interpolated-vertical-prior",
        "satellite_retrieval_quality": "high",
        "satellite_low_cloud_state": "detected",
        "satellite_range_bytes": 4096,
        "satellite_range_blocks": 7,
        "nowcast_point_lat": 30.2741,
        "nowcast_point_lon": 120.1551,
        "nowcast_cache_identity": "fixture-cache",
        "satellite_vertical_profile": {
            "valid_time_utc": DISPLAY_TIME.isoformat(),
            "sample_count": 1,
            "usable_sample_count": 1,
            "samples": [],
        },
        "calibrated_sample_coverage_ratio": 0.5,
    }


def _provider():
    def provider(valid_time):
        return _retrieval_context(valid_time)

    provider.native_retrieval_context = provider
    return provider


def _adjustment_inputs(tmp_path: Path):
    return {
        "target_time_utc": TARGET_TIME,
        "timezone_name": "Asia/Shanghai",
        "point": GeoPoint(30.2741, 120.1551),
        "azimuth_deg": 25.0,
        "radius_km": 900.0,
        "display_frame": _native_frame(tmp_path / "display.json", DISPLAY_TIME),
        "frames": (
            _native_frame(tmp_path / "earlier.json", EARLIER_TIME),
            _native_frame(tmp_path / "display.json", DISPLAY_TIME),
        ),
        "retrieval_context_provider": _provider(),
        "source_times_utc": (EARLIER_TIME, DISPLAY_TIME),
        "motion": None,
        "advection": None,
        "single_historical_observation": False,
    }


def _fake_engine(monkeypatch, value_mutation=None, receipt_mutation=None):
    import firesky.rust_desktop_data as native_data

    calls = []

    def fake_call(method, params, *, timeout_seconds=None):
        assert method == "desktop_nowcast_adjustment"
        assert timeout_seconds is None
        calls.append(params)
        request_bytes = Path(params["request_path"]).read_bytes()
        calls[-1]["request"] = json.loads(request_bytes)
        value = _nowcast_payload()
        if value_mutation is not None:
            value_mutation(value)
        encoded = json.dumps(value, separators=(",", ":")).encode()
        output_path = Path(params["output_path"])
        output_path.write_bytes(encoded)
        receipt = {
            "contract": "rust-desktop-nowcast-adjustment-file-v1",
            "runtime_python_used": False,
            "output_path": str(output_path),
            "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
        }
        if receipt_mutation is not None:
            receipt_mutation(receipt)
        return receipt

    monkeypatch.setattr(native_data, "call_engine", fake_call)
    return calls


def test_nowcast_adjustment_as_model_reconstructs_complete_payload():
    from firesky.models import GridCellFeature, NowcastAdjustment
    from firesky.rust_desktop_data import nowcast_adjustment_as_model

    adjustment = nowcast_adjustment_as_model(_nowcast_payload())

    assert isinstance(adjustment, NowcastAdjustment)
    assert adjustment.image_valid_time_utc == DISPLAY_TIME
    assert adjustment.image_valid_time_local.utcoffset().total_seconds() == 8 * 3600
    assert adjustment.satellite_source_times_utc == [EARLIER_TIME]
    assert adjustment.calibrated_sample_coverage_ratio == 0.5
    assert adjustment.satellite_vertical_profile["valid_time_utc"] == DISPLAY_TIME.isoformat()
    assert set(adjustment.observed_features_by_offset) == {0}
    assert isinstance(adjustment.observed_features_by_offset[0][0], GridCellFeature)
    assert adjustment.observed_feature_times_by_offset[0] == EARLIER_TIME
    column = adjustment.cloud_column_sets[0]["columns"][0]
    assert column["base_p10_agl_m"] == 1192.0
    assert column["base_p50_agl_m"] == 1292.0
    assert column["base_p90_agl_m"] == 1392.0
    assert column["top_agl_m"] == 1792.0


@pytest.mark.parametrize(
    "mutation,message",
    [
        ("receipt_contract", "receipt is invalid"),
        ("bytes", "output identity is invalid"),
        ("sha256", "output identity is invalid"),
        ("request_sha256", "request identity is invalid"),
    ],
)
def test_nowcast_adjustment_rejects_tampered_receipt(
    monkeypatch, tmp_path, mutation, message
):
    from firesky.rust_desktop_data import nowcast_adjustment

    def mutate_receipt(receipt):
        if mutation == "receipt_contract":
            receipt["contract"] = "wrong-file-contract"
        elif mutation == "bytes":
            receipt["bytes"] += 1
        elif mutation == "sha256":
            receipt["sha256"] = "0" * 64
        elif mutation == "request_sha256":
            receipt["request_sha256"] = "0" * 64

    _fake_engine(monkeypatch, receipt_mutation=mutate_receipt)
    with pytest.raises(RuntimeError, match=message):
        nowcast_adjustment(**_adjustment_inputs(tmp_path))


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_field",
        "contract",
        "nowcast_algorithm",
        "source",
        "image_channel",
        "cloud_column_schema_version",
    ],
)
def test_nowcast_adjustment_rejects_payload_identity(monkeypatch, tmp_path, mutation):
    from firesky.rust_desktop_data import nowcast_adjustment

    def mutate_value(value):
        if mutation == "missing_field":
            value.pop("summary")
        elif mutation == "contract":
            value["contract"] = "wrong-contract"
        elif mutation == "nowcast_algorithm":
            value["nowcast_algorithm"] = "obsolete"
        elif mutation == "source":
            value["source"] = "python-display-image"
        elif mutation == "image_channel":
            value["image_channel"] = "visible"
        elif mutation == "cloud_column_schema_version":
            value["cloud_column_schema_version"] = 3

    _fake_engine(monkeypatch, value_mutation=mutate_value)
    with pytest.raises(RuntimeError, match="response identity is invalid"):
        nowcast_adjustment(**_adjustment_inputs(tmp_path))


def test_out_of_window_scan_fails_closed_without_python_retrieval(monkeypatch):
    import firesky.satellite as satellite

    stale = SimpleNamespace(
        valid_time_utc=datetime(2026, 11, 7, 7, 30, tzinfo=timezone.utc),
        source="fixture",
        range_bytes=0,
        range_blocks=0,
        native_manifest_path=None,
        bands={"B07": object(), "B13": object(), "B15": object()},
    )
    monkeypatch.setattr(
        satellite,
        "calibrated_satellite_cloud_retrieval",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("stale scan called calibrated retrieval")
        ),
    )

    value = satellite.calibrated_only_nowcast_adjustment(
        _request(),
        TARGET_TIME,
        25.0,
        stale,
        lambda _point, _time: None,
        source_times_utc=(stale.valid_time_utc,),
        observation_frames=(stale,),
    )

    assert value is None


def test_native_adjustment_avoids_per_frame_python_retrieval(monkeypatch, tmp_path):
    import firesky.satellite as satellite

    display = _native_frame(tmp_path / "display.json", DISPLAY_TIME)
    earlier = _native_frame(tmp_path / "earlier.json", EARLIER_TIME)
    monkeypatch.setenv("FIRESKY_HSD_BACKEND", "rust")
    calls = _fake_engine(monkeypatch)
    monkeypatch.setattr(
        satellite,
        "calibrated_satellite_cloud_retrieval",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("native adjustment called per-frame Python retrieval")
        ),
    )

    adjustment = satellite.calibrated_only_nowcast_adjustment(
        _request(),
        TARGET_TIME,
        25.0,
        display,
        _provider(),
        source_times_utc=(EARLIER_TIME, DISPLAY_TIME),
        observation_frames=(earlier, display),
    )

    assert adjustment is not None
    assert len(calls) == 1
    request = calls[0]["request"]
    assert len(request["frames"]) == 2


def test_missing_native_context_uses_python_retrieval(monkeypatch, tmp_path):
    import firesky.satellite as satellite

    frame = _native_frame(tmp_path / "frame.json", EARLIER_TIME)
    monkeypatch.setenv("FIRESKY_HSD_BACKEND", "rust")
    calls = []

    def python_retrieval(*_args, **_kwargs):
        calls.append(True)
        return [], [], [], {}

    monkeypatch.setattr(
        satellite,
        "calibrated_satellite_cloud_retrieval",
        python_retrieval,
    )

    value = satellite.calibrated_only_nowcast_adjustment(
        _request(),
        TARGET_TIME,
        25.0,
        frame,
        lambda _point, _time: None,
        source_times_utc=(EARLIER_TIME,),
        observation_frames=(frame,),
    )

    assert value is None
    assert calls == [True]


def test_native_contract_failure_is_not_swallowed(monkeypatch, tmp_path):
    import firesky.satellite as satellite

    frame = _native_frame(tmp_path / "frame.json", EARLIER_TIME)
    monkeypatch.setenv("FIRESKY_HSD_BACKEND", "rust")
    _fake_engine(
        monkeypatch,
        receipt_mutation=lambda receipt: receipt.update(contract="wrong-contract"),
    )
    monkeypatch.setattr(
        satellite,
        "calibrated_satellite_cloud_retrieval",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("contract failure fell back to Python retrieval")
        ),
    )

    with pytest.raises(RuntimeError, match="receipt is invalid"):
        satellite.calibrated_only_nowcast_adjustment(
            _request(),
            TARGET_TIME,
            25.0,
            frame,
            _provider(),
            source_times_utc=(EARLIER_TIME,),
            observation_frames=(frame,),
        )


def test_reused_nowcast_diagnostics_leave_assessment_unchanged():
    from firesky.models import TimeStepAssessment
    from firesky.rust_desktop_data import nowcast_adjustment_as_model
    from firesky.service import apply_nowcast_observation_constraints

    nowcast = nowcast_adjustment_as_model(_nowcast_payload())
    assessment = TimeStepAssessment(
        forecast_hour=8,
        valid_time_utc=EARLIER_TIME,
        valid_time_local=EARLIER_TIME,
        solar_elevation_deg=-3.0,
        solar_azimuth_deg=25.0,
        burn_score=0.6,
        intensity_score=0.5,
        extent_score=0.4,
        vividness_score=0.55,
        visibility_score=0.7,
        low_cloud_penalty=0.1,
        candidate_count=1,
        burning_cloud_count=1,
        top_candidate=None,
        scene_scores={"clear": 0.5},
        forward_view_cloud_field_amount=0.3,
        forward_burnable_cloud_field_amount=0.2,
        observer_local_cloud_field_amount=0.25,
    )

    diagnosed_assessment, diagnosed = apply_nowcast_observation_constraints(
        assessment, nowcast, EARLIER_TIME, diagnose_nowcast=True
    )
    reused_assessment, reused = apply_nowcast_observation_constraints(
        assessment, nowcast, EARLIER_TIME, diagnose_nowcast=False
    )

    assert reused_assessment == diagnosed_assessment
    assert diagnosed is not nowcast
    assert reused is nowcast
