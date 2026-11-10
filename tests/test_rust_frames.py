"""Complete frame/core output, not just index or dominant-label parity."""
from dataclasses import replace
from datetime import date, datetime
import base64
import hashlib
import json
import os
from types import SimpleNamespace

import numpy as np
import pytest

from firesky import national_batch as batch
from firesky.models import ForecastRequest, GeoPoint
from firesky.national_point_core import validate_national_point_core
from firesky.tensor_bundle import load_tensor_bundle
from test_rust_assessment import inputs, python_reference, ec_grib_fixture
from test_rust_migration import engine_binary, rpc
from test_rust_quality import assert_object
from test_rust_diagnostics import ledger
from test_rust_profile_diagnostics import assert_diagnostics


def named_points(core):
    points = [dict(name="Named observer", lat=.25, lon=100.25, timezone_name="Asia/Shanghai",
                   elevation_source="observer", elevation_m=50.123456789)]
    if not core:
        points.append(dict(name="Second observer", lat=.5, lon=101.5, timezone_name="UTC",
                           elevation_source="observer", elevation_m=150))
    return points


def reference(request, monkeypatch):
    prepared, result = python_reference(request, monkeypatch)
    prepared = replace(prepared, model_source="ec" if request["model"] == "ecmwf-basic" else "gfs")
    points = resolved_points(prepared, request["points"])
    values = batch.build_national_frame_payloads(
        prepared, points, date.fromisoformat(request["date"]), request["phenomenon"],
        datetime.fromisoformat(request["cycle_utc"].replace("Z", "+00:00")),
        *result[:6], "cpu", include_point_core=request.get("include_point_core", False),
        point_spacing_deg=request.get("point_spacing_deg"), analysis_radius_km=request["radius_km"])
    return prepared, result, dict(results=values[0], point_core=values[1], frames=values[2])


def resolved_points(prepared, points):
    points = [dict(p) for p in points]
    for p in points:
        if p.get("elevation_source") == "model-grid":
            p["elevation_m"] = float(batch._bilinear_regular_values(
                prepared.latitudes, prepared.longitudes, prepared.grid[0, :, 24],
                np.array([p["lat"]]), np.array([p["lon"]]))[0])
    return points


def profile_bundle(values):
    return SimpleNamespace(**dict(zip(
        ["cloud_material_class_code", "liquid", "ice", "deep", "support_count",
         "uncertainty_m", "confidence"], np.moveaxis(values, -1, 0))))


def assemble_reference(prepared, points, request, values, samples, epochs, anchors,
                       sources, qualities, profiles, monkeypatch):
    # Isolate assembly from upstream GRIB/vertical-diagnostic float32 differences.
    # Those tensors are checked separately, with their existing stage tolerances.
    with monkeypatch.context() as patch:
        patch.setattr(batch, "_profile_diagnostic_bundle",
                      lambda *args: None if profiles is None else profile_bundle(profiles))
        result = batch.build_national_frame_payloads(
            prepared, points, date.fromisoformat(request["date"]), request["phenomenon"],
            datetime.fromisoformat(request["cycle_utc"].replace("Z", "+00:00")),
            values, samples, epochs, anchors, sources, qualities, "cpu",
            include_point_core=request["include_point_core"],
            point_spacing_deg=request.get("point_spacing_deg"),
            analysis_radius_km=request["radius_km"])
    return dict(results=result[0], point_core=result[1], frames=result[2])


def check_payload(got, wanted):
    assert set(got) == set(wanted)
    assert set(got["frames"]["performance"]) == set(wanted["frames"]["performance"])
    for value in got["frames"]["performance"].values():
        assert np.isfinite(value) and value >= 0
    # Wall clock/rates are measurements, not forecast contract numerics.
    got["frames"].pop("performance")
    wanted["frames"].pop("performance")
    compare(got, wanted)


def compare(got, wanted, path=""):
    if isinstance(wanted, list):
        assert isinstance(got, list) and len(got) == len(wanted), path
        for i, (a, b) in enumerate(zip(got, wanted)):
            compare(a, b, f"{path}[{i}]")
    elif isinstance(wanted, dict):
        assert isinstance(got, dict) and set(got) == set(wanted), path
        for key in wanted:
            compare(got[key], wanted[key], f"{path}.{key}")
    else:
        assert_object(got, wanted, path)


@pytest.mark.parametrize("model", ["gfs", "ecmwf-basic"])
@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
@pytest.mark.parametrize("core", [False, True])
@pytest.mark.parametrize("model_grid", [False, True])
def test_complete_stored_frames_and_core(engine_binary, inputs, tmp_path, monkeypatch, model, phenomenon, core, model_grid):
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
    path, request = inputs(model=model, phenomenon=phenomenon, points=named_points(core))
    if model_grid:
        for p in request["points"]:
            p["elevation_source"] = "model-grid"
            p.pop("elevation_m")
    request.update(include_point_core=core, point_spacing_deg=.25)
    path.write_text(json.dumps(request))
    prepared, expected, original = reference(request, monkeypatch)
    output = tmp_path / "frames"
    response = rpc(engine_binary, [dict(id="frame", method="build_stored_frames_to_files",
        params=dict(path=str(path), output_dir=str(output)))],
        env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))[0]
    assert response["type"] == "result", response
    bundle = load_tensor_bundle(output / "manifest.json", kind="regional-frames-v1", source_path=path)
    entry = bundle.manifest["metadata"]["frame_payload"]
    data = (output / entry["file"]).read_bytes()
    assert len(data) == entry["identity"]["bytes"]
    assert hashlib.sha256(data).hexdigest() == entry["identity"]["sha256"]
    got = json.loads(data)
    profiles = batch._profile_diagnostic_bundle(
        prepared, expected[1], batch._horizontal_stencil(prepared, request["points"]))
    assert_diagnostics(bundle.arrays["profile_diagnostics"], np.stack(profiles, axis=-1))
    np.testing.assert_allclose(bundle.arrays["columns"], prepared.grid, rtol=4e-6, atol=5e-5)
    wanted = assemble_reference(
        replace(prepared, grid=bundle.arrays["columns"]), resolved_points(prepared, request["points"]), request,
        bundle.arrays["assessment"], bundle.arrays["samples"],
        bundle.arrays["evaluation_epoch"], bundle.arrays["anchor_epoch"],
        expected[4], expected[5], bundle.arrays["profile_diagnostics"], monkeypatch)
    # Quantized raw-chain bytes must also match the independent original pipeline.
    assert got["frames"]["frames"] == original["frames"]["frames"]
    check_payload(got, wanted)
    np.testing.assert_allclose(bundle.arrays["assessment"], expected[0], rtol=4e-5, atol=5e-4)
    assert len(bundle.arrays) == 16
    assert entry["point_core_validator_ported"] is True
    assert entry["point_core_validated"] is False
    assert bundle.manifest["metadata"]["public_forecast_complete"] is False
    if core:
        p = resolved_points(prepared, request["points"])[0]
        req = ForecastRequest(target_date=date.fromisoformat(request["date"]), timezone_name=p["timezone_name"],
            point=GeoPoint(p["lat"], p["lon"], p["elevation_m"]), name=p["name"], phenomenon=phenomenon,
            model_source="ec" if model == "ecmwf-basic" else "gfs", radius_km=request["radius_km"],
            gfs_cycle_override=datetime.fromisoformat(request["cycle_utc"].replace("Z","+00:00")))
        validate_national_point_core(got["point_core"], req)


@pytest.mark.parametrize("fault", ["name", "two_cores", "spacing", "radius"])
def test_bad_frame_requests_do_not_publish(engine_binary, inputs, tmp_path, fault):
    path, request = inputs(points=named_points(False))
    if fault == "name": request["points"][0].pop("name")
    elif fault == "two_cores": request["include_point_core"] = True
    elif fault == "spacing": request["point_spacing_deg"] = 0
    elif fault == "radius": request["radius_km"] = 0
    path.write_text(json.dumps(request))
    output = tmp_path / "invalid-frame"
    bad, hello = rpc(engine_binary, [
        dict(id="bad", method="build_stored_frames_to_files", params=dict(path=str(path),output_dir=str(output))),
        dict(id="hello", method="hello")])
    assert bad["type"] == "error"
    assert hello["type"] == "result"
    assert not output.exists()


def inline_fixture(case, model, phenomenon, core):
    n = 1 if core else 3
    rng = np.random.default_rng(917)
    values, samples, _ = ledger(n * 20)
    values = values.reshape(n, 20, 82)
    samples = samples.reshape(n, 20, 9)
    values[..., 47] = np.tile([1, 2, 3, 0], 5)
    values[..., 70:73] = [4500, 8000, 125]
    samples[..., 0] = np.linspace(-12, 4, 20)
    samples[..., 1] = 280
    samples[..., 2] = np.tile([0, 1], 10)
    samples[..., 3] = 1
    samples[..., 4] = np.linspace(0, 1, 20)
    columns = rng.uniform(0, 1, (2, 6, 36)).astype(np.float32)
    profiles = np.zeros((n, 20, 7), np.float32)
    profiles[..., 0] = np.tile([1, 2, 3, 8], 5)
    profiles[..., 1:3] = rng.uniform(0, 1e-4, (n, 20, 2))
    profiles[..., 3] = rng.uniform(0, 1, (n, 20))
    profiles[..., 4:7] = [3, 1000, .6875]
    if case == "partial_invalid":
        values[:, ::3, 0] = 0
        values[:, 1::4, 5] = np.float32(.049999997)
    elif case == "no_burn":
        values[..., 44] = 0
    elif case == "no_family":
        values[..., 47] = np.tile([0, 4, -1, 260], 5)
    elif case == "no_profile":
        profiles = None
        columns[..., 3] = .7
        columns[0, 0, :8] = 0
    elif case == "blocked":
        values[..., 60] = .95
        values[..., 44] = .01
        values[..., 43] = .8
    elif case == "ties":
        values[:] = values[0, 0]
        values[..., 47] = 1
        values[..., 60] = .1
        values[..., 43:47] = [.8, .7, 1.7, 1.5]
        values[..., 62] = 1.6
        values[..., 5] = 1
        samples[..., 5] = .12
    elif case == "family_wrapping":
        values[..., 47] = np.tile([257, 258.5, 259, -255], 5)
    points = [dict(name=f"Observer {i}", lat=.25 + .25*i, lon=100.125 + .5*i,
                   timezone_name=["Asia/Shanghai", "America/New_York", "UTC"][i],
                   elevation_m=50.123456789 + i) for i in range(n)]
    anchor = datetime.fromisoformat("2026-11-09T09:45:01.123456+00:00").timestamp()
    anchors = np.array([anchor + i * 60 for i in range(n)])
    offsets = np.array(batch.national_event_offsets_minutes(phenomenon))
    epochs = anchors[:, None] + offsets[None] * 60
    prepared = batch.NationalBatchPrepared(
        grid=columns, hours=np.array([9, 12]), rows=2, cols=3, lat0=0, lon0=100,
        spacing=1, latitudes=np.repeat([0, 1], 3), longitudes=np.tile([100, 101, 102], 2),
        cache_paths=[], model_source=model,
        layer_profiles=None if profiles is None else np.zeros((2, 6, 1, 24), np.float32))
    sources = ["frozen-source"] * n
    qualities = ["cams", "CAMS", "unavailable"][:n]
    request = dict(date="2026-11-09", cycle_utc="2026-11-09T00:00:00Z",
                   phenomenon=phenomenon, include_point_core=core, radius_km=200)
    params = dict(
        points=points, times=[dict(evaluation=e.tolist(), anchor=float(a)) for e, a in zip(epochs, anchors)],
        values=values.tolist(), samples=samples.tolist(), columns=columns.tolist(),
        profiles=None if profiles is None else profiles.tolist(),
        aerosol_sources=sources, aerosol_qualities=qualities, rows=2, cols=3, origin=[0, 100],
        spacing=1, date=request["date"], cycle_utc=request["cycle_utc"], phenomenon=phenomenon,
        model_source=model, analysis_radius_km=200, include_point_core=core)
    return params, prepared, request, (points, values, samples, epochs, anchors, sources, qualities, profiles)


@pytest.mark.parametrize("case", [
    "normal", "partial_invalid", "no_burn", "no_family", "no_profile", "blocked", "ties", "family_wrapping",
])
@pytest.mark.parametrize("model,phenomenon,core", [
    ("gfs", "dawn", True), ("ec", "dusk", True), ("gfs", "dusk", False), ("ec", "dawn", False),
])
def test_inline_complete_frames(engine_binary, monkeypatch, case, model, phenomenon, core):
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
    params, prepared, request, args = inline_fixture(case, model, phenomenon, core)
    wanted = assemble_reference(prepared, args[0], request, *args[1:], monkeypatch)
    response = rpc(engine_binary, [dict(id="frames", method="assemble_observer_frames", params=params)])[0]
    assert response["type"] == "result", response
    got = response["result"]
    check_payload(got, wanted)
    if case == "ties":
        assert got["results"][0]["best_time_local"] == wanted["results"][0]["best_time_local"]
        assert got["results"][0]["best_time_local"] is not None


@pytest.mark.parametrize("valid_count", range(1, 21))
def test_mean_and_earliest_best_for_every_valid_count(engine_binary, monkeypatch, valid_count):
    monkeypatch.setenv("FIRESKY_EMPIRICAL_BACKEND", "python")
    params, prepared, request, args = inline_fixture("normal", "gfs", "dusk", True)
    args[1][:, valid_count:, 0] = 0
    params["values"] = args[1].tolist()
    wanted = assemble_reference(prepared, args[0], request, *args[1:], monkeypatch)
    response = rpc(engine_binary, [dict(id="mean", method="assemble_observer_frames", params=params)])[0]
    assert response["type"] == "result", response
    check_payload(response["result"], wanted)


@pytest.mark.parametrize("encoding,scale", [("u8", 1), ("u16", .001), ("u16", .01), ("i16", 10)])
def test_quantization_ties_neighbors_and_saturation(engine_binary, encoding, scale):
    ties = np.array([.5, 1.5, 2.5, 127.5, 254.5, 32766.5, 65534.5], np.float32)
    ties *= np.float32(1/255 if encoding == "u8" else scale)
    values = np.concatenate([
        ties, np.nextafter(ties, np.float32(-np.inf)), np.nextafter(ties, np.float32(np.inf)),
        -ties, np.array([0, -0., -1e30, 1e30, np.nan], np.float32)])
    clean = np.nan_to_num(values, nan=0)
    if encoding == "u8":
        expected = np.rint(np.clip(clean, 0, 1) * np.float32(255)).astype(np.uint8)
    elif encoding == "u16":
        expected = np.clip(np.rint(np.maximum(clean, 0) / np.float32(scale)), 0, 65535).astype("<u2")
    else:
        expected = np.clip(np.rint(clean / np.float32(scale)), -32768, 32767).astype("<i2")
    response = rpc(engine_binary, [dict(id="codec", method="quantize_frame_fields", params=dict(
        values=[None if np.isnan(v) else float(v) for v in values], encoding=encoding, scale=scale))])[0]
    assert response["type"] == "result", response
    assert base64.b64decode(response["result"]["value"], validate=True) == expected.tobytes()


@pytest.mark.parametrize("fault", ["all_invalid", "offset", "time_zone", "shape", "profile_shape",
                                  "two_cores", "radius", "unknown"])
def test_inline_invalid_requests_preserve_engine(engine_binary, fault):
    params = inline_fixture("normal", "gfs", "dusk", False)[0]
    if fault == "all_invalid":
        for row in params["values"][1]: row[0] = 0
    elif fault == "offset": params["times"][0]["evaluation"][0] += 1
    elif fault == "time_zone": params["points"][0]["timezone_name"] = "No/Zone"
    elif fault == "shape": params["values"][0][0].pop()
    elif fault == "profile_shape": params["profiles"][0].pop()
    elif fault == "two_cores": params["include_point_core"] = True
    elif fault == "radius": params["analysis_radius_km"] = 0
    elif fault == "unknown": params["python_fallback"] = True
    bad, hello = rpc(engine_binary, [dict(id="bad", method="assemble_observer_frames", params=params),
                                   dict(id="hello", method="hello")])
    assert bad["type"] == "error", bad
    assert hello["type"] == "result"
