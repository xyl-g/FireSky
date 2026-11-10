"""Differential tests for the empirical, GFS and cloud Rust migration stages.

Build both native variants and engine before running with
FIRESKY_REQUIRE_RUST_TESTS=1 to make unavailable artifacts a failure.
"""

import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone

import numpy as np
import pytest

from firesky import empirical_native
from firesky import cloud_native
from firesky import forward_field_scoring as reference

ROOT = Path(__file__).resolve().parents[1]


def unavailable(message):
    if os.environ.get("FIRESKY_REQUIRE_RUST_TESTS") == "1":
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture
def rust_backend():
    try:
        empirical_native._functions()
    except RuntimeError as exc:
        unavailable(str(exc))


@pytest.fixture
def engine_binary():
    path = Path(os.environ.get(
        "FIRESKY_RUST_ENGINE_BIN",
        ROOT / "engine" / "target" / "debug" / ("firesky-engine.exe" if os.name == "nt" else "firesky-engine"),
    ))
    if not path.is_file():
        unavailable("build the Rust engine with cargo build --manifest-path engine/Cargo.toml")
    return path


def rpc(binary, requests, *, env=None):
    completed = subprocess.run(
        [str(binary), "serve"],
        input="".join(json.dumps(request, allow_nan=False) + "\n" for request in requests),
        capture_output=True, text=True, check=True, timeout=30, env=env,
    )
    return [json.loads(line) for line in completed.stdout.splitlines()]


@pytest.mark.parametrize("inverse", [False, True])
def test_response_matches_numpy_including_nonfinite(rust_backend, monkeypatch, inverse):
    function = (
        reference.inverse_calibrated_quality_response if inverse
        else reference.calibrated_quality_response
    )
    values = np.array([np.nan, -np.inf, -1, 0, .001, .05, .4, 1, 1.812, 2.5, np.inf])
    for sample in (values, values.reshape(1, -1), np.empty((0, 2)), .73, float("nan")):
        monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
        expected = function(sample)
        monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "rust")
        actual = function(sample)
        np.testing.assert_allclose(actual, expected, rtol=2e-14, atol=2e-14, equal_nan=True)
        assert isinstance(actual, float) if np.ndim(sample) == 0 else actual.shape == sample.shape


def test_duration_response_matches_python(rust_backend, monkeypatch):
    for minutes in [-np.inf, np.nan, -5, 0, 3, 5, 10, 19.9, 20, 30, 40, 70, 120, np.inf]:
        monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
        expected = reference.duration_factor(minutes)
        monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "rust")
        assert reference.duration_factor(minutes) == pytest.approx(expected, abs=1e-14)


def test_randomized_windows_match_python(rust_backend, monkeypatch):
    rng = np.random.default_rng(20261108)
    for n in [0, 1, 2, 4, 5, 19, 37, 90]:
        for _ in range(12):
            values = rng.uniform(0, 2.5, n)
            active = rng.random(n) > .35
            widths = rng.uniform(-2, 8, n)
            # Repeated, unsorted offsets exercise stable ordering and scatter.
            offsets = rng.integers(-20, 20, n).astype(float)
            for ordering in (None, offsets):
                monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
                expected, expected_minutes = reference._unified_event_window_weights(
                    values, active, widths, ordering,
                )
                monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "rust")
                actual, actual_minutes = reference._unified_event_window_weights(
                    values, active, widths, ordering,
                )
                np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-12)
                assert actual_minutes == pytest.approx(expected_minutes, abs=1e-11)


def test_batch_windows_match_python(rust_backend, monkeypatch):
    rng = np.random.default_rng(17)
    values = rng.uniform(0, 2.5, (24, 37))
    active = rng.random(values.shape) > .3
    active[0] = False
    widths = rng.uniform(0, 8, values.shape[1])
    offsets = rng.permutation(values.shape[1]).astype(float)
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
    expected, expected_minutes = reference._unified_event_window_weights_batch(values, active, widths, offsets)
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "rust")
    actual, actual_minutes = reference._unified_event_window_weights_batch(values, active, widths, offsets)
    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(actual_minutes, expected_minutes, rtol=1e-12, atol=1e-12)


def test_numerically_tied_windows_preserve_objective(rust_backend, monkeypatch):
    # Different reduction orders can choose different equally good windows.
    # Do not mistake score equivalence for identical diagnostic composition.
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
    rng = np.random.default_rng(1)
    values = np.full(19, .8)
    active = np.ones(19, dtype=bool)
    for _ in range(100):
        widths = rng.uniform(0, 8, 19)
        expected, expected_minutes = reference._unified_event_window_weights(values, active, widths)
        actual, actual_minutes = empirical_native.event_window_weights(values, active, widths)
        assert actual @ values == pytest.approx(expected @ values, abs=1e-12)
        assert actual_minutes == pytest.approx(expected_minutes, abs=1e-11)


def test_integrated_observer_score_matches_reference(rust_backend, monkeypatch):
    rng = np.random.default_rng(31)
    for _ in range(10):
        burn = rng.uniform(.05, .7, 19)
        load = burn * rng.uniform(.5, 1, 19)
        material = load * rng.uniform(.4, .9, 19)
        params = dict(
            view_amount=np.full(19, .85),
            burnable_amount=burn,
            burnable_load=load,
            burnable_material_load=material,
            burnable_incoming_aerosol_load=material * .9,
            incoming_block_risk=np.full(19, .1),
            visibility=np.full(19, .8),
            vividness=np.full(19, .7),
            offsets_minutes=np.arange(-45, 46, 5, dtype=float),
            local_aod550=.15,
        )
        monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
        expected = reference.score_observer_cloud_field(**params)
        monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "rust")
        actual = reference.score_observer_cloud_field(**params)
        assert actual["index"] == pytest.approx(expected["index"], rel=1e-12, abs=1e-12)
        assert actual["components"].keys() == expected["components"].keys()
        for key, value in expected["components"].items():
            if isinstance(value, (float, int)):
                assert actual["components"][key] == pytest.approx(value, rel=1e-12, abs=1e-12), key
            else:
                assert actual["components"][key] == value, key


def test_native_window_rejects_invalid_inputs(rust_backend):
    for args in (
        ([1], [], [5], None),
        ([np.nan], [True], [5], None),
        ([1], [True], [np.inf], None),
        ([1], [True], [5], [np.nan]),
        ([1] * 4097, [True] * 4097, [5] * 4097, None),
    ):
        with pytest.raises(ValueError):
            empirical_native.event_window_weights(*args)


def test_engine_protocol_and_score_match_python(engine_binary, monkeypatch):
    params = {"values": [.8, .8, .8, .8, .1], "active": [True] * 5, "widths": [3, 4, 5, 6, 7]}
    hello, score, forecast = rpc(engine_binary, [
        {"id": "hello", "method": "hello"},
        {"id": "score", "method": "score_window", "params": params},
        {"id": "forecast", "method": "forecast"},
    ])
    assert hello["result"]["capabilities"] == [
        "score_window", "decode_grib", "decode_gfs", "prepare_gfs_features", "derive_gfs",
        "decode_gfs_to_files", "derive_gfs_to_files",
        "decode_ecmwf_basic_to_files", "derive_ecmwf_basic_to_files",
        "decode_cams_to_files", "map_cams_to_files", "interpolate_cams_to_files",
        "decode_cams_spectral_to_files", "diagnose_cams_spectral", "decode_ecmwf_full_to_files",
        "time_contract", "solar_events", "solar_snapshots", "solar_crossing",
        "solar_shadow_speed", "solar_glow_window",
        "prepare_regional_solar_to_files",
        "assess_stored_regional_to_files",
        "score_observer_fields",
        "score_nowcast_fields",
        "score_stored_regional_to_files",
        "diagnose_observer_fields", "diagnose_stored_regional_to_files", "forecast_characteristics",
        "diagnose_profile_columns", "diagnose_stored_profiles_to_files", "build_stored_frames_to_files",
        "assemble_observer_frames", "quantize_frame_fields",
        "validate_point_core", "materialize_point_core", "forecast_stored_point_to_files", "gfs_data",
        "plan_gfs_inputs", "forecast_cached_gfs_point_to_files", "cams_data", "ecmwf_data",
        "materialize_ecmwf_point", "plan_ecmwf_inputs", "forecast_cached_ecmwf_point_to_files",
        "build_stored_cross_section", "render_html_report", "forecast_ecmwf_service_to_files",
        "read_ecmwf_service_cross_section",
            "forecast_ecmwf_service_cached", "read_cached_ecmwf_service_cross_section",
            "location", "check_desktop_data", "desktop_inventory", "desktop_location",
            "desktop_publications", "desktop_prepare",
            "desktop_session",
        "desktop_nowcast_gfs_plan", "desktop_nowcast_prior",
        "desktop_nowcast_prior_batch",
        "desktop_nowcast_prior_shared_batch",
        "desktop_nowcast_prior_decoded_batch",
        "desktop_nowcast_calibrated_retrieval",
        "desktop_nowcast_adjustment",
            "desktop_nowcast_assessment",
            "desktop_nowcast_prepare",
            "desktop_nowcast_hsd_cache", "desktop_nowcast_hsd_export",
            "desktop_nowcast_hsd_motion", "desktop_nowcast_hsd_advection",
            "desktop_nowcast_hsd_retrieval",
    ]
    assert forecast["type"] == "error"
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
    weights, _ = reference._unified_event_window_weights(
        np.array(params["values"]), np.array(params["active"]), np.array(params["widths"]),
    )
    pre = float(weights @ params["values"]) * reference.duration_factor(sum(params["widths"]))
    assert score["result"]["index"] == pytest.approx(reference.calibrated_quality_response(pre), abs=1e-13)


@pytest.fixture
def grib_fixture(tmp_path):
    codes = pytest.importorskip("eccodes")
    path = tmp_path / "bitmap.grib2"
    with path.open("wb") as output:
        for values in ([1, 9999, 3, 4, 5, 6], [10, 11, 12, 13, 14, 15]):
            handle = codes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
            try:
                for key, value in {
                    "Ni": 3, "Nj": 2,
                    "latitudeOfFirstGridPointInDegrees": 1,
                    "longitudeOfFirstGridPointInDegrees": 100,
                    "latitudeOfLastGridPointInDegrees": 0,
                    "longitudeOfLastGridPointInDegrees": 102,
                    "iDirectionIncrementInDegrees": 1,
                    "jDirectionIncrementInDegrees": 1,
                    "missingValue": 9999,
                    "bitmapPresent": 1,
                }.items():
                    codes.codes_set(handle, key, value)
                codes.codes_set_values(handle, values)
                codes.codes_write(handle, output)
            finally:
                codes.codes_release(handle)
    return path


def test_grib_decodes_bitmap_coordinates_and_multiple_messages(engine_binary, grib_fixture):
    from eccodes import codes_grib_new_from_file, codes_get, codes_get_array, codes_release
    from firesky.gfs import _decoded_values_with_bitmap

    result = rpc(engine_binary, [{"id": "grib", "method": "decode_grib", "params": {
        "path": str(grib_fixture), "include_values": True,
    }}])[0]
    assert result["type"] == "result", result
    fields = result["result"]["fields"]
    assert len(fields) == 2
    assert fields[0]["missing_count"] == 1
    assert fields[0]["values"][1] is None
    assert fields[1]["missing_count"] == 0
    with grib_fixture.open("rb") as source:
        for field in fields:
            handle = codes_grib_new_from_file(source)
            try:
                assert field["short_name"] == codes_get(handle, "shortName")
                assert field["type_of_level"] == codes_get(handle, "typeOfLevel")
                np.testing.assert_allclose(
                    np.array(field["values"], dtype=np.float32),
                    _decoded_values_with_bitmap(handle), equal_nan=True, rtol=0, atol=0,
                )
                for key in ("latitudes", "longitudes"):
                    np.testing.assert_allclose(field[key], codes_get_array(handle, key), rtol=0, atol=0)
            finally:
                codes_release(handle)


def test_grib_missing_truncated_and_resource_limits(engine_binary, grib_fixture, tmp_path):
    empty = tmp_path / "empty.grib"
    empty.touch()
    truncated = tmp_path / "truncated.grib"
    truncated.write_bytes(grib_fixture.read_bytes()[:40])
    requests = [
        {"id": str(index), "method": "decode_grib", "params": params}
        for index, params in enumerate((
            {"path": str(tmp_path / "absent.grib")},
            {"path": str(empty)}, {"path": str(truncated)},
            {"path": str(grib_fixture), "max_messages": 1},
            {"path": str(grib_fixture), "max_points": 1},
            {"path": str(grib_fixture), "max_messages": 0},
        ))
    ]
    results = rpc(engine_binary, requests)
    assert all(result["type"] == "error" for result in results), results


def test_scoring_does_not_require_eccodes(engine_binary, monkeypatch):
    monkeypatch.setenv("FIRESKY_ECCODES_LIBRARY", "/nonexistent/firesky-eccodes")
    hello, score, decode = rpc(engine_binary, [
        {"id": "h", "method": "hello"},
        {"id": "s", "method": "score_window",
         "params": {"values": [.5], "active": [True], "widths": [5]}},
        {"id": "g", "method": "decode_grib", "params": {"path": "/unused"}},
    ])
    assert hello["type"] == score["type"] == "result"
    assert decode["type"] == "error"
    assert "ecCodes unavailable" in decode["error"]["message"]


@pytest.fixture
def rust_cloud_backend():
    try:
        cloud_native._functions()
    except RuntimeError as exc:
        unavailable(str(exc))


@pytest.fixture
def gfs_grib_fixture(tmp_path):
    codes = pytest.importorskip("eccodes")

    def write(path, *, shifted=False, different_step=False, missing_rh=False, overflow=False):
        fields = [
            ("isobaricInhPa", 300, "gh", [9000, 9100, 9200, 9300, 9400, 9500]),
            ("isobaricInhPa", 300, "r", [70, 80, 90, 95, 85, 75]),
            ("isobaricInhPa", 300, "tcc", [11, 9999, 30, 40, 50, 60]),
            ("isobaricInhPa", 300, "clwmr", [1e-6] * 6),
            ("highCloudLayer", 0, "hcc", [80] * 6),
            ("isobaricInhPa", 925, "gh", [800] * 6),
            ("isobaricInhPa", 925, "r", [80] * 6),
            ("isobaricInhPa", 975, "r", [75] * 6),
            ("surface", 0, "orog", [10] * 6),
            ("heightAboveGround", 2, "t", [290] * 6),
        ]
        with path.open("wb") as output:
            for index, (level_type, level, name, values) in enumerate(fields):
                if missing_rh and name == "r" and level == 300:
                    continue
                handle = codes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
                try:
                    for key, value in {
                        "centre": "kwbc",
                        "Ni": 3, "Nj": 2,
                        "latitudeOfFirstGridPointInDegrees": 1,
                        "longitudeOfFirstGridPointInDegrees": 179 + int(shifted and index == 1),
                        "latitudeOfLastGridPointInDegrees": 0,
                        "longitudeOfLastGridPointInDegrees": 181 + int(shifted and index == 1),
                        "iDirectionIncrementInDegrees": 1,
                        "jDirectionIncrementInDegrees": 1,
                        "shortName": name, "typeOfLevel": level_type, "level": level,
                        "missingValue": 9999, "bitmapPresent": 1,
                        "step": 6 + int(different_step and index == 1),
                    }.items():
                        codes.codes_set(handle, key, value)
                    if overflow and name == "clwmr":
                        codes.codes_set(handle, "packingType", "grid_ieee")
                        codes.codes_set(handle, "precision", 2)
                        values = [1e39, -1e39, *values[2:]]
                    codes.codes_set_values(handle, values)
                    codes.codes_write(handle, output)
                finally:
                    codes.codes_release(handle)
        return path

    return write


def test_gfs_grid_and_features_match_reference(engine_binary, gfs_grib_fixture, tmp_path, monkeypatch):
    from firesky import gfs
    from firesky.models import GeoPoint
    from firesky.rust_grid import decode_gfs_grid

    path = gfs_grib_fixture(tmp_path / "gfs.grib2")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setenv("FIRESKY_GFS_DECODE_BACKEND", "python")
    expected = gfs.decode_grib_grid(path)
    actual = decode_gfs_grid(path)
    np.testing.assert_array_equal(actual.latitudes, expected.latitudes)
    np.testing.assert_array_equal(actual.longitudes, expected.longitudes)
    assert actual.fields.keys() == expected.fields.keys()
    assert ("isobaricInhPa", 975, "r") in actual.fields
    assert ("heightAboveGround", 2, "t") not in actual.fields
    for key in expected.fields:
        np.testing.assert_allclose(actual.fields[key], expected.fields[key], atol=0, rtol=0, equal_nan=True)
        assert not actual.fields[key].flags.writeable
    for observer, radius in ((GeoPoint(lat=.5, lon=180), 500), (GeoPoint(lat=40, lon=100), 10)):
        expected_rows = gfs.native_features_from_decoded_grid(expected, observer, radius).native_buffer
        expected_rows = expected_rows._storage[:expected_rows.length * 20].reshape(-1, 20)
        result = rpc(engine_binary, [{"id": "f", "method": "prepare_gfs_features", "params": {
            "path": str(path), "observer": [observer.lat, observer.lon], "radius_km": radius,
        }}])[0]
        assert result["type"] == "result", result
        rows = np.asarray(result["result"]["features"], dtype=np.float64).reshape(-1, 20)
        np.testing.assert_allclose(rows, expected_rows, rtol=1e-12, atol=1e-9, equal_nan=True)
        monkeypatch.setenv("FIRESKY_GFS_DECODE_BACKEND", "rust")
        client = gfs.GfsNomadsClient(cache_dir=tmp_path / "client")
        snapshot = client.parse_snapshot(path, observer, radius).native_buffer
        client_rows = snapshot._storage[:snapshot.length * 20].reshape(-1, 20)
        np.testing.assert_allclose(client_rows, expected_rows, rtol=1e-12, atol=1e-9, equal_nan=True)
        monkeypatch.setenv("FIRESKY_GFS_DECODE_BACKEND", "python")
        if len(rows):
            np.testing.assert_array_equal(rows[:6, 11], [11, 80, 30, 40, 50, 60])


def test_gfs_layout_cycle_and_vertical_gaps_fail_closed(engine_binary, gfs_grib_fixture, tmp_path):
    for options, method, fragment in (
        ({"shifted": True}, "decode_gfs", "coordinate layouts"),
        ({"different_step": True}, "decode_gfs", "cycles or forecast steps"),
        ({"missing_rh": True}, "prepare_gfs_features", "missing at 300"),
    ):
        path = gfs_grib_fixture(tmp_path / f"{fragment}.grib2", **options)
        params = {"path": str(path)}
        if method == "prepare_gfs_features":
            params.update(observer=[.5, 180], radius_km=500)
        result = rpc(engine_binary, [{"id": "invalid", "method": method, "params": params}])[0]
        assert result["type"] == "error", result
        assert fragment in result["error"]["message"]


def test_gfs_decode_backend_cache_isolation(engine_binary, gfs_grib_fixture, tmp_path, monkeypatch):
    from firesky import gfs

    path = gfs_grib_fixture(tmp_path / "cached.grib2", overflow=True)
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setenv("FIRESKY_GFS_DECODE_BACKEND", "python")
    old_cache = gfs.decoded_grid_cache_path(path)
    with np.errstate(over="ignore"):
        python_grid = gfs.load_shared_decoded_grid(path)
    monkeypatch.setenv("FIRESKY_GFS_DECODE_BACKEND", "rust")
    rust_cache = gfs.decoded_grid_cache_path(path)
    rust_grid = gfs.load_shared_decoded_grid(path)
    assert old_cache != rust_cache
    assert "decoded-rust-grid-v2" in rust_cache.parts
    assert rust_grid is not python_grid
    np.testing.assert_array_equal(rust_grid.longitudes, python_grid.longitudes)
    for key in python_grid.fields:
        np.testing.assert_array_equal(rust_grid.fields[key], python_grid.fields[key])
    values = rust_grid.fields[("isobaricInhPa", 300, "clwmr")]
    assert np.isposinf(values[0]) and np.isneginf(values[1])
    monkeypatch.setenv("FIRESKY_GFS_DECODE_BACKEND", "python")
    assert gfs.load_shared_decoded_grid(path) is python_grid


def test_cloud_optics_match_float32_reference(rust_cloud_backend, monkeypatch):
    from firesky import national_batch as batch

    rng = np.random.default_rng(52)
    rows = np.column_stack((
        rng.uniform(0, 120, 5000), rng.uniform(-5, 105, 5000),
        np.power(10.0, rng.uniform(-12, -3, 5000)),
        np.power(10.0, rng.uniform(-12, -3, 5000)),
    )).astype(np.float32)
    rows[::7, 1] = np.nan
    rows[::11, 2] = np.nan
    rows[::13, 3] = np.nan
    edge = np.asarray([
        [100, np.nan, np.nan, np.nan], [100, 0, 1e-3, 1e-3],
        [np.inf, np.nan, 0, 0], [-np.inf, 100, np.inf, np.nan],
        [np.nan, -np.inf, -np.inf, np.nan],
    ], dtype=np.float32)
    for values in (rows, edge, rows[:0]):
        monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "python")
        expected = (batch._presence(*values.T), batch._blocker_opacity(*values.T))
        monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "rust")
        actual = (batch._presence(*values.T), batch._blocker_opacity(*values.T))
        for got, want in zip(actual, expected):
            assert got.dtype == np.float32
            np.testing.assert_allclose(got, want, rtol=2e-6, atol=2e-7)
    assert cloud_native.optics(100, np.nan, np.nan, np.nan) == (0, 0)


def test_effective_profiles_match_reference(rust_cloud_backend, monkeypatch):
    from firesky import national_batch as batch

    rng = np.random.default_rng(58)
    profile = np.full((3, 11, 24), np.nan, dtype=np.float32)
    profile[..., 0] = rng.uniform(20, 100, (3, 11))
    profile[..., 6] = rng.uniform(0, 100, (3, 11))
    profile[0, :, 6] = np.nan
    profile[..., 7] = 80
    profile[..., 11:13] = 1e-6
    profile[..., 13] = .2
    profile[..., 14] = [200, 250, 299.5, 300, 300.5, 500, 600, 700, 800, 850, 925]
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "python")
    expected = batch._effective_profile_cloud_fields(profile)
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "rust")
    actual = batch._effective_profile_cloud_fields(profile)
    for got, want in zip(actual, expected):
        np.testing.assert_allclose(got, want, rtol=2e-6, atol=2e-7, equal_nan=True)
        assert got.dtype == want.dtype
    with pytest.raises(ValueError):
        cloud_native.effective_profile(np.empty((2, 15)))


def test_integrated_columns_and_profiles_match_with_rust_optics(rust_cloud_backend, monkeypatch):
    from firesky import gfs, national_batch as batch

    rng = np.random.default_rng(62)
    latitudes, longitudes = np.meshgrid([30, 30.25, 30.5], [120, 120.25, 120.5], indexing="ij")
    fields = {("surface", 0, "orog"): np.full(9, 20, dtype=np.float32)}
    for level in gfs.PRESSURE_LEVELS:
        values = {
            "gh": np.full(9, 44330 * (1 - (level / 1013.25) ** .1903)),
            "r": rng.uniform(50, 95, 9), "t": np.full(9, 250),
            "tcc": rng.uniform(5, 80, 9),
            "clwmr": np.power(10.0, rng.uniform(-9, -5, 9)),
            "icmr": np.power(10.0, rng.uniform(-9, -5, 9)),
            "u": np.full(9, 5), "v": np.full(9, 3), "w": np.zeros(9),
        }
        fields.update({("isobaricInhPa", level, name): value.astype(np.float32) for name, value in values.items()})
    grid = gfs._DecodedGfsGrid(latitudes.ravel().astype(np.float32), longitudes.ravel().astype(np.float32), fields)
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "python")
    columns = batch.derive_grid_columns(grid)
    profiles, levels = batch.derive_grid_layer_profiles(grid)
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "rust")
    actual_columns = batch.derive_grid_columns(grid)
    actual_profiles, actual_levels = batch.derive_grid_layer_profiles(grid)
    assert actual_levels == levels
    np.testing.assert_allclose(actual_columns, columns, rtol=3e-6, atol=3e-6, equal_nan=True)
    np.testing.assert_allclose(actual_profiles, profiles, rtol=3e-6, atol=3e-6, equal_nan=True)


def test_derived_cache_identity_tracks_experimental_backends(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from firesky import national_batch as batch

    path = tmp_path / "cache-input.grib"
    path.write_bytes(b"fixture")
    args = ([path], datetime(2026, 11, 8, tzinfo=timezone.utc), [6], [119, 121, 31, 29], "0p25")
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "python")
    monkeypatch.setenv("FIRESKY_GFS_DECODE_BACKEND", "python")
    reference_identity = batch._model_derived_cache_identity(*args)
    reference_paths = batch._model_derived_cache_paths(path, 1, (9, 11, 24))
    assert reference_paths[0].parent.name == "national-layer-profiles"
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "rust")
    cloud_paths = batch._model_derived_cache_paths(path, 1, (9, 11, 24))
    monkeypatch.setenv("FIRESKY_GFS_DECODE_BACKEND", "rust")
    rust_identity = batch._model_derived_cache_identity(*args)
    rust_paths = batch._model_derived_cache_paths(path, 1, (9, 11, 24))
    assert reference_identity != rust_identity
    assert rust_identity["cloud_optics_backend"] == "rust-f32-v3"
    assert rust_identity["gfs_decoder_backend"] == "rust-grid-v2"
    assert len({paths[0].parent for paths in (reference_paths, cloud_paths, rust_paths)}) == 3
    assert all(a.name == b.name for a, b in zip(reference_paths, rust_paths))
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "python")
    monkeypatch.setenv("FIRESKY_GFS_DECODE_BACKEND", "python")
    assert batch._model_derived_cache_paths(path, 1, (9, 11, 24)) == reference_paths


def test_rust_decode_failure_does_not_silently_fall_back(tmp_path, monkeypatch):
    from firesky import gfs

    monkeypatch.setenv("FIRESKY_GFS_DECODE_BACKEND", "rust")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(tmp_path / "absent-engine"))
    with pytest.raises(RuntimeError, match="Rust engine unavailable"):
        gfs.decode_grib_grid(tmp_path / "input.grib")


@pytest.fixture
def rust_derivation():
    try:
        cloud_native._derivation_function()
    except RuntimeError as exc:
        unavailable(str(exc))


def canonical_derivation_grid(seed, levels, *, average=False):
    from firesky import gfs

    rng = np.random.default_rng(seed)
    latitudes, longitudes = np.meshgrid([29, 29.25, 29.5, 29.75], [120, 120.25, 120.5, 120.75, 121], indexing="ij")
    cells = latitudes.size
    fields = {
        ("surface", 0, "orog"): rng.uniform(-100, 5000, cells).astype(np.float32),
        ("surface", 0, "vis"): rng.uniform(1000, 50000, cells).astype(np.float32),
        ("surface", 0, "prate"): rng.uniform(0, .001, cells).astype(np.float32),
    }
    fractions = np.asarray([0, 0, 3, 8, 15, 30, 50, 75, 100, np.nan], dtype=np.float32)
    for level in levels:
        values = {
            "gh": np.full(cells, 44330 * (1 - (level / 1013.25) ** .1903), dtype=np.float32),
            "r": rng.uniform(50, 100, cells).astype(np.float32),
            "t": rng.uniform(220, 295, cells).astype(np.float32),
            "tcc": rng.choice(fractions, cells),
            "clwmr": np.power(10.0, rng.uniform(-10, -4, cells)).astype(np.float32),
            "icmr": np.power(10.0, rng.uniform(-10, -4, cells)).astype(np.float32),
            "u": rng.uniform(-40, 40, cells).astype(np.float32),
            "v": rng.uniform(-40, 40, cells).astype(np.float32),
            "w": rng.uniform(-1, 1, cells).astype(np.float32),
        }
        for name, values_array in values.items():
            values_array[rng.random(cells) < .12] = np.nan
            fields[("isobaricInhPa", level, name)] = values_array
    for family, short, bottom, top in (
        ("high", "hcc", 42000, 22000),
        ("middle", "mcc", 69000, 44000),
        ("low", "lcc", 97000, 76000),
    ):
        fields[(f"{family}CloudLayer", 0, f"avg_{short}" if average else short)] = rng.choice(
            np.asarray([0, 10, 55, 70, 75, 80, 100, np.nan], dtype=np.float32), cells)
        fields[(f"{family}CloudBottom", 0, "avg_pres")] = np.full(cells, bottom, dtype=np.float32)
        fields[(f"{family}CloudTop", 0, "avg_pres")] = np.full(cells, top, dtype=np.float32)
        fields[(f"{family}CloudTop", 0, "avg_pres")][::7] = 9999
    return gfs._DecodedGfsGrid(latitudes.ravel().astype(np.float32), longitudes.ravel().astype(np.float32), fields)


@pytest.mark.parametrize("levels", [(300,), (925,), (200, 300, 500, 850), (200, 250, 300, 400, 500, 600, 700, 750, 800, 850, 925)])
@pytest.mark.parametrize("average", [False, True])
def test_complete_rust_derivation_matches_reference(rust_derivation, monkeypatch, levels, average):
    from firesky import national_batch as batch

    for seed in range(12):
        grid = canonical_derivation_grid(seed, levels, average=average)
        monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "python")
        expected_columns, expected_profiles, expected_levels = batch._derive_grid_tensor_pair(grid)
        monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "rust")
        columns, profiles, actual_levels = batch._derive_grid_tensor_pair(grid)
        assert actual_levels == expected_levels
        np.testing.assert_allclose(columns, expected_columns, rtol=3e-6, atol=3e-5, equal_nan=True)
        np.testing.assert_allclose(profiles, expected_profiles, rtol=3e-6, atol=3e-5, equal_nan=True)


def test_complete_rust_derivation_never_calls_python_preparation(rust_derivation, monkeypatch):
    from firesky import gfs, national_batch as batch

    grid = canonical_derivation_grid(22, gfs.PRESSURE_LEVELS)
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "rust")

    def forbidden(*args, **kwargs):
        raise AssertionError("Python numeric derivation must not run")

    for name in ("_prepare_grid_derivation", "_pressure_boundary_height_m",
                 "_resolve_unsampled_layer_fractions", "_sparse_upper_high_support",
                 "_coherent_sparse_upper_high_support", "_aggregate_deep_cloud_risk",
                 "_vertical_cloud_structure", "_resolved_family_body_deep_risks",
                 "_apply_connected_deep_material_cap", "_presence", "_blocker_opacity"):
        monkeypatch.setattr(batch, name, forbidden)
    columns, profiles, levels = batch._derive_grid_tensor_pair(grid)
    assert columns.shape == (20, 36)
    assert profiles.shape == (20, len(levels), 24)


def test_standalone_gfs_derivation_matches_reference(engine_binary, gfs_grib_fixture, tmp_path, monkeypatch):
    from firesky import gfs, national_batch as batch

    path = gfs_grib_fixture(tmp_path / "derive.grib2")
    monkeypatch.setenv("FIRESKY_GFS_DECODE_BACKEND", "python")
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "python")
    grid = gfs.decode_grib_grid(path)
    columns, profiles, levels = batch._derive_grid_tensor_pair(grid)
    environment = dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/nonexistent-python")
    environment.pop("PYTHONHOME", None)
    result = rpc(engine_binary, [{"id": "derive", "method": "derive_gfs", "params": {"path": str(path)}}], env=environment)[0]
    assert result["type"] == "result", result
    payload = result["result"]
    assert payload["column_fields"] == 36 and payload["profile_fields"] == 24
    assert payload["pressure_levels"] == list(levels)
    np.testing.assert_allclose(np.asarray(payload["columns"], dtype=np.float32), columns, rtol=3e-6, atol=3e-5, equal_nan=True)
    np.testing.assert_allclose(np.asarray(payload["profiles"], dtype=np.float32), profiles, rtol=3e-6, atol=3e-5, equal_nan=True)

    from firesky.tensor_bundle import load_tensor_bundle
    output = tmp_path / "derived-tensors"
    response = rpc(engine_binary, [{
        "id": "files", "method": "derive_gfs_to_files",
        "params": {"path": str(path), "output_dir": str(output)},
    }], env=environment)[0]
    assert response["type"] == "result", response
    bundle = load_tensor_bundle(
        response["result"]["manifest_path"], kind="gfs-derived-v1", source_path=path, mmap=True,
    )
    assert bundle.manifest["metadata"]["pressure_levels"] == list(levels)
    np.testing.assert_array_equal(bundle.arrays["latitudes"], grid.latitudes)
    np.testing.assert_array_equal(bundle.arrays["longitudes"], grid.longitudes)
    np.testing.assert_allclose(bundle.arrays["columns"], columns, rtol=3e-6, atol=3e-5, equal_nan=True)
    np.testing.assert_allclose(bundle.arrays["profiles"], profiles, rtol=3e-6, atol=3e-5, equal_nan=True)
    for array in bundle.arrays.values():
        assert isinstance(array, np.memmap) and not array.flags.writeable
        array._mmap.close()


def test_file_derivation_exceeds_json_array_limit(engine_binary, tmp_path):
    codes = pytest.importorskip("eccodes")
    path = tmp_path / "large.grib2"
    levels = (200, 250, 300, 400, 500, 600, 700, 750, 800, 850, 925)
    cells = 200 * 180
    with path.open("wb") as output:
        for level in levels:
            for name, value in (("gh", 10000 * (1000 - level) / 800), ("r", 80)):
                handle = codes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
                try:
                    for key, field in {
                        "centre": "kwbc", "Ni": 200, "Nj": 180,
                        "latitudeOfFirstGridPointInDegrees": 45,
                        "latitudeOfLastGridPointInDegrees": .25,
                        "longitudeOfFirstGridPointInDegrees": 70,
                        "longitudeOfLastGridPointInDegrees": 119.75,
                        "iDirectionIncrementInDegrees": .25, "jDirectionIncrementInDegrees": .25,
                        "shortName": name, "typeOfLevel": "isobaricInhPa", "level": level,
                    }.items():
                        codes.codes_set(handle, key, field)
                    codes.codes_set_values(handle, np.full(cells, value))
                    codes.codes_write(handle, output)
                finally:
                    codes.codes_release(handle)
    params = {"path": str(path)}
    output = tmp_path / "large-tensors"
    environment = dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/nonexistent-python")
    limited, files, repeated = rpc(engine_binary, [
        {"id": "json", "method": "derive_gfs", "params": params},
        {"id": "files", "method": "derive_gfs_to_files", "params": dict(params, output_dir=str(output))},
        {"id": "again", "method": "derive_gfs_to_files", "params": dict(params, output_dir=str(output))},
    ], env=environment)
    assert limited["type"] == "error" and "output limit" in limited["error"]["message"]
    assert files["type"] == "result", files
    assert repeated["type"] == "error" and "output directory" in repeated["error"]["message"]
    from firesky.tensor_bundle import load_tensor_bundle
    bundle = load_tensor_bundle(files["result"]["manifest_path"], kind="gfs-derived-v1", mmap=True)
    assert bundle.arrays["profiles"].shape == (cells, len(levels), 24)
    assert bundle.arrays["profiles"].size + bundle.arrays["columns"].size > 10_000_000
    assert bundle.arrays["columns"].shape == (cells, 36)
    for array in bundle.arrays.values():
        array._mmap.close()


def compare_full_derivation(grid, monkeypatch):
    from firesky import national_batch as batch

    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "python")
    expected = batch._derive_grid_tensor_pair(grid)
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "rust")
    actual = batch._derive_grid_tensor_pair(grid)
    assert actual[2] == expected[2]
    for got, want in zip(actual[:2], expected[:2]):
        np.testing.assert_allclose(got, want, rtol=3e-6, atol=3e-5, equal_nan=True)
    return actual


@pytest.mark.parametrize("isolated", [False, True])
def test_full_derivation_sparse_patch_and_local_support(rust_derivation, monkeypatch, isolated):
    from firesky import gfs, national_batch as batch

    grid = canonical_derivation_grid(40, gfs.PRESSURE_LEVELS)
    fields = grid.fields
    fields[("surface", 0, "orog")][:] = 0
    for level in gfs.PRESSURE_LEVELS:
        for name in ("tcc", "clwmr", "icmr"):
            fields[("isobaricInhPa", level, name)][:] = 0
        fields[("isobaricInhPa", level, "gh")][:] = 44330 * (1 - (level / 1013.25) ** .1903)
    for family, name in (("high", "hcc"), ("middle", "mcc"), ("low", "lcc")):
        fields[(f"{family}CloudLayer", 0, name)][:] = 75 if family == "high" else 0
    level = 300 if isolated else 200
    fields[("isobaricInhPa", level, "tcc")][[0, 1, 5, 19]] = 30 if isolated else 3
    fields[("isobaricInhPa", level, "r")][:] = 96 if isolated else 60
    fields[("isobaricInhPa", level, "clwmr")][:] = 2.8e-6 if isolated else 5e-7
    # With any coherent sparse patch, the reference's 300 hPa sanitization
    # applies to all cells. Retain that existing contract during migration.
    fields[("isobaricInhPa", 300, "tcc")][10] = np.nan
    fields[("highCloudLayer", 0, "hcc")][10] = np.nan
    fields[("isobaricInhPa", 300, "clwmr")][10] = 1e-6
    columns, profiles, levels = compare_full_derivation(grid, monkeypatch)
    expected = .75 if isolated else .15
    np.testing.assert_allclose(columns[[0, 1, 5], 0], expected)
    np.testing.assert_allclose(columns[[0, 1, 5, 19], batch.NATIONAL_GRID_LOCAL_UPPER_SUPPORT], expected)
    index = levels.index(300)
    assert np.isnan(profiles[10, index, batch._PROFILE_FRACTION])
    assert columns[10, 0] == 0


@pytest.mark.parametrize("mode", ["instant", "average", "instant-gap"])
def test_full_derivation_bracketed_recovery_and_temporal_provenance(rust_derivation, monkeypatch, mode):
    from firesky import gfs, national_batch as batch

    grid = canonical_derivation_grid(41, gfs.PRESSURE_LEVELS)
    fields = grid.fields
    fields[("surface", 0, "orog")][:] = 0
    for level in gfs.PRESSURE_LEVELS:
        fields[("isobaricInhPa", level, "gh")][:] = 44330 * (1 - (level / 1013.25) ** .1903)
        fields[("isobaricInhPa", level, "tcc")][:] = 0
    fields[("isobaricInhPa", 500, "tcc")][:] = np.nan
    fields[("middleCloudLayer", 0, "mcc")][:] = 70
    fields[("middleCloudLayer", 0, "avg_mcc")] = np.full(20, 90, dtype=np.float32)
    if mode == "average":
        del fields[("middleCloudLayer", 0, "mcc")]
    fields[("middleCloudBottom", 0, "avg_pres")][:] = 58000
    fields[("middleCloudTop", 0, "avg_pres")][:] = 52000
    if mode == "instant-gap":
        fields[("middleCloudTop", 0, "pres")] = np.full(20, np.nan, dtype=np.float32)
    _, profiles, levels = compare_full_derivation(grid, monkeypatch)
    fraction = profiles[:, levels.index(500), batch._PROFILE_FRACTION]
    if mode == "instant":
        np.testing.assert_array_equal(fraction, 70)
    else:
        assert np.all(np.isnan(fraction))


def test_full_derivation_preserves_nonfinite_field_semantics(rust_derivation, monkeypatch):
    from firesky import gfs

    grid = canonical_derivation_grid(42, gfs.PRESSURE_LEVELS)
    for (kind, level, name), values in grid.fields.items():
        if kind == "isobaricInhPa" or (kind == "surface" and name in {"orog", "prate"}):
            values[0] = np.inf
            values[1] = -np.inf
            values[2] = np.nan
    with np.errstate(all="ignore"):
        compare_full_derivation(grid, monkeypatch)


@pytest.fixture
def ec_grib_fixture():
    codes = pytest.importorskip("eccodes")
    from firesky.ecmwf_ifs import ECMWF_IFS_PRESSURE_LEVELS

    def write(path, *, fault=None, pressure_z=False, antimeridian=False, clipped=True):
        fields = []
        for level in ECMWF_IFS_PRESSURE_LEVELS:
            height = 10000 * (1000 - level) / 800
            for name, values in (
                ("gh", [height + i for i in range(6)]),
                ("r", [-1, 20, 70, 85, 101, 95] if clipped else [70, 75, 80, 85, 90, 95]),
                ("t", [250] * 6), ("w", [-.01] * 6),
                ("u", [10] * 6), ("v", [-3] * 6),
            ):
                if fault == "missing" and level == 300 and name == "r":
                    continue
                if pressure_z and name == "gh":
                    name, values = "z", [v * 9.80665 for v in values]
                fields.append(("isobaricInhPa", level, name, values))
        fields.extend([
            ("surface", 0, "z", [980.665] * 6),
            ("entireAtmosphere", 0, "tcc", [.8, .6, .4, .7, .2, .9]),
        ])
        if fault == "duplicate":
            fields.append(fields[0])
        if fault == "optional":
            fields.append(("heightAboveGround", 2, "2t", [290] * 6))
        with path.open("wb") as output:
            for index, (level_type, level, name, values) in enumerate(fields):
                handle = codes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
                try:
                    shifted = int(fault == "grid" and index == 1)
                    west = (179 if antimeridian else 100) + shifted
                    step = 0 if name == "z" and level_type == "surface" else 6
                    if fault == "step" and index == 1:
                        step = 9
                    if fault == "terrain_step" and level_type == "surface":
                        step = 9
                    for key, value in {
                        "centre": "ecmf", "setLocalDefinition": 1, "localDefinitionNumber": 1,
                        "marsType": "an" if fault == "type" and index == 1 else "fc",
                        "marsStream": "enfo" if fault == "stream" and index == 1 else "oper",
                        "Ni": 3, "Nj": 2,
                        "latitudeOfFirstGridPointInDegrees": 1,
                        "latitudeOfLastGridPointInDegrees": 0,
                        "longitudeOfFirstGridPointInDegrees": west,
                        "longitudeOfLastGridPointInDegrees": west + 2,
                        "iDirectionIncrementInDegrees": 1, "jDirectionIncrementInDegrees": 1,
                        "shortName": name, "typeOfLevel": level_type, "level": level,
                        "dataDate": 20261108 if fault == "cycle" and index == 1 else 20261107,
                        "dataTime": 1200, "step": step, "missingValue": 9999, "bitmapPresent": 1,
                    }.items():
                        codes.codes_set(handle, key, value)
                    if fault == "bitmap" and index == 0:
                        values = [9999, *values[1:]]
                    if fault == "temperature" and name == "t" and level == 200:
                        values = [50] * 6
                    if fault == "cloud" and name == "tcc":
                        values = [1.2] * 6
                    if fault == "terrain" and level_type == "surface":
                        values = [100000] * 6
                    codes.codes_set_values(handle, values)
                    codes.codes_write(handle, output)
                finally:
                    codes.codes_release(handle)
        return path
    return write


@pytest.mark.parametrize("pressure_z,antimeridian,clipped", [
    (False, False, True), (True, False, True), (False, True, False), (True, True, False),
])
def test_ec_basic_tensor_matches_reference(
    engine_binary, ec_grib_fixture, tmp_path, monkeypatch, pressure_z, antimeridian, clipped,
):
    from dataclasses import fields
    from firesky import ecmwf_ifs as ec
    path = ec_grib_fixture(tmp_path / "ec.grib2", pressure_z=pressure_z, antimeridian=antimeridian, clipped=clipped)
    bbox = (179, -179, 1, 0) if antimeridian else (100.5, 102, 1, 0)
    kwargs = dict(bbox=bbox, expected_cycle_utc=datetime(2026, 11, 7, 12, tzinfo=timezone.utc), expected_forecast_hour=6)
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "python")
    expected = ec.decode_ecmwf_ifs_basic_tensor(path, **kwargs)
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "rust")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))

    def forbidden(*args, **kwargs):
        raise AssertionError("Python EC numeric/metadata decode was invoked")

    for name in ("require_ecmwf_ifs_capability", "_read_records", "_convert_values", "_validate_common_values"):
        monkeypatch.setattr(ec, name, forbidden)
    actual = ec.decode_ecmwf_ifs_basic_tensor(path, **kwargs)
    for field in fields(ec.EcmwfIfsInputTensor):
        got, want = getattr(actual, field.name), getattr(expected, field.name)
        if isinstance(want, np.ndarray):
            np.testing.assert_array_equal(got, want, err_msg=field.name)
            assert got.flags.writeable == want.flags.writeable, field.name
        elif field.name == "raw_fields":
            assert got.keys() == want.keys()
            for key in want:
                np.testing.assert_array_equal(got[key], want[key])
                assert not got[key].flags.writeable
        else:
            assert got == want, field.name


@pytest.mark.parametrize("fault", [
    "missing", "duplicate", "cycle", "step", "type", "stream", "grid", "bitmap",
    "temperature", "cloud", "terrain", "terrain_step",
])
def test_ec_invalid_inputs_fail_closed(engine_binary, ec_grib_fixture, tmp_path, monkeypatch, fault):
    from firesky import ecmwf_ifs as ec
    path = ec_grib_fixture(tmp_path / "bad.grib2", fault=fault)
    kwargs = dict(bbox=(101, 102, 1, 0), expected_forecast_hour=6)
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    for backend in ("python", "rust"):
        monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", backend)
        with pytest.raises(ec.EcmwfIfsCapabilityError):
            ec.decode_ecmwf_ifs_basic_tensor(path, **kwargs)
    assert not list(tmp_path.glob("tensors"))


def test_ec_independent_derived_tensors_and_cache_identity(engine_binary, ec_grib_fixture, tmp_path, monkeypatch):
    from firesky import ecmwf_ifs as ec, national_batch as batch
    from firesky.ecmwf_point import _tensor_to_native_grid
    from firesky.ecmwf_tensor_cache import _identity
    from firesky.tensor_bundle import load_tensor_bundle
    path = ec_grib_fixture(tmp_path / "ec.grib2", fault="optional", pressure_z=True)
    path.with_suffix(".grib2.json").write_text("{}")
    cycle = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)
    kwargs = dict(bbox=None, expected_cycle_utc=cycle, expected_forecast_hour=6, resolution="0p25")
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "python")
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "python")
    tensor = ec.decode_ecmwf_ifs_basic_tensor(path, **kwargs)
    assert tensor.surface_temperature_k is None
    grid, _, _, spacing = _tensor_to_native_grid(tensor)
    columns, profiles, levels = batch._derive_grid_tensor_pair(grid)
    python_identity = _identity(path, kwargs)
    python_backends = batch._experimental_derivation_backends("ec")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "rust")
    rust_identity = _identity(path, kwargs)
    assert python_identity != rust_identity
    assert rust_identity["decoder_backend"] == "rust-ec-basic-v1"
    assert len(rust_identity["rust_engine_sha256"]) == 64
    assert batch._experimental_derivation_backends("ec") != python_backends
    output = tmp_path / "derived"
    environment = dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/nonexistent-python")
    response = rpc(engine_binary, [{
        "id": "ec", "method": "derive_ecmwf_basic_to_files",
        "params": {"path": str(path), "output_dir": str(output),
                   "expected_cycle_utc": cycle.isoformat(), "expected_forecast_hour": 6},
    }], env=environment)[0]
    assert response["type"] == "result", response
    bundle = load_tensor_bundle(response["result"]["manifest_path"], kind="ecmwf-basic-derived-v1", source_path=path)
    assert bundle.manifest["metadata"]["cloud_proxy"] == "ec-basic-tcc-rh-v1"
    assert bundle.manifest["metadata"]["spacing_degrees"] == spacing
    assert bundle.manifest["metadata"]["pressure_levels"] == list(levels)
    np.testing.assert_array_equal(bundle.arrays["latitudes"], grid.latitudes)
    np.testing.assert_array_equal(bundle.arrays["longitudes"], grid.longitudes)
    np.testing.assert_allclose(bundle.arrays["columns"], columns, rtol=3e-6, atol=3e-5, equal_nan=True)
    np.testing.assert_allclose(bundle.arrays["profiles"], profiles, rtol=3e-6, atol=3e-5, equal_nan=True)


def test_ec_expected_parameters_and_limits(engine_binary, ec_grib_fixture, tmp_path, monkeypatch):
    path = ec_grib_fixture(tmp_path / "ec.grib2")
    cases = [
        {"expected_forecast_hour": 147}, {"expected_forecast_hour": 9},
        {"expected_cycle_utc": "2026-11-07T12:00:00+08:00"},
        {"expected_cycle_utc": "2026-11-08T12:00:00+00:00"},
        {"max_points": 5}, {"max_messages": 55}, {"max_messages": 0},
        {"bbox": [100, 102, -1, 1]}, {"bbox": [110, 112, 1, 0]},
    ]
    for index, extra in enumerate(cases):
        output = tmp_path / f"invalid-{index}"
        result = rpc(engine_binary, [{
            "id": "invalid", "method": "decode_ecmwf_basic_to_files",
            "params": {"path": str(path), "output_dir": str(output), **extra},
        }])[0]
        assert result["type"] == "error", (extra, result)
        assert not output.exists()
    from firesky import ecmwf_ifs as ec
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "rust")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", "/nonexistent-engine")
    with pytest.raises(ec.EcmwfIfsCapabilityError, match="unavailable"):
        ec.decode_ecmwf_ifs_basic_tensor(path)
    with pytest.raises(ec.EcmwfIfsCapabilityError, match="unavailable"):
        ec.decode_ecmwf_ifs_full_catalog_tensor(path)


def test_ec_rust_tensor_cache_roundtrip(engine_binary, ec_grib_fixture, tmp_path, monkeypatch):
    from firesky import ecmwf_ifs as ec
    from firesky.ecmwf_tensor_cache import load_or_decode_basic_tensor
    path = ec_grib_fixture(tmp_path / "ec.grib2")
    path.with_suffix(".grib2.json").write_text("{}")
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "rust")
    kwargs = dict(
        cache_root=tmp_path / "cache", cache_bytes=16 * 1024 * 1024,
        bbox=None, expected_cycle_utc=datetime(2026, 11, 7, 12, tzinfo=timezone.utc),
        expected_forecast_hour=6, resolution="0p25",
    )
    expected = load_or_decode_basic_tensor(path, decoder=ec.decode_ecmwf_ifs_basic_tensor, **kwargs)
    def forbidden(*args, **kwargs):
        raise AssertionError("cache was not used")
    actual = load_or_decode_basic_tensor(path, decoder=forbidden, **kwargs)
    np.testing.assert_array_equal(actual.geopotential_height_m, expected.geopotential_height_m)
    np.testing.assert_array_equal(actual.relative_humidity_percent, expected.relative_humidity_percent)
    assert dict(actual.value_diagnostics) == dict(expected.value_diagnostics)
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "python")
    with pytest.raises(AssertionError, match="cache was not used"):
        load_or_decode_basic_tensor(path, decoder=forbidden, **kwargs)
