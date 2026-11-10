"""Raw desktop request planning differential tests."""
import pytest
from datetime import datetime, timedelta, timezone

from firesky import desktop_data
from firesky.web_server import WebServerConfig
from test_rust_migration import engine_binary


@pytest.fixture
def config(tmp_path):
    return WebServerConfig(
        gfs_cache_dir=tmp_path / "gfs",
        ecmwf_cache_dir=tmp_path / "ec",
        cams_cache_dir=tmp_path / "cams",
        satellite_cache_dir=tmp_path / "hsd",
    )


def comparable(context):
    return {
        "points": context["points"],
        "common": context["common"],
        "cycle": context["cycle"],
        "bbox": context["bbox"],
        "area": context["area"],
        "hours": context["hours"],
        "times": context["times"],
        "ec_point": context["ec_point"],
        "terrain_pending": context["terrain_pending"],
    }


def test_desktop_rust_selector_uses_native_hsd_provider_by_default(
    monkeypatch, config
):
    from firesky.satellite_cloud_column import RustHsdCompactCacheProvider
    from firesky.web_server import _cached_satellite_frame_provider

    monkeypatch.delenv("FIRESKY_HSD_BACKEND", raising=False)
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    provider = _cached_satellite_frame_provider(config)

    assert isinstance(provider, RustHsdCompactCacheProvider)


def test_gfs_prior_selector_defaults_to_rust_with_python_override(monkeypatch):
    from firesky.service import _native_gfs_prior_enabled

    monkeypatch.delenv("FIRESKY_GFS_PRIOR_BACKEND", raising=False)
    assert _native_gfs_prior_enabled() is True
    monkeypatch.setenv("FIRESKY_GFS_PRIOR_BACKEND", " rust ")
    assert _native_gfs_prior_enabled() is True
    monkeypatch.setenv("FIRESKY_GFS_PRIOR_BACKEND", "python")
    assert _native_gfs_prior_enabled() is False
    for invalid in ("auto", "native", ""):
        monkeypatch.setenv("FIRESKY_GFS_PRIOR_BACKEND", invalid)
        with pytest.raises(ValueError, match="python or rust"):
            _native_gfs_prior_enabled()


def test_decoded_prior_internal_error_is_not_hidden_by_feature_fallback(monkeypatch):
    from firesky.models import ForecastRequest
    from firesky.geometry import GeoPoint
    from firesky.service import (
        GfsNowcastVerticalPriorProvider,
        _SharedGfsNowcastDomain,
    )

    class Client:
        resolution = "0p25"
        _res_grid_deg = 0.25

    provider = GfsNowcastVerticalPriorProvider(Client())
    request = ForecastRequest(
        target_date=datetime(2026, 11, 9, tzinfo=timezone.utc).date(),
        timezone_name="UTC",
        point=GeoPoint(30.0, 120.0),
        radius_km=100.0,
        gfs_cycle_override=datetime(2026, 11, 9, tzinfo=timezone.utc),
    )
    domain = _SharedGfsNowcastDomain(
        grid=object(),
        cache_path="unavailable.grib2",
        source_url="fixture",
        bbox=(0.0, 0.0, 0.0, 0.0),
    )
    provider._snapshot = lambda *_args: domain
    monkeypatch.setenv("FIRESKY_GFS_PRIOR_BACKEND", "rust")
    monkeypatch.setattr(
        provider,
        "_decoded_batch",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("sidecar unavailable")),
    )
    with pytest.raises(RuntimeError, match="sidecar unavailable"):
        provider._batch(
            request,
            [GeoPoint(30.0, 120.0)],
            datetime(2026, 11, 9, 1, tzinfo=timezone.utc),
        )


def test_decoded_prior_invalid_cache_uses_feature_batch_without_python_fallback(
    engine_binary, monkeypatch, tmp_path
):
    from firesky.models import ForecastRequest
    from firesky.geometry import GeoPoint
    from firesky.service import (
        GfsNowcastVerticalPriorProvider,
        _SharedGfsNowcastDomain,
    )

    class Client:
        resolution = "0p25"
        _res_grid_deg = 0.25

    provider = GfsNowcastVerticalPriorProvider(Client())
    request = ForecastRequest(
        target_date=datetime(2026, 11, 9, tzinfo=timezone.utc).date(),
        timezone_name="UTC",
        point=GeoPoint(30.0, 120.0),
        radius_km=100.0,
        gfs_cycle_override=datetime(2026, 11, 9, tzinfo=timezone.utc),
    )
    raw_path = tmp_path / "unavailable.grib2"
    raw_path.write_bytes(b"fixture")
    domain = _SharedGfsNowcastDomain(
        grid=object(),
        cache_path=str(raw_path),
        source_url="fixture",
        bbox=(0.0, 0.0, 0.0, 0.0),
    )
    provider._snapshot = lambda *_args: domain
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setenv("FIRESKY_GFS_PRIOR_BACKEND", "rust")
    with pytest.raises(AttributeError):
        provider._batch(
            request,
            [GeoPoint(30.0, 120.0)],
            datetime(2026, 11, 9, 1, tzinfo=timezone.utc),
        )


def test_native_gfs_prior_matches_python_interpolation(
    engine_binary, monkeypatch
):
    from firesky.models import GridCellFeature
    from firesky.rust_desktop_data import (
        nowcast_gfs_prior,
        nowcast_gfs_prior_shared_batch,
        nowcast_gfs_priors,
    )
    from firesky.satellite_cloud_column import vertical_prior_from_features
    from firesky.service import _interpolate_vertical_prior_features

    valid_time = datetime(2026, 11, 9, 8, 30, tzinfo=timezone.utc)
    cycle_time = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
    def feature(height, pressure, temperature, humidity, cloud):
        return GridCellFeature(
            lat=30.2741,
            lon=120.1551,
            distance_km=0.0,
            bearing_deg=0.0,
            pressure_hpa=pressure,
            geopotential_height_m=height,
            rh_percent=humidity,
            temperature_k=temperature,
            omega_pa_s=None,
            u_ms=None,
            v_ms=None,
            cloud_fraction_percent=cloud,
        )

    left = [
        feature(10.0, 1000, 291.0, 50.0, 5.0),
        feature(1000.0, 900, 286.0, 80.0, 70.0),
        feature(2500.0, 800, 280.0, 85.0, 60.0),
    ]
    right = [
        feature(14.0, 1000, 292.0, 55.0, 15.0),
        feature(1010.0, 900, 287.0, 82.0, 80.0),
        feature(2520.0, 800, 281.0, 88.0, 50.0),
    ]
    fraction = 0.5
    identity = "gfs-prior:test"
    expected_features = _interpolate_vertical_prior_features(left, right, fraction)
    expected = vertical_prior_from_features(
        valid_time_utc=valid_time,
        cycle_time_utc=cycle_time,
        forecast_hour=8,
        source="gfs-interpolated-vertical-prior",
        identity=identity,
        terrain_asl_m=7.0,
        features=expected_features,
        target_lat=30.2741,
        target_lon=120.1551,
    )

    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    request = dict(
        valid_time_utc=valid_time,
        cycle_time_utc=cycle_time,
        forecast_hour=8,
        source="gfs-interpolated-vertical-prior",
        identity=identity,
        terrain_left_asl_m=5.0,
        terrain_right_asl_m=9.0,
        target_lat=30.2741,
        target_lon=120.1551,
        fraction=fraction,
        left_features=left,
        right_features=right,
    )
    actual = nowcast_gfs_prior(**request)
    assert actual.valid_time_utc == expected.valid_time_utc
    assert actual.cycle_time_utc == expected.cycle_time_utc
    assert actual.forecast_hour == expected.forecast_hour
    assert actual.source == expected.source
    assert actual.identity == expected.identity
    assert actual.terrain_asl_m == pytest.approx(expected.terrain_asl_m)
    assert actual.target_lat == pytest.approx(expected.target_lat)
    assert actual.target_lon == pytest.approx(expected.target_lon)
    assert len(actual.levels) == len(expected.levels)
    for native_level, python_level in zip(actual.levels, expected.levels, strict=True):
        assert native_level.pressure_hpa == python_level.pressure_hpa
        assert native_level.height_asl_m == pytest.approx(python_level.height_asl_m)
        assert native_level.temperature_k == pytest.approx(python_level.temperature_k)
        assert native_level.rh_percent == pytest.approx(python_level.rh_percent)
        assert native_level.model_cloud_fraction == pytest.approx(
            python_level.model_cloud_fraction
        )
    batch = nowcast_gfs_priors(
        [{**request, "identity": f"gfs-prior:test-{index}"} for index in range(8)]
    )
    assert len(batch) == 8
    for item in batch:
        assert item.levels == actual.levels
        assert item.terrain_asl_m == pytest.approx(actual.terrain_asl_m)
    shared_batch = nowcast_gfs_prior_shared_batch(
        valid_time_utc=valid_time,
        cycle_time_utc=cycle_time,
        forecast_hour=8,
        source="gfs-interpolated-vertical-prior",
        fraction=fraction,
        left_features=left,
        right_features=right,
        requests=[
            {
                "identity": f"gfs-prior:shared-{index}",
                "terrain_left_asl_m": 5.0,
                "terrain_right_asl_m": 9.0,
                "target_lat": 30.2741,
                "target_lon": 120.1551,
            }
            for index in range(8)
        ],
    )
    assert len(shared_batch) == 8
    for item in shared_batch:
        assert item.levels == actual.levels
        assert item.terrain_asl_m == pytest.approx(actual.terrain_asl_m)


def test_gfs_prior_provider_uses_shared_native_batch(
    engine_binary, monkeypatch
):
    import numpy as np
    from types import SimpleNamespace

    from firesky.geometry import GeoPoint
    from firesky.models import ForecastRequest, GridCellFeature
    from firesky.service import (
        GfsNowcastVerticalPriorProvider,
        _SharedGfsNowcastDomain,
    )

    valid_time = datetime(2026, 11, 9, 8, 30, tzinfo=timezone.utc)
    cycle_time = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
    center = GeoPoint(30.2741, 120.1551)

    def features(offset):
        return [
            GridCellFeature(
                lat=30.2741 + offset,
                lon=120.1551 + offset,
                distance_km=0.0,
                bearing_deg=0.0,
                pressure_hpa=pressure,
                geopotential_height_m=height,
                rh_percent=humidity,
                temperature_k=temperature,
                omega_pa_s=None,
                u_ms=None,
                v_ms=None,
                cloud_fraction_percent=cloud,
            )
            for pressure, height, humidity, temperature, cloud in (
                (925, 700.0, 72.0, 286.0, 60.0),
                (850, 1500.0, 80.0, 280.0, 70.0),
                (800, 2200.0, 84.0, 275.0, 65.0),
            )
        ]

    left_features = features(0.0)
    right_features = features(0.0)
    right_features = [
        GridCellFeature(
            **{
                **item.__dict__,
                "geopotential_height_m": item.geopotential_height_m + 10.0,
                "temperature_k": item.temperature_k + 1.0,
            }
        )
        for item in right_features
    ]
    left_grid = SimpleNamespace(
        latitudes=np.asarray([center.lat], dtype=float),
        longitudes=np.asarray([center.lon], dtype=float),
        fields={
            ("surface", 0, "orog"): np.asarray([8.0], dtype=float),
        },
    )
    right_grid = SimpleNamespace(
        latitudes=np.asarray([center.lat], dtype=float),
        longitudes=np.asarray([center.lon], dtype=float),
        fields={
            ("surface", 0, "orog"): np.asarray([12.0], dtype=float),
        },
    )
    domains = {
        8: _SharedGfsNowcastDomain(
            grid=left_grid,
            cache_path="left",
            source_url="left",
            bbox=(0.0, 0.0, 0.0, 0.0),
        ),
        9: _SharedGfsNowcastDomain(
            grid=right_grid,
            cache_path="right",
            source_url="right",
            bbox=(0.0, 0.0, 0.0, 0.0),
        ),
    }

    class Client:
        resolution = "0p25"
        _res_grid_deg = 0.25

    provider = GfsNowcastVerticalPriorProvider(Client())
    provider._snapshot = lambda _request, _cycle, hour: domains[hour]

    import firesky.service as service

    monkeypatch.setattr(service, "choose_model_cycle", lambda *_args: cycle_time)
    monkeypatch.setattr(
        service,
        "features_from_decoded_grid",
        lambda grid, _point, _radius: (
            left_features if grid is left_grid else right_features
        ),
    )
    monkeypatch.setattr(
        service.GfsNowcastVerticalPriorProvider,
        "_decoded_batch",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setenv("FIRESKY_GFS_PRIOR_BACKEND", "rust")
    request = ForecastRequest(
        target_date=valid_time.date(),
        timezone_name="UTC",
        point=center,
        radius_km=100.0,
        gfs_cycle_override=cycle_time,
    )
    points = [
        GeoPoint(center.lat, center.lon),
        GeoPoint(center.lat + 0.01, center.lon + 0.01),
    ]
    values = provider._batch(request, points, valid_time)
    assert len(values) == len(points)
    assert all(value is not None for value in values)
    assert [value.identity for value in values] == [
        f"gfs-prior:{cycle_time:%Y%m%d%H}:f008-009:0p25:domain="
        f"{__import__('hashlib').sha256(b'left|right').hexdigest()[:16]}:"
        f"point={point.lat:+.5f},{point.lon:+.5f}"
        for point in points
    ]
    assert [value.terrain_asl_m for value in values] == pytest.approx([10.0, 10.0])
    large_points = [
        GeoPoint(center.lat + (index % 10) * 0.001, center.lon + (index // 10) * 0.001)
        for index in range(130)
    ]
    large_values = provider._batch(request, large_points, valid_time)
    assert len(large_values) == len(large_points)
    assert all(value is not None for value in large_values)


def test_gfs_prior_provider_decoded_sidecar_batch_matches_python_reference(
    engine_binary, monkeypatch, tmp_path
):
    import numpy as np

    from firesky.gfs import (
        _DecodedGfsGrid,
        decoded_grid_cache_path,
        write_mmap_decoded_grid,
    )
    from firesky.geometry import GeoPoint
    from firesky.models import ForecastRequest
    from firesky.service import (
        GfsNowcastVerticalPriorProvider,
        _SharedGfsNowcastDomain,
    )

    valid_time = datetime(2026, 11, 9, 8, 30, tzinfo=timezone.utc)
    cycle_time = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
    center = GeoPoint(30.2741, 120.1551)
    latitudes = np.asarray(
        [center.lat - 0.25, center.lat, center.lat + 0.25], dtype=np.float32
    )
    longitudes = np.asarray(
        [center.lon - 0.25, center.lon, center.lon + 0.25], dtype=np.float32
    )
    fields = {
        ("surface", 0, "orog"): np.asarray([4.0, 8.0, 12.0], dtype=np.float32),
    }
    for offset, level in enumerate((925, 850, 300)):
        prefix = np.asarray([offset, offset + 1.0, offset + 2.0], dtype=np.float32)
        fields[("isobaricInhPa", level, "gh")] = prefix * 100.0 + 700.0
        fields[("isobaricInhPa", level, "r")] = prefix * 10.0 + 50.0
        fields[("isobaricInhPa", level, "t")] = prefix + 280.0
        fields[("isobaricInhPa", level, "tcc")] = prefix * 20.0 + 10.0
    fields[("highCloudLayer", 0, "hcc")] = np.asarray(
        [80.0, 81.0, 82.0], dtype=np.float32
    )
    raw_paths = {}
    domains = {}
    for hour, multiplier in ((8, 0), (9, 10)):
        raw_path = tmp_path / f"f{hour:03}.grib2"
        raw_path.write_bytes(f"fixture-{hour}".encode())
        raw_paths[hour] = raw_path
        cache_path = decoded_grid_cache_path(raw_path)
        shifted_fields = {
            key: (
                values
                if key[2] == "orog"
                else values + np.float32(multiplier)
            )
            for key, values in fields.items()
        }
        write_mmap_decoded_grid(
            cache_path,
            _DecodedGfsGrid(latitudes.copy(), longitudes.copy(), shifted_fields),
        )
        domains[hour] = _SharedGfsNowcastDomain(
            grid=_DecodedGfsGrid(latitudes.copy(), longitudes.copy(), shifted_fields),
            cache_path=str(raw_path),
            source_url=f"fixture-{hour}",
            bbox=(0.0, 0.0, 0.0, 0.0),
        )

    class Client:
        resolution = "0p25"
        _res_grid_deg = 0.25

    provider = GfsNowcastVerticalPriorProvider(Client())
    provider._snapshot = lambda _request, _cycle, hour: domains[hour]
    import firesky.service as service

    monkeypatch.setattr(service, "choose_model_cycle", lambda *_args: cycle_time)
    request = ForecastRequest(
        target_date=valid_time.date(),
        timezone_name="UTC",
        point=center,
        radius_km=100.0,
        gfs_cycle_override=cycle_time,
    )
    points = [GeoPoint(center.lat, center.lon), GeoPoint(center.lat + 0.01, center.lon)]
    monkeypatch.setenv("FIRESKY_GFS_PRIOR_BACKEND", "python")
    expected = provider._batch(request, points, valid_time)
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setenv("FIRESKY_GFS_PRIOR_BACKEND", "rust")

    def reject_python_feature_extraction(*_args, **_kwargs):
        raise AssertionError("native decoded prior path fell back to Python features")

    monkeypatch.setattr(
        service, "features_from_decoded_grid", reject_python_feature_extraction
    )
    actual = provider._batch(request, points, valid_time)
    assert all(value is not None for value in expected + actual)
    for got, want in zip(actual, expected, strict=True):
        assert got.identity == want.identity
        assert got.valid_time_utc == want.valid_time_utc
        assert got.cycle_time_utc == want.cycle_time_utc
        assert got.forecast_hour == want.forecast_hour
        assert got.source == want.source
        assert got.target_lat == pytest.approx(want.target_lat)
        assert got.target_lon == pytest.approx(want.target_lon)
        assert got.terrain_asl_m == pytest.approx(want.terrain_asl_m)
        assert len(got.levels) == len(want.levels)
        for native_level, python_level in zip(got.levels, want.levels, strict=True):
            assert native_level.pressure_hpa == python_level.pressure_hpa
            assert native_level.height_asl_m == pytest.approx(python_level.height_asl_m)
            assert native_level.rh_percent == pytest.approx(python_level.rh_percent)
            assert native_level.temperature_k == pytest.approx(
                python_level.temperature_k
            )
            assert native_level.model_cloud_fraction == pytest.approx(
                python_level.model_cloud_fraction
            )


@pytest.mark.parametrize(
    "payload,regional",
    [
        (
            {
                "name": "杭州",
                "lat": 30.2741,
                "lon": 120.1551,
                "elevation_m": 8,
                "timezone_name": "Asia/Shanghai",
                "target_date": "2026-11-08",
                "phenomenon": "dusk",
                "model_source": "gfs",
            },
            False,
        ),
        (
            {
                "model_source": "ec",
                "target_date": "2026-11-08",
                "phenomenon": "dawn",
                "points": [
                    {
                        "name": "test",
                        "lat": 30.0,
                        "lon": 120.0,
                        "elevation_source": "observer",
                        "elevation_m": 4,
                        "timezone_name": "Asia/Shanghai",
                    }
                ],
            },
            True,
        ),
        (
            {
                "model_source": "gfs",
                "target_date": "2026-11-08",
                "phenomenon": "dusk",
                "regions": ["custom"],
                "custom_bounds": {
                    "west": 120.0,
                    "east": 120.5,
                    "south": 30.0,
                    "north": 30.5,
                },
                "custom_timezone_name": "Asia/Shanghai",
            },
            True,
        ),
    ],
)
def test_raw_context_matches_python_reference(
    engine_binary, monkeypatch, config, payload, regional
):
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "python")
    expected = desktop_data.model_context(
        payload, regional=regional, config=config, fast=True
    )
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    actual = desktop_data.model_context(
        payload, regional=regional, config=config, fast=True
    )
    assert comparable(actual) == comparable(expected)


def test_raw_rust_context_does_not_call_python_planner(
    engine_binary, monkeypatch, config
):
    payload = {
        "name": "杭州",
        "lat": 30.2741,
        "lon": 120.1551,
        "elevation_m": 8,
        "timezone_name": "Asia/Shanghai",
        "target_date": "2026-11-08",
        "phenomenon": "dusk",
        "model_source": "gfs",
    }

    def forbidden(*args, **kwargs):
        raise AssertionError("Python model_context implementation was called")

    monkeypatch.setattr(desktop_data, "_python_model_context", forbidden)
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    context = desktop_data.model_context(payload, config=config, fast=True)
    assert context["common"]["model_source"] == "gfs"
    assert context["points"][0]["_elevation_source"] == "model-grid"


def test_raw_nowcast_context_matches_python_with_pinned_cycle(
    engine_binary, monkeypatch, config
):
    payload = {
        "name": "杭州",
        "lat": 30.2741,
        "lon": 120.1551,
        "elevation_m": 8,
        "timezone_name": "Asia/Shanghai",
        "target_date": "2026-11-09",
        "phenomenon": "dusk",
        "radius_km": 900,
        "reference_time_utc": "2026-11-09T08:00:00Z",
        "gfs_cycle_override": "2026-11-09T00:00:00Z",
    }
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "python")
    expected = desktop_data.nowcast_context(payload, config=config, fast=True)
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    actual = desktop_data.nowcast_context(payload, config=config, fast=True)
    for key in ("anchor", "reference", "lookup", "cycle", "bbox", "times", "region", "area"):
        assert actual[key] == expected[key]
    assert actual["request"] == expected["request"]


def test_raw_nowcast_context_rejects_future_event_outside_four_hour_window(
    engine_binary, monkeypatch, config
):
    payload = {
        "lat": 30.2741,
        "lon": 120.1551,
        "elevation_m": 8,
        "timezone_name": "Asia/Shanghai",
        "target_date": "2026-11-10",
        "phenomenon": "dusk",
        "reference_time_utc": "2026-11-09T00:00:00Z",
        "gfs_cycle_override": "2026-11-09T00:00:00Z",
    }
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    with pytest.raises(RuntimeError, match="four hours|四小时"):
        desktop_data.nowcast_context(payload, config=config, fast=True)


def test_nowcast_cache_check_matches_python_without_hsd_frames(
    engine_binary, monkeypatch, config
):
    from firesky.satellite_cloud_column import AutoHsdCompactCacheProvider

    payload = {
        "name": "杭州",
        "lat": 30.2741,
        "lon": 120.1551,
        "elevation_m": 8,
        "timezone_name": "Asia/Shanghai",
        "target_date": "2026-11-09",
        "phenomenon": "dusk",
        "radius_km": 900,
        "reference_time_utc": "2026-11-09T08:00:00Z",
        "gfs_cycle_override": "2026-11-09T00:00:00Z",
    }
    monkeypatch.setattr(
        AutoHsdCompactCacheProvider,
        "fetch_cached_series",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(desktop_data, "published_cycles", lambda sources: {})
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "python")
    expected = desktop_data.check_nowcast_data(
        payload, config=config, probe=False, fast=True
    )
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    actual = desktop_data.check_nowcast_data(
        payload, config=config, probe=False, fast=True
    )
    assert actual == expected


def test_nowcast_gfs_plan_matches_python_prior_requirements(
    engine_binary, monkeypatch, config
):
    from firesky.geometry import bounding_box
    from firesky.gfs import canonical_subset_bbox
    from firesky.rust_desktop_data import nowcast_gfs_plan

    payload = {
        "lat": 30.2741,
        "lon": 120.1551,
        "elevation_m": 8,
        "timezone_name": "Asia/Shanghai",
        "target_date": "2026-11-09",
        "phenomenon": "dusk",
        "radius_km": 900,
        "reference_time_utc": "2026-11-09T08:00:00Z",
        "gfs_cycle_override": "2026-11-09T00:00:00Z",
    }
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    context = desktop_data.nowcast_context(payload, config=config, fast=True)
    valid_times = context["times"] + [
        datetime(2026, 11, 9, 9, 7, tzinfo=timezone.utc),
        datetime(2026, 11, 9, 9, 42, tzinfo=timezone.utc),
    ]
    actual = nowcast_gfs_plan(context, valid_times)

    expected_hours = set()
    cycle = context["cycle"]
    for valid_time in valid_times:
        elapsed = (valid_time - cycle).total_seconds() / 3600.0
        if elapsed < -1e-6:
            continue
        left = max(0, int(elapsed // 1))
        expected_hours.add(left)
        if abs(elapsed - left) >= 1e-6:
            expected_hours.add(left + 1)
    expected_bbox = canonical_subset_bbox(
        bounding_box(
            context["request"].point,
            max(context["request"].radius_km, 1000.0) + 2.0 * 0.25 * 111.2,
        ),
        0.25,
    )
    assert actual["cycle"] == context["cycle"]
    assert actual["hours"] == sorted(expected_hours)
    assert actual["bbox"] == expected_bbox


def test_nowcast_hsd_cache_matches_python_compact_validation(
    engine_binary, monkeypatch, config
):
    from datetime import timedelta
    import numpy as np
    from firesky.satellite_cloud_column import (
        CalibratedSatelliteFrame,
        HsdCompactCache,
        HsdCompactCacheProvider,
    )
    from firesky.rust_desktop_data import nowcast_hsd_cache

    point = type("Point", (), {"lat": 30.2741, "lon": 120.1551})()
    reference = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
    cache = HsdCompactCache(config.satellite_cache_dir / "compact")
    latitudes = np.full((3, 4), point.lat, dtype=np.float32)
    longitudes = np.full((3, 4), point.lon, dtype=np.float32)
    for valid_time in (
        reference - timedelta(minutes=5),
        reference - timedelta(minutes=15),
        reference - timedelta(minutes=40),
    ):
        frame = CalibratedSatelliteFrame(
            valid_time_utc=valid_time,
            latitudes=latitudes,
            longitudes=longitudes,
            bands={
                "B07": np.full((3, 4), 280.0, dtype=np.float32),
                "B13": np.full((3, 4), 281.0, dtype=np.float32),
                "B15": np.full((3, 4), 282.0, dtype=np.float32),
            },
            units={"B07": "K", "B13": "K", "B15": "K"},
            source="test-hsd",
            quality="medium",
            range_bytes=123,
            range_blocks=3,
        )
        cache.write(frame, point, 900.0)

    payload = {
        "lat": point.lat,
        "lon": point.lon,
        "radius_km": 900.0,
        "reference_time_utc": reference.isoformat(),
    }
    context = {
        "request": type("Request", (), {
            "point": point,
            "radius_km": 900.0,
        })(),
        "config": config,
        "lookup": reference,
    }
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    python_frames = HsdCompactCacheProvider(
        config.satellite_cache_dir / "compact"
    ).fetch_cached_series(reference, point, radius_km=900.0, count=2)
    actual = nowcast_hsd_cache(context, count=2)
    assert [item.valid_time_utc for item in python_frames] == [
        item["valid_time_utc"] for item in actual
    ]
    assert [item.source for item in python_frames] == [
        item["source"] for item in actual
    ]
    replacement = CalibratedSatelliteFrame(
        valid_time_utc=reference - timedelta(minutes=5),
        latitudes=latitudes,
        longitudes=longitudes,
        bands={
            "B07": np.full((3, 4), 280.0, dtype=np.float32),
            "B13": np.full((3, 4), 281.0, dtype=np.float32),
            "B15": np.full((3, 4), 282.0, dtype=np.float32),
        },
        units={"B07": "K", "B13": "K", "B15": "K"},
        source="replacement-hsd",
        quality="medium",
        range_bytes=456,
        range_blocks=4,
    )
    cache.write(replacement, point, 900.0)
    refreshed = nowcast_hsd_cache(context, count=1)
    assert refreshed[0]["source"] == "replacement-hsd"


def test_native_hsd_frame_export_matches_python_arrays(
    engine_binary, monkeypatch, config
):
    from datetime import timedelta
    import numpy as np
    from firesky.satellite_cloud_column import (
        CalibratedSatelliteFrame,
        HsdCompactCache,
        HsdCompactCacheProvider,
        RustHsdCompactCacheProvider,
    )
    from firesky.rust_desktop_data import nowcast_hsd_frames

    point = type("Point", (), {"lat": 30.2741, "lon": 120.1551})()
    reference = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
    cache = HsdCompactCache(config.satellite_cache_dir / "compact")
    latitudes = np.array(
        [[30.1, 30.2, 30.3], [30.4, 30.5, 30.6]],
        dtype=np.float32,
    )
    longitudes = np.array(
        [[120.0, 120.1, 120.2], [120.3, 120.4, 120.5]],
        dtype=np.float32,
    )
    for index, valid_time in enumerate((
        reference - timedelta(minutes=5),
        reference - timedelta(minutes=15),
    )):
        cache.write(
            CalibratedSatelliteFrame(
                valid_time_utc=valid_time,
                latitudes=latitudes,
                longitudes=longitudes,
                bands={
                    "B07": np.full((2, 3), 280.0 + index, dtype=np.float32),
                    "B13": np.full((2, 3), 281.0 + index, dtype=np.float32),
                    "B15": np.full((2, 3), 282.0 + index, dtype=np.float32),
                },
                units={"B07": "K", "B13": "K", "B15": "K"},
                source=f"test-hsd-{index}",
                quality="medium",
                range_bytes=123 + index,
                range_blocks=3 + index,
            ),
            point,
            900.0,
        )

    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    expected = HsdCompactCacheProvider(
        config.satellite_cache_dir / "compact"
    ).fetch_cached_series(reference, point, radius_km=900.0, count=2)
    actual = nowcast_hsd_frames(
        config.satellite_cache_dir,
        reference_time_utc=reference.isoformat(),
        lat=point.lat,
        lon=point.lon,
        radius_km=900.0,
        count=2,
    )
    assert [item.valid_time_utc for item in actual] == [
        item.valid_time_utc for item in expected
    ]
    assert [item.source for item in actual] == [item.source for item in expected]
    for native, python in zip(actual, expected, strict=True):
        np.testing.assert_array_equal(native.latitudes, python.latitudes)
        np.testing.assert_array_equal(native.longitudes, python.longitudes)
        for name in ("B07", "B13", "B15"):
            np.testing.assert_array_equal(native.bands[name], python.bands[name])

    provider = RustHsdCompactCacheProvider(
        config.satellite_cache_dir,
    )
    routed = provider.fetch_series(reference, point, radius_km=900.0, count=2)
    assert [item.source for item in routed] == [item.source for item in expected]

    cache.write(
        CalibratedSatelliteFrame(
            valid_time_utc=reference - timedelta(minutes=5),
            latitudes=latitudes,
            longitudes=longitudes,
            bands={
                "B07": np.full((2, 3), 290.0, dtype=np.float32),
                "B13": np.full((2, 3), 291.0, dtype=np.float32),
                "B15": np.full((2, 3), 292.0, dtype=np.float32),
            },
            units={"B07": "K", "B13": "K", "B15": "K"},
            source="replacement-hsd",
            quality="medium",
            range_bytes=456,
            range_blocks=4,
        ),
        point,
        900.0,
    )
    refreshed = nowcast_hsd_frames(
        config.satellite_cache_dir,
        reference_time_utc=reference.isoformat(),
        lat=point.lat,
        lon=point.lon,
        radius_km=900.0,
        count=1,
    )
    assert refreshed[0].source == "replacement-hsd"
    np.testing.assert_array_equal(
        refreshed[0].bands["B07"],
        np.full((2, 3), 290.0, dtype=np.float32),
    )


def test_native_hsd_motion_matches_python_phase_correlation(
    engine_binary, monkeypatch, config
):
    from datetime import timedelta
    import numpy as np
    from firesky.satellite_cloud_column import (
        CalibratedSatelliteFrame,
        HsdCompactCache,
        RustHsdCompactCacheProvider,
        estimate_hsd_motion,
    )

    point = type("Point", (), {"lat": 30.2741, "lon": 120.1551})()
    reference = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
    cache = HsdCompactCache(config.satellite_cache_dir / "compact")
    yy, xx = np.indices((32, 32), dtype=np.float32)
    base = (
        270.0
        + 10.0 * np.exp(-((xx - 12.0) ** 2 + (yy - 17.0) ** 2) / 45.0)
        + 2.0 * np.sin(xx / 2.0)
        + 1.5 * np.cos(yy / 3.0)
    ).astype(np.float32)
    shifted = np.roll(base, (2, -3), axis=(0, 1))
    latitudes = np.full((32, 32), point.lat, dtype=np.float32)
    longitudes = np.full((32, 32), point.lon, dtype=np.float32)
    for values, valid_time, source in (
        (base, reference - timedelta(minutes=10), "motion-previous"),
        (shifted, reference - timedelta(minutes=5), "motion-latest"),
    ):
        cache.write(
            CalibratedSatelliteFrame(
                valid_time_utc=valid_time,
                latitudes=latitudes,
                longitudes=longitudes,
                bands={"B07": values, "B13": values, "B15": values},
                units={"B07": "K", "B13": "K", "B15": "K"},
                source=source,
                quality="medium",
                range_bytes=123,
                range_blocks=3,
            ),
            point,
            900.0,
        )

    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    provider = RustHsdCompactCacheProvider(config.satellite_cache_dir)
    frames = provider.fetch_series(reference, point, radius_km=900.0, count=2)
    python_motion = estimate_hsd_motion(frames[1], frames[0])
    native_motion = provider.estimate_motion(frames[1], frames[0])
    assert native_motion.quality == python_motion.quality == "strong"
    assert native_motion.dx_px_per_hour == pytest.approx(
        python_motion.dx_px_per_hour, abs=1e-9
    )
    assert native_motion.dy_px_per_hour == pytest.approx(
        python_motion.dy_px_per_hour, abs=1e-9
    )
    assert native_motion.correlation == pytest.approx(
        python_motion.correlation, abs=1e-9
    )
    assert native_motion.overlap_ratio == pytest.approx(
        python_motion.overlap_ratio, abs=1e-12
    )
    assert native_motion.age_minutes == pytest.approx(
        python_motion.age_minutes, abs=1e-12
    )


def test_native_hsd_advection_matches_python_bilinear_shift(
    engine_binary, monkeypatch, config
):
    from datetime import timedelta
    import numpy as np
    from firesky.satellite_cloud_column import (
        CalibratedSatelliteFrame,
        HsdCompactCache,
        HsdMotionEstimate,
        RustHsdCompactCacheProvider,
        advect_hsd_frame,
        plan_hsd_advection,
    )

    point = type("Point", (), {"lat": 30.2741, "lon": 120.1551})()
    reference = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
    cache = HsdCompactCache(config.satellite_cache_dir / "compact")
    yy, xx = np.indices((18, 21), dtype=np.float32)
    base = (
        270.0
        + 8.0 * np.exp(-((xx - 7.5) ** 2 + (yy - 9.0) ** 2) / 30.0)
        + 1.5 * np.sin(xx / 2.0)
        + 0.75 * np.cos(yy / 3.0)
    ).astype(np.float32)
    latitudes = np.broadcast_to(
        np.linspace(29.9, 30.6, 18, dtype=np.float32)[:, None],
        base.shape,
    ).copy()
    longitudes = np.broadcast_to(
        np.linspace(119.8, 120.7, 21, dtype=np.float32)[None, :],
        base.shape,
    ).copy()
    for index, valid_time in enumerate(
        (reference - timedelta(minutes=10), reference - timedelta(minutes=5))
    ):
        values = (base + index).astype(np.float32)
        cache.write(
            CalibratedSatelliteFrame(
                valid_time_utc=valid_time,
                latitudes=latitudes,
                longitudes=longitudes,
                bands={
                    "B07": values,
                    "B13": values + np.float32(1.0),
                    "B15": values + np.float32(2.0),
                },
                units={"B07": "K", "B13": "K", "B15": "K"},
                source=f"advection-{index}",
                quality="medium",
                range_bytes=123,
                range_blocks=3,
            ),
            point,
            900.0,
        )

    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    provider = RustHsdCompactCacheProvider(config.satellite_cache_dir)
    frames = provider.fetch_series(reference, point, radius_km=900.0, count=2)
    motion = HsdMotionEstimate(
        dx_px_per_hour=0.75,
        dy_px_per_hour=-0.5,
        correlation=0.9,
        overlap_ratio=1.0,
        age_minutes=5.0,
        quality="strong",
        previous_time_utc=frames[1].valid_time_utc,
        latest_time_utc=frames[0].valid_time_utc,
    )
    plan = plan_hsd_advection(
        frames,
        reference + timedelta(hours=1),
        motion,
    )
    assert plan.applied
    expected = advect_hsd_frame(frames[0], plan, motion)
    actual = provider.advect(frames[0], plan, motion)
    assert provider.advect(frames[0], plan, motion) is actual
    assert actual.valid_time_utc == expected.valid_time_utc == plan.target_time_utc
    assert actual.source == expected.source
    assert actual.quality == expected.quality
    np.testing.assert_array_equal(actual.latitudes, expected.latitudes)
    np.testing.assert_array_equal(actual.longitudes, expected.longitudes)
    for name in ("B07", "B13", "B15"):
        np.testing.assert_allclose(
            actual.bands[name],
            expected.bands[name],
            rtol=0.0,
            atol=2e-5,
            equal_nan=True,
        )
    assert actual.native_manifest_path is not None


@pytest.mark.parametrize(
    "cloudy,source",
    [
        (True, "test-hsd-cloudy"),
        (False, "noaa-himawari9-hsd-calibrated:test-clear"),
    ],
)
def test_native_hsd_retrieval_matches_python_posterior(
    engine_binary, monkeypatch, config, cloudy, source
):
    from dataclasses import asdict
    import numpy as np
    from firesky.geometry import GeoPoint
    from firesky.rust_desktop_data import nowcast_hsd_retrieval
    from firesky.satellite import (
        calibrated_cloud_metrics,
        calibrated_satellite_cloud_retrieval,
        satellite_sample_points,
    )
    from firesky.satellite_cloud_column import (
        CalibratedSatelliteFrame,
        HsdCompactCache,
        RustHsdCompactCacheProvider,
        VerticalPrior,
        VerticalPriorLevel,
        retrieve_cloud_columns,
        sample_calibrated_frame,
    )

    point = GeoPoint(30.2741, 120.1551)
    valid_time = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
    cache = HsdCompactCache(config.satellite_cache_dir / "compact")
    lat_axis = np.asarray([point.lat - 0.05, point.lat, point.lat + 0.05], dtype=np.float32)
    lon_axis = np.asarray([point.lon - 0.05, point.lon, point.lon + 0.05], dtype=np.float32)
    latitudes = np.broadcast_to(lat_axis[:, None], (3, 3)).copy()
    longitudes = np.broadcast_to(lon_axis[None, :], (3, 3)).copy()
    if cloudy:
        bands = {
            "B07": np.full((3, 3), 279.0, dtype=np.float32),
            "B13": np.full((3, 3), 280.0, dtype=np.float32),
            "B15": np.full((3, 3), 281.0, dtype=np.float32),
        }
    else:
        bands = {
            "B07": np.full((3, 3), 292.0, dtype=np.float32),
            "B13": np.full((3, 3), 293.0, dtype=np.float32),
            "B15": np.full((3, 3), 294.0, dtype=np.float32),
        }
    frame = CalibratedSatelliteFrame(
        valid_time_utc=valid_time,
        latitudes=latitudes,
        longitudes=longitudes,
        bands=bands,
        units={"B07": "K", "B13": "K", "B15": "K"},
        source=source,
        quality="medium",
        range_bytes=123,
        range_blocks=3,
    )
    cache.write(frame, point, 900.0)

    prior = VerticalPrior(
        valid_time_utc=valid_time,
        cycle_time_utc=valid_time,
        forecast_hour=0,
        source="gfs-test",
        terrain_asl_m=8.0,
        identity="gfs-test-column",
        target_lat=point.lat,
        target_lon=point.lon,
        levels=(
            VerticalPriorLevel(1000, 10.0, 291.0, 50.0, 0.02),
            VerticalPriorLevel(900, 1000.0, 286.0, 80.0, 0.70),
            VerticalPriorLevel(800, 2500.0, 280.0, 85.0, 0.60),
            VerticalPriorLevel(700, 4000.0, 270.0, 60.0, 0.10),
        ),
    )

    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    native_frame = RustHsdCompactCacheProvider(config.satellite_cache_dir).fetch(
        valid_time + timedelta(minutes=5),
        point,
        radius_km=900.0,
    )
    assert native_frame is not None
    assert native_frame.native_manifest_path is not None
    sample = sample_calibrated_frame(native_frame, point.lat, point.lon)
    expected = retrieve_cloud_columns(
        native_frame,
        point,
        0.0,
        15.0,
        prior,
        sample=sample,
        motion_dx_px_per_hour=0.25,
        motion_dy_px_per_hour=-0.5,
    )
    actual = nowcast_hsd_retrieval(
        native_frame,
        [
            {
                "lat": point.lat,
                "lon": point.lon,
                "distance_km": 0.0,
                "bearing_deg": 15.0,
                "sample": sample,
                "prior": prior,
                "motion_dx_px_per_hour": 0.25,
                "motion_dy_px_per_hour": -0.5,
            }
        ],
    )[0]
    assert len(actual) == len(expected)
    for native_column, python_column in zip(actual, expected, strict=True):
        for name in (
            "schema_version",
            "algorithm",
            "valid_time_utc",
            "lat",
            "lon",
            "distance_km",
            "bearing_deg",
            "base_p10_asl_m",
            "base_p50_asl_m",
            "base_p90_asl_m",
            "top_asl_m",
            "terrain_asl_m",
            "phase",
            "optical_depth",
            "horizontal_cloud_probability",
            "low_cloud_state",
            "retrieval_quality",
            "source",
            "vertical_prior_identity",
            "vertical_prior_source",
            "cloud_type",
            "base_source",
            "motion_dx_px_per_hour",
            "motion_dy_px_per_hour",
        ):
            native_value = getattr(native_column, name)
            python_value = getattr(python_column, name)
            if isinstance(python_value, float):
                assert native_value == pytest.approx(python_value, rel=0.0, abs=1e-10), name
            else:
                assert native_value == python_value, name
        assert native_column.cloud_probability_by_height.keys() == (
            python_column.cloud_probability_by_height.keys()
        )
        for height, probability in python_column.cloud_probability_by_height.items():
            assert native_column.cloud_probability_by_height[height] == pytest.approx(
                probability, rel=0.0, abs=1e-10
            )

    def prior_provider(target, _valid_time):
        return prior

    monkeypatch.setenv("FIRESKY_HSD_BACKEND", "python")
    python_features, python_sets = calibrated_satellite_cloud_retrieval(
        native_frame,
        point,
        25.0,
        prior_provider,
        radius_km=1.0,
        motion_dx_px_per_hour=0.25,
        motion_dy_px_per_hour=-0.5,
    )
    monkeypatch.setenv("FIRESKY_HSD_BACKEND", "rust")
    native_features, native_sets, native_metrics = calibrated_satellite_cloud_retrieval(
        native_frame,
        point,
        25.0,
        prior_provider,
        radius_km=1.0,
        motion_dx_px_per_hour=0.25,
        motion_dy_px_per_hour=-0.5,
        return_metrics=True,
    )
    monkeypatch.delenv("FIRESKY_HSD_BACKEND", raising=False)
    monkeypatch.setenv("FIRESKY_DESKTOP_DATA_BACKEND", "rust")
    selector_features, selector_sets = calibrated_satellite_cloud_retrieval(
        native_frame,
        point,
        25.0,
        prior_provider,
        radius_km=1.0,
        motion_dx_px_per_hour=0.25,
        motion_dy_px_per_hour=-0.5,
    )
    batch_calls = []

    def batch_only_provider(targets, _valid_time):
        batch_calls.append(tuple(targets))
        return [prior] * len(targets)

    def unexpected_single_provider(_target, _valid_time):
        raise AssertionError("calibrated retrieval should use the batch prior provider")

    unexpected_single_provider.batch = batch_only_provider
    monkeypatch.setenv("FIRESKY_HSD_BACKEND", "python")
    batch_features, batch_sets = calibrated_satellite_cloud_retrieval(
        native_frame,
        point,
        25.0,
        unexpected_single_provider,
        radius_km=1.0,
        motion_dx_px_per_hour=0.25,
        motion_dy_px_per_hour=-0.5,
    )
    assert len(batch_calls) == 1
    assert len(batch_calls[0]) == len(satellite_sample_points(point, 25.0, 1.0))

    def assert_close(native_value, python_value):
        if isinstance(native_value, dict):
            assert native_value.keys() == python_value.keys()
            for key in native_value:
                try:
                    assert_close(native_value[key], python_value[key])
                except AssertionError as exc:
                    raise AssertionError(f"mismatch at {key}: {exc}") from exc
        elif isinstance(native_value, (list, tuple)):
            assert len(native_value) == len(python_value)
            for left, right in zip(native_value, python_value, strict=True):
                assert_close(left, right)
        elif isinstance(native_value, float):
            assert native_value == pytest.approx(python_value, rel=0.0, abs=1e-10)
        else:
            assert native_value == python_value

    assert_close(
        [asdict(item) for item in native_features],
        [asdict(item) for item in python_features],
    )
    assert_close(
        [item.as_dict() for item in native_sets],
        [item.as_dict() for item in python_sets],
    )
    assert native_metrics is not None
    assert_close(native_metrics, calibrated_cloud_metrics(python_sets, 25.0))
    assert_close(
        [asdict(item) for item in selector_features],
        [asdict(item) for item in native_features],
    )
    assert_close(
        [item.as_dict() for item in selector_sets],
        [item.as_dict() for item in native_sets],
    )
    assert_close(
        [asdict(item) for item in batch_features],
        [asdict(item) for item in python_features],
    )
    assert_close(
        [item.as_dict() for item in batch_sets],
        [item.as_dict() for item in python_sets],
    )


def _write_unified_retrieval_gfs_fixture(
    root,
    *,
    center,
    cycle_time,
    first_fields=None,
):
    import numpy as np
    from firesky.gfs import (
        _DecodedGfsGrid,
        decoded_grid_cache_path,
        write_mmap_decoded_grid,
    )
    from firesky.service import _SharedGfsNowcastDomain

    root.mkdir(parents=True, exist_ok=True)
    latitudes = np.asarray(
        [
            center.lat - 0.25,
            center.lat,
            center.lat + 0.25,
        ],
        dtype=np.float32,
    )
    longitudes = np.asarray(
        [
            center.lon - 0.25,
            center.lon,
            center.lon + 0.25,
        ],
        dtype=np.float32,
    )
    base = {
        ("surface", 0, "orog"): np.asarray([4.0, 8.0, 12.0], dtype=np.float32),
    }
    for level, height, humidity, temperature, cloud in (
        (925, 750.0, 82.0, 288.0, 20.0),
        (850, 1450.0, 88.0, 283.0, 55.0),
        (700, 3050.0, 76.0, 273.0, 42.0),
        (300, 9200.0, 45.0, 235.0, 18.0),
    ):
        base[("isobaricInhPa", level, "gh")] = np.asarray(
            [height - 10.0, height, height + 10.0],
            dtype=np.float32,
        )
        base[("isobaricInhPa", level, "r")] = np.asarray(
            [humidity - 2.0, humidity, humidity + 2.0],
            dtype=np.float32,
        )
        base[("isobaricInhPa", level, "t")] = np.asarray(
            [temperature - 1.0, temperature, temperature + 1.0],
            dtype=np.float32,
        )
        base[("isobaricInhPa", level, "tcc")] = np.asarray(
            [cloud - 5.0, cloud, cloud + 5.0],
            dtype=np.float32,
        )
    base[("highCloudLayer", 0, "hcc")] = np.asarray(
        [14.0, 18.0, 22.0],
        dtype=np.float32,
    )
    if first_fields is not None:
        base.update(first_fields)

    domains = {}
    for hour, delta in ((8, 0.0), (9, 8.0)):
        raw_path = root / f"f{hour:03d}.grib2"
        raw_path.write_bytes(f"unified-fixture-{hour}".encode())
        fields = {
            key: (
                values
                if key[2] == "orog"
                else np.asarray(values + np.float32(delta), dtype=np.float32)
            )
            for key, values in base.items()
        }
        cache_path = decoded_grid_cache_path(raw_path)
        write_mmap_decoded_grid(
            cache_path,
            _DecodedGfsGrid(latitudes.copy(), longitudes.copy(), fields),
        )
        domains[hour] = _SharedGfsNowcastDomain(
            grid=_DecodedGfsGrid(
                latitudes.copy(),
                longitudes.copy(),
                fields,
            ),
            cache_path=str(raw_path),
            source_url=f"unified-fixture-{hour}",
            bbox=(0.0, 0.0, 0.0, 0.0),
        )
    return domains, cycle_time


@pytest.mark.parametrize(
    "cloudy,channel_override,retrieval_radius_km",
    [
        (True, None, 10.0),
        (False, None, 10.0),
        (True, "nan", 10.0),
        (True, "empty", 10.0),
        (True, "outside", 40.0),
    ],
)
def test_unified_native_retrieval_matches_python_fallback(
    engine_binary,
    monkeypatch,
    config,
    cloudy,
    channel_override,
    retrieval_radius_km,
):
    from dataclasses import asdict
    import numpy as np
    from firesky.geometry import GeoPoint
    from firesky.models import ForecastRequest
    from firesky.satellite import (
        calibrated_cloud_metrics,
        calibrated_satellite_cloud_retrieval,
    )
    from firesky.satellite_cloud_column import (
        CalibratedSatelliteFrame,
        HsdCompactCache,
        RustHsdCompactCacheProvider,
    )
    from firesky.service import GfsNowcastVerticalPriorProvider

    point = GeoPoint(30.2741, 120.1551)
    valid_time = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
    cycle_time = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
    cache = HsdCompactCache(config.satellite_cache_dir / "compact")
    lat_axis = np.linspace(
        point.lat - 0.3,
        point.lat + 0.3,
        10,
        dtype=np.float32,
    )
    lon_axis = np.linspace(
        point.lon - 0.3,
        point.lon + 0.3,
        10,
        dtype=np.float32,
    )
    latitudes = np.broadcast_to(lat_axis[:, None], (10, 10)).copy()
    longitudes = np.broadcast_to(lon_axis[None, :], (10, 10)).copy()
    base = (
        279.0
        if cloudy
        else 292.0
    )
    bands = {
        "B07": np.full((10, 10), base, dtype=np.float32),
        "B13": np.full((10, 10), base + 1.0, dtype=np.float32),
        "B15": np.full((10, 10), base + 2.0, dtype=np.float32),
    }
    if channel_override == "nan":
        bands["B07"][0, 0] = np.nan
    cache.write(
        CalibratedSatelliteFrame(
            valid_time_utc=valid_time,
            latitudes=latitudes,
            longitudes=longitudes,
            bands=bands,
            units={"B07": "K", "B13": "K", "B15": "K"},
            source="unified-fixture",
            quality="medium",
            range_bytes=123,
            range_blocks=3,
        ),
        point,
        900.0,
    )
    empty_fields = {}
    if channel_override == "empty":
        for level in (925, 850, 700, 300):
            empty_fields[("isobaricInhPa", level, "gh")] = np.full(
                3,
                np.nan,
                dtype=np.float32,
            )
    class Client:
        resolution = "0p25"
        _res_grid_deg = 0.25

    provider = GfsNowcastVerticalPriorProvider(Client())
    domains, _ = _write_unified_retrieval_gfs_fixture(
        config.gfs_cache_dir.parent / "unified-gfs",
        center=point,
        cycle_time=cycle_time,
        first_fields=empty_fields,
    )
    provider._snapshot = lambda _request, _cycle, hour: domains[hour]
    request = ForecastRequest(
        target_date=valid_time.date(),
        timezone_name="UTC",
        point=point,
        radius_km=retrieval_radius_km,
        gfs_cycle_override=cycle_time,
    )
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setenv("FIRESKY_GFS_PRIOR_BACKEND", "rust")
    native_frame = RustHsdCompactCacheProvider(
        config.satellite_cache_dir
    ).fetch(valid_time + timedelta(minutes=5), point, radius_km=900.0)
    assert native_frame is not None
    assert native_frame.native_manifest_path is not None
    wrapped = provider.for_request(request)

    monkeypatch.setenv("FIRESKY_HSD_BACKEND", "python")
    expected_features, expected_sets, expected_priors, expected_metrics = (
        calibrated_satellite_cloud_retrieval(
            native_frame,
            point,
            25.0,
            wrapped,
            radius_km=retrieval_radius_km,
            motion_dx_px_per_hour=0.25,
            motion_dy_px_per_hour=-0.5,
            return_metrics=True,
            return_priors=True,
        )
    )
    monkeypatch.setenv("FIRESKY_HSD_BACKEND", "rust")

    import firesky.satellite as satellite

    def reject_python_sampling(*_args, **_kwargs):
        raise AssertionError("unified native retrieval called Python HSD sampling")

    monkeypatch.setattr(
        satellite,
        "sample_calibrated_frame",
        reject_python_sampling,
    )
    monkeypatch.setattr(
        __import__("firesky.service", fromlist=["features_from_decoded_grid"]),
        "features_from_decoded_grid",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unified native retrieval called Python GFS extraction")
        ),
    )
    actual_features, actual_sets, actual_priors, actual_metrics = (
        calibrated_satellite_cloud_retrieval(
            native_frame,
            point,
            25.0,
            wrapped,
            radius_km=retrieval_radius_km,
            motion_dx_px_per_hour=0.25,
            motion_dy_px_per_hour=-0.5,
            return_metrics=True,
            return_priors=True,
        )
    )

    def assert_close(actual, expected):
        if isinstance(actual, dict):
            assert actual.keys() == expected.keys()
            for key in actual:
                assert_close(actual[key], expected[key])
        elif isinstance(actual, (list, tuple)):
            assert len(actual) == len(expected)
            for left, right in zip(actual, expected, strict=True):
                assert_close(left, right)
        elif isinstance(actual, float):
            assert actual == pytest.approx(expected, rel=0.0, abs=1e-10)
        else:
            assert actual == expected

    assert_close(
        [asdict(item) for item in actual_features],
        [asdict(item) for item in expected_features],
    )
    assert_close(
        [item.as_dict() for item in actual_sets],
        [item.as_dict() for item in expected_sets],
    )
    assert len(actual_priors) == len(expected_priors)
    for (actual_lat, actual_lon, actual_prior), (
        expected_lat,
        expected_lon,
        expected_prior,
    ) in zip(actual_priors, expected_priors, strict=True):
        assert actual_lat == pytest.approx(expected_lat)
        assert actual_lon == pytest.approx(expected_lon)
        assert (
            None if actual_prior is None else asdict(actual_prior)
        ) == (
            None if expected_prior is None else asdict(expected_prior)
        )
    assert_close(
        actual_metrics,
        expected_metrics
        if expected_metrics is not None
        else calibrated_cloud_metrics(expected_sets, 25.0),
    )
    if channel_override == "outside":
        assert sum(
            item.retrieval_quality == "unknown" for item in actual_sets
        ) >= 7
    if cloudy and channel_override is None:
        from firesky.satellite import calibrated_only_nowcast_adjustment

        adjustment = calibrated_only_nowcast_adjustment(
            request,
            valid_time,
            25.0,
            native_frame,
            wrapped,
            source_times_utc=(valid_time,),
            observation_frames=(native_frame,),
        )
        assert adjustment is not None
        assert adjustment.calibrated_sample_coverage_ratio > 0.0
        profile = adjustment.satellite_vertical_profile
        assert profile is not None
        assert profile["usable_sample_count"] > 0
        assert any(
            sample["vertical_prior_source"].startswith("gfs")
            and any(level["height_m"] is not None for level in sample["levels"])
            for sample in profile["samples"]
        )


@pytest.mark.parametrize(
    "mutation,message",
    [
        ("receipt_contract", "receipt is invalid"),
        ("bytes", "output identity is invalid"),
        ("sha256", "output identity is invalid"),
        ("payload_contract", "response identity is invalid"),
    ],
)
def test_unified_retrieval_rejects_tampered_file_receipt(
    monkeypatch,
    tmp_path,
    mutation,
    message,
):
    import hashlib
    from types import SimpleNamespace
    import firesky.rust_desktop_data as native_data
    from firesky.geometry import GeoPoint

    valid_time = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
    cycle_time = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
    frame = SimpleNamespace(
        valid_time_utc=valid_time,
        native_manifest_path=tmp_path / "manifest.json",
    )

    def fake_call(_method, params, *, timeout_seconds=None):
        assert timeout_seconds is None
        output_path = params["output_path"]
        payload = {
            "contract": (
                "wrong-retrieval-contract"
                if mutation == "payload_contract"
                else "rust-desktop-nowcast-calibrated-retrieval-v1"
            ),
            "runtime_python_used": False,
            "features": [],
            "columns": [],
            "metrics": {},
            "priors": [None] * 25,
            "timings_ms": {
                "hsd_frame_load": 1.0,
                "hsd_sampling": 1.0,
                "gfs_grid_load": 1.0,
                "retrieval": 1.0,
                "assembly": 1.0,
            },
        }
        import json

        encoded = json.dumps(payload, separators=(",", ":")).encode()
        with open(output_path, "wb") as stream:
            stream.write(encoded)
        return {
            "contract": (
                "wrong-file-contract"
                if mutation == "receipt_contract"
                else "rust-desktop-nowcast-calibrated-retrieval-file-v1"
            ),
            "runtime_python_used": False,
            "output_path": output_path,
            "bytes": len(encoded) + (1 if mutation == "bytes" else 0),
            "sha256": (
                "0" * 64
                if mutation == "sha256"
                else hashlib.sha256(encoded).hexdigest()
            ),
        }

    monkeypatch.setattr(native_data, "call_engine", fake_call)
    with pytest.raises(RuntimeError, match=message):
        native_data.nowcast_calibrated_retrieval(
            frame=frame,
            point=GeoPoint(30.2741, 120.1551),
            azimuth_deg=25.0,
            radius_km=10.0,
            retrieval_context={
                "cycle_time_utc": cycle_time,
                "left_forecast_hour": 8,
                "right_forecast_hour": 9,
                "fraction": 0.5,
                "left_cache_path": tmp_path / "left",
                "right_cache_path": tmp_path / "right",
                "local_radius_km": 35.0,
                "domain_hash": "fixture",
                "resolution": "0p25",
            },
            return_priors=True,
        )


def test_unified_retrieval_file_receipt_exceeds_line_limit(monkeypatch, tmp_path):
    import hashlib
    import json
    from types import SimpleNamespace
    import firesky.rust_desktop_data as native_data
    from firesky.geometry import GeoPoint

    valid_time = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
    cycle_time = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)

    def fake_call(_method, params, *, timeout_seconds=None):
        assert timeout_seconds is None
        output_path = params["output_path"]
        payload = {
            "contract": "rust-desktop-nowcast-calibrated-retrieval-v1",
            "runtime_python_used": False,
            "features": [],
            "columns": [],
            "metrics": {"diagnostic": "x" * (1024 * 1024 + 128)},
            "priors": [None] * 150,
            "timings_ms": {
                "hsd_frame_load": 1.0,
                "hsd_sampling": 1.0,
                "gfs_grid_load": 1.0,
                "retrieval": 1.0,
                "assembly": 1.0,
            },
        }
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        assert len(encoded) > 1024 * 1024
        with open(output_path, "wb") as stream:
            stream.write(encoded)
        return {
            "contract": "rust-desktop-nowcast-calibrated-retrieval-file-v1",
            "runtime_python_used": False,
            "output_path": output_path,
            "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }

    monkeypatch.setattr(native_data, "call_engine", fake_call)
    features, columns, priors, metrics = native_data.nowcast_calibrated_retrieval(
        frame=SimpleNamespace(
            valid_time_utc=valid_time,
            native_manifest_path=tmp_path / "manifest.json",
        ),
        point=GeoPoint(30.2741, 120.1551),
        azimuth_deg=25.0,
        radius_km=900.0,
        retrieval_context={
            "cycle_time_utc": cycle_time,
            "left_forecast_hour": 8,
            "right_forecast_hour": 9,
            "fraction": 0.5,
            "left_cache_path": tmp_path / "left",
            "right_cache_path": tmp_path / "right",
            "local_radius_km": 35.0,
            "domain_hash": "fixture",
            "resolution": "0p25",
        },
        return_priors=True,
    )
    assert features == []
    assert columns == []
    assert len(priors) == 150
    assert metrics["diagnostic"].startswith("x")
