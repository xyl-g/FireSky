"""Raw GRIB -> solar/CAMS -> complete native assessment tensor differentials."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from firesky import aerosol, gfs, national_batch as batch
from firesky.tensor_bundle import load_tensor_bundle
from test_rust_cams import write_cams
from test_rust_migration import engine_binary, rpc, ec_grib_fixture


def rewrite_time(path, hour):
    import eccodes as codes
    messages = []
    with path.open("rb") as stream:
        while (handle := codes.codes_grib_new_from_file(stream)) is not None:
            messages.append(handle)
    try:
        with path.open("wb") as stream:
            for handle in messages:
                codes.codes_set(handle, "dataDate", 20261109)
                codes.codes_set(handle, "dataTime", 0)
                # EC terrain can remain step=0 under the existing strict rule.
                terrain = codes.codes_get(handle, "typeOfLevel") == "surface" and codes.codes_get(handle, "shortName") == "z"
                codes.codes_set(handle, "step", 0 if terrain else hour)
                codes.codes_write(handle, stream)
    finally:
        for handle in messages:
            codes.codes_release(handle)


def write_gfs(path, hour, *, missing=False, shifted=False, terrain_delta=0, spacing=1,
              longitude_start=100):
    codes = pytest.importorskip("eccodes")
    records = [("surface", 0, "orog", np.arange(6)*10+20+terrain_delta)]
    records += [("surface", 0, "vis", np.full(6, 30000))]
    records += [("surface", 0, "prate", np.zeros(6))]
    for level in gfs.PRESSURE_LEVELS:
        values = dict(gh=np.full(6, 44330*(1-(level/1013.25)**.190263)),
                      r=np.full(6, 85), t=np.full(6, 260),
                      u=np.full(6, 4), v=np.full(6, -2), w=np.zeros(6),
                      tcc=np.arange(6)*3+35 if level == 300 else np.zeros(6),
                      clwmr=np.zeros(6), icmr=np.full(6, 4e-6) if level == 300 else np.zeros(6))
        for name, value in values.items():
            if missing and level == 300 and name in ("tcc", "clwmr", "icmr"):
                continue
            records.append(("isobaricInhPa", level, name, value))
    with path.open("wb") as stream:
        for kind, level, name, values in records:
            handle = codes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
            try:
                for key, value in {
                    "centre": "kwbc", "Ni": 3, "Nj": 2,
                    "latitudeOfFirstGridPointInDegrees": 0,
                    "latitudeOfLastGridPointInDegrees": spacing,
                    "longitudeOfFirstGridPointInDegrees": longitude_start+int(shifted),
                    "longitudeOfLastGridPointInDegrees": longitude_start+int(shifted)+2*spacing,
                    "iDirectionIncrementInDegrees": spacing, "jDirectionIncrementInDegrees": spacing,
                    "jScansPositively": 1,
                    "shortName": name, "typeOfLevel": kind, "level": level,
                    "dataDate": 20261109, "dataTime": 0, "step": hour,
                    "packingType": "grid_ieee", "precision": 1,
                }.items():
                    codes.codes_set(handle, key, value)
                codes.codes_set_values(handle, values)
                codes.codes_write(handle, stream)
            finally:
                codes.codes_release(handle)
    return path


@pytest.fixture
def inputs(tmp_path, monkeypatch, ec_grib_fixture):
    for name in ("ASTRONOMY", "CLOUD", "CAMS"):
        monkeypatch.setenv(f"FIRESKY_{name}_BACKEND", "python")
    monkeypatch.setenv("FIRESKY_GFS_DECODE_BACKEND", "python")
    monkeypatch.setenv("FIRESKY_ECMWF_DECODE_BACKEND", "python")

    def make(*, model="gfs", phenomenon="dusk", points=None):
        hours = [9, 12, 15] if phenomenon == "dusk" else [21, 24, 27, 45, 48, 51]
        frames = []
        for hour in hours:
            path = tmp_path / f"{model}-{hour}.grib2"
            if model == "gfs":
                write_gfs(path, hour)
            else:
                ec_grib_fixture(path, clipped=False)
                rewrite_time(path, hour)
            frames.append(dict(hour=hour, path=str(path)))
        optics = []
        for hour in hours:
            path = write_cams(tmp_path / f"cams-{hour}.grib", hour=hour)
            optics.append(dict(valid_time=(datetime(2026,11,9,tzinfo=timezone.utc)+timedelta(hours=hour)).isoformat(),
                               path=str(path)))
        request = dict(model=model, cycle_utc="2026-11-09T00:00:00Z",
                       date="2026-11-09" if phenomenon == "dusk" else "2026-11-10",
                       phenomenon=phenomenon, radius_km=200, frames=frames, cams=optics,
                       points=points or [
                           dict(lat=.25, lon=100.25, timezone_name="Asia/Shanghai",
                                elevation_source="observer", elevation_m=50),
                           dict(lat=.5, lon=101.5, timezone_name="UTC", elevation_source="model-grid"),
                       ])
        path = tmp_path / "request.json"
        path.write_text(json.dumps(request))
        return path, request
    return make


def run(binary, path, output, **kwargs):
    return rpc(binary, [{"id": "assess", "method": "assess_stored_regional_to_files",
                         "params": dict(path=str(path), output_dir=str(output))}], **kwargs)[0]


def python_reference(request, monkeypatch):
    from firesky.ecmwf_ifs import decode_ecmwf_ifs_basic_tensor
    from firesky.ecmwf_point import _tensor_to_native_grid
    grids, columns, profiles = [], [], []
    cycle = datetime.fromisoformat(request["cycle_utc"].replace("Z", "+00:00"))
    for frame in request["frames"]:
        path = Path(frame["path"])
        if request["model"] == "gfs":
            grid = gfs.decode_grib_grid(path)
            if request.get("bbox") is not None:
                grid = gfs.subset_decoded_grid(grid, request["bbox"])
            batch._validate_gfs_model_grid_for_national(grid)
        else:
            tensor = decode_ecmwf_ifs_basic_tensor(path, bbox=request.get("bbox"), expected_cycle_utc=cycle,
                                                  expected_forecast_hour=frame["hour"])
            grid = _tensor_to_native_grid(tensor)[0]
        order = np.lexsort((grid.longitudes, grid.latitudes))
        grid = gfs._DecodedGfsGrid(grid.latitudes[order], grid.longitudes[order],
                                  {k: v[order] for k, v in grid.fields.items()})
        grids.append(grid)
        col, profile, levels = batch._derive_grid_tensor_pair(grid)
        columns.append(col)
        profiles.append(profile)
    first = grids[0]
    lats, lons = np.unique(first.latitudes), np.unique(first.longitudes)
    spacing = float(lats[1] - lats[0] if len(lats) > 1 else lons[1] - lons[0])
    prepared = batch.NationalBatchPrepared(
        grid=np.stack(columns), hours=np.array([f["hour"] for f in request["frames"]], dtype=np.float32),
        rows=len(lats), cols=len(lons), lat0=float(lats[0]), lon0=float(lons[0]), spacing=spacing,
        latitudes=first.latitudes, longitudes=first.longitudes, cache_paths=[],
        layer_profiles=np.stack(profiles), pressure_levels=levels,
        boundary_temporal_semantics=batch._cloud_boundary_temporal_semantics(first))
    points = [dict(p, _elevation_source=p["elevation_source"]) for p in request["points"]]
    batch.apply_decoded_model_surface_elevations(first, points)
    catalog = {datetime.fromisoformat(f["valid_time"]): Path(f["path"]) for f in request["cams"]}
    def select(cache, area, target):
        left, right = aerosol.cams_profile_bracket_times(target)
        def file(time):
            return SimpleNamespace(path=catalog[time], valid_time=time)
        return SimpleNamespace(left=file(left), right=file(right), right_weight=(
            0 if left == right else (target-left).total_seconds()/(right-left).total_seconds()))
    monkeypatch.setattr(batch, "find_cached_cams_bracket", select)
    result = batch.run_native_national_batch(
        prepared, points, date.fromisoformat(request["date"]), request["phenomenon"], cycle,
        request["radius_km"], Path("."), [1,100,0,102], "cpu")
    return prepared, result


@pytest.mark.parametrize("model", ["gfs", "ecmwf-basic"])
@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
def test_raw_assessment_matches_python_orchestration(engine_binary, inputs, tmp_path, monkeypatch, model, phenomenon):
    path, request = inputs(model=model, phenomenon=phenomenon)
    prepared, expected = python_reference(request, monkeypatch)
    output = tmp_path / "result"
    env = dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python")
    response = run(engine_binary, path, output, env=env)
    assert response["type"] == "result", response
    bundle = load_tensor_bundle(output / "manifest.json", kind="regional-assessment-v1", source_path=path)
    metadata = bundle.manifest["metadata"]
    assert metadata["assessment_fields"] == 82 and metadata["public_forecast_complete"] is False
    for name, wanted in zip(["assessment", "samples", "evaluation_epoch", "anchor_epoch"], expected[:4]):
        np.testing.assert_allclose(bundle.arrays[name], wanted, rtol=4e-5, atol=5e-4, equal_nan=True, err_msg=name)
    np.testing.assert_allclose(bundle.arrays["columns"], prepared.grid, rtol=4e-6, atol=5e-5, equal_nan=True)
    assert np.any(bundle.arrays["assessment"] != 0)
    assert np.all(bundle.arrays["samples"][:, :, 5][bundle.arrays["samples"][:, :, 6] >= .5] != np.float32(.12))
    assert np.any(bundle.arrays["aerosol_profiles"] > 0)
    for i, entry in enumerate(metadata["profile_bundles"]):
        child = output / entry["manifest"]
        assert hashlib.sha256(child.read_bytes()).hexdigest() == entry["identity"]["sha256"]
        profile = load_tensor_bundle(child, kind="regional-profiles-v1", source_path=request["frames"][i]["path"])
        np.testing.assert_allclose(profile.arrays["profiles"], prepared.layer_profiles[i], rtol=4e-6, atol=5e-5, equal_nan=True)
    for got, wanted in zip(bundle.arrays["aerosol_anchor_brackets"], expected[4]):
        left, right = [datetime.fromtimestamp(float(v), timezone.utc) for v in got[:2]]
        assert f"cams-ads {left:%Y-%m-%d %H:%MZ}..{right:%Y-%m-%d %H:%MZ} w={got[2]:.6f}" == wanted
    before = (output / "manifest.json").read_bytes()
    assert run(engine_binary, path, output)["type"] == "error"
    assert (output / "manifest.json").read_bytes() == before


@pytest.mark.parametrize("model", ["gfs", "ecmwf-basic"])
def test_cropped_grid_exact_cell_and_relative_sources(engine_binary, inputs, tmp_path, monkeypatch, model):
    path, request = inputs(model=model, points=[
        dict(lat=0, lon=100, timezone_name="Asia/Shanghai", elevation_source="model-grid"),
        dict(lat=1, lon=101, timezone_name="Asia/Shanghai", elevation_source="observer", elevation_m=25),
    ])
    request["bbox"] = [100, 101, 1, 0]
    prepared, expected = python_reference(request, monkeypatch)
    for record in request["frames"] + request["cams"]:
        record["path"] = Path(record["path"]).name
    path.write_text(json.dumps(request))
    output = tmp_path / "cropped"
    response = run(engine_binary, path, output)
    assert response["type"] == "result", response
    bundle = load_tensor_bundle(output / "manifest.json", kind="regional-assessment-v1", source_path=path)
    assert bundle.manifest["metadata"]["geometry"]["rows"] == 2
    assert bundle.manifest["metadata"]["geometry"]["cols"] == 2
    np.testing.assert_allclose(bundle.arrays["columns"], prepared.grid, rtol=4e-6, atol=5e-5, equal_nan=True)
    for name, wanted in zip(["assessment", "samples", "evaluation_epoch", "anchor_epoch"], expected[:4]):
        np.testing.assert_allclose(bundle.arrays[name], wanted, rtol=4e-5, atol=5e-4, equal_nan=True, err_msg=name)


def test_nonunit_grid_spacing_matches_reference(engine_binary, inputs, tmp_path, monkeypatch):
    path, request = inputs(points=[
        dict(lat=.125, lon=100.125, timezone_name="Asia/Shanghai", elevation_source="model-grid"),
    ])
    for frame in request["frames"]:
        write_gfs(Path(frame["path"]), frame["hour"], spacing=.5)
    prepared, expected = python_reference(request, monkeypatch)
    assert prepared.spacing == .5
    output = tmp_path / "nonunit"
    response = run(engine_binary, path, output)
    assert response["type"] == "result", response
    bundle = load_tensor_bundle(output / "manifest.json", kind="regional-assessment-v1", source_path=path)
    assert bundle.manifest["metadata"]["geometry"]["spacing"] == .5
    for name, wanted in zip(["assessment", "samples", "evaluation_epoch", "anchor_epoch"], expected[:4]):
        np.testing.assert_allclose(bundle.arrays[name], wanted, rtol=4e-5, atol=5e-4, equal_nan=True, err_msg=name)


def test_multi_rectangle_cams_real_decoding_matches_single_grid_and_rejects_bad_part(
        engine_binary, inputs, tmp_path):
    _, request = inputs()

    def assess(label):
        path = tmp_path / f"{label}.json"
        path.write_text(json.dumps(request))
        output = tmp_path / label
        response = run(engine_binary, path, output,
                       env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))
        return path, output, response

    baseline_path, baseline_output, baseline = assess("single")
    assert baseline["type"] == "result", baseline
    expected = load_tensor_bundle(baseline_output / "manifest.json",
                                 kind="regional-assessment-v1", source_path=baseline_path)
    for hour, row in zip((9, 12, 15), request["cams"]):
        west = write_cams(tmp_path / f"west-{hour}.grib", hour=hour,
                          longitudes=(100, 101), source_columns=(0, 1))
        east = write_cams(tmp_path / f"east-{hour}.grib", hour=hour,
                          longitudes=(101, 102), source_columns=(1, 2))
        row.update(path=str(west), paths=[str(east)])
    split_path, split_output, split = assess("split")
    assert split["type"] == "result", split
    actual = load_tensor_bundle(split_output / "manifest.json",
                               kind="regional-assessment-v1", source_path=split_path)
    for name in ("assessment", "samples", "aerosol_profiles", "evaluation_epoch", "anchor_epoch"):
        np.testing.assert_allclose(actual.arrays[name], expected.arrays[name],
                                   rtol=0, atol=0, equal_nan=True, err_msg=name)
    sources = actual.manifest["metadata"]["sources"]
    roles = [row["role"] for row in sources if str(row.get("role", "")).startswith("cams:")]
    assert len(roles) >= 2
    for prefix in {role.rsplit(":", 1)[0] for role in roles}:
        assert {role for role in roles if role.startswith(prefix + ":")} == {
            prefix + ":0", prefix + ":1"}

    write_cams(Path(request["cams"][1]["paths"][0]), hour=15,
               longitudes=(101, 102), source_columns=(1, 2))
    _, failed_output, failed = assess("mismatched-time")
    assert failed["type"] == "error", failed
    assert "different valid times" in failed["error"]["message"]
    assert not failed_output.exists()


def test_wrapped_real_grib_assessment_frames_and_frozen_profile_keep_both_sides(
        engine_binary, tmp_path):
    request = dict(
        model="gfs", cycle_utc="2026-11-09T00:00:00Z", date="2026-11-09",
        phenomenon="dusk", radius_km=200, bbox=[179, -179, 1, 0], frames=[], cams=[],
        points=[dict(name="West", lat=.25, lon=179.25, timezone_name="Pacific/Wake",
                     elevation_source="model-grid"),
                dict(name="East", lat=.5, lon=-179.5, timezone_name="Pacific/Wake",
                     elevation_source="model-grid")])
    for hour in (3, 6, 9):
        model = write_gfs(tmp_path / f"wrapped-gfs-{hour}.grib", hour, longitude_start=179)
        cams = write_cams(tmp_path / f"wrapped-full-{hour}.grib", hour=hour,
                          longitudes=(179, 180, 181))
        request["frames"].append(dict(hour=hour, path=str(model)))
        request["cams"].append(dict(
            valid_time=(datetime(2026, 11, 9, tzinfo=timezone.utc)
                        + timedelta(hours=hour)).isoformat(), path=str(cams)))

    def frames(label):
        path = tmp_path / f"{label}.json"
        path.write_text(json.dumps(request))
        output = tmp_path / label
        response = rpc(engine_binary, [dict(
            id=label, method="build_stored_frames_to_files",
            params=dict(path=str(path), output_dir=str(output)))],
            env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))[0]
        assert response["type"] == "result", response
        bundle = load_tensor_bundle(output / "manifest.json", kind="regional-frames-v1", source_path=path)
        return output, bundle

    full_output, expected = frames("wrapped-full")
    for hour, row in zip((3, 6, 9), request["cams"]):
        west = write_cams(tmp_path / f"wrapped-west-{hour}.grib", hour=hour,
                          longitudes=(179, 180), source_columns=(0, 1))
        east = write_cams(tmp_path / f"wrapped-east-{hour}.grib", hour=hour,
                          longitudes=(-180, -179), source_columns=(1, 2))
        row.update(path=str(west), paths=[str(east)])
    output, actual = frames("wrapped-split")
    for name in ("assessment", "samples", "columns", "profile_diagnostics", "aerosol_profiles"):
        np.testing.assert_array_equal(actual.arrays[name], expected.arrays[name], err_msg=name)
    np.testing.assert_array_equal(actual.arrays["longitudes"], [179, -180, -179] * 2)
    np.testing.assert_array_equal(actual.arrays["columns"][0, :, 24], [20, 30, 40, 50, 60, 70])
    np.testing.assert_array_equal(actual.arrays["observers"][:, 1], [179.25, -179.5])
    np.testing.assert_array_equal(actual.arrays["observers"][:, 2], [30, 50])
    assert actual.manifest["metadata"]["geometry"] == dict(
        rows=2, cols=3, lat0=0, lon0=179, spacing=1)
    for row, aod in zip(actual.arrays["samples"], [.1259567890123, .1264567890123]):
        valid = row[:, 6] >= .5
        assert valid.any()
        np.testing.assert_allclose(row[valid, 5], aod, rtol=0, atol=2e-8)
    payload = json.loads((output / actual.manifest["metadata"]["frame_payload"]["file"]).read_text())
    assert [row["lon"] for row in payload["results"]] == [179.25, -179.5]
    assert [row["observer_elevation_asl_m"] for row in payload["results"]] == [30, 50]

    descriptor = actual.manifest["metadata"]["cross_section_source"]
    response = rpc(engine_binary, [dict(
        id="east-profile", method="build_stored_cross_section", params=dict(
            source_path=str(output / descriptor["file"]), source_identity=descriptor["identity"],
            point=dict(lat=.5, lon=-179.5, elevation_m=50, timezone_name="Pacific/Wake"),
            offset_minutes=0, max_distance_km=0))])[0]
    assert response["type"] == "result", response
    assert response["result"]["observer"]["lon"] == -179.5
    assert response["result"]["terrain_elevation_asl_m"] == [50]


@pytest.mark.parametrize("fault", [
    "missing_cloud", "actual_step", "coordinates", "terrain_change", "missing_cams", "wrong_cams_time",
    "observer_outside", "bad_elevation", "unknown_field", "duplicate_hour", "duplicate_cams", "source_symlink",
    "cams_coverage", "bad_bbox",
])
def test_failed_chain_leaves_no_parent_or_profile_bundle(engine_binary, inputs, tmp_path, fault):
    path, request = inputs()
    first = Path(request["frames"][0]["path"])
    if fault == "missing_cloud": write_gfs(first, 9, missing=True)
    elif fault == "actual_step": write_gfs(first, 8)
    elif fault == "coordinates": write_gfs(Path(request["frames"][1]["path"]), 12, shifted=True)
    elif fault == "terrain_change": write_gfs(Path(request["frames"][1]["path"]), 12, terrain_delta=1)
    elif fault == "missing_cams": request["cams"].pop(0)
    elif fault == "wrong_cams_time": write_cams(Path(request["cams"][0]["path"]), hour=6)
    elif fault == "observer_outside": request["points"][0]["lat"] = 40
    elif fault == "bad_elevation": request["points"][0]["elevation_m"] = 9001
    elif fault == "unknown_field": request["extra"] = True
    elif fault == "duplicate_hour": request["frames"][1]["hour"] = 9
    elif fault == "duplicate_cams": request["cams"].append(request["cams"][0])
    elif fault == "cams_coverage":
        for frame in request["frames"]:
            write_gfs(Path(frame["path"]), frame["hour"], shifted=True)
        for point in request["points"]:
            point["lon"] += 1
    elif fault == "bad_bbox": request["bbox"] = [100, 102, -1, 1]
    elif fault == "source_symlink":
        linked = tmp_path / "linked.grib2"
        linked.symlink_to(first)
        request["frames"][0]["path"] = str(linked)
    path.write_text(json.dumps(request))
    output = tmp_path / "bad"
    response = run(engine_binary, path, output)
    assert response["type"] == "error", response
    assert not output.exists()
