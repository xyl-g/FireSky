"""Regional solar preparation differentials; Python is the reference only."""

from datetime import date, datetime, timezone
import json
import os
import subprocess

import numpy as np
import pytest

from firesky import national_batch as reference, rust_astronomy
from firesky.tensor_bundle import load_tensor_bundle
from test_rust_astronomy import CASES
from test_rust_migration import engine_binary, rpc, unavailable


def point(lat=31.23, lon=121.47, elevation_m=50, timezone_name="Asia/Shanghai"):
    return dict(lat=lat, lon=lon, elevation_m=elevation_m, timezone_name=timezone_name)


@pytest.fixture(autouse=True)
def backend(monkeypatch):
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND", "python")
    try:
        rust_astronomy._regional_function()
    except RuntimeError as exc:
        unavailable(str(exc))


def assert_arrays(actual, expected):
    assert len(actual) == len(expected)
    for index, (got, wanted) in enumerate(zip(actual, expected)):
        assert got.dtype == wanted.dtype
        assert got.shape == wanted.shape
        assert got.flags.c_contiguous and got.flags.writeable
        if wanted.dtype == np.float64:
            # acos is ill-conditioned near polar north; scalar libm and NumPy
            # differ by <1e-7 degrees there, without changing sample validity.
            tolerance = 5e-6 if np.max(np.abs(wanted), initial=0) > 1e8 else (1e-7 if index == 2 else 2e-9)
        elif wanted.dtype == np.float32:
            tolerance = 5e-4
        else:
            tolerance = 0
        np.testing.assert_allclose(got, wanted, rtol=0, atol=tolerance, err_msg=f"array {index}")


REGIONAL_CASES = CASES + [
    (90, 0, 0, "UTC", "2026-06-21"),
    (-90, 0, 0, "UTC", "2026-12-21"),
    (89, 0, 9000, "UTC", "2026-12-21"),
    (-89, 0, -500, "UTC", "2026-06-21"),
    (-33.45, -70.67, 123.456789, "America/Santiago", "2026-09-06"),
    (21.0, -157.0, 3.1415926535, "Pacific/Honolulu", "1900-01-01"),
]


@pytest.mark.parametrize("lat,lon,height,tz,day", REGIONAL_CASES)
@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
def test_regional_timeline_and_samples(monkeypatch, lat, lon, height, tz, day, phenomenon):
    points = [point(lat, lon, height, tz)]
    day = date.fromisoformat(day)
    cycle = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    hours = np.arange(-48, 49, 3, dtype=np.float64)
    expected = reference.prepare_solar_event_timeline(points, day, phenomenon)
    samples = reference.prepare_solar_samples(points, day, phenomenon, cycle, hours)
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND", "rust")
    assert_arrays(reference.prepare_solar_event_timeline(points, day, phenomenon), expected)
    actual = reference.prepare_solar_samples(points, day, phenomenon, cycle, hours)
    assert_arrays(actual, samples)
    np.testing.assert_array_equal(actual[1][:, :, [2, 3, 6]], samples[1][:, :, [2, 3, 6]])
    np.testing.assert_array_equal(actual[1][:, :, 5], np.float32(.12))


def test_random_mixed_zones_and_empty(monkeypatch):
    rng = np.random.default_rng(20261109)
    zones = ["Asia/Shanghai", "UTC", "Pacific/Kiritimati", "America/New_York"]
    points = [point(float(rng.uniform(-65, 65)), float(rng.uniform(-180, 180)),
                    float(rng.uniform(-500, 9000)), zones[i % len(zones)])
              for i in range(512)]
    day = date(2026, 11, 9)
    expected = reference.prepare_solar_event_timeline(points, day, "dawn")
    empty = reference.prepare_solar_event_timeline([], day, "dusk")
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND", "rust")
    assert_arrays(reference.prepare_solar_event_timeline(points, day, "dawn"), expected)
    assert_arrays(reference.prepare_solar_event_timeline([], day, "dusk"), empty)


def test_rust_path_does_not_use_python_numerical_helpers(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Python regional numerical helper was called")
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND", "rust")
    for name in ("_astral_solar_event_utc_minutes", "_solar_position_from_utc_epoch",
                 "_solar_event_epoch_on_local_date"):
        monkeypatch.setattr(reference, name, forbidden)
    result = reference.prepare_solar_samples(
        [point()], date(2026, 11, 9), "dusk",
        datetime(2026, 11, 9, tzinfo=timezone.utc), np.arange(-24, 49, 3))
    assert result[1].shape == (1, 20, 9)


@pytest.mark.parametrize("hours", [0, [], [[0, 24]], [0, 0], [24, 0], [0, np.nan], [0, np.inf]])
def test_invalid_hours_rejected(monkeypatch, hours):
    args = ([point()], date(2026, 11, 9), "dusk", datetime(2026, 11, 9, tzinfo=timezone.utc), hours)
    with pytest.raises(ValueError):
        reference.prepare_solar_samples(*args)
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND", "rust")
    with pytest.raises(ValueError):
        reference.prepare_solar_samples(*args)


@pytest.mark.parametrize("height", [True, None, "50", float("nan"), float("inf"), -501, 9001])
def test_invalid_elevation_rejected(monkeypatch, height):
    args = ([point(elevation_m=height)], date(2026, 11, 9), "dawn")
    with pytest.raises(ValueError):
        reference.prepare_solar_event_timeline(*args)
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND", "rust")
    with pytest.raises(ValueError):
        reference.prepare_solar_event_timeline(*args)


@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
def test_coverage_checks_only_valid_samples(monkeypatch, phenomenon):
    points, day = [point()], date(2026, 11, 9)
    cycle = datetime(2026, 11, 9, tzinfo=timezone.utc)
    timeline = reference.prepare_solar_event_timeline(points, day, phenomenon)
    offsets = (timeline[4] - cycle.timestamp()) / 3600
    required = offsets[timeline[3]]
    hours = np.array([required.min() - .001, required.max() + .001])
    expected = reference.prepare_solar_samples(points, day, phenomenon, cycle, hours)
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND", "rust")
    assert_arrays(reference.prepare_solar_samples(points, day, phenomenon, cycle, hours), expected)
    for backend in ["python", "rust"]:
        monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND", backend)
        with pytest.raises(RuntimeError):
            reference.prepare_solar_samples(points, day, phenomenon, cycle, hours + 24)


def write_request(path, **overrides):
    request = dict(points=[point()], date="2026-11-09", phenomenon="dusk",
                   cycle_utc="2026-11-09T00:00:00Z", hours=list(range(-24, 49, 3)))
    request.update(overrides)
    path.write_text(json.dumps(request), encoding="utf-8")
    return path


def prepare_rpc(binary, path, output):
    return rpc(binary, [{"id": "regional", "method": "prepare_regional_solar_to_files",
                        "params": {"path": str(path), "output_dir": str(output)}}])[0]


def test_standalone_tensor_bundle_without_python(engine_binary, tmp_path):
    path = write_request(tmp_path / "request.json")
    output = tmp_path / "result"
    request = {"id": "regional", "method": "prepare_regional_solar_to_files",
               "params": {"path": str(path), "output_dir": str(output)}}
    env = dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/does/not/exist")
    process = subprocess.run([str(engine_binary), "serve"], input=json.dumps(request)+"\n",
                             text=True, capture_output=True, env=env, check=True, timeout=30)
    result = json.loads(process.stdout)
    assert result["type"] == "result", result
    bundle = load_tensor_bundle(output / "manifest.json", kind="regional-solar-v1", source_path=path)
    timeline = reference.prepare_solar_event_timeline([point()], date(2026, 11, 9), "dusk")
    for name, expected in zip(
        ["observers", "elevation", "azimuth", "valid", "evaluation_epoch", "anchor_epoch"], timeline
    ):
        actual = bundle.arrays[name]
        np.testing.assert_allclose(actual, expected, rtol=0, atol=5e-6)
    samples = reference.prepare_solar_samples(
        [point()], date(2026, 11, 9), "dusk", datetime(2026, 11, 9, tzinfo=timezone.utc),
        np.arange(-24, 49, 3))[1]
    np.testing.assert_allclose(bundle.arrays["samples"], samples, rtol=0, atol=5e-4)
    metadata = bundle.manifest["metadata"]
    assert metadata["polar_policy"] == "legacy-hour-angle-clamp"
    assert metadata["force_anchor_valid"] is True
    assert metadata["local_aod"].startswith("placeholder-0.12")
    before = (output / "manifest.json").read_bytes()
    assert prepare_rpc(engine_binary, path, output)["type"] == "error"
    assert (output / "manifest.json").read_bytes() == before


@pytest.mark.parametrize("changes", [
    {"points": []}, {"date": "2026-1-09"}, {"phenomenon": "noon"}, {"hours": []},
    {"cycle_utc": None}, {"hours": None}, {"hours": [0, 24, 24]},
    {"hours": [0]}, {"extra": 1}, {"points": [point(timezone_name="unknown/zone")]},
    {"points": [point(lat=91)]}, {"points": [dict(point(), extra=1)]},
])
def test_bad_request_leaves_no_bundle(engine_binary, tmp_path, changes):
    path = write_request(tmp_path / "bad.json", **changes)
    output = tmp_path / "out"
    assert prepare_rpc(engine_binary, path, output)["type"] == "error"
    assert not output.exists()


def test_request_symlink_and_no_sample_variant(engine_binary, tmp_path):
    path = write_request(tmp_path / "request.json", cycle_utc=None, hours=None)
    linked = tmp_path / "linked.json"
    linked.symlink_to(path)
    assert prepare_rpc(engine_binary, linked, tmp_path / "bad")["type"] == "error"
    output = tmp_path / "out"
    assert prepare_rpc(engine_binary, path, output)["type"] == "result"
    bundle = load_tensor_bundle(output / "manifest.json", kind="regional-solar-v1", source_path=path)
    assert len(bundle.arrays) == 6 and "samples" not in bundle.arrays


@pytest.mark.parametrize("lat,lon,zone,day", [
    (65, -180, "Pacific/Apia", date(2026, 6, 18)),
    (-65, -180, "America/New_York", date(2026, 8, 13)),
])
def test_unavailable_requested_local_day_is_not_silently_reassigned(monkeypatch, lat, lon, zone, day):
    points = [point(lat, lon, 0, zone)]
    for backend in ["python", "rust"]:
        monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND", backend)
        with pytest.raises(ValueError):
            reference.prepare_solar_event_timeline(points, day, "dusk")


def test_skipped_local_date_retains_legacy_result(monkeypatch):
    from zoneinfo import ZoneInfo
    points = [point(-13.8, -171.8, 0, "Pacific/Apia")]
    day = date(2011, 12, 30)
    expected = reference.prepare_solar_event_timeline(points, day, "dusk")
    # The reference does not reject this nonexistent local day. Preserve its
    # behavior during migration; do not claim the result occurs on that day.
    assert datetime.fromtimestamp(float(expected[-1][0]), ZoneInfo("Pacific/Apia")).date() != day
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND", "rust")
    assert_arrays(reference.prepare_solar_event_timeline(points, day, "dusk"), expected)


@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
def test_complete_production_domain(monkeypatch, phenomenon):
    from firesky.web_server import national_points_from_payload
    points = national_points_from_payload({
        "regions": ["china"], "model_source": "gfs",
        "gfs_resolution": "0p25", "spacing_deg": .25,
    })
    assert len(points) == 36685
    day = date(2026, 11, 9)
    cycle = datetime(2026, 11, 9, tzinfo=timezone.utc)
    hours = np.arange(-24, 49, 3, dtype=np.float64)
    expected = reference.prepare_solar_samples(points, day, phenomenon, cycle, hours)
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND", "rust")
    actual = reference.prepare_solar_samples(points, day, phenomenon, cycle, hours)
    assert_arrays(actual, expected)
    np.testing.assert_array_equal(actual[1][:, :, [2, 3, 6]], expected[1][:, :, [2, 3, 6]])


def test_oversized_request_rejected_before_reading(engine_binary, tmp_path):
    path = tmp_path / "oversized.json"
    with path.open("wb") as stream:
        stream.truncate(64 * 1024 * 1024 + 1)
    assert prepare_rpc(engine_binary, path, tmp_path / "out")["type"] == "error"
    assert not (tmp_path / "out").exists()
