"""Differential tests for the assessment feature-row contract v2.

Contract ``hsd-assessment-feature-row-v2`` (27 columns): the legacy 20-column
native feature row plus the 2 m surface pair, the satellite footprint geometry
and ``horizontal_cloud_probability`` / ``cloud_probability_given_cloud``.
None travels as NaN, unknown never backfills as 0, and the legacy 20-column
path keeps its exact previous semantics.

Build the native library before running with FIRESKY_REQUIRE_RUST_TESTS=1
(``make native``); otherwise the native differential cases skip.
"""

import dataclasses
import math
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from firesky import native_core
from firesky.models import AerosolReading, DomainSnapshot, GridCellFeature
from firesky.scoring import (
    FeatureSpatialIndex,
    apparent_cloud_elevation_deg,
    assess_timestep,
    build_cloud_columns,
    candidate_signals,
    cloud_fraction_score,
    condensate_component_score,
    condensate_presence_score,
    estimated_cloud_base_m,
    explicit_cloud_presence_score,
    feature_blocker_opacity_score,
    feature_cloud_opacity_score,
    feature_cloud_presence_score,
)

ROOT = Path(__file__).resolve().parents[1]


def unavailable(message: str):
    if os.environ.get("FIRESKY_REQUIRE_RUST_TESTS") == "1":
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture
def assessment_native():
    library = native_core.native_library()
    if library is None or not native_core.native_available():
        unavailable("build the native library (make native)")
    if getattr(library, "firesky_feature_metrics_batch_v2", None) is None:
        unavailable(
            "native library lacks firesky_feature_metrics_batch_v2; rebuild with make native"
        )
    return library


def make_feature(**overrides) -> GridCellFeature:
    values = dict(
        lat=35.0,
        lon=138.0,
        distance_km=30.0,
        bearing_deg=90.0,
        pressure_hpa=850,
        geopotential_height_m=1500.0,
        rh_percent=70.0,
        temperature_k=290.0,
        omega_pa_s=None,
        u_ms=None,
        v_ms=None,
        cloud_fraction_percent=None,
        cloud_liquid_kg_kg=None,
        cloud_ice_kg_kg=None,
        source_lat=None,
        source_lon=None,
        source_distance_km=None,
        source_bearing_deg=None,
        surface_visibility_m=None,
        precip_rate_kg_m2_s=None,
        surface_temperature_k=None,
        surface_dewpoint_k=None,
        footprint_azimuth_width_deg=None,
        footprint_near_distance_km=None,
        footprint_far_distance_km=None,
        horizontal_cloud_probability=None,
        cloud_probability_given_cloud=None,
    )
    values.update(overrides)
    return GridCellFeature(**values)


def make_snapshot(features) -> DomainSnapshot:
    return DomainSnapshot(
        model_name="assessment-v2-fixture",
        forecast_hour=0,
        init_time_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        valid_time_utc=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        features=features,
        source_url="file://fixture",
        cache_path="fixture",
    )


V2_ONLY_FIELDS = (
    "surface_temperature_k",
    "surface_dewpoint_k",
    "footprint_azimuth_width_deg",
    "footprint_near_distance_km",
    "footprint_far_distance_km",
    "horizontal_cloud_probability",
    "cloud_probability_given_cloud",
)


def clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def assert_optional_equal(unpacked, packed_value, field):
    # None packs to NaN and NaN unpacks to None: unknown stays unknown, never 0.
    if packed_value is None or (isinstance(packed_value, float) and math.isnan(packed_value)):
        assert getattr(unpacked, field) is None, field
    else:
        assert getattr(unpacked, field) == packed_value, field


def metric_pairs(index_native, index_python):
    for name in (
        "_cloud_presence",
        "_explicit_presence",
        "_liquid_presence",
        "_ice_presence",
        "_cloud_opacity",
        "_blocker_opacity",
        "_lightpath_opacity",
        "_cloud_base",
        "_apparent_top",
    ):
        native_values = getattr(index_native, name)
        python_values = getattr(index_python, name)
        assert len(native_values) == len(python_values), name
        yield name, native_values, python_values


def test_assessment_feature_row_contract_names():
    assert native_core.FEATURE_ROW_V2_FIELD_COUNT == 27
    assert native_core.FEATURE_ROW_V2_CONTRACT == "hsd-assessment-feature-row-v2"
    assert len(native_core.FEATURE_ROW_V2_NAMES) == native_core.FEATURE_ROW_V2_FIELD_COUNT
    assert native_core.FEATURE_ROW_V2_NAMES[:20] == (
        "lat",
        "lon",
        "distance_km",
        "bearing_deg",
        "pressure_hpa",
        "geopotential_height_m",
        "rh_percent",
        "temperature_k",
        "omega_pa_s",
        "u_ms",
        "v_ms",
        "cloud_fraction_percent",
        "cloud_liquid_kg_kg",
        "cloud_ice_kg_kg",
        "source_lat",
        "source_lon",
        "source_distance_km",
        "source_bearing_deg",
        "surface_visibility_m",
        "precip_rate_kg_m2_s",
    )
    for offset, name in zip(
        (
            native_core.FEATURE_ROW_V2_SURFACE_TEMPERATURE_K,
            native_core.FEATURE_ROW_V2_SURFACE_DEWPOINT_K,
            native_core.FEATURE_ROW_V2_FOOTPRINT_AZIMUTH_WIDTH_DEG,
            native_core.FEATURE_ROW_V2_FOOTPRINT_NEAR_DISTANCE_KM,
            native_core.FEATURE_ROW_V2_FOOTPRINT_FAR_DISTANCE_KM,
            native_core.FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY,
            native_core.FEATURE_ROW_V2_CLOUD_PROBABILITY_GIVEN_CLOUD,
        ),
        (
            "surface_temperature_k",
            "surface_dewpoint_k",
            "footprint_azimuth_width_deg",
            "footprint_near_distance_km",
            "footprint_far_distance_km",
            "horizontal_cloud_probability",
            "cloud_probability_given_cloud",
        ),
    ):
        assert native_core.FEATURE_ROW_V2_NAMES[offset] == name


def test_feature_row_v2_roundtrip_at_none_nan_zero_one_boundaries():
    boundary_values = {
        "surface_temperature_k": [None, math.nan, 0.0, 1.0, 290.0],
        "surface_dewpoint_k": [None, math.nan, 0.0, 1.0, 288.5],
        "footprint_azimuth_width_deg": [None, math.nan, 0.0, 1.0, 2.4],
        "footprint_near_distance_km": [None, math.nan, 0.0, 1.0, 12.0],
        "footprint_far_distance_km": [None, math.nan, 0.0, 1.0, 48.0],
        "horizontal_cloud_probability": [None, math.nan, 0.0, 1.0, 0.8],
        "cloud_probability_given_cloud": [None, math.nan, 0.0, 1.0, 0.6],
    }
    for field, values in boundary_values.items():
        for value in values:
            feature = make_feature(**{field: value})
            row = native_core.feature_to_row_v2(feature)
            assert len(row) == native_core.FEATURE_ROW_V2_FIELD_COUNT
            unpacked = native_core.row_to_feature_v2(row, 0)
            assert_optional_equal(unpacked, value, field)
            packed = row[native_core.FEATURE_ROW_V2_NAMES.index(field)]
            if value is None or (isinstance(value, float) and math.isnan(value)):
                assert math.isnan(packed), field
            else:
                assert packed == value, field
                assert not math.isnan(packed), field


def test_feature_row_v2_roundtrip_keeps_the_whole_row():
    feature = make_feature(
        temperature_k=293.0,
        omega_pa_s=-0.02,
        u_ms=3.5,
        v_ms=-1.25,
        cloud_fraction_percent=55.0,
        cloud_liquid_kg_kg=1.0e-4,
        cloud_ice_kg_kg=2.0e-5,
        source_lat=34.5,
        source_lon=137.5,
        source_distance_km=8.0,
        source_bearing_deg=95.0,
        surface_visibility_m=18000.0,
        precip_rate_kg_m2_s=0.0,
        surface_temperature_k=290.0,
        surface_dewpoint_k=288.5,
        footprint_azimuth_width_deg=2.4,
        footprint_near_distance_km=12.0,
        footprint_far_distance_km=48.0,
        horizontal_cloud_probability=0.8,
        cloud_probability_given_cloud=0.6,
    )
    row = native_core.feature_to_row_v2(feature)
    assert len(row) == native_core.FEATURE_ROW_V2_FIELD_COUNT
    # Columns 0..19 keep the legacy pack semantics verbatim: an equivalent
    # feature without the v2-only columns packs to exactly the same prefix.
    legacy_twin = make_feature(**{
        key: getattr(feature, key)
        for key in (
            "temperature_k",
            "omega_pa_s",
            "u_ms",
            "v_ms",
            "cloud_fraction_percent",
            "cloud_liquid_kg_kg",
            "cloud_ice_kg_kg",
            "source_lat",
            "source_lon",
            "source_distance_km",
            "source_bearing_deg",
            "surface_visibility_m",
            "precip_rate_kg_m2_s",
        )
    })
    assert row[:20] == native_core.feature_to_row(legacy_twin)
    unpacked = native_core.row_to_feature_v2(row, 0)
    for name in (
        "lat",
        "lon",
        "distance_km",
        "bearing_deg",
        "pressure_hpa",
        "geopotential_height_m",
        "rh_percent",
        "temperature_k",
        "omega_pa_s",
        "u_ms",
        "v_ms",
        "cloud_fraction_percent",
        "cloud_liquid_kg_kg",
        "cloud_ice_kg_kg",
        "source_lat",
        "source_lon",
        "source_distance_km",
        "source_bearing_deg",
        "surface_visibility_m",
        "precip_rate_kg_m2_s",
        *V2_ONLY_FIELDS,
    ):
        assert getattr(unpacked, name) == getattr(feature, name), name


def test_legacy_feature_row_contract_is_unchanged():
    feature = make_feature(
        temperature_k=293.0,
        omega_pa_s=None,
        u_ms=3.5,
        cloud_fraction_percent=55.0,
        surface_visibility_m=18000.0,
    )
    row = native_core.feature_to_row(feature)
    assert len(row) == 20
    assert row == (
        feature.lat,
        feature.lon,
        feature.distance_km,
        feature.bearing_deg,
        float(feature.pressure_hpa),
        feature.geopotential_height_m,
        feature.rh_percent,
        293.0,
        math.nan,
        3.5,
        math.nan,
        55.0,
        math.nan,
        math.nan,
        math.nan,
        math.nan,
        math.nan,
        math.nan,
        18000.0,
        math.nan,
    )
    unpacked = native_core.row_to_feature(row, 0)
    assert unpacked.temperature_k == 293.0
    assert unpacked.omega_pa_s is None
    assert unpacked.u_ms == 3.5
    assert unpacked.surface_visibility_m == 18000.0
    assert unpacked.precip_rate_kg_m2_s is None
    # The legacy pack still refuses satellite probability features: the old
    # 20-column buffer cannot carry them, so the failure stays explicit.
    with pytest.raises(ValueError):
        native_core.feature_to_row(make_feature(horizontal_cloud_probability=0.5))
    with pytest.raises(ValueError):
        native_core.feature_to_row(make_feature(cloud_probability_given_cloud=0.5))
    # Cross-contract buffers fail closed both ways.
    with pytest.raises(ValueError):
        native_core.NativeFeatureBuffer.from_numpy([[math.nan] * 27])
    with pytest.raises(ValueError):
        native_core.NativeAssessmentFeatureBuffer.from_numpy([[math.nan] * 20])
    legacy_buffer = native_core.NativeFeatureBuffer.from_numpy([[math.nan] * 20])
    v2_buffer = native_core.NativeAssessmentFeatureBuffer.from_numpy(
        [[math.nan] * native_core.FEATURE_ROW_V2_FIELD_COUNT]
    )
    with pytest.raises(ValueError):
        native_core.NativeSnapshotFeatures(v2_buffer)
    with pytest.raises(ValueError):
        native_core.NativeAssessmentSnapshotFeatures(legacy_buffer)
    assert legacy_buffer.field_count == 20
    assert legacy_buffer.has_satellite_probability is False
    assert v2_buffer.field_count == 27
    assert v2_buffer.has_satellite_probability is True
    assert native_core.NativeSnapshotFeatures(legacy_buffer).has_satellite_probability is False
    assert (
        native_core.NativeAssessmentSnapshotFeatures(v2_buffer).has_satellite_probability is True
    )


def test_assessment_buffer_roundtrip_through_the_snapshot_view():
    features = [
        make_feature(
            horizontal_cloud_probability=0.0 if i == 0 else 0.8,
            cloud_probability_given_cloud=None if i == 1 else 0.6,
            surface_temperature_k=None if i == 2 else 290.0,
            footprint_far_distance_km=None if i == 3 else 48.0,
        )
        for i in range(4)
    ]
    buffer = native_core.NativeAssessmentFeatureBuffer(features)
    assert buffer.length == 4
    assert buffer.field_count == native_core.FEATURE_ROW_V2_FIELD_COUNT
    snapshot = native_core.NativeAssessmentSnapshotFeatures(buffer)
    unpacked = snapshot.materialize()
    for original, decoded in zip(features, unpacked):
        for name in V2_ONLY_FIELDS:
            assert_optional_equal(decoded, getattr(original, name), name)


def test_assessment_metrics_match_python_semantics(assessment_native):
    # The battery covers the combinations the acceptance contract names:
    # horizontal=0, conditional=None, missing condensate, missing cloud
    # fraction falling back to RH and the outside-the-radius gate. Unknown
    # cells travel as None on the feature and therefore as NaN in the packed
    # row, so any NaN-as-0 backfill on the native side would break the
    # element-wise equality with the Python index below.
    cases = [
        ("horizontal-0-conditional-present", make_feature(
            horizontal_cloud_probability=0.0,
            cloud_probability_given_cloud=0.9,
            rh_percent=95.0,
        )),
        ("horizontal-0-conditional-none", make_feature(
            horizontal_cloud_probability=0.0,
            rh_percent=95.0,
        )),
        ("horizontal-1-conditional-none", make_feature(
            horizontal_cloud_probability=1.0,
            rh_percent=30.0,
        )),
        ("horizontal-mid-conditional-none", make_feature(
            horizontal_cloud_probability=0.8,
            rh_percent=95.0,
        )),
        ("horizontal-none-conditional-only", make_feature(
            cloud_probability_given_cloud=0.6,
        )),
        ("unknown-horizontal-with-fraction", make_feature(
            cloud_fraction_percent=40.0,
        )),
        ("fraction-zero-stays-hard-zero", make_feature(
            cloud_fraction_percent=0.0,
            rh_percent=95.0,
        )),
        ("condensate-only", make_feature(
            cloud_liquid_kg_kg=1.0e-4,
            cloud_ice_kg_kg=2.0e-5,
            rh_percent=20.0,
        )),
        ("condensate-zero-total", make_feature(
            cloud_liquid_kg_kg=-1.0e-4,
            rh_percent=95.0,
        )),
        ("rh-fallback-only", make_feature(rh_percent=95.0)),
        ("far-field-outside-radius", make_feature(
            distance_km=500.0,
            horizontal_cloud_probability=0.8,
            cloud_probability_given_cloud=0.9,
            cloud_liquid_kg_kg=1.0e-4,
        )),
    ]
    features = [feature for _, feature in cases]
    snapshot = native_core.NativeAssessmentSnapshotFeatures(
        native_core.NativeAssessmentFeatureBuffer(features)
    )
    native_index = FeatureSpatialIndex(
        snapshot,
        diagnostic_radius_km=400.0,
        observer_elevation_m=250.0,
    )
    assert native_index._native_mode is True
    assert native_index._native_layout_v2 is True
    python_index = FeatureSpatialIndex(
        list(features),
        diagnostic_radius_km=400.0,
        observer_elevation_m=250.0,
    )
    assert python_index._native_mode is False
    for name, native_values, python_values in metric_pairs(native_index, python_index):
        for position, (native_value, python_value) in enumerate(zip(native_values, python_values)):
            assert native_value == python_value, f"{name}[{position}] ({cases[position][0]})"

    for position, (label, feature) in enumerate(cases):
        within_radius = feature.distance_km <= 400.0
        explicit = explicit_cloud_presence_score(feature)
        fraction = cloud_fraction_score(feature)
        condensate = condensate_presence_score(feature)
        presence_native = native_index._cloud_presence[position]
        explicit_native = native_index._explicit_presence[position]
        liquid_native = native_index._liquid_presence[position]
        ice_native = native_index._ice_presence[position]
        opacity_native = native_index._cloud_opacity[position]
        blocker_native = native_index._blocker_opacity[position]
        if not within_radius:
            assert (presence_native, explicit_native, liquid_native, ice_native) == (0.0, 0.0, 0.0, 0.0)
            continue
        # Explicit presence is exactly the standalone semantic; 0 stays 0 and
        # unknown stays unknown (the metric array shows it as 0 but presence
        # then comes from RH, not from a packed 0% probability).
        assert explicit_native == (explicit if explicit is not None else 0.0), label
        # Presence is the effective index composition: explicit, else RH.
        if explicit is not None:
            expected_presence = clamp01(explicit)
        else:
            expected_presence = clamp01((feature.rh_percent - 45.0) / 45.0)
        assert presence_native == expected_presence, label
        # Condensate components follow condensate_component_score.
        assert liquid_native == condensate_component_score(feature.cloud_liquid_kg_kg), label
        assert ice_native == condensate_component_score(feature.cloud_ice_kg_kg), label
        # Opacity/blocker composition, as written in the index loop.
        rh_opacity = clamp01((feature.rh_percent - 82.0) / 16.0)
        if feature.horizontal_cloud_probability is not None:
            probability = clamp01(float(feature.horizontal_cloud_probability))
            standalone_opacity = feature_cloud_opacity_score(feature)
            assert standalone_opacity == (0.0 if probability <= 0.0 else probability), label
            assert feature_blocker_opacity_score(feature) == standalone_opacity, label
            if probability <= 0.0:
                expected_opacity = 0.0
            else:
                expected_opacity = clamp01(0.22 * rh_opacity + 0.78 * standalone_opacity)
        elif fraction is not None:
            material = condensate if condensate is not None else 0.5
            explicit_opacity = clamp01(fraction * (0.35 + 0.65 * material))
            if fraction <= 0.0:
                expected_opacity = 0.0
            else:
                expected_opacity = clamp01(0.22 * rh_opacity + 0.78 * explicit_opacity)
        else:
            expected_opacity = (
                clamp01(0.22 * rh_opacity + 0.78 * condensate)
                if condensate is not None
                else rh_opacity
            )
        assert opacity_native == expected_opacity, label
        if fraction is None and condensate is None:
            assert blocker_native == clamp01((feature.rh_percent - 88.0) / 10.0), label
        else:
            assert blocker_native == opacity_native, label


def test_assessment_metrics_match_python_semantics_for_model_features(assessment_native):
    features = [
        make_feature(
            surface_temperature_k=290.0,
            surface_dewpoint_k=288.5,
            cloud_fraction_percent=55.0,
            cloud_liquid_kg_kg=1.0e-4,
        ),
        make_feature(
            cloud_fraction_percent=55.0,
            rh_percent=95.0,
            bearing_deg=180.0,
            distance_km=60.0,
        ),
    ]
    snapshot = native_core.NativeAssessmentSnapshotFeatures(
        native_core.NativeAssessmentFeatureBuffer(features)
    )
    native_index = FeatureSpatialIndex(snapshot, diagnostic_radius_km=400.0)
    assert native_index._native_mode is True
    python_index = FeatureSpatialIndex(list(features), diagnostic_radius_km=400.0)
    for name, native_values, python_values in metric_pairs(native_index, python_index):
        for position, (native_value, python_value) in enumerate(zip(native_values, python_values)):
            assert native_value == python_value, f"{name}[{position}]"
    # The v2 surface-LCL cloud base is the Python semantic, not the legacy table.
    assert native_index._cloud_base[0] == estimated_cloud_base_m(features[0], 0.0)
    assert native_index._cloud_base[0] != estimated_cloud_base_m(make_feature())


def test_assessment_metrics_carry_observer_elevation(assessment_native):
    features = [
        make_feature(
            surface_temperature_k=290.0,
            surface_dewpoint_k=288.5,
            horizontal_cloud_probability=0.8,
        ),
        make_feature(distance_km=60.0),
        make_feature(surface_temperature_k=295.0, surface_dewpoint_k=283.0, distance_km=400.0),
    ]
    snapshot = native_core.NativeAssessmentSnapshotFeatures(
        native_core.NativeAssessmentFeatureBuffer(features)
    )
    native_index = FeatureSpatialIndex(
        snapshot, diagnostic_radius_km=500.0, observer_elevation_m=250.0
    )
    assert native_index._native_mode is True
    python_index = FeatureSpatialIndex(
        list(features), diagnostic_radius_km=500.0, observer_elevation_m=250.0
    )
    for name, native_values, python_values in metric_pairs(native_index, python_index):
        for position, (native_value, python_value) in enumerate(zip(native_values, python_values)):
            assert native_value == python_value, f"{name}[{position}]"
    for position, feature in enumerate(features):
        assert native_index._cloud_base[position] == estimated_cloud_base_m(feature, 250.0)
        assert native_index._apparent_top[position] == apparent_cloud_elevation_deg(
            feature.distance_km,
            feature.geopotential_height_m,
            250.0,
        )


def test_feature_index_native_mode_gate(assessment_native):
    satellite_features = [make_feature(horizontal_cloud_probability=0.8) for _ in range(3)]
    model_features = [make_feature(cloud_fraction_percent=55.0) for _ in range(3)]

    satellite_snapshot = native_core.NativeAssessmentSnapshotFeatures(
        native_core.NativeAssessmentFeatureBuffer(satellite_features)
    )
    unlocked_index = FeatureSpatialIndex(
        satellite_snapshot,
        diagnostic_radius_km=400.0,
        observer_elevation_m=250.0,
    )
    # The dead end is gone: satellite probabilities and a non-zero observer
    # elevation both keep the native metrics path through the v2 contract.
    assert unlocked_index._native_mode is True
    assert unlocked_index._native_layout_v2 is True
    assert unlocked_index._has_satellite_probability is True
    # Scoring kernels that still consume legacy 20-column rows stay off; the
    # Python implementations remain authoritative for those until they get
    # their own v2 ABI.
    assert unlocked_index._native_scoring_enabled is False
    assert unlocked_index.candidate_rows(20.0, 100.0) is None
    assert unlocked_index.boundary_rows(100.0) is None

    model_v2_snapshot = native_core.NativeAssessmentSnapshotFeatures(
        native_core.NativeAssessmentFeatureBuffer(model_features)
    )
    model_v2_index = FeatureSpatialIndex(model_v2_snapshot, diagnostic_radius_km=400.0)
    assert model_v2_index._native_mode is True
    assert model_v2_index._native_scoring_enabled is False

    legacy_snapshot = native_core.NativeSnapshotFeatures(
        native_core.NativeFeatureBuffer(model_features)
    )
    legacy_index = FeatureSpatialIndex(legacy_snapshot, diagnostic_radius_km=400.0)
    assert legacy_index._native_mode is True
    assert legacy_index._native_layout_v2 is False
    assert legacy_index._native_scoring_enabled is True
    legacy_elevated_index = FeatureSpatialIndex(
        legacy_snapshot,
        diagnostic_radius_km=400.0,
        observer_elevation_m=250.0,
    )
    assert legacy_elevated_index._native_mode is False

    plain_satellite_index = FeatureSpatialIndex(
        list(satellite_features),
        diagnostic_radius_km=400.0,
        observer_elevation_m=0.0,
    )
    assert plain_satellite_index._native_mode is False
    assert plain_satellite_index._has_satellite_probability is True


def test_v2_columns_never_reach_legacy_kernels(assessment_native, monkeypatch):
    # build_cloud_columns must not hand a 27-column buffer to the legacy
    # aggregation ABI (it would be re-read at the wrong stride). The stub
    # records any call: a call means the gate leaked, even if the caller
    # would otherwise swallow the failure and fall back to Python.
    calls = []

    def forbidden_aggregation(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("native aggregation must not consume v2 rows")

    monkeypatch.setattr(
        "firesky.scoring.native_aggregate_cloud_columns",
        forbidden_aggregation,
    )
    features = [
        make_feature(
            horizontal_cloud_probability=0.8,
            cloud_probability_given_cloud=0.6,
            pressure_hpa=pressure,
            geopotential_height_m=900.0 + pressure,
        )
        for pressure in (500, 650, 850, 925)
    ]
    features.append(
        make_feature(pressure_hpa=850, geopotential_height_m=1200.0, distance_km=70.0)
    )
    snapshot = native_core.NativeAssessmentSnapshotFeatures(
        native_core.NativeAssessmentFeatureBuffer(features)
    )
    index = FeatureSpatialIndex(snapshot, diagnostic_radius_km=400.0)
    assert index._native_mode is True
    columns = build_cloud_columns(make_snapshot(features), index, 400.0)
    assert not calls
    assert columns
    for column in columns.values():
        for feature in column.layers.values():
            expected_probability = None if feature.distance_km == 70.0 else 0.8
            assert feature.horizontal_cloud_probability == expected_probability
            if expected_probability is not None:
                assert feature.cloud_probability_given_cloud == 0.6


def _random_assessment_features(seed: int, count: int):
    rng = random.Random(seed)
    features = []
    for _ in range(count):
        horizontal = rng.choice([None, None, 0.0, 0.3, 0.8, 1.0])
        features.append(
            make_feature(
                distance_km=rng.uniform(5.0, 380.0),
                bearing_deg=rng.uniform(0.0, 360.0),
                pressure_hpa=rng.choice([500, 600, 700, 750, 800, 850, 925]),
                geopotential_height_m=rng.uniform(800.0, 9000.0),
                rh_percent=rng.uniform(10.0, 100.0),
                temperature_k=rng.uniform(220.0, 300.0),
                cloud_fraction_percent=(
                    rng.choice([None, 0.0, 40.0, 80.0]) if horizontal is None else None
                ),
                cloud_liquid_kg_kg=(
                    rng.choice([None, None, 1.0e-5, 1.0e-3]) if horizontal is None else None
                ),
                surface_temperature_k=rng.choice([None, 288.0, 295.0]),
                surface_dewpoint_k=rng.choice([None, 284.0, 291.0]),
                horizontal_cloud_probability=horizontal,
                cloud_probability_given_cloud=(
                    rng.choice([None, 0.2, 0.9]) if horizontal is not None else None
                ),
                footprint_azimuth_width_deg=rng.choice([None, 2.4]),
            )
        )
    return features


def assert_assessment_tree_close(left, right, path: str = "") -> None:
    if isinstance(left, float) or isinstance(right, float):
        # Whole-assessment accumulation order differs in the last ULP between
        # the native-metric arrays and the Python loop; everything above that
        # noise floor must match exactly.
        assert left == pytest.approx(right, abs=1e-12), path
        return
    if dataclasses.is_dataclass(left):
        assert dataclasses.is_dataclass(right), path
        assert_assessment_tree_close(
            dataclasses.asdict(left), dataclasses.asdict(right), path
        )
        return
    if isinstance(left, dict):
        assert set(left) == set(right), path
        for key in left:
            assert_assessment_tree_close(left[key], right[key], f"{path}.{key}")
        return
    if isinstance(left, (list, tuple)):
        assert len(left) == len(right), path
        for position, (left_item, right_item) in enumerate(zip(left, right)):
            assert_assessment_tree_close(left_item, right_item, f"{path}[{position}]")
        return
    assert left == right, path


@pytest.mark.parametrize("observer_elevation_m", [0.0, 240.0])
def test_assess_timestep_v2_native_path_matches_python_path(
    assessment_native, observer_elevation_m
):
    # The unblocked index must not change the assessment: same features, one
    # run over the v2 snapshot (native metrics + grouping, Python selectors)
    # and one run fully in Python.
    features = _random_assessment_features(42, 240)
    python_snapshot = make_snapshot(features)
    v2_snapshot = make_snapshot(
        native_core.NativeAssessmentSnapshotFeatures(
            native_core.NativeAssessmentFeatureBuffer(features)
        )
    )
    aerosol = AerosolReading(aod550=0.25, source="fixture")
    python_assessment = assess_timestep(
        python_snapshot,
        28.0,
        160.0,
        aerosol,
        observer_elevation_m=observer_elevation_m,
    )
    v2_assessment = assess_timestep(
        v2_snapshot,
        28.0,
        160.0,
        aerosol,
        observer_elevation_m=observer_elevation_m,
    )
    assert_assessment_tree_close(v2_assessment, python_assessment)


def test_legacy_native_metrics_stay_on_the_old_contract(assessment_native):
    # Legacy buffers keep the fixed layer-depth table and sea-level geometry:
    # the surface pair and the observer elevation are invisible to them. The
    # one pre-existing divergence (legacy native cloud base is the layer-depth
    # table while the Python fallback applies the surface LCL) is asserted
    # from both sides so neither can drift.
    features = [
        make_feature(
            surface_temperature_k=290.0,
            surface_dewpoint_k=288.5,
            cloud_fraction_percent=55.0,
        ),
        make_feature(distance_km=60.0, bearing_deg=200.0),
    ]
    snapshot = native_core.NativeSnapshotFeatures(native_core.NativeFeatureBuffer(features))
    legacy_index = FeatureSpatialIndex(snapshot, diagnostic_radius_km=400.0)
    assert legacy_index._native_mode is True
    python_index = FeatureSpatialIndex(list(features), diagnostic_radius_km=400.0)
    for name, native_values, python_values in metric_pairs(legacy_index, python_index):
        if name == "_cloud_base":
            continue
        for position, (native_value, python_value) in enumerate(zip(native_values, python_values)):
            assert native_value == python_value, f"{name}[{position}]"
    assert legacy_index._cloud_base[0] == 1050.0  # 1500 m - 450 m (850 hPa table)
    assert python_index._cloud_base[0] == 187.5  # 125 m/K * (290.0 - 288.5)
    assert estimated_cloud_base_m(features[0], 0.0) == 187.5


# ---------------------------------------------------------------------------
# Engine assessment operation (cloud columns + spatial index + candidate
# signals ported onto hsd-assessment-feature-row-v2 rows).
# ---------------------------------------------------------------------------

ASSESSMENT_SIGNAL_FIELDS = (
    "lat",
    "lon",
    "pressure_hpa",
    "distance_km",
    "bearing_deg",
    "sector_delta_deg",
    "apparent_elevation_deg",
    "aerosol_transmission_score",
    "path_clearance_score",
    "illumination_height_m",
    "cloud_height_m",
    "score",
    "rh_percent",
    "cloud_base_m",
    "cloud_type",
    "cloud_type_burnability_score",
    "deep_cloud_risk",
    "aerosol_sensitivity_score",
    "aerosol_path_variability_score",
    "observer_path_aod550",
    "inflow_path_aod550",
    "cloud_evidence_score",
    "cloud_fraction_percent",
    "cloud_liquid_kg_kg",
    "cloud_ice_kg_kg",
    "atmospheric_clarity_score",
    "illumination_score",
    "footprint_azimuth_width_deg",
    "footprint_near_distance_km",
    "footprint_far_distance_km",
    "horizontal_cloud_probability",
)

ASSESSMENT_COLUMN_FIELDS = (
    "lat",
    "lon",
    "distance_km",
    "bearing_deg",
    "high_presence",
    "mid_presence",
    "low_presence",
    "deep_cloud_risk",
    "thickness_risk",
    "cloud_type",
    "burnability_score",
    "cloud_base_m",
    "cloud_top_m",
    "liquid_presence",
    "ice_presence",
    "explicit_cloud_evidence",
    "horizontal_cloud_probability",
    "footprint_azimuth_width_deg",
    "footprint_near_distance_km",
    "footprint_far_distance_km",
)


ASSESSMENT_BOUNDARY_FIELDS = (
    "layer",
    "distance_km",
    "bearing_deg",
    "sector_delta_deg",
    "cloud_base_m",
    "cloud_top_m",
    "required_base_m",
    "apparent_base_elevation_deg",
    "path_clearance_score",
    "aerosol_transmission_score",
    "score",
    "edge_contrast_score",
    "clear_slot_score",
    "thickness_score",
    "cloud_type",
    "cloud_type_burnability_score",
    "deep_cloud_risk",
    "aerosol_sensitivity_score",
    "aerosol_path_variability_score",
    "observer_path_aod550",
    "inflow_path_aod550",
    "regime",
    "local_cloud_presence_score",
    "geometric_reach_km",
    "physical_transmission_min",
    "physical_transmission_max",
    "atmospheric_clarity_score",
    "edge_distance_lower_km",
    "edge_distance_upper_km",
)

ASSESSMENT_CLOUD_HOLE_FIELDS = (
    "distance_km",
    "bearing_deg",
    "sector_delta_deg",
    "clear_slot_score",
    "edge_contrast_score",
    "rear_target_distance_km",
    "rear_target_bearing_deg",
    "rear_target_cloud_type",
    "rear_target_burnability_score",
    "rear_target_score",
    "alignment_score",
    "path_clearance_score",
    "rear_blocking_risk",
    "score",
)

ASSESSMENT_SOLAR_INFLOW_FIELDS = (
    "score",
    "distance_km",
    "bearing_deg",
    "pressure_hpa",
    "rh_percent",
    "cloud_base_m",
    "cloud_top_m",
    "ray_height_m",
    "target_distance_km",
    "target_bearing_deg",
    "target_height_m",
)

ASSESSMENT_CROSS_SECTION_FIELDS = (
    "azimuth_deg",
    "samples_count",
    "lightpath_integrity_score",
    "path_extinction_score",
    "vertical_cloud_type",
    "cloud_type_burnability_score",
    "deep_cloud_risk",
    "blocking_risk",
    "target_cloud_score",
    "summary",
)

ASSESSMENT_PROFILE_SAMPLE_FIELDS = (
    "distance_km",
    "bearing_deg",
    "cloud_base_m",
    "cloud_top_m",
    "cloud_type",
    "high_presence",
    "mid_presence",
    "low_presence",
    "deep_cloud_risk",
    "thickness_risk",
    "required_illumination_height_m",
    "blocker_risk",
    "lightpath_clearance_score",
    "target_cloud_score",
    "illumination_status",
)

ASSESSMENT_PROFILE_LEVEL_FIELDS = (
    "pressure_hpa",
    "height_m",
    "rh_percent",
    "temperature_c",
    "cloud_fraction_percent",
    "cloud_liquid_kg_kg",
    "cloud_ice_kg_kg",
)


def assert_engine_signal_list_matches(engine_signals, python_signals, fields, label, float_tolerance):
    assert len(engine_signals) == len(python_signals), f"{label} length"
    for position, (engine_signal, python_signal) in enumerate(
        zip(engine_signals, python_signals, strict=True)
    ):
        for field in fields:
            engine_value = engine_signal[field]
            python_value = getattr(python_signal, field)
            if python_value is None:
                assert engine_value is None, f"{label}[{position}].{field}"
            elif isinstance(python_value, str):
                assert engine_value == python_value, f"{label}[{position}].{field}"
            elif isinstance(python_value, int) and not isinstance(python_value, bool):
                assert engine_value == python_value, f"{label}[{position}].{field}"
            elif float_tolerance is None:
                assert engine_value == python_value, f"{label}[{position}].{field}"
            else:
                assert engine_value == pytest.approx(python_value, abs=float_tolerance), (
                    f"{label}[{position}].{field}"
                )


def engine_assessment_binary():
    from firesky.rust_grid import engine_binary

    binary = Path(
        os.environ.get(
            "FIRESKY_RUST_ENGINE_BIN", ROOT / "engine/target/release/firesky-engine"
        )
    )
    if not Path(str(binary)).is_file():
        unavailable("build the Rust engine (bash scripts/build_rust_engine.sh)")
    return engine_binary()


@pytest.fixture
def assessment_engine(monkeypatch):
    engine_assessment_binary()
    from firesky import rust_desktop_process

    monkeypatch.setenv(
        "FIRESKY_RUST_ENGINE_BIN",
        str(Path(os.environ.get("FIRESKY_RUST_ENGINE_BIN", ROOT / "engine/target/release/firesky-engine")).resolve()),
    )
    rust_desktop_process.close()
    yield
    rust_desktop_process.close()


def python_reference_assessment(features, solar_elevation_deg, solar_azimuth_deg, aerosol, observer_elevation_m, max_distance_km=1000.0):
    """The production Python wiring: index + columns + candidate_signals."""
    snapshot = make_snapshot(features)
    feature_index = FeatureSpatialIndex(
        features,
        diagnostic_radius_km=max_distance_km,
        observer_elevation_m=observer_elevation_m,
    )
    columns = build_cloud_columns(snapshot, feature_index, max_distance_km)
    signals = candidate_signals(
        snapshot,
        solar_elevation_deg,
        solar_azimuth_deg,
        aerosol,
        columns,
        feature_index,
        observer_elevation_m,
    )
    return feature_index, columns, signals


def python_reference_full(features, solar_elevation_deg, solar_azimuth_deg, aerosol, observer_elevation_m, max_distance_km=1000.0):
    """assess_timestep's four-signal wiring: candidates, decks, boundaries,
    cloud holes, inflow blockage, penalties and the solar-path cross section."""
    from firesky.cross_section import diagnose_cross_section
    from firesky.scoring import (
        boundary_lightpath_signals,
        cloud_hole_lightpath_signals,
        diagnose_solar_inflow_blockage,
        local_deck_edge_signals,
        low_cloud_penalty,
        surface_weather_penalty,
    )

    snapshot = make_snapshot(features)
    feature_index, columns, signals = python_reference_assessment(
        features,
        solar_elevation_deg,
        solar_azimuth_deg,
        aerosol,
        observer_elevation_m,
        max_distance_km,
    )
    local_deck = local_deck_edge_signals(
        snapshot, solar_elevation_deg, solar_azimuth_deg, aerosol, columns, observer_elevation_m,
    )
    remote_boundary = boundary_lightpath_signals(
        snapshot, solar_elevation_deg, solar_azimuth_deg, aerosol, columns, feature_index, observer_elevation_m,
    )
    # scoring.py:3780-3784 — local decks first, stable score sort.
    boundary = sorted(
        [*local_deck, *remote_boundary], key=lambda item: item.score, reverse=True
    )
    cloud_holes = cloud_hole_lightpath_signals(
        snapshot, solar_elevation_deg, solar_azimuth_deg, columns, observer_elevation_m,
    )
    top = signals[0] if signals else None
    inflow = diagnose_solar_inflow_blockage(
        snapshot, top, solar_elevation_deg, solar_azimuth_deg, feature_index, observer_elevation_m,
    )
    low_penalty = low_cloud_penalty(snapshot, solar_azimuth_deg, feature_index)
    weather_penalty, visibility_m, precip_rate = surface_weather_penalty(snapshot, feature_index)
    cross = diagnose_cross_section(
        snapshot, solar_elevation_deg, solar_azimuth_deg, columns, observer_elevation_m,
    )
    return {
        "candidate_signals": signals,
        "local_deck_signals": local_deck,
        "remote_boundary_signals": remote_boundary,
        "boundary_signals": boundary,
        "cloud_hole_signals": cloud_holes,
        "solar_inflow_blockage": inflow,
        "low_cloud_penalty": low_penalty,
        "surface_weather_penalty": (weather_penalty, visibility_m, precip_rate),
        "cross_section": cross,
    }


def assert_engine_candidates_match_python(
    features,
    solar_elevation_deg,
    solar_azimuth_deg,
    aerosol,
    observer_elevation_m,
    max_distance_km=1000.0,
    float_tolerance=None,
):
    from firesky.rust_desktop_data import nowcast_assessment

    _, python_columns, python_signals = python_reference_assessment(
        features,
        solar_elevation_deg,
        solar_azimuth_deg,
        aerosol,
        observer_elevation_m,
        max_distance_km,
    )
    payload = nowcast_assessment(
        scans=[_engine_scan(features, solar_elevation_deg, solar_azimuth_deg)],
        timezone_name="Asia/Shanghai",
        aerosol=aerosol,
        observer_elevation_m=observer_elevation_m,
        max_distance_km=max_distance_km,
    )
    engine_scan = payload["scans"][0]
    engine_signals = engine_scan["candidate_signals"]
    assert len(engine_signals) == len(python_signals)
    for position, (engine_signal, python_signal) in enumerate(
        zip(engine_signals, python_signals, strict=True)
    ):
        for field in ASSESSMENT_SIGNAL_FIELDS:
            engine_value = engine_signal[field]
            python_value = getattr(python_signal, field)
            if python_value is None:
                assert engine_value is None, f"signal[{position}].{field}"
            elif isinstance(python_value, str):
                assert engine_value == python_value, f"signal[{position}].{field}"
            elif isinstance(python_value, int) and not isinstance(python_value, bool):
                assert engine_value == python_value, f"signal[{position}].{field}"
            elif float_tolerance is None:
                assert engine_value == python_value, f"signal[{position}].{field}"
            else:
                assert engine_value == pytest.approx(python_value, abs=float_tolerance), (
                    f"signal[{position}].{field}"
                )
    reference = python_reference_full(
        features,
        solar_elevation_deg,
        solar_azimuth_deg,
        aerosol,
        observer_elevation_m,
        max_distance_km,
    )
    assert_engine_signal_list_matches(
        engine_scan["local_deck_signals"], reference["local_deck_signals"], ASSESSMENT_BOUNDARY_FIELDS,
        "local_deck", float_tolerance,
    )
    assert_engine_signal_list_matches(
        engine_scan["remote_boundary_signals"], reference["remote_boundary_signals"], ASSESSMENT_BOUNDARY_FIELDS,
        "remote_boundary", float_tolerance,
    )
    # The merge keeps local decks ahead on equal scores (stable sort).
    assert_engine_signal_list_matches(
        engine_scan["boundary_signals"], reference["boundary_signals"], ASSESSMENT_BOUNDARY_FIELDS,
        "boundary", float_tolerance,
    )
    for position, (engine_regime, python_regime) in enumerate(
        zip(
            [signal["regime"] for signal in engine_scan["boundary_signals"]],
            [signal.regime for signal in reference["boundary_signals"]],
            strict=True,
        )
    ):
        assert engine_regime == python_regime, f"boundary[{position}].regime"
    assert_engine_signal_list_matches(
        engine_scan["cloud_hole_signals"], reference["cloud_hole_signals"], ASSESSMENT_CLOUD_HOLE_FIELDS,
        "cloud_hole", float_tolerance,
    )
    if reference["solar_inflow_blockage"] is None:
        assert engine_scan["solar_inflow_blockage"] is None
    else:
        assert engine_scan["solar_inflow_blockage"] is not None
        for field in ASSESSMENT_SOLAR_INFLOW_FIELDS:
            engine_value = engine_scan["solar_inflow_blockage"][field]
            python_value = getattr(reference["solar_inflow_blockage"], field)
            if isinstance(python_value, int) and not isinstance(python_value, bool):
                assert engine_value == python_value, f"solar_inflow.{field}"
            elif float_tolerance is None:
                assert engine_value == python_value, f"solar_inflow.{field}"
            else:
                assert engine_value == pytest.approx(python_value, abs=float_tolerance), (
                    f"solar_inflow.{field}"
                )
    assert engine_scan["low_cloud_penalty"] == pytest.approx(
        reference["low_cloud_penalty"], abs=float_tolerance or 0.0
    )
    engine_weather = engine_scan["surface_weather_penalty"]
    python_weather = reference["surface_weather_penalty"]
    assert engine_weather["penalty"] == pytest.approx(python_weather[0], abs=float_tolerance or 0.0)
    assert engine_weather["visibility_m"] == python_weather[1] or (
        python_weather[1] is not None
        and engine_weather["visibility_m"] == pytest.approx(python_weather[1], abs=float_tolerance or 0.0)
    )
    assert engine_weather["precip_rate_kg_m2_s"] == python_weather[2] or (
        python_weather[2] is not None
        and engine_weather["precip_rate_kg_m2_s"] == pytest.approx(python_weather[2], abs=float_tolerance or 0.0)
    )
    engine_cross = engine_scan["cross_section"]
    python_cross = reference["cross_section"]
    for field in ASSESSMENT_CROSS_SECTION_FIELDS:
        engine_value = engine_cross[field]
        python_value = getattr(python_cross, field)
        if field == "summary":
            assert engine_value == python_value, "cross_section.summary"
        elif isinstance(python_value, int) and not isinstance(python_value, bool):
            assert engine_value == python_value, f"cross_section.{field}"
        elif float_tolerance is None:
            assert engine_value == python_value, f"cross_section.{field}"
        else:
            assert engine_value == pytest.approx(python_value, abs=float_tolerance), (
                f"cross_section.{field}"
            )
    assert len(engine_cross["profile"]) == len(python_cross.profile)
    for position, (engine_sample, python_sample) in enumerate(
        zip(engine_cross["profile"], python_cross.profile, strict=True)
    ):
        for field in ASSESSMENT_PROFILE_SAMPLE_FIELDS:
            engine_value = engine_sample[field]
            python_value = getattr(python_sample, field)
            if field == "levels":
                assert len(engine_value) == len(python_value), f"profile[{position}].levels"
                for level_engine, level_python in zip(engine_value, python_value, strict=True):
                    for level_field in ASSESSMENT_PROFILE_LEVEL_FIELDS:
                        assert getattr(level_engine, level_field) == getattr(
                            level_python, level_field
                        ), f"profile[{position}].levels.{level_field}"
            elif isinstance(python_value, str):
                assert engine_value == python_value, f"profile[{position}].{field}"
            elif float_tolerance is None:
                assert engine_value == python_value, f"profile[{position}].{field}"
            else:
                assert engine_value == pytest.approx(python_value, abs=float_tolerance), (
                    f"profile[{position}].{field}"
                )
    assert len(engine_scan["columns"]) == len(python_columns)
    for position, (engine_column, python_column) in enumerate(
        zip(engine_scan["columns"], python_columns.values(), strict=True)
    ):
        for field in ASSESSMENT_COLUMN_FIELDS:
            engine_value = engine_column[field]
            python_value = getattr(python_column, field)
            if python_value is None:
                assert engine_value is None, f"column[{position}].{field}"
            elif isinstance(python_value, str):
                assert engine_value == python_value, f"column[{position}].{field}"
            elif float_tolerance is None:
                assert engine_value == python_value, f"column[{position}].{field}"
            else:
                assert engine_value == pytest.approx(python_value, abs=float_tolerance), (
                    f"column[{position}].{field}"
                )
        engine_layers = dict(engine_column["layers"])
        assert set(engine_layers) == set(python_column.layers.keys()), f"column[{position}].layers"
        for pressure, layer_position in engine_layers.items():
            assert features[layer_position] is python_column.layers[pressure]
    return payload, python_signals


def synthetic_assessment_features(seed=7, count=140):
    rng = random.Random(seed)
    features = []
    for index in range(count):
        horizontal = rng.choice([None, None, 0.0, 0.3, 0.8, 1.0])
        distance = rng.choice([rng.uniform(5.0, 900.0), rng.uniform(900.0, 980.0)])
        features.append(
            make_feature(
                lat=30.0 + rng.uniform(-4.0, 4.0),
                lon=120.0 + rng.uniform(-4.0, 4.0),
                distance_km=distance,
                bearing_deg=rng.uniform(0.0, 360.0),
                pressure_hpa=rng.choice([200, 250, 300, 400, 500, 600, 700, 750, 800, 850, 925]),
                geopotential_height_m=rng.uniform(500.0, 11000.0),
                rh_percent=rng.choice([rng.uniform(10.0, 100.0), 95.0, 30.0]),
                temperature_k=rng.choice([None, rng.uniform(210.0, 300.0)]),
                omega_pa_s=rng.choice([None, -0.6, -0.05, 0.2]),
                cloud_fraction_percent=(
                    rng.choice([None, 0.0, 35.0, 75.0, 100.0]) if horizontal is None else None
                ),
                cloud_liquid_kg_kg=(
                    rng.choice([None, None, 1.0e-6, 1.0e-3]) if horizontal is None else None
                ),
                cloud_ice_kg_kg=(rng.choice([None, None, 5.0e-5]) if horizontal is None else None),
                source_lat=None if index % 3 else 30.0 + rng.uniform(-4.0, 4.0),
                source_lon=None if index % 3 else 120.0 + rng.uniform(-4.0, 4.0),
                source_distance_km=None if index % 3 else rng.uniform(1.0, 30.0),
                source_bearing_deg=None if index % 3 else rng.uniform(0.0, 360.0),
                surface_temperature_k=rng.choice([None, 288.0, 296.0]),
                surface_dewpoint_k=rng.choice([None, 283.0, 291.0]),
                horizontal_cloud_probability=horizontal,
                cloud_probability_given_cloud=(
                    rng.choice([None, 0.2, 0.9]) if horizontal is not None else None
                ),
                footprint_azimuth_width_deg=(2.4 if horizontal is not None else None),
                footprint_near_distance_km=(12.0 if horizontal is not None else None),
                footprint_far_distance_km=(48.0 if horizontal is not None else None),
            )
        )
    return features


def test_engine_assessment_matches_python_across_feature_kinds(assessment_engine):
    from firesky.models import AerosolReading

    features = synthetic_assessment_features()
    aerosol = AerosolReading(0.25, "synthetic", "measured", 30.0, 120.0)
    for solar_elevation, solar_azimuth, observer_elevation in (
        (28.0, 160.0, 0.0),
        (28.0, 210.0, 240.0),
        (-1.5, 120.0, 25.0),
        (-4.0, 120.0, 25.0),
        (-12.0, 300.0, 0.0),
    ):
        # The differential itself proves the dawn branch: a missing or
        # different min_score/dawn_boost implementation would break equality.
        assert_engine_candidates_match_python(
            features, solar_elevation, solar_azimuth, aerosol, observer_elevation
        )


def test_engine_assessment_keeps_storage_order_for_tied_scores(assessment_engine):
    from firesky.models import AerosolReading

    # Two features with identical physics but distinct positions produce the
    # same score; the stable sort must keep storage (query) order on both
    # sides, so the candidate list order must not flip.
    first = make_feature(
        lat=30.4, lon=120.4, distance_km=100.0, bearing_deg=45.0,
        pressure_hpa=600, geopotential_height_m=6000.0, rh_percent=80.0,
        horizontal_cloud_probability=0.8, cloud_probability_given_cloud=0.7,
    )
    second = make_feature(
        lat=31.2, lon=121.2, distance_km=100.0, bearing_deg=45.0,
        pressure_hpa=600, geopotential_height_m=6000.0, rh_percent=80.0,
        horizontal_cloud_probability=0.8, cloud_probability_given_cloud=0.7,
    )
    features = [first, second]
    aerosol = AerosolReading(0.2, "tie", "measured", 30.0, 120.0)
    payload, python_signals = assert_engine_candidates_match_python(
        features, 24.0, 45.0, aerosol, 0.0
    )
    assert len(python_signals) >= 2
    assert pytest.approx(python_signals[0].score, abs=1e-15) == python_signals[1].score
    engine_scan = payload["scans"][0]
    assert engine_scan["candidate_signals"][0]["lat"] == first.lat
    assert engine_scan["candidate_signals"][1]["lat"] == second.lat


def test_engine_assessment_covers_missing_probability_and_condensate_fallbacks(
    assessment_engine,
):
    from firesky.models import AerosolReading

    features = [
        # Satellite row with unknown conditional profile.
        make_feature(horizontal_cloud_probability=0.7, cloud_probability_given_cloud=None,
                     rh_percent=88.0),
        # Pure RH fallback: no probability, no fraction, no condensate.
        make_feature(rh_percent=95.0, distance_km=180.0, bearing_deg=160.0,
                     pressure_hpa=600, geopotential_height_m=5200.0),
        # Condensate-only evidence.
        make_feature(cloud_liquid_kg_kg=1.0e-3, rh_percent=20.0, distance_km=220.0,
                     bearing_deg=165.0, pressure_hpa=650, geopotential_height_m=4800.0),
        # Explicit 0% fraction stays a hard zero (RH cannot revive it).
        make_feature(cloud_fraction_percent=0.0, rh_percent=98.0, distance_km=260.0,
                     bearing_deg=170.0, pressure_hpa=700, geopotential_height_m=4400.0),
        # Below the explicit 0.06 dawn gate.
        make_feature(horizontal_cloud_probability=0.03, distance_km=300.0, bearing_deg=175.0,
                     pressure_hpa=750, geopotential_height_m=4000.0),
    ]
    aerosol = AerosolReading(0.3, "fallback", "measured", 30.0, 120.0)
    assert_engine_candidates_match_python(features, 15.0, 165.0, aerosol, 0.0)
    assert_engine_candidates_match_python(features, -6.0, 165.0, aerosol, 12.0)


def test_engine_assessment_with_path_samples(assessment_engine):
    from firesky.models import AerosolReading, AerosolSample

    features = synthetic_assessment_features(seed=11, count=80)
    # Candidates along the sample line so the binned path AOD is exercised.
    for index in range(6):
        features.append(
            make_feature(
                distance_km=120.0 + 25.0 * index, bearing_deg=150.0 + 2.0 * index,
                pressure_hpa=600, geopotential_height_m=6200.0, rh_percent=85.0,
                horizontal_cloud_probability=0.8, cloud_probability_given_cloud=0.7,
            )
        )
    samples = [
        AerosolSample(lat=30.0 + 0.02 * index, lon=120.0 + 0.02 * index,
                      distance_km=40.0 + 30.0 * index,
                      bearing_deg=148.0 + 1.0 * index, aod550=0.2 + 0.06 * index)
        for index in range(8)
    ]
    aerosol = AerosolReading(0.22, "path-samples", "measured", 30.0, 120.0,
                             path_samples=samples)
    # statistics.mean sums exact rationals while the kernel uses a compensated
    # sum: the binned AOD agreement holds well below scoring resolution.
    payload, python_signals = assert_engine_candidates_match_python(
        features, 26.0, 150.0, aerosol, 0.0, float_tolerance=1e-9
    )
    assert any(
        signal.aerosol_path_variability_score > 0.0 for signal in python_signals
    ), "the sample geometry must exercise the binned path AOD" 


def test_engine_assessment_empty_feature_set(assessment_engine):
    from firesky.models import AerosolReading

    aerosol = AerosolReading(0.3, "empty", "measured", 30.0, 120.0)
    payload, python_signals = assert_engine_candidates_match_python(
        [], 20.0, 100.0, aerosol, 0.0
    )
    assert python_signals == []
    assert payload["scans"][0]["candidate_signals"] == []
    assert payload["scans"][0]["columns"] == []


def test_engine_assessment_rejects_wrong_row_width(assessment_engine):
    """The engine contract gate refuses rows that are not 27 columns wide."""
    import json
    import tempfile

    from firesky.rust_desktop_process import call_engine

    def bad_request(features):
        return {
            "features": features,
            "solar_elevation_deg": 20.0,
            "solar_azimuth_deg": 100.0,
            "observer_elevation_m": 0.0,
            "aerosol": {
                "aod550": 0.3,
                "observer_lat": 30.0,
                "observer_lon": 120.0,
                "path_samples": [],
            },
            "max_distance_km": 1000.0,
        }

    with tempfile.TemporaryDirectory(prefix="firesky-assessment-gate-") as root:
        root = Path(root)
        request_path = root / "request.json"
        output_path = root / "assessment.json"
        payload = json.dumps(bad_request([[None] * 20]), allow_nan=False).encode()
        request_path.write_bytes(payload)
        with pytest.raises(RuntimeError):
            call_engine(
                "desktop_nowcast_assessment",
                {"request_path": str(request_path), "output_path": str(output_path)},
            )
        # Extra fields are rejected by deny_unknown_fields as well.
        payload = json.dumps({**bad_request([]), "unexpected": True}, allow_nan=False).encode()
        request_path.write_bytes(payload)
        output_path = root / "assessment-2.json"
        with pytest.raises(RuntimeError):
            call_engine(
                "desktop_nowcast_assessment",
                {"request_path": str(request_path), "output_path": str(output_path)},
            )
        assert not output_path.exists()


def test_engine_assessment_boundary_merge_tie_keeps_stable_order(assessment_engine):
    from firesky.models import AerosolReading

    # Two identical boundary features in distinct columns produce two remote
    # boundary signals with exactly equal scores; the merged list must keep
    # them in storage order on both sides (scoring.py:3780-3784 stable sort,
    # local decks first by list order).
    features = []
    for index in range(3):
        for offset in (0.0, 0.4):
            features.append(
                make_feature(
                    lat=30.2 + offset + 0.01 * index,
                    lon=120.2 + offset,
                    distance_km=150.0 + 30.0 * index,
                    bearing_deg=150.0,
                    pressure_hpa=500,
                    geopotential_height_m=5800.0,
                    rh_percent=88.0,
                    horizontal_cloud_probability=0.9,
                    cloud_probability_given_cloud=0.7,
                )
            )
    aerosol = AerosolReading(0.2, "merge-tie", "measured", 30.0, 120.0)
    payload, reference = _engine_and_reference(features, 24.0, 150.0, aerosol, 0.0)
    python_scores = [signal.score for signal in reference["remote_boundary_signals"]]
    # The duplicated features really do produce an exact tie to sort through.
    assert len(set(python_scores)) < len(python_scores), "the fixture must contain equal boundary scores"
    engine_scores = [signal["score"] for signal in payload["scans"][0]["boundary_signals"]]
    assert engine_scores == python_scores
    engine_order = [signal["distance_km"] for signal in payload["scans"][0]["boundary_signals"]]
    python_order = [signal.distance_km for signal in reference["boundary_signals"]]
    assert engine_order == python_order


def _engine_scan(features, solar_elevation_deg, solar_azimuth_deg, radar=None, *, valid_time_utc="2026-01-01T01:00:00Z"):
    return {
        "features": list(features),
        "valid_time_utc": valid_time_utc,
        "forecast_hour": 0,
        "solar_elevation_deg": solar_elevation_deg,
        "solar_azimuth_deg": solar_azimuth_deg,
        "radar": radar,
        "satellite_material_evidence": None,
        "solar_incoming_block_risk": 0.0,
        "forward_burnable_cloud_load": None,
        "forward_burnable_cloud_material_load": None,
        "forward_burnable_cloud_incoming_aerosol_load": None,
        "observer_lat": None,
    }


def _engine_and_reference(features, solar_elevation_deg, solar_azimuth_deg, aerosol, observer_elevation_m):
    from firesky.rust_desktop_data import nowcast_assessment

    reference = python_reference_full(
        features, solar_elevation_deg, solar_azimuth_deg, aerosol, observer_elevation_m
    )
    payload = nowcast_assessment(
        scans=[_engine_scan(features, solar_elevation_deg, solar_azimuth_deg)],
        timezone_name="Asia/Shanghai",
        aerosol=aerosol,
        observer_elevation_m=observer_elevation_m,
    )
    return payload, reference


def test_engine_assessment_solar_inflow_blockage(assessment_engine):
    from firesky.models import AerosolReading

    # Top candidate near the sun with a dense, taller blocker beyond it inside
    # the inflow corridor: the blockage diagnosis must fire on both sides and
    # match field by field (ray height, intersection risk, rear geometry).
    features = [
        # The top candidate sits near the sun with a clear illuminated base.
        make_feature(
            distance_km=120.0, bearing_deg=150.0, pressure_hpa=600,
            geopotential_height_m=5200.0, rh_percent=90.0,
            horizontal_cloud_probability=0.98, cloud_probability_given_cloud=0.85,
        ),
        # The sunward ray climbs ~210 m/km at 12 degrees, so the blocking cirrus
        # must sit just behind the target with a deck bracketing the ray height.
        make_feature(
            distance_km=140.0, bearing_deg=151.0, pressure_hpa=200,
            geopotential_height_m=9000.0, rh_percent=97.0,
            horizontal_cloud_probability=0.7, cloud_probability_given_cloud=0.6,
        ),
    ]
    aerosol = AerosolReading(0.2, "inflow", "measured", 30.0, 120.0)
    payload, reference = _engine_and_reference(features, 12.0, 150.0, aerosol, 0.0)
    assert reference["candidate_signals"], "the top candidate must exist"
    assert reference["solar_inflow_blockage"] is not None, "the blocker must trigger the diagnosis"
    engine_scan = payload["scans"][0]
    assert engine_scan["solar_inflow_blockage"] is not None
    for field in ASSESSMENT_SOLAR_INFLOW_FIELDS:
        engine_value = engine_scan["solar_inflow_blockage"][field]
        python_value = getattr(reference["solar_inflow_blockage"], field)
        if isinstance(python_value, int) and not isinstance(python_value, bool):
            assert engine_value == python_value, f"solar_inflow.{field}"
        else:
            assert engine_value == python_value, f"solar_inflow.{field}"


def test_engine_assessment_empty_cross_section_is_neutral(assessment_engine):
    from firesky.models import AerosolReading

    # Every column sits far from the solar ray, so the profile corridor finds
    # no samples: samples_count=0 with the empty-diagnosis shape. The Python
    # composition then keeps profile_factor at the neutral 0.70 instead of
    # vetoing (scoring.py:3895-3900) — the port must not fabricate a profile.
    features = [
        make_feature(
            distance_km=120.0 + 40.0 * index, bearing_deg=20.0 + 5.0 * index,
            pressure_hpa=600, geopotential_height_m=5000.0, rh_percent=90.0,
            horizontal_cloud_probability=0.8, cloud_probability_given_cloud=0.7,
        )
        for index in range(4)
    ]
    aerosol = AerosolReading(0.3, "empty-cross", "measured", 30.0, 120.0)
    payload, reference = _engine_and_reference(features, 20.0, 160.0, aerosol, 0.0)
    engine_cross = payload["scans"][0]["cross_section"]
    python_cross = reference["cross_section"]
    assert python_cross.samples_count == 0
    assert engine_cross["samples_count"] == 0
    assert engine_cross["summary"] == python_cross.summary
    assert engine_cross["summary"] == ["剖面诊断：太阳方向没有足够的模式网格样本。"]
    assert engine_cross["blocking_risk"] == python_cross.blocking_risk == 1.0
    assert engine_cross["lightpath_integrity_score"] == python_cross.lightpath_integrity_score == 0.0
    assert engine_cross["vertical_cloud_type"] == "unknown"
    assert engine_cross["profile"] == []


ASSESSMENT_STEP_FLOAT_FIELDS = (
    "solar_elevation_deg",
    "solar_azimuth_deg",
    "burn_score",
    "intensity_score",
    "extent_score",
    "vividness_score",
    "visibility_score",
    "low_cloud_penalty",
    "boundary_score",
    "cloud_hole_score",
    "local_sky_score",
    "remote_sky_score",
    "convective_risk",
    "geometric_cloud_coverage",
    "structure_gate_score",
    "selected_aerosol_path_variability_score",
    "forward_view_cloud_field_amount",
    "forward_burnable_cloud_field_amount",
    "observer_local_cloud_field_amount",
)

ASSESSMENT_STEP_INT_FIELDS = (
    "forecast_hour",
    "candidate_count",
    "burning_cloud_count",
    "strong_burning_cloud_count",
)

ASSESSMENT_STEP_STR_FIELDS = ("selected_regime",)


def _python_full_step(features, solar_elevation_deg, solar_azimuth_deg, aerosol, observer_elevation_m, radar=None, *, valid_time_utc, map_radius_km=1000.0):
    """assess_timestep + observation constraints + material evidence."""
    from datetime import datetime
    from types import SimpleNamespace
    from firesky.scoring import assess_timestep
    from firesky.service import apply_nowcast_observation_constraints

    valid_time = datetime.fromisoformat(str(valid_time_utc).replace("Z", "+00:00"))
    snapshot = DomainSnapshot(
        model_name="assessment-v2-fixture",
        forecast_hour=0,
        init_time_utc=valid_time,
        valid_time_utc=valid_time,
        features=features,
        source_url="file://fixture",
        cache_path="fixture",
    )
    step = assess_timestep(
        snapshot,
        solar_elevation_deg,
        solar_azimuth_deg,
        aerosol,
        include_map_cells=False,
        map_radius_km=map_radius_km,
        observer_elevation_m=observer_elevation_m,
    )
    # The satellite wrapper injects the neutral material evidence separately.
    object.__setattr__(step, "satellite_retrieval_material_evidence", None)
    nowcast = SimpleNamespace(
        radar_source=radar.get("source") if radar else None,
        radar_coverage_ratio=radar.get("coverage_ratio") if radar else None,
        radar_age_minutes=radar.get("age_minutes") if radar else None,
        radar_max_dbz=radar.get("max_dbz") if radar else None,
        radar_local_precip_ratio=radar.get("local_precip_ratio") if radar else None,
        radar_path_precip_ratio=radar.get("path_precip_ratio") if radar else None,
        radar_convective_ratio=radar.get("convective_ratio") if radar else None,
        radar_convective_risk=radar.get("convective_risk") if radar else None,
        summary=[],
        horizon_clearance_score=0.5,
    )
    constrained, _ = apply_nowcast_observation_constraints(
        step,
        nowcast,
        snapshot.valid_time_utc,
        path_azimuth_deg=solar_azimuth_deg,
        observer_elevation_m=observer_elevation_m,
        diagnose_nowcast=False,
    )
    return constrained


def _python_ledger_rows(steps, aod550, event_time_local, core_window_minutes):
    """national_batch.py's exact float32 row construction, as an oracle."""
    import numpy as np

    count = len(steps)
    values = np.zeros((count, 82), dtype=np.float32)
    samples = np.zeros((count, 9), dtype=np.float32)
    offsets = np.asarray(
        [
            (step.valid_time_local - steps[0].valid_time_local).total_seconds() / 60.0
            for step in steps
        ],
        dtype=np.float32,
    )
    burnable_gate = np.asarray(
        [bool(getattr(step, "observation_burnable", True)) for step in steps],
        dtype=bool,
    )
    ceilings = [getattr(step, "observation_score_ceiling", None) for step in steps]
    score_ceiling = (
        np.asarray([2.5 if c is None else float(c) for c in ceilings], dtype=np.float32)
        if any(c is not None for c in ceilings)
        else None
    )
    for index, step in enumerate(steps):
        values[index, 2] = float(getattr(step, "intensity_score", 0.0))
        view_amount = float(np.clip(step.forward_view_cloud_field_amount, 0.0, 1.0))
        burnable_amount = float(np.clip(step.forward_burnable_cloud_field_amount, 0.0, 1.0))
        local_view_amount = float(np.clip(step.observer_local_cloud_field_amount, 0.0, 1.0))
        incoming_block = float(np.clip(getattr(step, "solar_incoming_block_risk", 0.0), 0.0, 1.0))
        values[index, 3] = burnable_amount
        values[index, 4] = float(getattr(step, "vividness_score", 0.0))
        values[index, 5] = float(getattr(step, "visibility_score", 0.0))
        values[index, 37] = 1.0 - incoming_block
        values[index, 38] = 1.0
        values[index, 39] = incoming_block
        values[index, 40] = float(np.clip(
            burnable_amount / max(view_amount, 1e-6) if view_amount > 0.0 else 0.0,
            0.0, 1.0,
        ))
        values[index, 42] = values[index, 5]
        values[index, 43] = view_amount
        values[index, 44] = burnable_amount
        values[index, 54] = local_view_amount
        material_quality = float(np.clip(
            (
                getattr(step.top_candidate, "cloud_type_burnability_score", None)
                if step.top_candidate is not None else None
            )
            or (
                getattr(step.top_boundary, "cloud_type_burnability_score", None)
                if step.top_boundary is not None else None
            )
            or (
                getattr(step.top_cloud_hole, "rear_target_burnability_score", None)
                if step.top_cloud_hole is not None else None
            )
            or 0.0,
            0.0, 1.0,
        ))
        satellite_material = getattr(step, "satellite_retrieval_material_evidence", None)
        if satellite_material is not None:
            material_quality = float(np.clip(satellite_material, 0.0, 1.0))
        derived_material_load = burnable_amount * 2.5 * material_quality
        values[index, 46] = float(np.clip(derived_material_load, 0.0, 2.5))
        values[index, 45] = float(np.clip(values[index, 46], 0.0, 2.5))
        values[index, 62] = float(np.clip(values[index, 46], 0.0, 2.5))
        values[index, 60] = incoming_block
        samples[index, 5] = float(aod550)
    valid_indices = np.arange(count, dtype=np.int64)
    if event_time_local is not None and core_window_minutes is not None:
        core_indices = np.asarray([
            index for index, step in enumerate(steps)
            if abs((step.valid_time_local - event_time_local).total_seconds())
            <= float(core_window_minutes) * 60.0
        ], dtype=np.int64)
        if core_indices.size:
            valid_indices = core_indices
    rows = np.zeros((count, 18), dtype=np.float64)
    rows[:, :8] = values[:, [43, 44, 45, 46, 62, 60, 5, 4]]
    rows[:, 8] = offsets
    rows[:, 9] = samples[:, 5]
    rows[:, 10:15] = values[:, [54, 55, 56, 61, 63]]
    rows[:, 15] = burnable_gate.astype(np.float64)
    rows[:, 16] = (
        np.full(count, 2.5, dtype=np.float64)
        if score_ceiling is None
        else np.asarray(score_ceiling, dtype=np.float64)
    )
    valid_mask = np.zeros(count, dtype=bool)
    valid_mask[valid_indices] = True
    rows[:, 17] = valid_mask.astype(np.float64)
    return rows


def _assert_full_step_matches(engine_scan, python_step):
    from dataclasses import fields as dataclass_fields

    for field in ASSESSMENT_STEP_FLOAT_FIELDS:
        engine_value = engine_scan[field]
        python_value = getattr(python_step, field)
        assert engine_value == pytest.approx(python_value, abs=0.0) or engine_value == python_value, (
            f"step.{field}: {engine_value} vs {python_value}"
        )
    for field in ASSESSMENT_STEP_INT_FIELDS:
        assert engine_scan[field] == getattr(python_step, field), f"step.{field}"
    for field in ASSESSMENT_STEP_STR_FIELDS:
        assert engine_scan[field] == getattr(python_step, field), f"step.{field}"
    assert datetime.fromisoformat(engine_scan["valid_time_utc"]) == python_step.valid_time_utc
    assert engine_scan["low_cloud_penalty"] == python_step.low_cloud_penalty
    assert engine_scan["candidate_count"] == python_step.candidate_count
    assert engine_scan["observation_burnable"] == python_step.observation_burnable
    engine_ceiling = engine_scan["observation_score_ceiling"]
    python_ceiling = python_step.observation_score_ceiling
    if python_ceiling is None:
        assert engine_ceiling is None
    else:
        assert engine_ceiling == pytest.approx(python_ceiling)
    assert engine_scan["satellite_retrieval_material_evidence"] == (
        python_step.satellite_retrieval_material_evidence
    )
    assert engine_scan["selected_observer_path_aod550"] == python_step.selected_observer_path_aod550
    assert engine_scan["selected_inflow_path_aod550"] == python_step.selected_inflow_path_aod550
    assert engine_scan["color_diagnosis"] == python_step.color_diagnosis
    assert engine_scan["reasons"] == python_step.reasons
    assert dict(engine_scan["scene_scores"]) == dict(python_step.scene_scores)
    assert engine_scan["local_sky_score"] == python_step.local_sky_score
    assert engine_scan["remote_sky_score"] == python_step.remote_sky_score
    assert engine_scan["convective_risk"] == python_step.convective_risk
    # top objects: the already-verified family lists must agree field by field.
    for key, python_object in (
        ("top_candidate", python_step.top_candidate),
        ("top_boundary", python_step.top_boundary),
        ("top_cloud_hole", python_step.top_cloud_hole),
    ):
        engine_object = engine_scan[key]
        if python_object is None:
            assert engine_object is None, f"step.{key}"
            continue
        assert engine_object is not None, f"step.{key}"
        if key == "top_candidate":
            field_names = ASSESSMENT_SIGNAL_FIELDS
        elif key == "top_boundary":
            field_names = ASSESSMENT_BOUNDARY_FIELDS
        else:
            field_names = ASSESSMENT_CLOUD_HOLE_FIELDS
        for field in field_names:
            engine_value = engine_object[field]
            python_value = getattr(python_object, field)
            if python_value is None:
                assert engine_value is None, f"step.{key}.{field}"
            elif isinstance(python_value, str):
                assert engine_value == python_value, f"step.{key}.{field}"
            else:
                assert engine_value == python_value, f"step.{key}.{field}"
    if python_step.solar_inflow_blockage is None:
        assert engine_scan["solar_inflow_blockage"] is None
    else:
        for field in ASSESSMENT_SOLAR_INFLOW_FIELDS:
            assert engine_scan["solar_inflow_blockage"][field] == getattr(
                python_step.solar_inflow_blockage, field
            ), f"step.solar_inflow_blockage.{field}"
    assert engine_scan["boundary_uncertainty"] is None and python_step.boundary_uncertainty is None
    # cross_section with profile and summary.
    engine_cross = engine_scan["cross_section"]
    python_cross = python_step.cross_section
    for field in ASSESSMENT_CROSS_SECTION_FIELDS:
        engine_value = engine_cross[field]
        python_value = getattr(python_cross, field)
        if field == "summary":
            assert engine_value == python_value, "cross_section.summary"
        elif isinstance(python_value, int) and not isinstance(python_value, bool):
            assert engine_value == python_value, f"cross_section.{field}"
        else:
            assert engine_value == python_value, f"cross_section.{field}"
    assert len(engine_cross["profile"]) == len(python_cross.profile)
    # map cells: both sides are off by default.
    assert engine_scan["map_cells"] == [] and list(python_step.map_cells) == []


def _assert_rows_match(engine_rows, python_rows):
    import numpy as np

    engine = np.asarray(engine_rows, dtype=np.float64)
    expected = np.asarray(python_rows, dtype=np.float64)
    assert engine.shape == expected.shape
    np.testing.assert_allclose(engine, expected, rtol=3e-11, atol=1e-11)


def test_engine_batch_step_and_rows_match_python_on_pinned_fixture(assessment_engine):
    """Full-step acceptance differential: both pinned scans, radar fields from
    the native adjustment, rows against national_batch's float32 oracle."""
    from datetime import timezone as tz
    from types import SimpleNamespace

    from firesky.models import AerosolReading
    from firesky.satellite import calibrated_only_nowcast_adjustment
    from firesky.service import solar_snapshot

    first_manifest, second_manifest, left_path, right_path = pinned_fixture_paths()
    required = (
        first_manifest, second_manifest, left_path, right_path,
        Path(str(left_path) + ".json"), Path(str(right_path) + ".json"),
    )
    missing = [str(path) for path in required if not Path(path).is_file()]
    if missing:
        if os.environ.get("FIRESKY_REQUIRE_RUST_TESTS") == "1":
            pytest.fail(f"pinned nowcast cache is unavailable: {missing}")
        pytest.skip(f"pinned nowcast cache is unavailable: {missing}")

    from scripts.bench_nowcast_adjustment import fixture as pinned_fixture

    os.environ.setdefault("FIRESKY_GFS_PRIOR_BACKEND", "rust")
    os.environ.setdefault("FIRESKY_HSD_BACKEND", "rust")
    from firesky import rust_desktop_process

    rust_desktop_process.close()
    _, wrapped, request, older, latest = pinned_fixture(
        Path(first_manifest).resolve(),
        Path(second_manifest).resolve(),
        Path(left_path).resolve(),
        Path(right_path).resolve(),
    )
    aerosol = AerosolReading(
        0.15, "pinned-nowcast-assessment-differential", "measured",
        request.point.lat, request.point.lon,
    )
    target = older.valid_time_utc + (latest.valid_time_utc - older.valid_time_utc) / 2
    try:
        nowcast = calibrated_only_nowcast_adjustment(
            request, target, 25.0, latest, wrapped,
            source_times_utc=(older.valid_time_utc, latest.valid_time_utc),
            observation_frames=(older, latest),
        )
    finally:
        rust_desktop_process.close()
    if nowcast is None:
        if os.environ.get("FIRESKY_REQUIRE_RUST_TESTS") == "1":
            pytest.fail("pinned nowcast adjustment is unavailable")
        pytest.skip("pinned nowcast adjustment is unavailable")
    radar = {
        "source": nowcast.radar_source,
        "coverage_ratio": nowcast.radar_coverage_ratio,
        "age_minutes": nowcast.radar_age_minutes,
        "max_dbz": nowcast.radar_max_dbz,
        "local_precip_ratio": nowcast.radar_local_precip_ratio,
        "path_precip_ratio": nowcast.radar_path_precip_ratio,
        "convective_ratio": nowcast.radar_convective_ratio,
        "convective_risk": nowcast.radar_convective_risk,
    }
    offsets = sorted(nowcast.observed_features_by_offset)
    assert len(offsets) == 2
    scans = []
    python_steps = []
    for offset in offsets:
        features = list(nowcast.observed_features_by_offset[offset])
        assert features
        source_time = nowcast.observed_feature_times_by_offset[offset]
        solar = solar_snapshot(request.point, source_time, request.timezone_name)
        step = _python_full_step(
            features, solar.elevation_deg, solar.azimuth_deg, aerosol,
            request.point.elevation_m, radar,
            valid_time_utc=source_time.astimezone(tz.utc).isoformat(),
            map_radius_km=request.radius_km,
        )
        python_steps.append(step)
        scans.append(_engine_scan(
            features, solar.elevation_deg, solar.azimuth_deg, radar,
            valid_time_utc=source_time.astimezone(tz.utc).isoformat(),
        ))
    from firesky.rust_desktop_data import nowcast_assessment, nowcast_assessment_as_model

    try:
        payload = nowcast_assessment(
            scans=scans,
            timezone_name=request.timezone_name,
            aerosol=aerosol,
            observer_elevation_m=request.point.elevation_m,
            map_radius_km=request.radius_km,
            include_map_cells=False,
        )
        for position, (engine_scan, python_step) in enumerate(
            zip(payload["scans"], python_steps, strict=True)
        ):
            _assert_full_step_matches(engine_scan, python_step)
        _assert_rows_match(payload["rows"], _python_ledger_rows(
            python_steps, aerosol.aod550,
            None, None,
        ))
        # The bridge rebuilds complete TimeStepAssessment objects.
        rebuilt = nowcast_assessment_as_model(payload, scans=scans)
        assert len(rebuilt["assessments"]) == len(python_steps)
        for rebuilt_step, python_step in zip(rebuilt["assessments"], python_steps, strict=True):
            assert rebuilt_step.burn_score == python_step.burn_score
            assert rebuilt_step.selected_regime == python_step.selected_regime
            assert rebuilt_step.reasons == python_step.reasons
            assert rebuilt_step.observation_burnable == python_step.observation_burnable
    finally:
        rust_desktop_process.close()


def test_engine_assessment_rows_apply_core_window_mask(assessment_engine):
    from datetime import datetime, timezone as tz

    from firesky.models import AerosolReading
    from firesky.rust_desktop_data import nowcast_assessment

    features = [make_feature(horizontal_cloud_probability=0.8)]
    aerosol = AerosolReading(0.2, "mask", "measured", 30.0, 120.0)
    # The event anchor is a UTC instant; the engine converts both sides into
    # the request timezone before comparing (fire_cloud_quality_from_assessments
    # compares local times). 01:00Z is 09:00 Shanghai; 01:05Z is 09:05.
    event_time = datetime(2026, 1, 1, 1, 0, tzinfo=tz.utc)
    payload = nowcast_assessment(
        scans=[
            _engine_scan(features, 24.0, 45.0, valid_time_utc="2026-01-01T01:00:00Z"),
            _engine_scan(features, 24.0, 45.0, valid_time_utc="2026-01-01T01:05:00Z"),
        ],
        timezone_name="Asia/Shanghai",
        aerosol=aerosol,
        observer_elevation_m=0.0,
        event_time_utc=event_time,
        core_window_minutes=10.0,
    )
    assert payload["rows"][0][17] == 1.0
    assert payload["rows"][1][17] == 1.0
    payload = nowcast_assessment(
        scans=[
            _engine_scan(features, 24.0, 45.0, valid_time_utc="2026-01-01T01:00:00Z"),
            _engine_scan(features, 24.0, 45.0, valid_time_utc="2026-01-01T01:30:00Z"),
        ],
        timezone_name="Asia/Shanghai",
        aerosol=aerosol,
        observer_elevation_m=0.0,
        event_time_utc=event_time,
        core_window_minutes=10.0,
    )
    assert payload["rows"][0][17] == 1.0
    assert payload["rows"][1][17] == 0.0


def test_engine_assessment_detail_model_roundtrip(assessment_engine):
    """The slim "model" detail (contract v2) drops the six never-consumed
    list fields on the wire, still rebuilds the identical TimeStepAssessment
    objects, and keeps the full detail as the element-by-element default."""
    from firesky.models import AerosolReading
    from firesky.rust_desktop_data import nowcast_assessment, nowcast_assessment_as_model

    features = [
        make_feature(
            distance_km=120.0 + 30.0 * index, bearing_deg=150.0 + 2.0 * index,
            pressure_hpa=600, geopotential_height_m=5600.0, rh_percent=92.0,
            horizontal_cloud_probability=0.9, cloud_probability_given_cloud=0.75,
        )
        for index in range(3)
    ]
    aerosol = AerosolReading(0.25, "detail", "measured", 30.0, 120.0)
    scans = [_engine_scan(features, 16.0, 150.0)]

    full_payload = nowcast_assessment(
        scans=scans,
        timezone_name="Asia/Shanghai",
        aerosol=aerosol,
        observer_elevation_m=0.0,
    )
    assert full_payload["detail"] == "full"
    model_payload = nowcast_assessment(
        scans=scans,
        timezone_name="Asia/Shanghai",
        aerosol=aerosol,
        observer_elevation_m=0.0,
        detail="model",
    )
    assert model_payload["detail"] == "model"

    dropped = {
        "columns",
        "candidate_signals",
        "local_deck_signals",
        "remote_boundary_signals",
        "boundary_signals",
        "cloud_hole_signals",
    }
    full_scan = full_payload["scans"][0]
    model_scan = model_payload["scans"][0]
    assert dropped <= set(full_scan), "full detail must carry every diagnostic list"
    assert dropped.isdisjoint(set(model_scan)), "model detail must drop all six"
    # Ledger rows and the quality wrapper must be identical on both levels.
    assert model_payload["rows"] == full_payload["rows"]
    assert model_payload["quality"] == full_payload["quality"]
    # The model rebuild must produce identical TimeStepAssessment objects.
    from dataclasses import fields as dataclass_fields
    from firesky.models import TimeStepAssessment

    rebuilt_full = nowcast_assessment_as_model(full_payload, scans=scans)["assessments"]
    rebuilt_model = nowcast_assessment_as_model(model_payload, scans=scans)["assessments"]
    assert len(rebuilt_full) == len(rebuilt_model) == 1
    for field in dataclass_fields(TimeStepAssessment):
        assert getattr(rebuilt_model[0], field.name) == getattr(
            rebuilt_full[0], field.name
        ), f"model detail must rebuild the same {field.name}"

    # Unknown detail levels must be rejected by the engine (fail closed).
    with pytest.raises(RuntimeError):
        nowcast_assessment(
            scans=scans,
            timezone_name="Asia/Shanghai",
            aerosol=aerosol,
            observer_elevation_m=0.0,
            detail="compact",
        )


def test_engine_assessment_rejects_tampered_receipt(monkeypatch, tmp_path):
    import hashlib
    import json as json_module

    from firesky import rust_desktop_data as native_data
    from firesky.rust_desktop_data import nowcast_assessment

    def fake_engine(receipt_mutation=None, value_mutation=None):
        def fake_call(method, params, *, timeout_seconds=None):
            assert method == "desktop_nowcast_assessment"
            assert timeout_seconds is None
            request_bytes = Path(params["request_path"]).read_bytes()
            value = json_module.loads(Path(params["output_path"]).read_bytes()) if Path(
                params["output_path"]
            ).exists() else None
            if value is None:
                # Run the real engine first, then apply mutations on top.
                real_call = native_data.__dict__.get("_real_call_for_test")
                real_call(method, params, timeout_seconds=None)
                value = json_module.loads(Path(params["output_path"]).read_bytes())
            if value_mutation is not None:
                value_mutation(value)
            encoded = json_module.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
            output_path = Path(params["output_path"])
            output_path.write_bytes(encoded)
            receipt = {
                "contract": "rust-desktop-nowcast-assessment-file-v1",
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

    features = [make_feature(horizontal_cloud_probability=0.8)]
    scans = [_engine_scan(features, 24.0, 45.0)]
    aerosol = AerosolReading(0.2, "tamper", "measured", 30.0, 120.0)
    kwargs = dict(
        scans=scans, timezone_name="Asia/Shanghai", aerosol=aerosol,
        observer_elevation_m=0.0,
    )
    # Run once against the real engine to cache nothing; the fake engine must
    # produce a payload itself. Use a stub payload built from the real run.
    real_payload = None
    real_call = native_data.call_engine

    def recording_call(method, params, *, timeout_seconds=None):
        nonlocal real_payload
        result = real_call(method, params, timeout_seconds=timeout_seconds)
        real_payload = json_module.loads(Path(params["output_path"]).read_bytes())
        return result

    monkeypatch.setattr(native_data, "call_engine", recording_call)
    nowcast_assessment(**kwargs)
    assert real_payload is not None

    def fake_call(method, params, *, timeout_seconds=None):
        request_bytes = Path(params["request_path"]).read_bytes()
        value = json_module.loads(json_module.dumps(real_payload))
        encoded = json_module.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
        output_path = Path(params["output_path"])
        output_path.write_bytes(encoded)
        receipt = {
            "contract": "rust-desktop-nowcast-assessment-file-v1",
            "runtime_python_used": False,
            "output_path": str(output_path),
            "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
        }
        return receipt

    # 1. Tampered receipt fields must each be rejected.
    for mutation, message in (
        ("contract", "receipt is invalid"),
        ("bytes", "output identity is invalid"),
        ("sha256", "output identity is invalid"),
        ("request_sha256", "request identity is invalid"),
    ):
        def mutating_call(method, params, *, timeout_seconds=None, mutation=mutation):
            receipt = fake_call(method, params, timeout_seconds=timeout_seconds)
            if mutation == "contract":
                receipt["contract"] = "wrong-file-contract"
            elif mutation == "bytes":
                receipt["bytes"] += 1
            elif mutation == "sha256":
                receipt["sha256"] = "0" * 64
            elif mutation == "request_sha256":
                receipt["request_sha256"] = "0" * 64
            return receipt

        monkeypatch.setattr(native_data, "call_engine", mutating_call)
        with pytest.raises(RuntimeError, match=message):
            nowcast_assessment(**kwargs)

    # 2. Tampered payload identity must be rejected after a successful native run.
    for mutation in ("missing_field", "contract", "feature_row_contract", "runtime_python_used", "scan_count"):
        def payload_mutating_call(method, params, *, timeout_seconds=None, mutation=mutation):
            receipt = fake_call(method, params, timeout_seconds=timeout_seconds)
            output_path = Path(params["output_path"])
            value = json_module.loads(output_path.read_bytes())
            if mutation == "missing_field":
                value.pop("reasons") if "reasons" in value else value["scans"][0].pop("reasons")
            elif mutation == "contract":
                value["contract"] = "wrong-contract"
            elif mutation == "feature_row_contract":
                value["feature_row_contract"] = "legacy-20-field-buffer"
            elif mutation == "runtime_python_used":
                value["runtime_python_used"] = True
            elif mutation == "scan_count":
                value["scan_count"] = 99
            output_path.write_bytes(json_module.dumps(value, ensure_ascii=False).encode())
            receipt["bytes"] = output_path.stat().st_size
            receipt["sha256"] = hashlib.sha256(output_path.read_bytes()).hexdigest()
            return receipt

        monkeypatch.setattr(native_data, "call_engine", payload_mutating_call)
        with pytest.raises(RuntimeError, match="invalid"):
            nowcast_assessment(**kwargs)


def test_engine_assessment_contract_failure_is_not_swallowed(monkeypatch):
    """A contract mismatch must surface, never silently reroute to Python."""
    import hashlib
    import json as json_module

    from firesky import rust_desktop_data as native_data

    calls = []
    python_fallback = []

    def bad_call(method, params, *, timeout_seconds=None):
        calls.append(method)
        output_path = Path(params["output_path"])
        value = {
            "contract": "rust-desktop-nowcast-assessment-v2",
            "feature_row_contract": "legacy-20-field-buffer",
            "runtime_python_used": False,
            "detail": "full",
            "scan_count": 1,
            "rows": [[0.0] * 18],
            "score_ceiling_applied": False,
            "quality": {"contract": "rust-desktop-nowcast-quality-v2"},
            "scans": [],
        }
        encoded = json_module.dumps(value).encode()
        output_path.write_bytes(encoded)
        return {
            "contract": "rust-desktop-nowcast-assessment-file-v1",
            "runtime_python_used": False,
            "output_path": str(output_path),
            "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "request_sha256": hashlib.sha256(Path(params["request_path"]).read_bytes()).hexdigest(),
        }

    monkeypatch.setattr(native_data, "call_engine", bad_call)
    monkeypatch.setattr(
        native_data, "nowcast_assessment_as_model",
        lambda *args, **kwargs: python_fallback.append(True) or (_ for _ in ()).throw(
            AssertionError("contract failure fell through to the model rebuild")
        ),
    )
    from firesky.models import AerosolReading
    from firesky.rust_desktop_data import nowcast_assessment

    aerosol = AerosolReading(0.2, "contract", "measured", 30.0, 120.0)
    with pytest.raises(RuntimeError):
        nowcast_assessment(
            scans=[_engine_scan([make_feature(horizontal_cloud_probability=0.8)], 24.0, 45.0)],
            timezone_name="Asia/Shanghai",
            aerosol=aerosol,
            observer_elevation_m=0.0,
        )
    assert calls == ["desktop_nowcast_assessment"]
    assert python_fallback == []


def test_engine_batch_step_matches_python_with_active_radar(assessment_engine):
    """Radar constraints active: reliability high enough for the strong veto
    path (observation_burnable=False, convective regime, score ceiling)."""
    from datetime import timezone as tz

    from firesky.models import AerosolReading
    from firesky.rust_desktop_data import nowcast_assessment

    radar = {
        "source": "jma-tokyo",
        "coverage_ratio": 1.0,
        "age_minutes": 5.0,
        "max_dbz": 55.0,
        "local_precip_ratio": 0.8,
        "path_precip_ratio": 0.9,
        "convective_ratio": 0.7,
        "convective_risk": 0.5,
    }
    features = [
        make_feature(
            distance_km=120.0 + 30.0 * index, bearing_deg=150.0 + 2.0 * index,
            pressure_hpa=600, geopotential_height_m=5600.0, rh_percent=92.0,
            horizontal_cloud_probability=0.9, cloud_probability_given_cloud=0.75,
        )
        for index in range(4)
    ]
    aerosol = AerosolReading(0.25, "radar-step", "measured", 30.0, 120.0)
    python_step = _python_full_step(
        features, 16.0, 150.0, aerosol, 0.0, radar,
        valid_time_utc="2026-01-01T01:00:00Z",
    )
    payload = nowcast_assessment(
        scans=[_engine_scan(features, 16.0, 150.0, radar)],
        timezone_name="Asia/Shanghai",
        aerosol=aerosol,
        observer_elevation_m=0.0,
    )
    engine_scan = payload["scans"][0]
    # The radar veto must genuinely engage on both sides.
    assert python_step.observation_burnable is False
    assert engine_scan["observation_burnable"] is False
    assert python_step.observation_score_ceiling is not None
    assert python_step.selected_regime == "convective"
    _assert_full_step_matches(engine_scan, python_step)
    assert any("雷达约束" in reason for reason in python_step.reasons)
    assert engine_scan["reasons"] == python_step.reasons


def test_nowcast_assessment_backend_selector(monkeypatch):
    from firesky.rust_desktop_data import nowcast_assessment_backend

    monkeypatch.delenv("FIRESKY_NOWCAST_ASSESSMENT_BACKEND", raising=False)
    assert nowcast_assessment_backend() == "python"
    monkeypatch.setenv("FIRESKY_NOWCAST_ASSESSMENT_BACKEND", "rust")
    assert nowcast_assessment_backend() == "rust"
    monkeypatch.setenv("FIRESKY_NOWCAST_ASSESSMENT_BACKEND", "PYTHON")
    assert nowcast_assessment_backend() == "python"
    monkeypatch.setenv("FIRESKY_NOWCAST_ASSESSMENT_BACKEND", "bogus")
    with pytest.raises(ValueError):
        nowcast_assessment_backend()


def _synthetic_radar_step(features, radar, aerosol, observer_elevation_m=0.0):
    return _python_full_step(
        features, 16.0, 150.0, aerosol, observer_elevation_m, radar,
        valid_time_utc="2026-01-01T01:00:00Z",
    )


def _stub_nowcast(features, radar, *, summary=None, observed_time_utc=None):
    """A real frozen NowcastAdjustment carrying the radar/observation fields."""
    from firesky.models import NOWCAST_ALGORITHM_VERSION, NowcastAdjustment

    observed_time = observed_time_utc or datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)
    return NowcastAdjustment(
        source="stub-satellite",
        image_valid_time_utc=observed_time,
        image_valid_time_local=observed_time.astimezone(ZoneInfo("Asia/Shanghai")),
        image_channel="hsd-b07+b13+b15",
        image_area="stub",
        near_cloud_ratio=0.5,
        far_cloud_ratio=0.5,
        trend_score=0.5,
        visibility_penalty=0.0,
        target_bonus=0.5,
        confidence=0.5,
        summary=list(summary or []),
        radar_source=radar.get("source"),
        radar_coverage_ratio=radar.get("coverage_ratio"),
        radar_age_minutes=radar.get("age_minutes"),
        radar_max_dbz=radar.get("max_dbz"),
        radar_local_precip_ratio=radar.get("local_precip_ratio"),
        radar_path_precip_ratio=radar.get("path_precip_ratio"),
        radar_convective_ratio=radar.get("convective_ratio"),
        radar_convective_risk=radar.get("convective_risk") or 0.0,
        satellite_motion_quality="observation",
        satellite_advection_horizon_hours=0.0,
        satellite_source_times_utc=[observed_time],
        observed_features_by_offset={-5: features},
        observed_feature_times_by_offset={-5: observed_time},
        nowcast_algorithm=NOWCAST_ALGORITHM_VERSION,
        cloud_column_schema_version=4,
        cloud_column_sets=[{
            "schema_version": 4,
            "valid_time_utc": observed_time.astimezone(timezone.utc).isoformat(),
            "source": "stub",
            "vertical_prior_identity": "stub-prior",
            "retrieval_quality": "unknown",
            "low_cloud_state": "unknown",
            "columns": [],
            "motion_dx_px_per_hour": 0.0,
            "motion_dy_px_per_hour": 0.0,
        }],
        vertical_prior_identity="stub-prior",
        vertical_prior_source="unknown",
        satellite_retrieval_quality="unknown",
        satellite_low_cloud_state="unknown",
        satellite_range_bytes=1,
        satellite_range_blocks=1,
        nowcast_point_lat=30.0,
        nowcast_point_lon=120.0,
        nowcast_cache_identity="stub-cache",
    )


def test_nowcast_observation_assessments_radar_missing_is_identity(assessment_engine, monkeypatch):
    """radar_source=None → reliability 0, no strong constraint, no echo-based
    clear-sky inference: both backends produce the same step and the nowcast
    diagnosis carries no radar reason."""
    from firesky.models import AerosolReading
    from firesky.service import nowcast_observation_assessments

    features = [
        make_feature(
            distance_km=120.0 + 30.0 * index, bearing_deg=150.0 + 2.0 * index,
            pressure_hpa=600, geopotential_height_m=5600.0, rh_percent=92.0,
            horizontal_cloud_probability=0.9, cloud_probability_given_cloud=0.75,
        )
        for index in range(3)
    ]
    aerosol = AerosolReading(0.25, "no-radar", "measured", 30.0, 120.0)
    nowcast_stub = _stub_nowcast(features, {"source": None}, summary=["base line"])
    request = SimpleNamespace(
        point=SimpleNamespace(lat=30.0, lon=120.0, elevation_m=0.0),
        timezone_name="Asia/Shanghai",
        radius_km=900.0,
        include_map_cells=False,
    )
    monkeypatch.delenv("FIRESKY_NOWCAST_ASSESSMENT_BACKEND", raising=False)
    python_steps, python_nowcast = nowcast_observation_assessments(
        request, nowcast_stub, aerosol, backend="python"
    )
    rust_steps, rust_nowcast = nowcast_observation_assessments(
        request, nowcast_stub, aerosol, backend="rust"
    )
    assert len(python_steps) == len(rust_steps) == 1
    assert python_steps == rust_steps, "dual-backend steps must be structurally equal"
    assert python_nowcast == rust_nowcast
    # No radar reason anywhere; ceiling stays None; burnable stays True.
    assert not any("雷达约束" in reason for reason in rust_steps[0].reasons)
    assert rust_steps[0].observation_score_ceiling is None
    assert rust_steps[0].observation_burnable is True
    assert rust_nowcast.radar_reliability_score == 0.0
    assert rust_nowcast.summary == ["base line"]


def test_nowcast_observation_assessments_low_coverage_radar_not_strong(assessment_engine, monkeypatch):
    """reliability<0.25: the reason carries the no-strong-constraint suffix on
    both backends and the step fields stay equal."""
    from firesky.models import AerosolReading
    from firesky.service import nowcast_observation_assessments

    features = [
        make_feature(
            distance_km=120.0 + 30.0 * index, bearing_deg=150.0 + 2.0 * index,
            pressure_hpa=600, geopotential_height_m=5600.0, rh_percent=92.0,
            horizontal_cloud_probability=0.9, cloud_probability_given_cloud=0.75,
        )
        for index in range(3)
    ]
    aerosol = AerosolReading(0.25, "low-cov", "measured", 30.0, 120.0)
    radar = {
        "source": "jma-tokyo",
        "coverage_ratio": 0.2,
        "age_minutes": 5.0,
        "max_dbz": 30.0,
        "local_precip_ratio": 0.05,
        "path_precip_ratio": 0.1,
        "convective_ratio": 0.05,
        "convective_risk": 0.1,
    }
    nowcast_stub = _stub_nowcast(features, radar)
    request = SimpleNamespace(
        point=SimpleNamespace(lat=30.0, lon=120.0, elevation_m=0.0),
        timezone_name="Asia/Shanghai",
        radius_km=900.0,
        include_map_cells=False,
    )
    python_steps, _ = nowcast_observation_assessments(request, nowcast_stub, aerosol, backend="python")
    rust_steps, rust_nowcast = nowcast_observation_assessments(request, nowcast_stub, aerosol, backend="rust")
    assert python_steps == rust_steps
    suffix = "覆盖或时效不足，不施加强约束，也不用无回波推断晴空。"
    assert any(suffix in reason for reason in rust_steps[0].reasons)
    assert rust_nowcast.radar_reliability_score == pytest.approx(0.2)
    # reliability * raw risk stays weak: no convective regime flip.
    assert rust_steps[0].selected_regime != "convective"
    assert rust_steps[0].observation_burnable is True


def test_nowcast_observation_assessments_clear_scan_yields_no_candidates(assessment_engine, monkeypatch):
    """A scan of entirely unknown/clear columns: candidate_count=0 and the
    reasons take the no-strong-target branch on both backends."""
    from firesky.models import AerosolReading
    from firesky.service import nowcast_observation_assessments

    features = [
        make_feature(
            distance_km=120.0 + 30.0 * index, bearing_deg=150.0 + 2.0 * index,
            pressure_hpa=600, geopotential_height_m=5600.0, rh_percent=30.0,
            horizontal_cloud_probability=None, cloud_probability_given_cloud=None,
            cloud_fraction_percent=None, cloud_liquid_kg_kg=None, cloud_ice_kg_kg=None,
        )
        for index in range(3)
    ]
    aerosol = AerosolReading(0.25, "clear-scan", "measured", 30.0, 120.0)
    # The stub observes at 00:00 local (16:00Z): the sun is deep below the
    # horizon, so every unknown column fails the illumination gate and no
    # candidate is fabricated from RH-only evidence.
    nowcast_stub = _stub_nowcast(
        features, {"source": None},
        observed_time_utc=datetime(2026, 1, 1, 16, 0, tzinfo=timezone.utc),
    )
    request = SimpleNamespace(
        point=SimpleNamespace(lat=30.0, lon=120.0, elevation_m=0.0),
        timezone_name="Asia/Shanghai",
        radius_km=900.0,
        include_map_cells=False,
    )
    python_steps, _ = nowcast_observation_assessments(request, nowcast_stub, aerosol, backend="python")
    rust_steps, _ = nowcast_observation_assessments(request, nowcast_stub, aerosol, backend="rust")
    assert python_steps == rust_steps
    assert python_steps[0].solar_elevation_deg < 0.0
    assert rust_steps[0].candidate_count == 0
    assert rust_steps[0].top_candidate is None
    assert "在霞光方向上没有找到足够强的中高云目标。" in rust_steps[0].reasons


def test_unknown_retrieval_quality_yields_no_material_evidence():
    """retrieval_quality=unknown columns produce no material evidence; a
    usable column produces the neutral 0.72 reference."""
    from firesky.service import _satellite_retrieval_material_evidence_for_sets

    unknown_set = {"columns": [{"horizontal_cloud_probability": 0.9, "retrieval_quality": "unknown"}]}
    assert _satellite_retrieval_material_evidence_for_sets([unknown_set]) is None
    # An unknown-quality column does not contribute even beside a usable one.
    usable_set = {"columns": [{"horizontal_cloud_probability": 0.9, "retrieval_quality": "good"}]}
    assert _satellite_retrieval_material_evidence_for_sets([usable_set]) == 0.72
    mixed = {"columns": [
        {"horizontal_cloud_probability": 0.9, "retrieval_quality": "good"},
        {"horizontal_cloud_probability": 0.9, "retrieval_quality": "unknown"},
    ]}
    assert _satellite_retrieval_material_evidence_for_sets([mixed]) == 0.72
    # Unknown material must not be invented from a missing probability.
    missing_probability = {"columns": [{"horizontal_cloud_probability": None, "retrieval_quality": "good"}]}
    assert _satellite_retrieval_material_evidence_for_sets([missing_probability]) is None


def test_cloud_column_set_time_guards_fail_closed():
    """Cloud-column set times must be UTC; a naive or missing time raises."""
    from firesky.service import _cloud_column_sets_for_observation_time

    naive = SimpleNamespace(cloud_column_sets=[
        {"valid_time_utc": "2026-01-01T09:00:00", "columns": []}
    ])
    with pytest.raises(ValueError, match="must be UTC"):
        _cloud_column_sets_for_observation_time(
            naive, datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)
        )
    malformed = SimpleNamespace(cloud_column_sets=[{"columns": []}])
    with pytest.raises(ValueError, match="time is invalid"):
        _cloud_column_sets_for_observation_time(
            malformed, datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)
        )
    good = SimpleNamespace(cloud_column_sets=[
        {"valid_time_utc": "2026-01-01T01:00:00Z", "columns": []}
    ])
    assert _cloud_column_sets_for_observation_time(
        good, datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)
    ) == [{"valid_time_utc": "2026-01-01T01:00:00Z", "columns": []}]


def test_nowcast_core_indexes_window_and_fallback():
    from firesky.service import NOWCAST_CORE_WINDOW_MINUTES, nowcast_core_indexes

    anchor = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)

    def step_at(minutes):
        return SimpleNamespace(valid_time_utc=anchor + timedelta(minutes=minutes))

    inside = [step_at(-5), step_at(0), step_at(5)]
    outside = [step_at(-30), step_at(0), step_at(30)]
    assert nowcast_core_indexes(inside, anchor) == [0, 1, 2]
    # Only the core step stays when the others sit outside ±NOWCAST_CORE_WINDOW_MINUTES.
    window = nowcast_core_indexes(outside, anchor)
    assert window == [1]
    assert NOWCAST_CORE_WINDOW_MINUTES > 0
    # The fallback: an empty core keeps every step (they only feed the trend).
    empty = [step_at(-60), step_at(60)]
    assert nowcast_core_indexes(empty, anchor) == [0, 1]


def test_nowcast_duration_is_qualified_rules():
    from firesky.service import nowcast_duration_is_qualified

    base = SimpleNamespace(
        satellite_motion_quality="weak",
        satellite_advection_horizon_hours=0.0,
    )
    single = [SimpleNamespace(
        valid_time_utc=datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc),
        cloud_source_times_utc=[datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)],
    )]
    # Fewer than two assessments never qualify.
    assert nowcast_duration_is_qualified(base, single) is False
    two_scans = single + [SimpleNamespace(
        valid_time_utc=datetime(2026, 1, 1, 1, 5, tzinfo=timezone.utc),
        cloud_source_times_utc=[datetime(2026, 1, 1, 1, 5, tzinfo=timezone.utc)],
    )]
    # Two distinct scored source times qualify directly.
    assert nowcast_duration_is_qualified(base, two_scans) is True
    # One shared source time plus advection (strong motion, positive horizon).
    advected = SimpleNamespace(
        satellite_motion_quality="strong",
        satellite_advection_horizon_hours=0.25,
    )
    shared = [
        SimpleNamespace(
            valid_time_utc=datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc),
            cloud_source_times_utc=[datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)],
        ),
        SimpleNamespace(
            valid_time_utc=datetime(2026, 1, 1, 1, 5, tzinfo=timezone.utc),
            cloud_source_times_utc=[datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc)],
        ),
    ]
    assert nowcast_duration_is_qualified(advected, shared) is True
    # Weak motion without a second source time does not qualify.
    assert nowcast_duration_is_qualified(base, shared) is False


def test_event_quality_best_index_first_max_on_tie():
    """Two steps with identical ledger inputs tie on instant_quality_index;
    max() keeps the earlier index (first-wins)."""
    from firesky.models import TimeStepAssessment
    from firesky.service import apply_unified_event_quality

    base = datetime(2026, 1, 1, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    def make_step(minutes):
        # Real frozen dataclasses: apply_unified_event_quality rebuilds steps
        # through dataclasses.replace.
        return TimeStepAssessment(
            forecast_hour=0,
            valid_time_utc=base + timedelta(minutes=minutes),
            valid_time_local=base + timedelta(minutes=minutes),
            solar_elevation_deg=15.0,
            solar_azimuth_deg=150.0,
            burn_score=0.5,
            intensity_score=0.3,
            extent_score=0.5,
            vividness_score=0.6,
            visibility_score=0.8,
            low_cloud_penalty=0.0,
            candidate_count=0,
            burning_cloud_count=0,
            top_candidate=None,
            forward_view_cloud_field_amount=0.5,
            forward_burnable_cloud_field_amount=0.4,
            observer_local_cloud_field_amount=0.3,
        )

    steps = [make_step(-5), make_step(0), make_step(5)]
    _, quality, best_index = apply_unified_event_quality(
        steps,
        0.2,
        event_time_local=base,
        core_window_minutes=15,
        apply_duration=False,
    )
    indices = [step["instant_quality_index"] for step in quality["steps"]]
    assert indices[0] == indices[1] == indices[2], "the fixture must actually tie"
    assert best_index == 0, "the first max must win the tie (earlier index)"
    # Reversing the storage order flips which step is first — the tie rule
    # follows storage order, never the wall clock beyond it.
    _, quality, best_index = apply_unified_event_quality(
        list(reversed(steps)),
        0.2,
        event_time_local=base,
        core_window_minutes=15,
        apply_duration=False,
    )
    assert best_index == 0


def test_native_assessment_failure_does_not_fall_back(monkeypatch):
    """A native batch failure must propagate, never silently reroute."""
    from firesky import rust_desktop_data as native_data
    from firesky.service import nowcast_observation_assessments

    def failing_call(method, params, *, timeout_seconds=None):
        assert method == "desktop_nowcast_assessment"
        raise RuntimeError("engine unavailable")

    monkeypatch.setenv("FIRESKY_NOWCAST_ASSESSMENT_BACKEND", "rust")
    monkeypatch.setattr(native_data, "call_engine", failing_call)
    nowcast_stub = _stub_nowcast([make_feature()], {"source": None})
    request = SimpleNamespace(
        point=SimpleNamespace(lat=30.0, lon=120.0, elevation_m=0.0),
        timezone_name="Asia/Shanghai",
        radius_km=900.0,
        include_map_cells=False,
    )
    aerosol = AerosolReading(0.2, "fail", "measured", 30.0, 120.0)
    with pytest.raises(RuntimeError, match="engine unavailable"):
        nowcast_observation_assessments(request, nowcast_stub, aerosol, backend="rust")


def test_nowcast_observation_assessments_dual_backend_on_pinned_fixture(assessment_engine):
    """Matrix ⑧: on the pinned dual-scan fixture the python and rust backends
    produce structurally identical constrained assessments and nowcasts."""
    from firesky.models import AerosolReading
    from firesky.satellite import calibrated_only_nowcast_adjustment

    first_manifest, second_manifest, left_path, right_path = pinned_fixture_paths()
    required = (
        first_manifest, second_manifest, left_path, right_path,
        Path(str(left_path) + ".json"), Path(str(right_path) + ".json"),
    )
    missing = [str(path) for path in required if not Path(path).is_file()]
    if missing:
        if os.environ.get("FIRESKY_REQUIRE_RUST_TESTS") == "1":
            pytest.fail(f"pinned nowcast cache is unavailable: {missing}")
        pytest.skip(f"pinned nowcast cache is unavailable: {missing}")

    from scripts.bench_nowcast_adjustment import fixture as pinned_fixture

    os.environ.setdefault("FIRESKY_GFS_PRIOR_BACKEND", "rust")
    os.environ.setdefault("FIRESKY_HSD_BACKEND", "rust")
    from firesky import rust_desktop_process
    from firesky.service import nowcast_observation_assessments

    rust_desktop_process.close()
    _, wrapped, request, older, latest = pinned_fixture(
        Path(first_manifest).resolve(),
        Path(second_manifest).resolve(),
        Path(left_path).resolve(),
        Path(right_path).resolve(),
    )
    aerosol = AerosolReading(
        0.15, "pinned-nowcast-assessment-differential", "measured",
        request.point.lat, request.point.lon,
    )
    target = older.valid_time_utc + (latest.valid_time_utc - older.valid_time_utc) / 2
    try:
        nowcast = calibrated_only_nowcast_adjustment(
            request, target, 25.0, latest, wrapped,
            source_times_utc=(older.valid_time_utc, latest.valid_time_utc),
            observation_frames=(older, latest),
        )
    finally:
        rust_desktop_process.close()
    if nowcast is None:
        if os.environ.get("FIRESKY_REQUIRE_RUST_TESTS") == "1":
            pytest.fail("pinned nowcast adjustment is unavailable")
        pytest.skip("pinned nowcast adjustment is unavailable")
    try:
        python_steps, python_nowcast = nowcast_observation_assessments(
            request, nowcast, aerosol, backend="python"
        )
        rust_steps, rust_nowcast = nowcast_observation_assessments(
            request, nowcast, aerosol, backend="rust"
        )
    finally:
        rust_desktop_process.close()
    assert len(python_steps) == len(rust_steps) == 2
    assert python_steps == rust_steps, "dual-backend steps must be structurally equal"
    assert python_nowcast == rust_nowcast


def pinned_fixture_paths():
    from scripts.bench_nowcast_adjustment import (
        DEFAULT_CENTERS,
        DEFAULT_FIRST_SUFFIX,
        DEFAULT_LEFT,
        DEFAULT_RIGHT,
        DEFAULT_SECOND_SUFFIX,
    )

    return (
        DEFAULT_CENTERS[0] / DEFAULT_FIRST_SUFFIX / "manifest.json",
        DEFAULT_CENTERS[0] / DEFAULT_SECOND_SUFFIX / "manifest.json",
        DEFAULT_LEFT,
        DEFAULT_RIGHT,
    )


def test_engine_assessment_matches_python_on_pinned_hsd_gfs_fixture(
    assessment_engine,
):
    """Acceptance differential: the pinned 08:55Z/09:05Z dual-scan fixture."""
    from firesky.models import AerosolReading
    from firesky.satellite import calibrated_only_nowcast_adjustment
    from firesky.service import solar_snapshot

    first_manifest, second_manifest, left_path, right_path = pinned_fixture_paths()
    required = (
        first_manifest,
        second_manifest,
        left_path,
        right_path,
        Path(str(left_path) + ".json"),
        Path(str(right_path) + ".json"),
    )
    missing = [str(path) for path in required if not Path(path).is_file()]
    if missing:
        if os.environ.get("FIRESKY_REQUIRE_RUST_TESTS") == "1":
            pytest.fail(f"pinned nowcast cache is unavailable: {missing}")
        pytest.skip(f"pinned nowcast cache is unavailable: {missing}")

    from scripts.bench_nowcast_adjustment import fixture as pinned_fixture

    os.environ.setdefault("FIRESKY_GFS_PRIOR_BACKEND", "rust")
    os.environ.setdefault("FIRESKY_HSD_BACKEND", "rust")
    from firesky import rust_desktop_process

    rust_desktop_process.close()
    _, wrapped, request, older, latest = pinned_fixture(
        Path(first_manifest).resolve(),
        Path(second_manifest).resolve(),
        Path(left_path).resolve(),
        Path(right_path).resolve(),
    )
    aerosol = AerosolReading(
        0.15,
        "pinned-nowcast-assessment-differential",
        "measured",
        request.point.lat,
        request.point.lon,
    )
    target = older.valid_time_utc + (latest.valid_time_utc - older.valid_time_utc) / 2
    try:
        nowcast = calibrated_only_nowcast_adjustment(
            request,
            target,
            25.0,
            latest,
            wrapped,
            source_times_utc=(older.valid_time_utc, latest.valid_time_utc),
            observation_frames=(older, latest),
        )
    finally:
        rust_desktop_process.close()
    if nowcast is None:
        if os.environ.get("FIRESKY_REQUIRE_RUST_TESTS") == "1":
            pytest.fail("pinned nowcast adjustment is unavailable")
        pytest.skip("pinned nowcast adjustment is unavailable")
    offsets = sorted(nowcast.observed_features_by_offset)
    assert len(offsets) == 2
    compared_scans = 0
    try:
        for offset in offsets:
            features = list(nowcast.observed_features_by_offset[offset])
            assert features
            source_time = nowcast.observed_feature_times_by_offset[offset]
            solar = solar_snapshot(request.point, source_time, request.timezone_name)
            assert_engine_candidates_match_python(
                features,
                solar.elevation_deg,
                solar.azimuth_deg,
                aerosol,
                request.point.elevation_m,
            )
            compared_scans += 1
    finally:
        rust_desktop_process.close()
    assert compared_scans == 2
def provider_features_for_scan(wrapped, request, frame):
    """Materialize one scan's calibrated features through the pinned provider."""
    from firesky.rust_desktop_data import calibrated_retrieval

    context = wrapped.native_retrieval_context(frame.valid_time_utc)
    if context is None:
        raise RuntimeError("pinned fixture has no retrieval context for a scan")
    features, columns = calibrated_retrieval(
        frame,
        request,
        context,
        return_priors=False,
    )
    return features


def test_conftest_repairs_joined_pytest_path_args():
    """The CI runner must survive its own quoting bug: four test paths joined
    into one argument are split back apart by tests/conftest.py before
    collection, so the focused gate command runs instead of erroring with
    exit 4 ("file or directory not found")."""
    import os
    import subprocess
    import sys as sys_module

    if os.environ.get("FIRESKY_CONFTEST_REPAIR_PROBE"):
        # The probe run reproduces the gate command, which collects this very
        # test; without this guard each level would spawn another pytest run
        # forever.  The probe still proves the joined paths collect cleanly.
        import pytest

        pytest.skip("inner conftest-repair probe run")

    joined = " ".join((
        "tests/test_rust_nowcast_assessment.py",
        "tests/test_rust_nowcast_adjustment.py",
        "tests/test_rust_quality.py",
        "tests/test_scoring_invariants.py",
    ))
    completed = subprocess.run(
        [sys_module.executable, "-m", "pytest", joined, "-q"],
        capture_output=True,
        text=True,
        timeout=600,
        env=dict(os.environ, FIRESKY_CONFTEST_REPAIR_PROBE="1"),
    )
    assert completed.returncode == 0, completed.stdout[-2000:] + completed.stderr[-500:]
