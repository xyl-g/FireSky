"""Production prefetch-planner and cache-resolved forecast differentials."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess

import numpy as np
import pytest

from firesky import aerosol, gfs, web_server as web
from firesky.tensor_bundle import load_tensor_bundle
from test_rust_assessment import write_gfs
from test_rust_cams import write_cams
from test_rust_frames import compare
from test_rust_gfs_data import metadata, http_source
from test_rust_migration import engine_binary, rpc

CYCLE = datetime(2026, 11, 9, tzinfo=timezone.utc)
BBOX = [100., 102., 1., 0.]


@pytest.fixture(autouse=True)
def python_reference(monkeypatch):
    for name in ("ASTRONOMY", "GFS_DATA", "GFS_DECODE", "CAMS", "CLOUD", "EMPIRICAL"):
        monkeypatch.setenv(f"FIRESKY_{name}_BACKEND", "python")


def recipe(tmp_path, *, phenomenon="dusk", count=1, batch_min_points=64):
    return dict(date="2026-11-10" if phenomenon == "dawn" else "2026-11-09",
                phenomenon=phenomenon, radius_km=200.,
                cycle_utc=CYCLE.isoformat(), batch_min_points=batch_min_points,
                points=[dict(name=f"Observer {i}", lat=.25, lon=100.25,
                             timezone_name="Asia/Shanghai", elevation_source="observer",
                             elevation_m=50.) for i in range(count)],
                gfs=dict(cache_dir=str(tmp_path / "gfs")),
                cams_cache_dir=str(tmp_path / "cams"), bbox=BBOX)


def call(binary, tmp_path, request, *, method="plan_gfs_inputs", output=None):
    path = tmp_path / "plan-request.json"
    path.write_text(json.dumps(request, allow_nan=False))
    params = dict(path=str(path))
    if output is not None:
        params["output_dir"] = str(output)
    return rpc(binary, [dict(id="plan", method=method, params=params)],
               env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))[0]


def planned(binary, tmp_path, request):
    response = call(binary, tmp_path, request)
    assert response["type"] == "result", response
    return response["result"]


def reference(request, monkeypatch, *, grids=None):
    """Exercise the owning production planner, not a copied hour algorithm."""
    points = [dict(p, _elevation_source=p["elevation_source"],
                   elevation_m=p.get("elevation_m", 0.)) for p in request["points"]]
    cycle = (datetime.fromisoformat(request["cycle_utc"]) if request.get("cycle_utc")
             else web._national_model_cycle(
                 dict(model_source="gfs", target_date=request["date"],
                      phenomenon=request["phenomenon"]), points,
                 now_utc=datetime.fromisoformat(request["now_utc"])))
    radius = request["radius_km"]
    bbox = request.get("bbox") or web.national_gfs_bbox(
        points, light_path_padding_km=web._national_gfs_source_radius_km(radius))
    common = dict(model_source="gfs", forecast_mode="model", satellite_source="off",
                  target_date=request["date"], phenomenon=request["phenomenon"],
                  radius_km=radius, gfs_resolution=request["gfs"].get("resolution", "0p25"),
                  _model_cycle_override=cycle.isoformat(), _model_fixed_bbox=bbox,
                  _compute_region=None)
    targets, areas = [], []

    class Gfs:
        def __init__(self, **kwargs):
            pass
        def prefetch_subset(self, cycle, hour, bbox):
            return grids[hour] if grids else Path(f"/fixture/f{hour:03}.grib2")
        def load_decoded_subset(self, cycle, hour, bbox):
            return gfs.decode_grib_grid(grids[hour]), grids[hour]
        def preload_snapshots(self, *args):
            pass

    class Cams:
        def __init__(self, **kwargs):
            pass
        def download_targets_batch(self, area, times, **kwargs):
            areas.append(area)
            targets.extend(times)
            return set(times)

    with monkeypatch.context() as patch:
        patch.setenv("FIRESKY_NATIONAL_BATCH_MIN_POINTS", str(request["batch_min_points"]))
        patch.setattr(web, "GfsNomadsClient", Gfs)
        patch.setattr(web, "CamsAdsAerosolProvider", Cams)
        web._prefetch_national_inputs(points, common, web.WebServerConfig())
    return dict(cycle=cycle, bbox=list(bbox), area=areas[0], targets=targets,
                hours=common["_model_forecast_hours"], terrain=common["_terrain_source"],
                points=points)


@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
@pytest.mark.parametrize("count,threshold", [(1, 1), (1, 64), (2, 64), (63, 64), (64, 64), (65, 64)])
@pytest.mark.parametrize("late", [False, True])
def test_full_time_policy_matches_production_prefetch(engine_binary, tmp_path, monkeypatch,
                                                      phenomenon, count, threshold, late):
    request = recipe(tmp_path, phenomenon=phenomenon, count=count, batch_min_points=threshold)
    if late:
        request["cycle_utc"] = (CYCLE - timedelta(hours=120)).isoformat()
    expected = reference(request, monkeypatch)
    got = planned(engine_binary, tmp_path, request)
    assert datetime.fromisoformat(got["cycle_utc"]) == expected["cycle"]
    assert got["forecast_hours"] == expected["hours"]
    assert got["cams_area"] == expected["area"]
    assert got["cams_area_key"] == aerosol.cams_area_key(expected["area"])
    assert [datetime.fromisoformat(v) for v in got["cams_valid_times"]] == expected["targets"]
    assert got["missing_hours"] == got["forecast_hours"]
    assert got["inputs_located"] is False and got["terrain_pending"] is False


@pytest.mark.parametrize("location", [
    (31.2, 121.5, "Asia/Shanghai"), (40.7, -74., "America/New_York"),
    (-33.9, 151.2, "Australia/Sydney"), (51.5, -.1, "Europe/London"),
])
@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
@pytest.mark.parametrize("historical", [False, True])
def test_automatic_cycle_domain_and_cams_area(engine_binary, tmp_path, monkeypatch,
                                             location, phenomenon, historical):
    request = recipe(tmp_path, phenomenon=phenomenon)
    request.pop("bbox")
    request.pop("cycle_utc")
    request["now_utc"] = "2026-11-19T09:15:27+00:00" if historical else "2026-11-09T00:15:27+00:00"
    lat, lon, tz = location
    request["points"][0].update(lat=lat, lon=lon, timezone_name=tz)
    expected = reference(request, monkeypatch)
    got = planned(engine_binary, tmp_path, request)
    assert datetime.fromisoformat(got["cycle_utc"]) == expected["cycle"]
    assert got["bbox"] == expected["bbox"] and got["cams_area"] == expected["area"]
    assert got["forecast_hours"] == expected["hours"]
    assert got["publication_verified"] is False


def seed_gfs(root, hours, *, terrain_delta=0):
    files = {}
    for hour in hours:
        path, _ = metadata(root, f"f{hour:03}.grib2", hour=hour, bbox=BBOX)
        files[hour] = write_gfs(path, hour, terrain_delta=terrain_delta)
    return files


def seed_cams(root, targets, area, *, run=CYCLE, key=None, mutation=None, folder=""):
    root = root / folder
    root.mkdir(parents=True, exist_ok=True)
    paths = []
    for target in targets:
        lead = int((target - run).total_seconds() / 3600)
        path = root / f"cams-optics-v2-{run:%Y-%m-%d-%H%M}-{lead:03}-{key or aerosol.cams_area_key(area)}.grib"
        write_cams(path, hour=int((target-CYCLE).total_seconds()/3600), mutation=mutation)
        paths.append(path)
    return paths


@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
@pytest.mark.parametrize("batch", [False, True])
def test_model_grid_terrain_corrects_plan_and_binds_prepass(engine_binary, tmp_path, monkeypatch,
                                                           phenomenon, batch):
    request = recipe(tmp_path, phenomenon=phenomenon, batch_min_points=1 if batch else 64)
    point = request["points"][0]
    point["elevation_source"] = "model-grid"
    point.pop("elevation_m")
    initial = planned(engine_binary, tmp_path, request)
    assert initial["terrain_pending"] is True and initial["inputs_located"] is False
    hours = sorted({h + delta for h in initial["forecast_hours"] for delta in range(-2, 3) if h+delta >= 0})
    files = seed_gfs(Path(request["gfs"]["cache_dir"]), hours, terrain_delta=3000)
    expected = reference(request, monkeypatch, grids=files)
    seed_cams(Path(request["cams_cache_dir"]), expected["targets"], expected["area"])
    got = planned(engine_binary, tmp_path, request)
    assert got["forecast_hours"] == expected["hours"]
    assert [datetime.fromisoformat(v) for v in got["cams_valid_times"]] == expected["targets"]
    assert got["terrain_source"]["forecast_hour"] == expected["terrain"]["model_grid_source"]["forecast_hour"]
    assert got["terrain_source"]["forecast_hour"] in got["forecast_hours"]
    assert got["model_grid_elevation_range_m"] == [expected["points"][0]["elevation_m"]]*2
    assert got["inputs_located"] is True and got["terrain_pending"] is False


@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
@pytest.mark.parametrize("model_grid", [False, True])
def test_automatically_selected_cache_inputs_match_frozen_full_point(
        engine_binary, tmp_path, monkeypatch, phenomenon, model_grid):
    request = recipe(tmp_path, phenomenon=phenomenon)
    if model_grid:
        request["points"][0]["elevation_source"] = "model-grid"
        request["points"][0].pop("elevation_m")
    initial = planned(engine_binary, tmp_path, request)
    seed_gfs(Path(request["gfs"]["cache_dir"]),
             sorted({h+d for h in initial["forecast_hours"] for d in range(-1, 2) if h+d >= 0}))
    plan = planned(engine_binary, tmp_path, request)
    seed_cams(Path(request["cams_cache_dir"]), [datetime.fromisoformat(v) for v in plan["cams_valid_times"]],
              plan["cams_area"])
    output = tmp_path / "automatic"
    response = call(engine_binary, tmp_path, request, method="forecast_cached_gfs_point_to_files", output=output)
    assert response["type"] == "result", response
    original_request = tmp_path / "plan-request.json"
    bundle = load_tensor_bundle(output/"manifest.json", kind="point-forecast-v1", source_path=original_request)
    plan_entry = bundle.manifest["metadata"]["input_plan"]
    encoded = (output / plan_entry["file"]).read_bytes()
    assert plan_entry["identity"] == dict(bytes=len(encoded), sha256=hashlib.sha256(encoded).hexdigest())
    plan = json.loads(encoded)
    frozen = dict(model="gfs", cycle_utc=plan["cycle_utc"], date=request["date"],
                  phenomenon=phenomenon, points=request["points"], frames=plan["model_files"],
                  cams=plan["cams_files"], radius_km=request["radius_km"], bbox=plan["prepared_bbox"])
    frozen_path = tmp_path / "frozen.json"
    frozen_path.write_text(json.dumps(frozen))
    frozen_out = tmp_path / "frozen-output"
    response = rpc(engine_binary, [dict(id="frozen", method="forecast_stored_point_to_files",
        params=dict(path=str(frozen_path),output_dir=str(frozen_out)))])[0]
    assert response["type"] == "result", response
    other = load_tensor_bundle(frozen_out/"manifest.json", kind="point-forecast-v1", source_path=frozen_path)
    for key in bundle.arrays:
        np.testing.assert_array_equal(bundle.arrays[key], other.arrays[key])
    compare(json.loads((output/"point_forecast.json").read_bytes()),
            json.loads((frozen_out/"point_forecast.json").read_bytes()))
    from datetime import date
    from firesky.models import ForecastRequest, GeoPoint
    from firesky.national_point_core import materialize_national_point_core
    from test_rust_frames import reference as frame_reference, resolved_points
    prepared, _, expected = frame_reference(dict(frozen, include_point_core=True), monkeypatch)
    p = resolved_points(prepared, frozen["points"])[0]
    req = ForecastRequest(target_date=date.fromisoformat(request["date"]), phenomenon=phenomenon,
        point=GeoPoint(p["lat"],p["lon"],p["elevation_m"]), timezone_name=p["timezone_name"],
        name=p["name"], model_source="gfs", radius_km=request["radius_km"], gfs_cycle_override=CYCLE)
    compare(json.loads((output/"point_forecast.json").read_bytes()),
            materialize_national_point_core(req, expected["point_core"]))
    assert bundle.manifest["metadata"]["point_forecast"]["automatic_input_selection"] is True
    assert bundle.manifest["metadata"]["point_forecast"]["public_forecast_complete"] is False
    before = (output/"manifest.json").read_bytes()
    response = call(engine_binary, tmp_path, request,
                    method="forecast_cached_gfs_point_to_files", output=output)
    assert response["type"] == "error"
    assert (output/"manifest.json").read_bytes() == before


def expand_cams_area(path):
    import eccodes as codes
    messages = []
    with path.open("rb") as source:
        while (handle := codes.codes_grib_new_from_file(source)) is not None:
            messages.append(handle)
    try:
        with path.open("wb") as output:
            for handle in messages:
                for key, value in dict(latitudeOfFirstGridPointInDegrees=3,
                    latitudeOfLastGridPointInDegrees=-2, longitudeOfFirstGridPointInDegrees=98,
                    longitudeOfLastGridPointInDegrees=104, iDirectionIncrementInDegrees=3,
                    jDirectionIncrementInDegrees=5).items():
                    codes.codes_set(handle, key, value)
                codes.codes_write(handle, output)
    finally:
        for handle in messages:
            codes.codes_release(handle)


@pytest.mark.parametrize("case", ["newer-covering", "newer-uncovered", "newer-corrupt",
                                  "exact-priority", "filename-time-mismatch", "same-tier"])
def test_cams_selection_newest_area_validation_and_ties(
        engine_binary, tmp_path, monkeypatch, case):
    request = recipe(tmp_path)
    initial = planned(engine_binary, tmp_path, request)
    targets = [datetime.fromisoformat(v) for v in initial["cams_valid_times"]]
    root = Path(request["cams_cache_dir"])
    older = seed_cams(root, targets, initial["cams_area"])
    newer = seed_cams(root, targets, initial["cams_area"],
        run=CYCLE if case in ("exact-priority", "same-tier") else CYCLE+timedelta(hours=6),
        key=None if case in ("filename-time-mismatch", "same-tier") else "covering",
        mutation="missing" if case == "newer-corrupt" else None, folder="other")
    if case in ("newer-covering", "exact-priority"):
        for path in newer:
            expand_cams_area(path)
    if case == "filename-time-mismatch":
        for path, target in zip(newer, targets):
            write_cams(path, hour=int((target-CYCLE).total_seconds()/3600)+3)
    expected = {
        target: aerosol.find_cached_cams_bracket(root, initial["cams_area"], target).left.path
        for target in targets
    }
    got = planned(engine_binary, tmp_path, request)
    actual = {datetime.fromisoformat(row["valid_time"]): Path(row["path"]) for row in got["cams_files"]}
    assert actual == expected
    if case == "newer-covering":
        assert set(actual.values()) == set(newer)
    if case in ("newer-uncovered", "newer-corrupt", "exact-priority"):
        assert set(actual.values()) == set(older)
    if case == "filename-time-mismatch":
        seed_gfs(Path(request["gfs"]["cache_dir"]), initial["forecast_hours"])
        output = tmp_path / "bad-time-output"
        response = call(engine_binary, tmp_path, request,
                        method="forecast_cached_gfs_point_to_files", output=output)
        assert response["type"] == "error"
        assert "actual valid time" in response["error"]["message"]
        assert not output.exists()


def test_cams_live_mutation_and_missing_recovery(engine_binary, tmp_path):
    request = recipe(tmp_path)
    initial = planned(engine_binary, tmp_path, request)
    targets = [datetime.fromisoformat(v) for v in initial["cams_valid_times"]]
    root = Path(request["cams_cache_dir"])
    seed_cams(root, targets, initial["cams_area"])
    latest = seed_cams(root, targets, initial["cams_area"], run=CYCLE+timedelta(hours=6), folder="latest")
    path = tmp_path / "plan-request.json"
    path.write_text(json.dumps(request))
    # Use the same long-lived process to exercise future probe-cache changes.
    process = subprocess.Popen([str(engine_binary), "serve"], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    def send():
        process.stdin.write(json.dumps(dict(id="p", method="plan_gfs_inputs", params=dict(path=str(path))))+"\n")
        process.stdin.flush()
        assert select.select([process.stdout], [], [], 10)[0]
        response = json.loads(process.stdout.readline())
        assert response["type"] == "result", response
        return response["result"]
    try:
        assert set(row["path"] for row in send()["cams_files"]) == set(map(str, latest))
        for file in latest:
            file.write_bytes(b"bad contents")
        assert all("/latest/" not in row["path"] for row in send()["cams_files"])
        for file, target in zip(latest, targets):
            write_cams(file, hour=int((target-CYCLE).total_seconds()/3600))
        assert set(row["path"] for row in send()["cams_files"]) == set(map(str, latest))
        latest[0].unlink()
        assert any("/latest/" not in row["path"] for row in send()["cams_files"])
    finally:
        process.stdin.close()
        process.wait(timeout=10)
        assert process.returncode == 0, process.stderr.read()
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize("fault", ["empty", "radius", "timezone", "lat", "observer-elevation",
                                  "model-elevation", "threshold", "dateline", "unknown",
                                  "empty-cache", "unknown-config", "nonutc-cycle"])
def test_invalid_plans_fail_before_forecast_publication(engine_binary, tmp_path, fault):
    request = recipe(tmp_path)
    if fault == "empty": request["points"] = []
    elif fault == "radius": request["radius_km"] = 1001
    elif fault == "timezone": request["points"][0]["timezone_name"] = "unknown/zone"
    elif fault == "lat": request["points"][0]["lat"] = 91
    elif fault == "observer-elevation": request["points"][0].pop("elevation_m")
    elif fault == "model-elevation": request["points"][0]["elevation_source"] = "model-grid"
    elif fault == "threshold": request["batch_min_points"] = 0
    elif fault == "dateline": request["bbox"] = [179, -179, 1, 0]
    elif fault == "unknown": request["unknown"] = True
    elif fault == "empty-cache": request["cams_cache_dir"] = ""
    elif fault == "unknown-config": request["gfs"]["password"] = "not-allowed"
    elif fault == "nonutc-cycle": request["cycle_utc"] = "2026-11-09T00:00:00+08:00"
    output = tmp_path / "bad-plan"
    response = call(engine_binary, tmp_path, request,
                    method="forecast_cached_gfs_point_to_files", output=output)
    assert response["type"] == "error", response
    assert not output.exists()


@pytest.mark.parametrize("count,threshold", [(1, 1), (1, 64)])
def test_scalar_clamp_vs_batch_negative_bracket_policy(engine_binary, tmp_path, monkeypatch, count, threshold):
    request = recipe(tmp_path, count=count, batch_min_points=threshold)
    request["cycle_utc"] = "2026-11-10T00:00:00+00:00"
    if threshold == 1:
        with pytest.raises(RuntimeError, match="周期晚"):
            reference(request, monkeypatch)
        assert call(engine_binary, tmp_path, request)["type"] == "error"
    else:
        expected = reference(request, monkeypatch)
        got = planned(engine_binary, tmp_path, request)
        assert got["forecast_hours"] == expected["hours"] == [0]


def test_missing_inputs_preserve_existing_output_and_relative_cache_roots(engine_binary, tmp_path):
    request = recipe(tmp_path)
    request["gfs"]["cache_dir"] = "relative-gfs"
    request["cams_cache_dir"] = "relative-cams"
    got = planned(engine_binary, tmp_path, request)
    assert got["inputs_located"] is False
    assert (tmp_path/"relative-gfs/.gfs-metadata-index-v1/index.json").is_file()
    output = tmp_path / "keep-output"
    output.mkdir()
    keep = output / "unowned"
    keep.write_text("untouched")
    response = call(engine_binary, tmp_path, request,
                    method="forecast_cached_gfs_point_to_files", output=output)
    assert response["type"] == "error" and "incomplete" in response["error"]["message"]
    assert list(output.iterdir()) == [keep] and keep.read_text() == "untouched"


@pytest.mark.parametrize("target_date", ["2026-03-08", "2026-11-01"])
def test_scalar_dst_gap_and_fold_wall_clock_rules(engine_binary, tmp_path, monkeypatch, target_date):
    request = recipe(tmp_path, phenomenon="dawn")
    request["date"] = target_date
    request["cycle_utc"] = f"{target_date}T00:00:00+00:00"
    request["points"][0].update(lat=35., lon=-15. if target_date.endswith("03-08") else 0.,
                                timezone_name="America/New_York")
    expected = reference(request, monkeypatch)
    got = planned(engine_binary, tmp_path, request)
    assert got["forecast_hours"] == expected["hours"]
    assert [datetime.fromisoformat(v) for v in got["cams_valid_times"]] == expected["targets"]


@pytest.mark.parametrize("batch", [False, True])
def test_scalar_full_sun_failure_is_not_replaced_with_regional_polar_policy(
        engine_binary, tmp_path, monkeypatch, batch):
    request = recipe(tmp_path, batch_min_points=1 if batch else 64)
    request["date"] = "2026-06-21"
    request["cycle_utc"] = "2026-06-20T00:00:00+00:00"
    request["points"][0].update(lat=61., lon=100., timezone_name="UTC")
    if not batch:
        with pytest.raises(ValueError):
            reference(request, monkeypatch)
        assert call(engine_binary, tmp_path, request)["type"] == "error"
    else:
        expected = reference(request, monkeypatch)
        got = planned(engine_binary, tmp_path, request)
        assert got["forecast_hours"] == expected["hours"]


def test_explicit_download_failure_is_not_hidden_by_incomplete_plan(
        engine_binary, tmp_path, monkeypatch, http_source):
    proxy, state = http_source
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, proxy)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "")
    request = recipe(tmp_path)
    request["allow_gfs_download"] = True
    request["gfs"].update(download_attempts=1, timeout_seconds=2)
    response = call(engine_binary, tmp_path, request)
    assert response["type"] == "error"
    assert "GFS download failed after 1 attempts" in response["error"]["message"]
    assert state["counts"] == {"CONNECT": 1}
    assert not list(tmp_path.rglob("*.grib2"))
    assert not list(tmp_path.rglob(".firesky-tmp-*"))
