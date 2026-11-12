"""EC-specific report and independent event/raw/CAMS orchestration contracts."""
from datetime import datetime, timedelta, timezone
import copy
import json
import os
from pathlib import Path

import numpy as np
import pytest

from firesky import ecmwf_point as reference, geometry, national_batch as batch
from firesky.models import AerosolReading, ForecastReport, ForecastRequest, GeoPoint
from firesky.forecast_characteristics import cloud_evolution_diagnosis, score_curve_window
from firesky.tensor_bundle import load_tensor_bundle
from test_rust_point_core import core_case
from test_rust_ecmwf_data import ec_server, config, CYCLE
from test_rust_migration import engine_binary, rpc, ec_grib_fixture
from test_rust_assessment import inputs
from test_rust_frames import compare, named_points
from test_rust_cams import write_cams


@pytest.fixture(autouse=True)
def reference_backends(monkeypatch):
    for name in ("ECMWF_DATA", "ECMWF_DECODE", "ASTRONOMY", "CLOUD", "EMPIRICAL", "CAMS"):
        monkeypatch.setenv(f"FIRESKY_{name}_BACKEND", "python")
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")


def call(binary, method, params):
    return rpc(binary, [dict(id="ec", method=method, params=params)],
               env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))[0]


def report_reference(req, core, hours, samples, *, return_report=False):
    """Execute the owning EC step/quality/diagnostic functions without rescoring."""
    quality = reference._native_quality_from_point_core(
        core, batch.national_event_offsets_minutes(req.phenomenon))
    anchor = datetime.fromisoformat(core["anchor_local"])
    cycle = datetime.fromisoformat(core["cycle_time_utc"])
    epochs = [anchor.timestamp()+s["offset_minutes"]*60 for s in core["steps"]]
    steps = [reference._native_step_assessment(req, cycle, t, sample, q, hours)
             for t, sample, q in zip(epochs, samples, quality["steps"])]
    best_index = quality["best_index"]
    best = steps[best_index] if best_index is not None else None
    characteristics = reference._step_characteristics(steps, quality)
    duration = round(quality["quality_components"]["duration_minutes"])
    summary = [
        "ECMWF IFS basic 是独立云模式数值预测，不读取 GFS 或实况缓存。",
        f"EC cycle {cycle:%Y-%m-%d %H:%MZ}，评估 {len(steps)} 个事件时刻。",
        "当前 public 合同使用 TCC + PL RH 云柱代理；HCC/MCC/LCC 和 model-level 云水/云冰不可用，结果属于 EC-basic proxy。",
        "EC surface z 按 geopotential m2/s2 / 9.80665 转为地形米；原始 z 和 native 网格保留在输入缓存/张量中。",
        "每个事件评估时刻都由 shared native scorer 使用对应 CAMS aerext532 11 层垂直 profile 的 3 小时 bracket；未找到完整 CAMS profile 会直接拒绝质量发布。",
        "quality_index 来自 native forward-field scorer；本路径未调用 legacy assess_timestep。",
        "confidence_score=0.42 仅表示输入覆盖诊断，不是统计置信度。",
        *(best.reasons if best else ["EC IFS 时间窗没有足够云目标。"]),
    ]
    report = ForecastReport(
        observer_name=req.name, phenomenon=req.phenomenon, model_source="ec",
        target_date=req.target_date, timezone_name=req.timezone_name,
        anchor_local=geometry.solar_event_time_local(req.target_date, req.point, req.timezone_name, req.phenomenon),
        cycle_time_utc=cycle, model_cycles={"ec": cycle},
        aerosol=AerosolReading(core["aerosol"]["aod550"], core["aerosol"]["source"],
                              "cams", observer_lat=req.point.lat, observer_lon=req.point.lon),
        overall_score=core["overall_score"], burn_score=core["peak_quality_index"]/2.5,
        intensity_score=best.intensity_score if best else 0.,
        extent_score=best.extent_score if best else 0.,
        vividness_score=best.vividness_score if best else 0.,
        duration_minutes=duration, potential_duration_minutes=duration, confidence_score=.42,
        best_time_local=best.valid_time_local if best else None, summary=summary, steps=steps,
        model_weights={"ec": 1.}, model_weight_samples={"ec": len(steps)},
        model_component_scores={"ec": core["overall_score"]},
        compute_backend_requested=req.compute_backend, compute_backend="cpu",
        selected_regime=best.selected_regime if best else "unknown",
        scene_scores=dict(best.scene_scores) if best else {},
        local_sky_score=best.local_sky_score if best else 0.,
        remote_sky_score=best.remote_sky_score if best else 0.,
        convective_risk=best.convective_risk if best else 0.,
        color_diagnosis=dict(best.color_diagnosis) if best else {},
        cloud_evolution=cloud_evolution_diagnosis(characteristics, best_index=best_index),
        timing_window=score_curve_window(characteristics, regime=best.selected_regime if best else "unknown"),
    )
    return (report if return_report else report.to_dict()), quality, reference._dominant_burnable_from_point_core(core, quality, "ec")


def source_samples(req, core):
    cycle = datetime.fromisoformat(core["cycle_time_utc"])
    anchor = datetime.fromisoformat(core["anchor_local"])
    times = [anchor+timedelta(minutes=q["offset_minutes"]) for q in core["steps"]]
    hours = list(reference.required_ecmwf_forecast_hours(cycle, times))
    samples = np.zeros((20, 9), dtype=np.float32)
    for i, (q, time) in enumerate(zip(core["steps"], times)):
        a, b, w = reference.ecmwf_source_bracket(cycle, time)
        samples[i, :7] = [q["solar_elevation_deg"], q["solar_azimuth_deg"],
                         hours.index(a), hours.index(b), w, .12, q["valid"]]
    return hours, samples


@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
@pytest.mark.parametrize("case", ["normal", "partial_invalid", "no_burn", "no_family",
                                 "no_profile", "blocked", "ties", "family_wrapping"])
def test_complete_ec_report_quality_and_dominant(engine_binary, core_case, phenomenon, case):
    req, request, core = core_case("ec", phenomenon, case)
    hours, samples = source_samples(req, core)
    response = call(engine_binary, "materialize_ecmwf_point",
                    dict(request=request, core=core, source_hours=hours, samples=samples.tolist()))
    assert response["type"] == "result", response
    actual = response["result"]
    report, quality, dominant = report_reference(req, core, hours, samples)
    compare(actual["report"], report)
    compare(actual["native_quality"], quality)
    compare(actual["dominant_burnable"], dominant)
    from firesky.web_server import web_report_payload
    report, _, _ = report_reference(req, core, hours, samples, return_report=True)
    compare(actual["web_report_payload"], web_report_payload(report, req.point.elevation_m, quality_payload=quality))
    assert actual["confidence_semantics"] == reference.ECMWF_IFS_BASIC_CONFIDENCE_SEMANTICS


@pytest.mark.parametrize("fault", ["gfs", "nowcast", "count", "hours", "index", "fractional",
                                 "weight", "bracket", "core", "backend"])
def test_ec_report_rejects_wrong_inputs(engine_binary, core_case, fault):
    req, request, core = core_case("ec")
    hours, samples = source_samples(req, core)
    params = dict(request=request, core=core, source_hours=hours, samples=samples.tolist())
    if fault == "gfs": request["model_source"] = "gfs"
    elif fault == "nowcast": request["forecast_mode"] = "nowcast"
    elif fault == "count": params["samples"].pop()
    elif fault == "hours": params["source_hours"].reverse()
    elif fault == "index": params["samples"][0][2] = 99
    elif fault == "fractional": params["samples"][0][2] = .5
    elif fault == "weight": params["samples"][0][4] = .99
    elif fault == "bracket": params["samples"][0][2:4] = [len(hours)-1]*2
    elif fault == "core": core["quality_index"] += .1
    elif fault == "backend": request["compute_backend"] = "gpu"
    assert call(engine_binary, "materialize_ecmwf_point", params)["type"] == "error"


def recipe(tmp_path, endpoint=None, phenomenon="dusk", *, location=None):
    point = dict(name="EC observer", lat=.25, lon=100.25, elevation_m=50.,
                 elevation_source="observer", timezone_name="Asia/Shanghai")
    if location:
        point.update(location)
    return dict(date="2026-11-09" if phenomenon == "dusk" else "2026-11-10",
                phenomenon=phenomenon, point=point, radius_km=200., cycle_utc=CYCLE.isoformat(),
                ec=config(tmp_path, endpoint), cams_cache_dir=str(tmp_path/"cams"),
                allow_ec_download=endpoint is not None)


def request_object(request):
    p = request["point"]
    return ForecastRequest(
        target_date=datetime.fromisoformat(request["date"]).date(), phenomenon=request["phenomenon"],
        point=GeoPoint(p["lat"], p["lon"], p["elevation_m"]), timezone_name=p["timezone_name"],
        name=p["name"], model_source="ec", radius_km=request["radius_km"],
        compute_backend=request.get("compute_backend", "auto"))


def plan(binary, tmp_path, request, *, forecast=False, output=None):
    path = tmp_path/"ec-plan.json"
    path.write_text(json.dumps(request, allow_nan=False))
    params = dict(path=str(path))
    if forecast:
        params["output_dir"] = str(output or tmp_path/"output")
    response = call(binary, "forecast_cached_ecmwf_point_to_files" if forecast else "plan_ecmwf_inputs", params)
    return path, response


@pytest.mark.parametrize("location", [
    dict(lat=31.2, lon=121.5, timezone_name="Asia/Shanghai"),
    dict(lat=40.7, lon=-74., timezone_name="America/New_York"),
    dict(lat=-33.9, lon=151.2, timezone_name="Australia/Sydney"),
    dict(lat=51.5, lon=-.1, timezone_name="Europe/London"),
])
@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
@pytest.mark.parametrize("radius", [50., 200., 1200.])
def test_ec_event_plan_matches_actual_solar_hour_and_domain_functions(
        engine_binary, tmp_path, location, phenomenon, radius):
    request = recipe(tmp_path, phenomenon=phenomenon, location=location)
    request["radius_km"] = radius
    # An earlier explicit cycle covers both hemispheres and all local dates.
    request["cycle_utc"] = "2026-11-08T00:00:00Z"
    _, response = plan(engine_binary, tmp_path, request)
    assert response["type"] == "result", response
    actual = response["result"]
    req = request_object(request)
    evaluations = reference.ecmwf_point_evaluation_times(req)
    expected_hours = reference.required_ecmwf_forecast_hours(
        datetime.fromisoformat(request["cycle_utc"]), evaluations)
    domain = reference._ecmwf_point_domain_geometry(radius)
    compare(actual["bbox"], list(geometry.bounding_box(req.point, domain[2])))
    assert actual["forecast_hours"] == list(expected_hours)
    assert [datetime.fromisoformat(v) for v in actual["evaluation_times_utc"]] == list(evaluations)
    assert actual["candidate_radius_km"] == domain[0]
    assert actual["source_domain_radius_km"] == domain[2]
    assert actual["missing_hours"] == list(expected_hours)
    assert not actual["inputs_located"]
    assert actual["cams_area"] is None


def seed_cams(request, planned):
    from firesky.aerosol import cams_area_key
    area = planned["cams_area"]
    key = cams_area_key(area)
    root = Path(request["cams_cache_dir"])
    root.mkdir(parents=True, exist_ok=True)
    for time in map(datetime.fromisoformat, planned["cams_valid_times"]):
        hour = int((time-CYCLE).total_seconds()/3600)
        path = root/f"cams-optics-v2-{CYCLE:%Y-%m-%d-%H%M}-{hour:03}-{key}.grib"
        write_cams(path, hour=hour)


@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
def test_one_request_resolves_downloads_cams_and_scores_ec_without_python(
        engine_binary, tmp_path, ec_server, phenomenon):
    endpoint, state = ec_server
    request = recipe(tmp_path, endpoint, phenomenon)
    request["compute_backend"] = "cpu"
    _, response = plan(engine_binary, tmp_path, request)
    assert response["type"] == "result", response
    initial = response["result"]
    from firesky.aerosol import cams_profile_bracket_times
    p = request["point"]
    _, raw_samples, _, anchors = batch.prepare_solar_samples(
        [p], datetime.fromisoformat(request["date"]).date(), phenomenon,
        CYCLE, np.asarray(initial["forecast_hours"], dtype=np.float32))
    valid = raw_samples[0, :, 6] >= .5
    indices = np.unique(raw_samples[0, valid, 2:4]).astype(int)
    optical_hours = [initial["forecast_hours"][i] for i in indices]
    expected_cams = {t for h in optical_hours for t in cams_profile_bracket_times(CYCLE+timedelta(hours=h))}
    expected_cams.update(cams_profile_bracket_times(datetime.fromtimestamp(anchors[0], timezone.utc)))
    assert initial["cams_model_forecast_hours"] == optical_hours
    assert [datetime.fromisoformat(t) for t in initial["cams_valid_times"]] == sorted(expected_cams)
    if phenomenon == "dusk":
        assert len(optical_hours) < len(initial["forecast_hours"])
    n = len(initial["forecast_hours"])
    assert state.calls["head"] == 0 and state.calls["index"] == n+1
    assert initial["model_cache_hits_validated"] and not initial["inputs_located"]
    seed_cams(request, initial)
    request["allow_ec_download"] = False
    before = dict(state.calls)
    path, response = plan(engine_binary, tmp_path, request, forecast=True)
    assert response["type"] == "result", response
    assert dict(state.calls) == before
    bundle = load_tensor_bundle(response["result"]["manifest_path"], kind="point-forecast-v1", source_path=path)
    root = Path(response["result"]["manifest_path"]).parent
    assert len(bundle.arrays) == 16
    metadata = bundle.manifest["metadata"]
    assert metadata["input_plan"]["contract"] == "rust-ecmwf-input-plan-v1"
    point = json.loads((root/metadata["ecmwf_point_report"]["file"]).read_text())
    frames = json.loads((root/metadata["frame_payload"]["file"]).read_text())
    req = request_object(request)
    hours = initial["forecast_hours"]
    samples = bundle.arrays["samples"][0]
    expected_report, quality, dominant = report_reference(req, frames["point_core"], hours, samples)
    compare(point["report"], expected_report)
    compare(point["native_quality"], quality)
    compare(point["dominant_burnable"], dominant)
    assert point["report"]["confidence_score"] == .42
    assert all(s["selected_column_diagnostic_source"].startswith("ec-")
               for s in point["native_quality"]["steps"])
    # Exercise the real independent application workflow from the same raw
    # files, including strict derived caching and actual cached CAMS selection.
    adapter = reference.EcmwfIfsAdapter(cache_dir=request["ec"]["cache_dir"], source="ecmwf")
    expected = reference.run_ecmwf_basic_point_forecast(
        req, adapter=adapter, cycle_override=CYCLE,
        cache_dir=request["ec"]["cache_dir"], cams_cache_dir=request["cams_cache_dir"])
    expected_quality = copy.deepcopy(expected.native_quality)
    for key in ("ground_area_burnable_amount_mean", "ground_area_burnable_load_mean"):
        np.testing.assert_allclose(point["native_quality"]["quality_components"][key],
                                   expected_quality["quality_components"][key], rtol=3e-6, atol=3e-8)
        expected_quality["quality_components"][key] = point["native_quality"]["quality_components"][key]
    for actual_step, wanted_step in zip(point["native_quality"]["steps"], expected_quality["steps"]):
        for key in ("ground_area_burnable_cloud_field_amount", "ground_area_burnable_cloud_load"):
            np.testing.assert_allclose(actual_step[key], wanted_step[key], rtol=3e-6, atol=3e-8)
            wanted_step[key] = actual_step[key]
    compare(point["native_quality"], expected_quality)
    compare(point["report"], expected.report.to_dict())
    compare(point["dominant_burnable"], expected.provenance["dominant_burnable"])
    from firesky.web_server import web_report_payload
    compare(point["web_report_payload"], web_report_payload(
        expected.report, req.point.elevation_m, quality_payload=expected_quality))
    assert dict(state.calls) == before


@pytest.mark.parametrize("fault", ["name", "lat", "zone", "elevation", "model-grid", "radius",
                                 "cams-cache", "source", "resolution", "date", "hour", "horizon",
                                 "unknown", "backend", "ec-cache"])
def test_invalid_ec_plan_does_not_touch_network(engine_binary, tmp_path, ec_server, fault):
    endpoint, state = ec_server
    request = recipe(tmp_path, endpoint)
    if fault == "name": request["point"]["name"] = ""
    elif fault == "lat": request["point"]["lat"] = 91.
    elif fault == "zone": request["point"]["timezone_name"] = "invalid"
    elif fault == "elevation": request["point"]["elevation_m"] = 9999.
    elif fault == "model-grid": request["point"].update(elevation_source="model-grid", elevation_m=None)
    elif fault == "radius": request["radius_km"] = 0.
    elif fault == "cams-cache": request["cams_cache_dir"] = ""
    elif fault == "source": request["ec"]["source"] = "invalid"
    elif fault == "resolution": request["ec"]["resolution"] = "9km"
    elif fault == "date": request["date"] = "2026-02-30"
    elif fault == "hour": request["cycle_utc"] = "2026-11-09T00:01:00Z"
    elif fault == "horizon": request["cycle_utc"] = "2026-10-01T00:00:00Z"
    elif fault == "unknown": request["gfs"] = {}
    elif fault == "backend": request["compute_backend"] = "gpu"
    elif fault == "ec-cache": request["ec"]["cache_dir"] = ""
    _, response = plan(engine_binary, tmp_path, request)
    assert response["type"] == "error", response
    assert not state.calls


@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
def test_late_ec_event_uses_six_hour_sources(engine_binary, tmp_path, phenomenon):
    request = recipe(tmp_path, phenomenon=phenomenon)
    request["cycle_utc"] = (CYCLE-timedelta(days=6)).isoformat()
    _, response = plan(engine_binary, tmp_path, request)
    assert response["type"] == "result", response
    evaluations = reference.ecmwf_point_evaluation_times(request_object(request))
    hours = reference.required_ecmwf_forecast_hours(datetime.fromisoformat(request["cycle_utc"]), evaluations)
    assert response["result"]["forecast_hours"] == list(hours)
    assert min(hours) >= 144 and all(h % 6 == 0 for h in hours)


def test_ec_domain_ending_at_positive_180_is_not_a_wrap(engine_binary, tmp_path):
    request = recipe(tmp_path)
    request["point"].update(lat=0., lon=180.-800./111.)
    request["cycle_utc"] = "2026-11-08T00:00:00Z"
    _, response = plan(engine_binary, tmp_path, request)
    assert response["type"] == "result", response
    req = request_object(request)
    compare(response["result"]["bbox"], list(geometry.bounding_box(req.point, 800.)))
    assert response["result"]["bbox"][1] == 180.


@pytest.mark.parametrize("fallback", [False, True])
def test_automatic_ec_cycle_matches_publication_aware_application(
        engine_binary, tmp_path, ec_server, monkeypatch, fallback):
    endpoint, state = ec_server
    request = recipe(tmp_path, endpoint)
    request["allow_ec_download"] = False
    request.pop("cycle_utc")
    request["now_utc"] = "2026-11-09T23:00:00Z"
    req = request_object(request)
    anchor = geometry.solar_event_time_local(
        req.target_date, req.point, req.timezone_name, req.phenomenon).astimezone(timezone.utc)
    evaluations = reference.ecmwf_point_evaluation_times(req)
    if fallback:
        initial_hours = reference.required_ecmwf_forecast_hours(CYCLE, evaluations)
        state.heads[(CYCLE, max(initial_hours))] = 404
    monkeypatch.setattr(reference, "ecmwf_artifact_available",
                        lambda source, cycle, hour, **kw: state.heads.get((cycle, hour), 200) == 200)
    expected = reference.choose_ecmwf_ifs_cycle_for_event(
        anchor, evaluations, cache_dir=request["ec"]["cache_dir"],
        available_utc=datetime.fromisoformat(request["now_utc"]))
    _, response = plan(engine_binary, tmp_path, request)
    assert response["type"] == "result", response
    assert datetime.fromisoformat(response["result"]["cycle_utc"]) == expected
    assert state.calls["head"] == 1+int(fallback) and state.calls["range"] == 0


@pytest.mark.parametrize("status", [403, 500])
def test_automatic_ec_probe_failure_does_not_masquerade_as_missing(
        engine_binary, tmp_path, ec_server, status):
    endpoint, state = ec_server
    request = recipe(tmp_path, endpoint)
    request.pop("cycle_utc")
    request["now_utc"] = "2026-11-09T23:00:00Z"
    evaluations = reference.ecmwf_point_evaluation_times(request_object(request))
    hour = max(reference.required_ecmwf_forecast_hours(CYCLE, evaluations))
    state.heads[(CYCLE, hour)] = status
    _, response = plan(engine_binary, tmp_path, request)
    assert response["type"] == "error", response
    assert state.calls["head"] == 1 and state.calls["range"] == 0


@pytest.mark.parametrize("delta", [1.9, 2.1])
def test_ec_source_terrain_tolerance_matches_existing_application(
        engine_binary, tmp_path, inputs, delta):
    import eccodes as codes
    path, request = inputs(model="ecmwf-basic", points=named_points(True))
    modified = Path(request["frames"][-1]["path"])
    messages = []
    with modified.open("rb") as stream:
        while (handle := codes.codes_grib_new_from_file(stream)) is not None:
            try:
                if codes.codes_get(handle, "shortName") == "z":
                    values = codes.codes_get_values(handle)
                    codes.codes_set_values(handle, values+delta*9.80665)
                messages.append(codes.codes_get_message(handle))
            finally:
                codes.codes_release(handle)
    modified.write_bytes(b"".join(messages))
    response = call(engine_binary, "forecast_stored_point_to_files",
                    dict(path=str(path), output_dir=str(tmp_path/"terrain-output")))
    assert (response["type"] == "result") == (delta <= 2.), response
    if delta > 2.:
        assert "orography" in response["error"]["message"]
        assert not (tmp_path/"terrain-output").exists()
