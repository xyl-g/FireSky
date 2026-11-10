"""Selected-column diagnostics must not borrow raytraced target material."""
from dataclasses import replace
import os
import json

import numpy as np
import pytest

from firesky import national_batch as batch
from test_rust_migration import engine_binary, rpc
from test_rust_assessment import inputs, python_reference, ec_grib_fixture
from firesky.tensor_bundle import load_tensor_bundle


def nullable(values):
    result = values.astype(object)
    result[~np.isfinite(values)] = None
    return result.tolist()

def assert_diagnostics(actual, expected):
    actual = np.asarray(actual, dtype=np.float32)
    for field in [0, 4, 5]:
        np.testing.assert_array_equal(actual[..., field], expected[..., field])
    for field in [1, 2]:
        np.testing.assert_allclose(actual[..., field], expected[..., field], rtol=3e-6, atol=1e-12)
    for field in [3, 6]:
        np.testing.assert_allclose(actual[..., field], expected[..., field], rtol=3e-6, atol=1e-7)


def fixture(seed=31):
    rng = np.random.default_rng(seed)
    levels = np.array([200, 250, 300, 400, 500, 600, 700, 750, 800, 850, 925], np.float32)
    profiles = rng.uniform(0, 1, (3, 6, len(levels), 24)).astype(np.float32)
    profiles[..., 0] = 85
    profiles[..., 1] *= 12000
    profiles[..., 3] -= .5
    profiles[..., 6:11] *= 100
    profiles[..., 11:13] *= 1e-5
    profiles[..., 14] = levels
    profiles[..., 15:17] *= 100
    columns = np.zeros((3, 6, 36), np.float32)
    columns[..., 14] = .001
    columns[..., 17:23] = [6000, 10000, 2000, 7000, 500, 2000]
    points = np.array([[0, 100], [.5, 100.5], [1, 102], [-1, 99], [2, 103],
                       [.125, 101.75], [.8, 100.2]], np.float32)
    samples = np.zeros((len(points), 9), np.float32)
    samples[:, 2] = [0, 1, 2, -4, 7, 0, 1]
    samples[:, 3] = [1, 2, 0, 2, 3, 2, 1]
    samples[:, 4] = [0, .1234567, .5, -1, 2, .9, 0]
    prepared = batch.NationalBatchPrepared(
        grid=columns, hours=np.arange(3), rows=2, cols=3, lat0=0, lon0=100, spacing=1,
        latitudes=np.repeat([0, 1], 3), longitudes=np.tile([100, 101, 102], 2),
        cache_paths=[], layer_profiles=profiles, pressure_levels=tuple(levels))
    return prepared, points, samples


def params(prepared, points, samples):
    return dict(profiles=nullable(prepared.layer_profiles),
                aggregates=nullable(prepared.grid[..., [14, 17, 18, 19, 20, 21, 22]]),
                pressure_levels=list(map(float, prepared.pressure_levels)), points=points.tolist(),
                samples=samples.tolist(), origin=[prepared.lat0, prepared.lon0],
                spacing=prepared.spacing, rows=prepared.rows, cols=prepared.cols,
                average_envelope=prepared.boundary_temporal_semantics == "average-envelope-not-instantaneous",
                include_sampled=True)


@pytest.mark.parametrize("case", ["finite", "missing", "all_missing", "no_cloud", "one_level",
                                 "average", "unknown_levels", "fallback_levels", "fractional_time_indices", "deep"])
def test_selected_profiles_match_python(engine_binary, monkeypatch, case):
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "python")
    prepared, points, samples = fixture()
    profiles = prepared.layer_profiles
    if case == "missing":
        profiles[0, 0, ::2] = np.nan
        profiles[1, 1, 1::2] = np.nan
        profiles[:, 3, :, 6] = np.nan
        prepared.grid[1, 1, 17:23] = np.nan
    elif case == "all_missing":
        profiles[:] = np.nan
        prepared.grid[:] = np.nan
    elif case == "no_cloud":
        profiles[..., 6] = 0
        profiles[..., 13] = 0
    elif case == "one_level":
        profiles[..., 6] = 0
        profiles[..., 13] = 0
        profiles[:, :, 2, 6] = 60
    elif case == "average":
        prepared = replace(prepared, boundary_temporal_semantics="average-envelope-not-instantaneous")
    elif case == "unknown_levels":
        prepared = replace(prepared, pressure_levels=tuple([201, 249.5, 300, 400, 500, 600, 700, 750, 800, 850, 925]))
    elif case == "fallback_levels":
        prepared = replace(prepared, pressure_levels=())
    elif case == "fractional_time_indices":
        samples[:, 2:4] += .75
    elif case == "deep":
        profiles[..., 6] = 80
        profiles[..., 7] = 95
        profiles[..., 15] = 95
        profiles[..., 3] = -.4
    stencil = batch._horizontal_stencil_for_coordinates(
        points[:, 0], points[:, 1], lat0=0, lon0=100, spacing=1, rows=2, cols=3)
    wanted_profile = batch._horizontal_sample_tensor_step(profiles, samples, stencil)
    wanted_aggregate = batch._horizontal_sample_tensor_step(
        prepared.grid[..., [14, 17, 18, 19, 20, 21, 22]], samples, stencil)
    expected = batch._profile_diagnostic_bundle(prepared, samples[:, None], stencil)
    response = rpc(engine_binary, [dict(id="profile", method="diagnose_profile_columns",
                                        params=params(prepared, points, samples))])[0]
    assert response["type"] == "result", response
    result = response["result"]
    np.testing.assert_array_equal(np.array(result["sampled_profiles"], np.float32).view(np.uint32),
                                  wanted_profile.view(np.uint32))
    np.testing.assert_array_equal(np.array(result["sampled_aggregates"], np.float32).view(np.uint32),
                                  wanted_aggregate.view(np.uint32))
    assert_diagnostics(result["values"], np.stack(expected, axis=-1)[:, 0])
    assert result["confidence_affects_quality"] is False
    assert result["target_phase_proven"] is False


@pytest.mark.parametrize("fault", ["shape", "levels", "aggregates", "points", "spacing", "samples", "unknown"])
def test_bad_profile_request_preserves_server(engine_binary, fault):
    prepared, points, samples = fixture()
    request = params(prepared, points, samples)
    if fault == "shape": request["profiles"][0][0].pop()
    elif fault == "levels":
        request["pressure_levels"] = []
        request["profiles"][0][0][0][14] = None
    elif fault == "aggregates": request["aggregates"].pop()
    elif fault == "points": request["points"] = []
    elif fault == "spacing": request["spacing"] = 0
    elif fault == "samples": request["samples"].pop()
    elif fault == "unknown": request["python_fallback"] = True
    bad, hello = rpc(engine_binary, [dict(id="bad", method="diagnose_profile_columns", params=request),
                                   dict(id="h", method="hello")])
    assert bad["type"] == "error"
    assert hello["type"] == "result"


@pytest.mark.parametrize("model", ["gfs", "ecmwf-basic"])
@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
def test_raw_profile_diagnostics_without_python(engine_binary, inputs, tmp_path, monkeypatch, model, phenomenon):
    path, request = inputs(model=model, phenomenon=phenomenon)
    prepared, reference = python_reference(request, monkeypatch)
    stencil = batch._horizontal_stencil(prepared, request["points"])
    expected = batch._profile_diagnostic_bundle(prepared, reference[1], stencil)
    output = tmp_path / "profile-diagnostics"
    response = rpc(engine_binary, [dict(id="profiles", method="diagnose_stored_profiles_to_files",
        params=dict(path=str(path), output_dir=str(output)))],
        env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))[0]
    assert response["type"] == "result", response
    bundle = load_tensor_bundle(output / "manifest.json", kind="regional-profile-diagnostics-v1", source_path=path)
    assert_diagnostics(bundle.arrays["profile_diagnostics"], np.stack(expected, axis=-1))
    np.testing.assert_allclose(bundle.arrays["assessment"], reference[0], rtol=4e-5, atol=5e-4)
    metadata = bundle.manifest["metadata"]["profile_diagnostics"]
    assert metadata["confidence_affects_quality"] is False
    assert metadata["maximum_resident_profile_frames"] == 2
    assert metadata["frame_loads"] <= len(request["frames"])
    assert bundle.manifest["metadata"]["public_forecast_complete"] is False
    assert len(bundle.arrays) == 16


def add_boundaries(path, hour, average):
    import eccodes as codes
    with path.open("ab") as stream:
        for family, bottom, top in [("high", 45000, 20000), ("middle", 85000, 45000), ("low", 100000, 90000)]:
            for boundary, pressure in [("Bottom", bottom), ("Top", top)]:
                handle = codes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
                try:
                    for key, value in dict(
                        centre="kwbc", Ni=3, Nj=2, latitudeOfFirstGridPointInDegrees=0,
                        latitudeOfLastGridPointInDegrees=1, longitudeOfFirstGridPointInDegrees=100,
                        longitudeOfLastGridPointInDegrees=102, iDirectionIncrementInDegrees=1,
                        jDirectionIncrementInDegrees=1, jScansPositively=1,
                        shortName="pres", typeOfLevel=f"{family}Cloud{boundary}", level=0,
                        dataDate=20261109, dataTime=0, step=hour, packingType="grid_ieee", precision=1,
                    ).items():
                        codes.codes_set(handle, key, value)
                    if average:
                        codes.codes_set(handle, "stepType", "avg")
                        codes.codes_set(handle, "startStep", 0)
                        codes.codes_set(handle, "endStep", hour)
                    codes.codes_set_values(handle, np.full(6, pressure))
                    codes.codes_write(handle, stream)
                finally:
                    codes.codes_release(handle)


@pytest.mark.parametrize("average", [False, True])
def test_raw_boundary_semantics_are_preserved(engine_binary, inputs, tmp_path, monkeypatch, average):
    from pathlib import Path
    path, request = inputs()
    for frame in request["frames"]:
        add_boundaries(Path(frame["path"]), frame["hour"], average)
    prepared, reference = python_reference(request, monkeypatch)
    semantics = "average-envelope-not-instantaneous" if average else "instantaneous"
    assert prepared.boundary_temporal_semantics == semantics
    wanted = batch._profile_diagnostic_bundle(
        prepared, reference[1], batch._horizontal_stencil(prepared, request["points"]))
    output = tmp_path / "boundary-result"
    response = rpc(engine_binary, [dict(id="boundary", method="diagnose_stored_profiles_to_files",
        params=dict(path=str(path), output_dir=str(output)))])[0]
    assert response["type"] == "result", response
    bundle = load_tensor_bundle(output / "manifest.json", kind="regional-profile-diagnostics-v1", source_path=path)
    assert bundle.manifest["metadata"]["profile_diagnostics"]["boundary_temporal_semantics"] == semantics
    assert_diagnostics(bundle.arrays["profile_diagnostics"], np.stack(wanted, axis=-1))


def test_failed_profile_chain_removes_owned_children(engine_binary, inputs, tmp_path):
    path, request = inputs()
    request["cams"].clear()
    # Keep the catalog syntactically valid so failure follows profile publication.
    request["cams"].append(dict(valid_time="2026-11-09T00:00:00Z", path="missing.grib"))
    path.write_text(json.dumps(request))
    output = tmp_path / "failed-profiles"
    response = rpc(engine_binary, [dict(id="fail", method="diagnose_stored_profiles_to_files",
        params=dict(path=str(path), output_dir=str(output)))])[0]
    assert response["type"] == "error"
    assert not output.exists()
