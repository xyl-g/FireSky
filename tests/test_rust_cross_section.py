"""Full cross-section parity, explicit profile layout, and corrupted-input rejection."""
import hashlib
import json
import os
from pathlib import Path
from datetime import date, datetime

import numpy as np
import pytest

from firesky import national_batch as batch
from firesky.tensor_bundle import load_tensor_bundle
from test_rust_assessment import inputs, ec_grib_fixture
from test_rust_frames import compare as strict_compare, named_points
from test_rust_migration import engine_binary, rpc


def identity(path):
    data = path.read_bytes()
    return dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def save_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def call(binary, params):
    return rpc(binary, [dict(id="cross", method="build_stored_cross_section", params=params)],
               env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))[0]


def compare(actual, expected, path=""):
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys(), path
        for key in expected:
            compare(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert len(actual) == len(expected), path
        for index, (a, b) in enumerate(zip(actual, expected)):
            compare(a, b, f"{path}[{index}]")
    elif expected is not None and path.endswith((
        ".geometric_horizon_margin_m", ".observer_terrain_clearance_m",
    )):
        # Python's compensated math.hypot vs libm: one ULP at Earth radius.
        assert actual == pytest.approx(expected, rel=3e-11, abs=2e-9), path
    else:
        strict_compare(actual, expected, path)


@pytest.fixture(autouse=True)
def python_backends(monkeypatch):
    monkeypatch.setenv("FIRESKY_ASTRONOMY_BACKEND", "python")
    monkeypatch.setenv("FIRESKY_CLOUD_BACKEND", "python")


def fixture(tmp_path, *, phenomenon="dusk", case="normal", model="ec", rows=7, cols=9, spacing=3.):
    levels = np.array([200, 250, 300, 400, 500, 600, 700, 750, 800, 850, 925], np.float32)
    rng = np.random.default_rng(211)
    profiles = rng.uniform(0, 1, (4, rows*cols, len(levels), 24)).astype(np.float32)
    profiles[..., 0] *= 100
    profiles[..., 1] = 44330*(1-(levels/1013.25)**.190263)
    profiles[..., 1] += rng.uniform(-500, 500, profiles.shape[:-1]).astype(np.float32)
    profiles[..., 2] = 220+profiles[..., 2]*70
    profiles[..., 3] -= .5
    profiles[..., 4:6] = profiles[..., 4:6]*40-20
    profiles[..., 6:11] *= 100
    profiles[..., 11:13] *= 1e-4
    profiles[..., 14] = levels
    profiles[..., 15:17] *= 100
    profiles[..., 17:23] = [6800, 10800, 2300, 6800, 200, 2300]
    profiles[..., 23] = rng.uniform(0, 300, (4, rows*cols, 1))
    elevation = 50.123456789
    if case == "missing":
        profiles[0, ::2, ::2, :23] = np.nan
        profiles[1, 1::2, 1::2, :23] = np.nan
        profiles[..., 6] = np.nan
        profiles[..., 11:13] = np.nan
    elif case == "no_cloud":
        profiles[..., 6] = 0
        profiles[..., 13] = 0
    elif case in ("ice", "water", "unknown"):
        profiles[..., 11] = 0 if case in ("ice", "unknown") else 3e-5
        profiles[..., 12] = 0 if case in ("water", "unknown") else 3e-5
    elif case == "high_station":
        elevation = 4200.
    elif case == "below_sea":
        elevation = -300.
    elif case == "ridge":
        profiles[..., 23] = 6000.
    elif case in ("adaptive", "continuous", "isolated"):
        profiles[..., 6] = 0.
        for level, amount in {200: .5, 250: .6, 300: .6, 400: .65, 500: .8}.items():
            profiles[..., levels.tolist().index(level), 6] = 100*amount
        if case == "continuous":
            profiles[..., levels.tolist().index(500), 6] = 20.
        elif case == "isolated":
            profiles[..., levels < 400, 6] = 0.
    elif case == "missing_upper_pair":
        levels[1] = 260.
        profiles[..., 14] = levels
    elif case == "single_level":
        profiles = profiles[:, :, 2:3].copy()
        levels = levels[2:3].copy()
    elif case == "missing_terrain":
        profiles[..., 23] = np.nan
    elif case == "partial_terrain":
        profiles[0, :, :, 23] = np.nan
    elif case == "all_missing":
        profiles[..., :23] = np.nan
    hours = [0, 24, 48, 72]
    entries = []
    for index, hour in enumerate(hours):
        root = tmp_path/f"profiles_{index:03}"
        root.mkdir()
        array_path = root/"profiles.npy"
        np.save(array_path, profiles[index])
        manifest = dict(
            schema="firesky.tensor-bundle.v1", producer="firesky-engine/test",
            kind="regional-profiles-v1", source=dict(bytes=0, sha256="0"*64),
            metadata=dict(frame_index=index, hour=hour, pressure_levels=levels.tolist(), profile_fields=24),
            arrays=dict(profiles=dict(file="profiles.npy", dtype="<f4",
                                     shape=list(profiles[index].shape), **identity(array_path))),
        )
        save_json(root/"manifest.json", manifest)
        entries.append(dict(manifest=f"profiles_{index:03}/manifest.json",
                            identity=identity(root/"manifest.json")))
    geometry = dict(rows=rows, cols=cols, lat0=-10., lon0=90., spacing=spacing)
    source = dict(contract="rust-cross-section-source-v1", model_source=model,
                  terrain_source="EC IFS surface geopotential" if model == "ec" else "GFS surface orography",
                  target_date="2026-11-09" if phenomenon == "dusk" else "2026-11-10",
                  phenomenon=phenomenon, cycle_utc="2026-11-09T00:00:00Z", hours=hours,
                  pressure_levels=levels.tolist(), geometry=geometry, profile_bundles=entries)
    source_path = tmp_path/"cross_section_source.json"
    save_json(source_path, source)
    combined = tmp_path/"python_profiles.npy"
    np.save(combined, profiles)
    reference = dict(source, **geometry, layer_profile_path=str(combined))
    point = dict(lat=.25, lon=100.25, elevation_m=elevation, timezone_name="Asia/Shanghai")
    params = dict(source_path=str(source_path), source_identity=identity(source_path), point=point,
                  offset_minutes=0, max_distance_km=1000, distance_step_km=25)
    return source, reference, params


@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
@pytest.mark.parametrize("case", [
    "normal", "missing", "no_cloud", "ice", "water", "unknown", "high_station",
    "below_sea", "ridge", "adaptive", "continuous", "isolated", "missing_upper_pair",
    "single_level", "partial_terrain", "all_missing",
])
def test_complete_cross_section(engine_binary, tmp_path, phenomenon, case):
    _, reference, params = fixture(tmp_path, phenomenon=phenomenon, case=case)
    offsets = [-75, -35, 0, 20] if phenomenon == "dawn" else [-20, 0, 35, 75]
    for offset in offsets:
        params["offset_minutes"] = offset
        expected = batch.build_national_cross_section(reference, params["point"], offset)
        response = call(engine_binary, params)
        assert response["type"] == "result", response
        compare(response["result"], expected)


@pytest.mark.parametrize("distance,step", [(0, 25), (1000, 37.7), (9.95, .3), (150, 25)])
def test_distance_sampling_and_timezone(engine_binary, tmp_path, distance, step):
    _, reference, params = fixture(tmp_path, model="gfs")
    params.update(max_distance_km=distance, distance_step_km=step)
    params["point"]["timezone_name"] = "America/New_York"
    expected = batch.build_national_cross_section(reference, params["point"], 0,
                                                  max_distance_km=distance, distance_step_km=step)
    response = call(engine_binary, params)
    assert response["type"] == "result", response
    compare(response["result"], expected)


@pytest.mark.parametrize("fault", [
    "source_hash", "source_symlink", "child_symlink", "array_symlink", "array_hash",
    "missing_array", "wrong_shape", "wrong_dtype", "frame_hour", "frame_index", "frame_levels",
    "path_escape", "absolute_child", "contract", "hours", "levels", "frame_count",
    "grid_shape", "spacing", "timezone", "elevation", "point_bool", "offset", "offset_bool",
    "radius", "step", "step_count", "unknown", "missing_terrain", "duplicate_array",
])
def test_bad_cross_section_does_not_break_rpc(engine_binary, tmp_path, fault):
    source, _, params = fixture(tmp_path, case="missing_terrain" if fault == "missing_terrain" else "normal")
    source_path = Path(params["source_path"])
    frame_path = tmp_path/source["profile_bundles"][0]["manifest"]
    frame = json.loads(frame_path.read_text())
    array = frame_path.parent/"profiles.npy"
    if fault == "source_hash": params["source_identity"]["sha256"] = "0"*64
    elif fault == "source_symlink":
        target = tmp_path/"linked.json"
        target.symlink_to(source_path)
        params["source_path"] = str(target)
    elif fault == "child_symlink":
        frame_path.parent.rename(tmp_path/"actual")
        frame_path.parent.symlink_to(tmp_path/"actual", target_is_directory=True)
    elif fault == "array_symlink":
        array.rename(array.with_suffix(".actual"))
        array.symlink_to(array.with_suffix(".actual"))
    elif fault == "array_hash":
        content = bytearray(array.read_bytes())
        content[-1] ^= 1
        array.write_bytes(content)
    elif fault == "missing_array": array.unlink()
    elif fault == "wrong_shape": frame["arrays"]["profiles"]["shape"][0] += 1
    elif fault == "wrong_dtype": frame["arrays"]["profiles"]["dtype"] = "<f8"
    elif fault == "frame_hour": frame["metadata"]["hour"] = 1
    elif fault == "frame_index": frame["metadata"]["frame_index"] = 1
    elif fault == "frame_levels": frame["metadata"]["pressure_levels"].reverse()
    elif fault == "path_escape": source["profile_bundles"][0]["manifest"] = "../manifest.json"
    elif fault == "absolute_child": source["profile_bundles"][0]["manifest"] = str(frame_path)
    elif fault == "contract": source["contract"] = "python-mmap"
    elif fault == "hours": source["hours"].reverse()
    elif fault == "levels": source["pressure_levels"].reverse()
    elif fault == "frame_count": source["profile_bundles"].pop()
    elif fault == "grid_shape": source["geometry"]["cols"] += 1
    elif fault == "spacing": source["geometry"]["spacing"] = 0
    elif fault == "timezone": params["point"]["timezone_name"] = "invalid"
    elif fault == "elevation": params["point"]["elevation_m"] = 9001
    elif fault == "point_bool": params["point"]["elevation_m"] = True
    elif fault == "offset": params["offset_minutes"] = .5
    elif fault == "offset_bool": params["offset_minutes"] = True
    elif fault == "radius": params["max_distance_km"] = 1001
    elif fault == "step": params["distance_step_km"] = 0
    elif fault == "step_count": params.update(max_distance_km=1000, distance_step_km=.1)
    elif fault == "unknown": params["python_fallback"] = True
    if fault in ("wrong_shape", "wrong_dtype", "frame_hour", "frame_index", "frame_levels", "duplicate_array"):
        save_json(frame_path, frame)
        if fault == "duplicate_array":
            text = frame_path.read_text()
            text = text.replace('"arrays": {', '"arrays": {"profiles": '+json.dumps(frame["arrays"]["profiles"])+",")
            frame_path.write_text(text)
        source["profile_bundles"][0]["identity"] = identity(frame_path)
    save_json(source_path, source)
    if fault != "source_hash":
        params["source_identity"] = identity(source_path)
    bad, hello = rpc(engine_binary, [dict(id="bad", method="build_stored_cross_section", params=params),
                                    dict(id="hello", method="hello")],
                     env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))
    assert bad["type"] == "error", bad
    assert hello["type"] == "result"


def test_only_selected_two_frames_are_loaded(engine_binary, tmp_path):
    source, reference, params = fixture(tmp_path)
    for index in [2, 3]:
        (tmp_path/source["profile_bundles"][index]["manifest"]).unlink()
    response = call(engine_binary, params)
    assert response["type"] == "result", response
    compare(response["result"], batch.build_national_cross_section(reference, params["point"], 0))


def test_exact_hour_needs_only_one_frame(engine_binary, tmp_path):
    source, reference, params = fixture(tmp_path)
    _, _, epochs, _ = batch.prepare_solar_samples(
        [params["point"]], date.fromisoformat(source["target_date"]), source["phenomenon"],
        datetime.fromisoformat(source["cycle_utc"]), np.array(source["hours"]),
    )
    cycle_epoch = datetime.fromisoformat(source["cycle_utc"]).timestamp()
    source["hours"][1] = float((epochs[0, 4]-cycle_epoch)/3600)
    reference["hours"] = source["hours"]
    frame_path = tmp_path/source["profile_bundles"][1]["manifest"]
    frame = json.loads(frame_path.read_text())
    frame["metadata"]["hour"] = source["hours"][1]
    save_json(frame_path, frame)
    source["profile_bundles"][1]["identity"] = identity(frame_path)
    for index in [0, 2, 3]:
        (tmp_path/source["profile_bundles"][index]["manifest"]).unlink()
    source_path = Path(params["source_path"])
    save_json(source_path, source)
    params["source_identity"] = identity(source_path)
    response = call(engine_binary, params)
    assert response["type"] == "result", response
    compare(response["result"], batch.build_national_cross_section(reference, params["point"], 0))


@pytest.mark.parametrize("model", ["gfs", "ecmwf-basic"])
def test_assessment_publishes_usable_cross_section_source(engine_binary, inputs, tmp_path, model):
    path, request = inputs(model=model, points=named_points(True))
    output = tmp_path/"stored"
    response = rpc(engine_binary, [dict(
        id="stored", method="forecast_stored_point_to_files",
        params=dict(path=str(path), output_dir=str(output)),
    )], env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))[0]
    assert response["type"] == "result", response
    bundle = load_tensor_bundle(response["result"]["manifest_path"])
    descriptor = bundle.manifest["metadata"]["cross_section_source"]
    source_path = output/descriptor["file"]
    assert descriptor["identity"] == identity(source_path)
    assert descriptor["combined_python_mmap"] is False
    source = json.loads(source_path.read_text())
    profiles = np.stack([load_tensor_bundle(output/f["manifest"]).arrays["profiles"]
                         for f in source["profile_bundles"]])
    combined = tmp_path/"combined.npy"
    np.save(combined, profiles)
    reference = dict(source, **source["geometry"], layer_profile_path=str(combined))
    point = {k: v for k, v in request["points"][0].items()
             if k in ("lat", "lon", "elevation_m", "timezone_name")}
    params = dict(source_path=str(source_path), source_identity=descriptor["identity"], point=point,
                  offset_minutes=0)
    actual = call(engine_binary, params)
    assert actual["type"] == "result", actual
    expected = batch.build_national_cross_section(reference, point, 0)
    compare(actual["result"], expected)
