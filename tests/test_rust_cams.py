"""Compact production CAMS differential and standalone file-chain tests."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from firesky import aerosol, rust_cams
from firesky.national_batch import _map_cams_optics_to_model_grid
from firesky.tensor_bundle import load_tensor_bundle
from test_rust_migration import engine_binary, rpc, unavailable

LEVELS = aerosol.CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS


@pytest.fixture
def cams_native():
    try:
        rust_cams._functions()
    except RuntimeError as exc:
        unavailable(str(exc))


def write_cams(path, *, hour=0, minute=0, mutation=None,
               longitudes=(100, 101, 102), source_columns=(0, 1, 2)):
    codes = pytest.importorskip("eccodes")
    records = [("aod550", 0)] + [("aerext532", level) for level in LEVELS]
    if mutation == "missing":
        records.pop()
    if mutation == "duplicate":
        records[-1] = records[-2]
    with path.open("wb") as output:
        for index, (name, level) in enumerate(records):
            handle = codes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
            try:
                for key, value in {
                    "centre": "ecmf", "shortName": name,
                    "typeOfLevel": "surface" if name == "aod550" else "isobaricInhPa",
                    "level": level, "Ni": len(longitudes), "Nj": 2,
                    "latitudeOfFirstGridPointInDegrees": 1,
                    "longitudeOfFirstGridPointInDegrees": longitudes[0],
                    "latitudeOfLastGridPointInDegrees": 0,
                    "longitudeOfLastGridPointInDegrees": longitudes[-1],
                    "iDirectionIncrementInDegrees": 1, "jDirectionIncrementInDegrees": 1,
                    "dataDate": 20261109, "dataTime": minute, "forecastTime": hour,
                    "packingType": "grid_ieee", "precision": 2,
                }.items():
                    codes.codes_set(handle, key, value)
                values = np.arange(6, dtype=float) / 1000 + 0.1234567890123 if index == 0 else (
                    np.arange(6, dtype=float) * 1e-8 + 0.0001234567890123 + index * 1e-7
                )
                if mutation == "time" and index == 1:
                    codes.codes_set(handle, "forecastTime", hour + 3)
                if mutation == "grid" and index == 1:
                    codes.codes_set(handle, "longitudeOfFirstGridPointInDegrees", 101)
                if mutation == "unknown" and index == 1:
                    codes.codes_set(handle, "shortName", "t")
                if mutation == "aod" and index == 0:
                    values[0] = 100.01
                if mutation == "negative" and index == 1:
                    values[0] = -0.1
                if mutation == "zero" and index > 0:
                    values[:] = 0
                if mutation == "bitmap" and index == 1:
                    codes.codes_set(handle, "missingValue", 9999)
                    codes.codes_set(handle, "bitmapPresent", 1)
                    values[0] = 9999
                values = values.reshape(2, 3)[:, source_columns].ravel()
                codes.codes_set_values(handle, values)
                codes.codes_write(handle, output)
            finally:
                codes.codes_release(handle)
    return path


def coordinate_bundle(root, lats, lons):
    root.mkdir()
    arrays = {}
    for name, values in (("latitudes", lats), ("longitudes", lons)):
        value = np.asarray(values)
        path = root / f"{name}.npy"
        np.save(path, value, allow_pickle=False)
        payload = path.read_bytes()
        arrays[name] = {"file": path.name, "dtype": value.dtype.str,
                        "shape": list(value.shape), "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest()}
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({
        "schema": "firesky.tensor-bundle.v1", "producer": "firesky-engine/test",
        "kind": "model-test", "source": {"bytes": 5, "sha256": hashlib.sha256(b"model").hexdigest()},
        "metadata": {}, "arrays": arrays,
    }))
    return manifest


@pytest.mark.parametrize("minute", [0, 6])
def test_compact_decoder_f64_time_mutability_and_cache_namespace(engine_binary, tmp_path, monkeypatch, minute):
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    path = write_cams(tmp_path / "cams.grib", minute=minute)
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
    expected = aerosol.load_cams_optics_grid(path)
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "rust")
    actual = aerosol.load_cams_optics_grid(path)
    assert actual is aerosol.load_cams_optics_grid(path)
    assert actual is not expected
    assert actual.path == path
    assert actual.valid_time == expected.valid_time == datetime(2026, 11, 9, 0, minute, tzinfo=timezone.utc)
    for name in ("lats", "lons", "aod550", "aerext532_m_inv"):
        a, b = getattr(actual, name), getattr(expected, name)
        assert a.dtype == np.float64 and a.flags.writeable == b.flags.writeable
        np.testing.assert_array_equal(a.view("u8"), b.view("u8"))


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "time", "grid", "unknown", "aod", "negative", "zero", "bitmap"])
def test_bad_compact_inputs_reject_without_fallback(engine_binary, tmp_path, monkeypatch, mutation):
    path = write_cams(tmp_path / "bad.grib", mutation=mutation)
    output = tmp_path / "out"
    result = rpc(engine_binary, [{"id": "c", "method": "decode_cams_to_files",
        "params": {"path": str(path), "output_dir": str(output)}}])[0]
    assert result["type"] == "error", result
    assert not output.exists()
    if mutation == "bitmap":
        # The reference does not mask extinction bitmaps; the Rust decoder
        # deliberately rejects missing values instead of accepting sentinel 9999.
        monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
        assert aerosol._cams_optics_file_valid(path)
        monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "rust")
        monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
        assert not aerosol._cams_optics_file_valid(path)


def test_real_dateline_decode_merge_deduplicates_edge_and_preserves_all_levels(engine_binary, tmp_path):
    full = write_cams(tmp_path / "full.grib", longitudes=(179, 180, 181))
    west = write_cams(tmp_path / "west.grib", longitudes=(179, 180), source_columns=(0, 1))
    east = write_cams(tmp_path / "east.grib", longitudes=(-180, -179), source_columns=(1, 2))

    def decode(label, path, paths=()):
        output = tmp_path / label
        response = rpc(engine_binary, [dict(
            id=label, method="decode_cams_to_files",
            params=dict(path=str(path), paths=[str(p) for p in paths], output_dir=str(output)))],
            env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))[0]
        return output, response

    full_output, baseline = decode("single", full)
    split_output, merged = decode("merged", west, (east,))
    assert baseline["type"] == merged["type"] == "result", (baseline, merged)
    expected = load_tensor_bundle(full_output / "manifest.json", kind="cams-optics-v1", source_path=full)
    actual = load_tensor_bundle(split_output / "manifest.json", kind="cams-optics-v1", source_path=west)
    assert actual.arrays["latitudes"].shape == (6,)
    assert len(actual.manifest["metadata"]["sources"]) == 2

    def ordered(bundle):
        lons = (bundle.arrays["longitudes"] + 180) % 360 - 180
        order = np.lexsort((lons, bundle.arrays["latitudes"]))
        return order, lons

    expected_order, expected_lons = ordered(expected)
    actual_order, actual_lons = ordered(actual)
    np.testing.assert_array_equal(actual_lons[actual_order], expected_lons[expected_order])
    for name in ("latitudes", "aod550"):
        np.testing.assert_array_equal(actual.arrays[name][actual_order], expected.arrays[name][expected_order])
    np.testing.assert_array_equal(actual.arrays["aerext532"][:, actual_order],
                                  expected.arrays["aerext532"][:, expected_order])

    write_cams(east, hour=3, longitudes=(-180, -179), source_columns=(1, 2))
    failed_output, failed = decode("wrong-time", west, (east,))
    assert failed["type"] == "error", failed
    assert "different valid times" in failed["error"]["message"]
    assert not failed_output.exists()


@pytest.mark.parametrize("layout", ["regular", "holes", "duplicates", "single_axis"])
def test_mapping_differential_geometry_and_nonfinite_channels(cams_native, monkeypatch, layout):
    rng = np.random.default_rng(20261109)
    lats, lons = np.meshgrid(np.array([0., 1., 2.]), np.array([179., 180., 181.]), indexing="ij")
    lats, lons = lats.ravel(), lons.ravel()
    if layout == "holes":
        lats, lons = lats[:-1], lons[:-1]
    elif layout == "duplicates":
        lats, lons = np.append(lats, lats[0]), np.append(lons, lons[0])
    elif layout == "single_axis":
        lats[:] = 1
    n = lats.size
    aod = rng.uniform(0, 0.6, n)
    ext = rng.uniform(1e-9, .01, (11, n))
    aod[1] = np.nan
    ext[0, 3] = np.inf
    ext[1, 4] = -1
    ext[2, 0] = 1e39
    optics = SimpleNamespace(lats=lats, lons=lons, aod550=aod, aerext532_m_inv=ext)
    targets = np.array([0, .5, 1, 2, 3, np.nan, np.inf, -np.inf])
    target_lons = np.array([179, 179.5, 180, 181, 182, 180, 181, np.nan])
    with np.errstate(all="ignore"):
        monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
        expected = _map_cams_optics_to_model_grid(optics, targets, target_lons)
        monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "rust")
        actual = _map_cams_optics_to_model_grid(optics, targets, target_lons)
    for a, b in zip(actual, expected):
        np.testing.assert_array_equal(a, b)
    assert actual[1].flags.c_contiguous
    assert rust_cams.map_optics(optics, np.empty(0), np.empty(0))[1].shape == (0, 11)


def test_random_mapping_and_temporal_f32_exact(cams_native, monkeypatch):
    rng = np.random.default_rng(773)
    lats, lons = np.meshgrid(np.arange(17) / 3, np.arange(23) / 3 + 100, indexing="ij")
    order = rng.permutation(lats.size)
    optics = SimpleNamespace(lats=lats.ravel()[order], lons=lons.ravel()[order],
        aod550=rng.uniform(0, 1, order.size),
        aerext532_m_inv=rng.uniform(0, .01, (11, order.size)))
    targets, target_lons = rng.uniform(-1, 6, 1017), rng.uniform(99, 109, 1017)
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
    expected = _map_cams_optics_to_model_grid(optics, targets, target_lons)
    actual = rust_cams.map_optics(optics, targets, target_lons)
    for a, b in zip(actual, expected):
        np.testing.assert_array_equal(a, b)
    for weight in [0, np.float32(1/3), .5, 1]:
        left = rng.uniform(0, .1, (1017, 11)).astype("f4")
        right = rng.uniform(0, .1, (1017, 11)).astype("f4")
        np.testing.assert_array_equal(rust_cams.blend(left, right, weight),
            left + (right - left) * np.float32(weight))
    with pytest.raises(ValueError):
        rust_cams.blend([1], [1], np.nan)


def test_scale_height_differential_shapes_thresholds_and_defaults(cams_native, monkeypatch):
    rng = np.random.default_rng(83)
    profiles = np.exp(rng.uniform(-24, -2, (11, 2, 257)))
    profiles[:, 0, :8] = np.array([0, 1e-9, np.nextafter(1e-9, 1), .02,
                                 np.nextafter(.02, 0), np.nan, np.inf, -1])
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
    expected = aerosol.effective_aerosol_scale_height_km(profiles)
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "rust")
    actual = aerosol.effective_aerosol_scale_height_km(profiles)
    np.testing.assert_allclose(actual, expected, rtol=3e-12, atol=3e-12)
    assert actual.shape == (2, 257) and actual.dtype == np.float64
    assert aerosol.effective_aerosol_scale_height_km(np.empty((11, 0))).shape == (0,)


def test_standalone_interpolation_reads_model_files_without_python(engine_binary, tmp_path, monkeypatch, cams_native):
    left = write_cams(tmp_path / "left.grib")
    right = write_cams(tmp_path / "right.grib", hour=3)
    target = coordinate_bundle(tmp_path / "model", np.array([0., .5, 1.], dtype="<f8"),
                               np.array([100., 101.25, 102.], dtype="<f4"))
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
    l = _map_cams_optics_to_model_grid(aerosol.load_cams_optics_grid(left), np.array([0., .5, 1.]),
                                      np.array([100., 101.25, 102.]))
    r = _map_cams_optics_to_model_grid(aerosol.load_cams_optics_grid(right), np.array([0., .5, 1.]),
                                      np.array([100., 101.25, 102.]))
    output = tmp_path / "interpolated"
    env = {**os.environ, "PATH": "", "PYTHONPATH": "", "FIRESKY_PYTHON": "/no-python"}
    result = rpc(engine_binary, [{"id": "c", "method": "interpolate_cams_to_files", "params": {
        "left_path": str(left), "right_path": str(right), "target_manifest": str(target),
        "target_time": "2026-11-09T09:00:00+08:00", "output_dir": str(output),
    }}], env=env)[0]
    assert result["type"] == "result", result
    bundle = load_tensor_bundle(result["result"]["manifest_path"], kind="cams-mapped-v1", source_path=left)
    weight = np.float32(1/3)
    np.testing.assert_array_equal(bundle.arrays["aod550"], l[0] + (r[0] - l[0]) * weight)
    np.testing.assert_array_equal(bundle.arrays["aerext532"], l[1] + (r[1] - l[1]) * weight)
    np.testing.assert_array_equal(bundle.arrays["covered"], [1, 1, 1])
    assert bundle.manifest["metadata"]["right_source"]["sha256"] == hashlib.sha256(right.read_bytes()).hexdigest()


@pytest.mark.parametrize("mutation", ["time", "coverage", "hash", "shape", "symlink", "duplicate", "trailing", "fortran"])
def test_standalone_rejects_bad_brackets_and_target_bundles(engine_binary, tmp_path, mutation):
    path = write_cams(tmp_path / "cams.grib")
    target = coordinate_bundle(tmp_path / "model", np.array([0., .5, 1.]), np.array([100., 101., 102.]))
    manifest = json.loads(target.read_text())
    if mutation == "hash":
        manifest["arrays"]["latitudes"]["sha256"] = "0" * 64
    elif mutation == "shape":
        manifest["arrays"]["latitudes"]["shape"] = [1, 3]
    elif mutation == "coverage":
        np.save(target.parent / "latitudes.npy", np.array([10., .5, 1.]))
    elif mutation == "symlink":
        real = target.parent / "real.npy"
        (target.parent / "latitudes.npy").rename(real)
        (target.parent / "latitudes.npy").symlink_to(real)
    elif mutation == "trailing":
        with (target.parent / "latitudes.npy").open("ab") as f:
            f.write(b"unexpected")
    elif mutation == "fortran":
        # One-dimensional order F is encoded C by numpy; force a bad header.
        array = target.parent / "latitudes.npy"
        array.write_bytes(array.read_bytes().replace(b"False", b"True "))
    if mutation in {"coverage", "trailing", "fortran"}:
        payload = (target.parent / "latitudes.npy").read_bytes()
        manifest["arrays"]["latitudes"].update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    target.write_text(json.dumps(manifest))
    if mutation == "duplicate":
        target.write_text(target.read_text().replace('"arrays": {', '"arrays": {"latitudes":' +
            json.dumps(manifest["arrays"]["latitudes"]) + ",", 1))
    output = tmp_path / "out"
    result = rpc(engine_binary, [{"id": "c", "method": "interpolate_cams_to_files", "params": {
        "left_path": str(path), "right_path": str(path), "target_manifest": str(target),
        "target_time": "2026-11-09T01:00:00Z" if mutation == "time" else "2026-11-09T00:00:00Z",
        "output_dir": str(output),
    }}])[0]
    assert result["type"] == "error", result
    assert not output.exists()


def test_application_result_cache_keys_separate_cams_backend(monkeypatch):
    from firesky import web_server

    payload = {"lat": 30., "lon": 120., "elevation_m": 0.,
               "timezone_name": "Asia/Shanghai", "target_date": "2026-11-09",
               "phenomenon": "dusk", "model_source": "gfs"}
    monkeypatch.setattr(web_server, "_point_model_cycle",
        lambda _: datetime(2026, 11, 9, tzinfo=timezone.utc))
    monkeypatch.setattr(web_server, "_national_model_cycle",
        lambda *args: datetime(2026, 11, 9, tzinfo=timezone.utc))
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
    point_python = web_server.point_forecast_cache_key(payload)
    regional_python = web_server.national_forecast_cache_identity(payload)
    assert "cams_optics_backend" not in regional_python
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "rust")
    assert web_server.point_forecast_cache_key(payload) != point_python
    regional_rust = web_server.national_forecast_cache_identity(payload)
    assert regional_rust != regional_python
    assert regional_rust.pop("cams_optics_backend") == "rust-cams-v1"
    assert regional_rust == regional_python


def test_standalone_single_map_and_output_collision(engine_binary, tmp_path, monkeypatch):
    path = write_cams(tmp_path / "cams.grib")
    target = coordinate_bundle(tmp_path / "model", np.array([.5, 20.]), np.array([101.25, 101.]))
    output = tmp_path / "mapped"
    params = {"path": str(path), "target_manifest": str(target), "output_dir": str(output)}
    result = rpc(engine_binary, [{"id": "m", "method": "map_cams_to_files", "params": params}])[0]
    assert result["type"] == "result", result
    monkeypatch.setenv("FIRESKY_CAMS_BACKEND", "python")
    expected = _map_cams_optics_to_model_grid(aerosol.load_cams_optics_grid(path),
        np.array([.5, 20.]), np.array([101.25, 101.]))
    bundle = load_tensor_bundle(result["result"]["manifest_path"], kind="cams-mapped-v1")
    for name, values in zip(("aod550", "aerext532", "covered"), expected):
        np.testing.assert_array_equal(bundle.arrays[name], values)
    original = (output / "manifest.json").read_bytes()
    failure = rpc(engine_binary, [{"id": "m", "method": "map_cams_to_files", "params": params}])[0]
    assert failure["type"] == "error"
    assert (output / "manifest.json").read_bytes() == original


def test_production_aerosol_assembly_differential(engine_binary, cams_native, tmp_path, monkeypatch):
    from firesky import national_batch as batch

    left = write_cams(tmp_path / "left.grib")
    right = write_cams(tmp_path / "right.grib", hour=3)
    cycle = datetime(2026, 11, 9, tzinfo=timezone.utc)
    selection = SimpleNamespace(
        left=SimpleNamespace(path=left, valid_time=cycle),
        right=SimpleNamespace(path=right, valid_time=cycle.replace(hour=3)),
        right_weight=1/3,
    )
    monkeypatch.setattr(batch, "find_cached_cams_bracket", lambda *args: selection)
    monkeypatch.setenv("FIRESKY_RUST_ENGINE_BIN", str(engine_binary))
    outputs = []
    for backend in ("python", "rust"):
        monkeypatch.setenv("FIRESKY_CAMS_BACKEND", backend)
        prepared = SimpleNamespace(
            grid=np.zeros((1, 6, batch.GRID_FIELD_COUNT), dtype=np.float32),
            hours=[1], latitudes=np.repeat([0., 1.], 3),
            longitudes=np.tile([100., 101., 102.], 2),
            lat0=0., lon0=100., spacing=1., rows=2, cols=3,
        )
        samples = np.zeros((1, 1, batch.NATIONAL_SAMPLE_FIELDS), dtype=np.float32)
        samples[..., batch.NATIONAL_SAMPLE_VALID] = 1
        profiles, paths, sources, qualities, coverage = batch._apply_cams_aerosol_profiles(
            prepared, samples, np.array([[.5, 101.25, 0]], dtype=np.float32),
            np.array([cycle.replace(hour=1).timestamp()]), cycle, tmp_path,
            [1., 100., 0., 102.],
        )
        outputs.append((prepared.grid.copy(), samples.copy(), profiles, paths, sources, qualities, coverage))
    for actual, expected in zip(outputs[1], outputs[0]):
        if isinstance(actual, np.ndarray):
            np.testing.assert_array_equal(actual, expected)
        else:
            assert actual == expected
    assert outputs[1][-1] == 1


def test_target_limit_rejected_before_cams_decode(engine_binary, tmp_path):
    target = coordinate_bundle(tmp_path / "model",
        np.zeros(500001, dtype="<f4"), np.ones(500001, dtype="<f4"))
    # A regular placeholder hashes successfully but is not GRIB. The error
    # must come from the target limit, before GRIB parsing or output creation.
    source = tmp_path / "not-grib"
    source.write_bytes(b"placeholder")
    output = tmp_path / "out"
    result = rpc(engine_binary, [{"id": "m", "method": "map_cams_to_files", "params": {
        "path": str(source), "target_manifest": str(target), "output_dir": str(output),
    }}])[0]
    assert result["type"] == "error"
    assert "cell limit" in result["error"]["message"]
    assert not output.exists()
