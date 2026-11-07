"""Unit contract for the nowcast publish-coverage semantics (v4).

v4 契约：有可用校准采样列就发布（coverage > 0），覆盖率随结果披露；
仅当整个视图没有任何可用列（coverage == 0）时 fail-closed。
"""

from datetime import datetime, timezone

from firesky.satellite import calibrated_sample_coverage_ratio
from firesky.satellite_cloud_column import (
    SATELLITE_CLOUD_COLUMN_ALGORITHM,
    SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION,
    SatelliteCloudColumn,
    SatelliteCloudColumnSet,
)

VALID = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def _column(bearing: float, distance: float, *, quality: str = "medium") -> SatelliteCloudColumn:
    return SatelliteCloudColumn(
        schema_version=SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION,
        algorithm=SATELLITE_CLOUD_COLUMN_ALGORITHM,
        valid_time_utc=VALID,
        lat=30.2741,
        lon=120.1551,
        distance_km=distance,
        bearing_deg=bearing,
        base_p10_asl_m=None,
        base_p50_asl_m=None,
        base_p90_asl_m=None,
        top_asl_m=None,
        terrain_asl_m=8.0,
        phase="water",
        optical_depth=None,
        cloud_probability_by_height={},
        horizontal_cloud_probability=None,
        low_cloud_state="clear",
        retrieval_quality=quality,
        source="test-hsd",
        vertical_prior_identity="gfs-test",
        vertical_prior_source="gfs-interpolated-vertical-prior",
    )


def _column_set(*columns: SatelliteCloudColumn) -> SatelliteCloudColumnSet:
    return SatelliteCloudColumnSet(
        schema_version=SATELLITE_CLOUD_COLUMN_SCHEMA_VERSION,
        valid_time_utc=VALID,
        source="hsd",
        vertical_prior_identity="gfs-test",
        retrieval_quality="medium",
        low_cloud_state="clear",
        columns=tuple(columns),
    )


SAMPLES = [(90.0, 30.0), (90.0, 70.0), (180.0, 30.0), (180.0, 70.0)]


def test_all_usable_samples_give_full_coverage():
    columns = [_column(b, d) for b, d in SAMPLES]
    ratio = calibrated_sample_coverage_ratio(
        [_column_set(*columns)], sample_points=SAMPLES
    )
    assert ratio == 1.0


def test_unknown_columns_count_as_unusable():
    columns = [
        _column(b, d, quality="unknown" if b == 180.0 else "medium")
        for b, d in SAMPLES
    ]
    ratio = calibrated_sample_coverage_ratio(
        [_column_set(*columns)], sample_points=SAMPLES
    )
    assert ratio == 0.5


def test_sample_without_nearby_column_is_unusable():
    # 三个采样点有列，第四个（bearing 270）完全无列。
    columns = [_column(90.0, 30.0), _column(90.0, 70.0), _column(180.0, 30.0)]
    ratio = calibrated_sample_coverage_ratio(
        [_column_set(*columns)],
        sample_points=[*SAMPLES, (270.0, 30.0)],
    )
    assert ratio == 0.6


def test_matching_tolerance_is_bearing_1deg_distance_5km():
    # 列与采样点偏差在容差边缘之外 → 不匹配。
    columns = [_column(91.5, 30.0), _column(90.0, 36.0)]
    ratio = calibrated_sample_coverage_ratio(
        [_column_set(*columns)], sample_points=[(90.0, 30.0)]
    )
    assert ratio == 0.0
    # 容差之内 → 匹配并计 usable。
    columns = [_column(90.8, 33.0)]
    ratio = calibrated_sample_coverage_ratio(
        [_column_set(*columns)], sample_points=[(90.0, 30.0)]
    )
    assert ratio == 1.0


def test_non_gfs_prior_is_unusable():
    column = _column(90.0, 30.0)
    object.__setattr__(column, "vertical_prior_source", "ecmwf-vertical-prior")
    ratio = calibrated_sample_coverage_ratio(
        [_column_set(column)], sample_points=[(90.0, 30.0)]
    )
    assert ratio == 0.0


def test_empty_columns_and_empty_samples_give_zero():
    assert (
        calibrated_sample_coverage_ratio([_column_set()], sample_points=SAMPLES)
        == 0.0
    )
    columns = [_column(90.0, 30.0)]
    assert (
        calibrated_sample_coverage_ratio(
            [_column_set(*columns)], sample_points=[]
        )
        == 0.0
    )
