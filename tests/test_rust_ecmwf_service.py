"""Native EC provenance/export and integrity-bound profile response contracts."""
from dataclasses import replace
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from firesky import html_report, national_batch as batch, ecmwf_point as reference
from firesky.tensor_bundle import load_tensor_bundle
from test_rust_cross_section import identity, fixture as cross_fixture, compare as cross_compare
from test_rust_ecmwf_point import (
    call, recipe, request_object, seed_cams, plan, report_reference, source_samples,
    core_case, ec_server, ec_grib_fixture, CYCLE,
)
from test_rust_frames import compare
from test_rust_migration import engine_binary, rpc

GENERATED = "2026-11-09 20:30 CST"


@pytest.fixture(autouse=True)
def python_backends(monkeypatch):
    for name in ("ECMWF_DATA", "ECMWF_DECODE", "ASTRONOMY", "CLOUD", "EMPIRICAL", "CAMS"):
        monkeypatch.setenv(f"FIRESKY_{name}_BACKEND", "python")
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")


def python_html(report, monkeypatch, generated=GENERATED):
    stamp = SimpleNamespace(astimezone=lambda: SimpleNamespace(strftime=lambda _: generated))
    monkeypatch.setattr(html_report, "datetime", SimpleNamespace(now=lambda: stamp))
    return html_report.render_html_report(report)


def example_report(core_case, phenomenon="dusk", case="normal"):
    req, _, core = core_case("ec", phenomenon, case)
    hours, samples = source_samples(req, core)
    return report_reference(req, core, hours, samples, return_report=True)[0]


@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
@pytest.mark.parametrize("case", ["normal", "no_burn", "partial_invalid", "ties", "no_profile"])
def test_complete_html_exactly_matches_python(engine_binary, core_case, monkeypatch, phenomenon, case):
    report = example_report(core_case, phenomenon, case)
    result = call(engine_binary, "render_html_report",
                  dict(report=report.to_dict(), generated_local=GENERATED))
    assert result["type"] == "result", result
    assert result["result"]["contract"] == "rust-html-report-v1"
    assert result["result"]["html"] == python_html(report, monkeypatch)


@pytest.mark.parametrize("model", ["gfs", "satellite", "<unknown&'\">"])
@pytest.mark.parametrize("step_count", [0, 1, 2, 20])
def test_html_empty_single_unknown_model_and_escaping(
        engine_binary, core_case, monkeypatch, model, step_count):
    report = example_report(core_case)
    report = replace(report, observer_name="湖畔 <script>\"&'测试", model_source=model,
                     summary=["<script>alert('x')</script>", '"&中文'],
                     steps=report.steps[:step_count],
                     best_time_local=report.steps[0].valid_time_local+timedelta(minutes=2, seconds=30))
    if report.steps:
        candidate = report.steps[0].top_candidate
        if candidate:
            candidate = replace(candidate, cloud_type='云<>&"\'')
        report.steps[0] = replace(report.steps[0], reasons=['<reason>&"\''], top_candidate=candidate)
    generated = "<generated>&\"'"
    result = call(engine_binary, "render_html_report",
                  dict(report=report.to_dict(), generated_local=generated))
    assert result["type"] == "result", result
    assert result["result"]["html"] == python_html(report, monkeypatch, generated)
    assert "<script>" not in result["result"]["html"]


@pytest.mark.parametrize("score", [0., .2999, .30, .4999, .5, .6999, .70, 1.])
def test_html_nowcast_and_score_thresholds(engine_binary, core_case, monkeypatch, score):
    report = example_report(core_case)
    # Rendering consumes only these nowcast fields; serialization of the rest
    # of NowcastAdjustment is outside this renderer's contract.
    nowcast = dict(cloud_blanket_score=.6, cloud_texture_score=.77, horizon_clearance_score=.5)
    report = replace(report, forecast_mode="nowcast", burn_score=score)
    payload = report.to_dict()
    payload["nowcast"] = nowcast
    report = replace(report, nowcast=SimpleNamespace(**nowcast))
    result = call(engine_binary, "render_html_report", dict(report=payload, generated_local=GENERATED))
    assert result["type"] == "result", result
    assert result["result"]["html"] == python_html(report, monkeypatch)


@pytest.mark.parametrize("zone_name", ["America/New_York", "Europe/London", "Australia/Sydney", "UTC"])
def test_html_local_timezone_and_nullable_duration(engine_binary, core_case, monkeypatch, zone_name):
    report = example_report(core_case)
    zone = ZoneInfo(zone_name)
    times = [
        datetime(2026, 11, 1, 0, 55, tzinfo=zone),
        datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0),
        datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1),
        datetime(2026, 11, 1, 2, 5, tzinfo=zone),
    ]
    report = replace(report, timezone_name=zone_name, anchor_local=times[0], best_time_local=times[2],
                     potential_duration_minutes=None,
                     steps=[replace(s, valid_time_local=t) for s, t in zip(report.steps, times)])
    result = call(engine_binary, "render_html_report",
                  dict(report=report.to_dict(), generated_local=GENERATED))
    assert result["type"] == "result", result
    assert result["result"]["html"] == python_html(report, monkeypatch)


@pytest.mark.parametrize("fault", [
    "zone", "date", "phenomenon", "count", "steps", "time", "score", "reasons",
    "candidate", "duration", "generated", "unknown",
])
def test_invalid_html_keeps_rpc_alive(engine_binary, core_case, fault):
    payload = example_report(core_case).to_dict()
    params = dict(report=payload, generated_local=GENERATED)
    if fault == "zone": payload["timezone_name"] = "invalid"
    elif fault == "date": payload["target_date"] = "2026-02-30"
    elif fault == "phenomenon": payload["phenomenon"] = "unknown"
    elif fault == "count": payload["steps"][0]["burning_cloud_count"] = -1
    elif fault == "steps": payload["steps"] *= 205
    elif fault == "time": payload["steps"][0]["valid_time_local"] = "2026-11-09"
    elif fault == "score": payload["intensity_score"] = None
    elif fault == "reasons": payload["summary"] = [None]
    elif fault == "candidate": payload["steps"][0]["top_candidate"] = {}
    elif fault == "duration": payload["potential_duration_minutes"] = "30"
    elif fault == "generated": params["generated_local"] = "bad\nlabel"
    elif fault == "unknown": params["output"] = "/tmp/should-not-write"
    responses = rpc(engine_binary, [
        dict(id="bad", method="render_html_report", params=params), dict(id="hello", method="hello"),
    ])
    assert responses[0]["type"] == "error", responses[0]
    assert responses[1]["type"] == "result"


@pytest.mark.parametrize("count", [None, 3, 3.0])
@pytest.mark.parametrize("duration", [None, 30, 30.0])
def test_html_optional_counts_and_integral_float_duration(
    engine_binary, core_case, count, duration,
):
    payload = example_report(core_case).to_dict()
    payload["steps"][0]["burning_cloud_count"] = count
    payload["potential_duration_minutes"] = duration
    result = call(engine_binary, "render_html_report",
                  dict(report=payload, generated_local=GENERATED))
    assert result["type"] == "result", result
    assert ("未提供" in result["result"]["html"]) == (count is None)


def service_forecast(binary, root, endpoint, phenomenon):
    request = recipe(root, endpoint, phenomenon)
    request["point"]["name"] = 'EC 湖畔 <>&"\''
    path, response = plan(binary, root, request)
    assert response["type"] == "result", response
    initial = response["result"]
    seed_cams(request, initial)
    request["allow_ec_download"] = False
    path.write_text(json.dumps(request))
    response = call(binary, "forecast_ecmwf_service_to_files",
                    dict(path=str(path), output_dir=str(root/"service")))
    assert response["type"] == "result", response
    manifest_path = Path(response["result"]["manifest_path"])
    bundle = load_tensor_bundle(manifest_path, kind="ecmwf-service-forecast-v1", source_path=path)
    return request, initial, manifest_path, bundle, json.loads((manifest_path.parent/"ecmwf_service.json").read_text())


@pytest.mark.parametrize("phenomenon", ["dawn", "dusk"])
def test_ec_service_provenance_html_cross_section_without_python(
        engine_binary, tmp_path, ec_server, monkeypatch, phenomenon):
    endpoint, state = ec_server
    request, initial, manifest_path, bundle, service = service_forecast(engine_binary, tmp_path, endpoint, phenomenon)
    before = dict(state.calls)
    root = manifest_path.parent
    metadata = bundle.manifest["metadata"]
    assert len(bundle.arrays) == 16
    assert metadata["ecmwf_service"]["identity"] == identity(root/"ecmwf_service.json")
    assert metadata["ecmwf_service"]["provenance_complete"]
    assert not service["public_forecast_complete"]
    assert not service["production_http_complete"]
    assert not service["response_cache_complete"]
    assert service["html_artifact"]["identity"] == identity(root/"report.html")
    req = request_object(request)
    adapter = reference.EcmwfIfsAdapter(cache_dir=request["ec"]["cache_dir"], source="ecmwf")
    expected = reference.run_ecmwf_basic_point_forecast(
        req, adapter=adapter, cycle_override=CYCLE, cache_dir=request["ec"]["cache_dir"],
        cams_cache_dir=request["cams_cache_dir"])
    provenance = service["provenance"]
    assert expected.provenance.keys() <= provenance.keys()
    for key, value in expected.provenance.items():
        if key not in ("layer_profile_path", "timing", "dominant_burnable"):
            compare(provenance[key], value, key)
    assert provenance["layer_profile_path"] is None
    assert provenance["native_profile_source"]["file"] == "cross_section_source.json"
    assert provenance["native_profile_source"]["identity"] == identity(root/"cross_section_source.json")
    assert len(provenance["cams_source_descriptions"]) == 1
    assert set(provenance["cams_consumed_valid_times_utc"]) == {
        datetime.fromisoformat(t).isoformat().replace("+00:00", "Z") for t in initial["cams_valid_times"]}
    assert all(v >= 0 for v in provenance["timing"].values())
    for source in provenance["consumed_source_identities"]:
        assert source["identity"] == identity(Path(source["path"]))
    # Compare renderer bytes from the actual native report, independently of
    # tolerated upstream GRIB/scoring float differences.
    frames = json.loads((root/"forecast.json").read_text())
    point = json.loads((root/"ecmwf_point.json").read_text())
    assert provenance["dominant_burnable"] == point["dominant_burnable"]
    python_report, _, _ = report_reference(
        req, frames["point_core"], initial["forecast_hours"], bundle.arrays["samples"][0], return_report=True)
    actual_html = (root/"report.html").read_text()
    import re
    generated = re.search(r"生成时间 ([^<]*)</span>", actual_html).group(1)
    assert actual_html == python_html(python_report, monkeypatch, generated)
    assert service["report"]["html_report_path"] == str(root/"report.html")
    response = service["response"]
    from firesky.web_server import web_report_payload
    expected_web = web_report_payload(
        replace(python_report, html_report_path=str(root/"report.html")), req.point.elevation_m,
        quality_payload=service["native_quality"])
    overrides = {
        "source_roles", "point_refinement", "dominant_burnable_pressure_family", "dominant_burnable_cloud_phase",
        "dominant_burnable_cloud_base_asl_mean_m", "dominant_burnable_cloud_top_asl_mean_m",
        "dominant_burnable_target_terrain_asl_mean_m", "dominant_burnable_cloud_base_agl_mean_m",
        "dominant_burnable_cloud_top_agl_mean_m", "dominant_burnable_cloud_base_above_observer_mean_m",
        "dominant_burnable_cloud_top_above_observer_mean_m", "dominant_burnable_cloud_genus_diagnosis",
        "cloud_genus_proxy", "cloud_phase_proxy", "diagnostic_source", "geometry_window", "boundary_uncertainty",
    }
    for key, value in expected_web.items():
        if key not in overrides:
            compare(response[key], value, key)
    assert response["html_report_url"] is None
    assert response["response_cache"]["status"] == "not-integrated"
    assert response["ecmwf_provenance"] == provenance
    for suffix, key in [
        ("pressure_family", "pressure_family"), ("cloud_phase", "cloud_phase"),
        ("cloud_base_asl_mean_m", "cloud_base_asl_mean_m"),
        ("cloud_top_asl_mean_m", "cloud_top_asl_mean_m"),
        ("target_terrain_asl_mean_m", "target_terrain_asl_mean_m"),
        ("cloud_base_agl_mean_m", "cloud_base_agl_mean_m"),
        ("cloud_top_agl_mean_m", "cloud_top_agl_mean_m"),
        ("cloud_base_above_observer_mean_m", "cloud_base_above_observer_mean_m"),
        ("cloud_top_above_observer_mean_m", "cloud_top_above_observer_mean_m"),
        ("cloud_genus_diagnosis", "genus_diagnosis"),
    ]:
        assert response[f"dominant_burnable_{suffix}"] == provenance["dominant_burnable"][key]
    for key, value in response["data_provenance"].items():
        if key in provenance:
            assert value == provenance[key]
    assert response["map_token"] is None and response["map_times"] == []
    assert response["cross_section_request"]["profile_key"] == service["profile_key"]
    # Cross-section parity from identical verified per-hour native tensors.
    source = json.loads((root/"cross_section_source.json").read_text())
    profiles = np.stack([np.load(root/v["manifest"].replace("manifest.json", "profiles.npy"))
                         for v in source["profile_bundles"]])
    combined = tmp_path/"combined.npy"
    np.save(combined, profiles)
    source.update(source["geometry"], layer_profile_path=str(combined))
    offset = 0 if python_report.best_time_local is None else (
        python_report.best_time_local-python_report.anchor_local).total_seconds()/60
    expected_cross = batch.build_national_cross_section(source, request["point"], offset)
    cross_compare(response["cross_section"], expected_cross)
    read = dict(manifest_path=str(manifest_path), manifest_identity=identity(manifest_path),
                profile_key=service["profile_key"], lat=req.point.lat, lon=req.point.lon, offset_minutes=offset)
    result = call(engine_binary, "read_ecmwf_service_cross_section", read)
    assert result["type"] == "result", result
    assert result["result"] == response["cross_section"]
    # A bundle can move without interpreting an absolute Python mmap path.
    moved = tmp_path/"relocated"
    shutil.copytree(root, moved)
    read["manifest_path"] = str(moved/"manifest.json")
    result = call(engine_binary, "read_ecmwf_service_cross_section", read)
    assert result["type"] == "result", result
    assert result["result"] == response["cross_section"]
    assert dict(state.calls) == before


@pytest.mark.parametrize("fault", [
    "manifest_hash", "manifest_symlink", "manifest_name", "manifest_kind", "service_hash", "service_symlink",
    "service_file", "service_contract", "key", "key_unicode", "key_uppercase", "key_short",
    "lat", "lon", "offset", "source_file", "source_hash", "source_symlink", "array_hash", "unknown",
])
def test_registered_profile_integrity_and_request_rejection(engine_binary, tmp_path, fault):
    _, _, cross = cross_fixture(tmp_path)
    point = dict(cross["point"], name="test")
    key = "ec-native:"+"a"*64
    service = dict(contract="rust-ecmwf-service-v1", profile_key=key, observer=point,
                   provenance=dict(native_profile_source=dict(
                       file="cross_section_source.json", identity=cross["source_identity"],
                       contract="rust-cross-section-source-v1")))
    service_path = tmp_path/"ecmwf_service.json"
    service_path.write_text(json.dumps(service))
    manifest_path = tmp_path/"manifest.json"
    manifest = dict(schema="firesky.tensor-bundle.v1", producer="firesky-engine/test",
                    kind="ecmwf-service-forecast-v1", metadata=dict(ecmwf_service=dict(
                        file="ecmwf_service.json", identity=identity(service_path), contract="rust-ecmwf-service-v1")))
    params = dict(manifest_path=str(manifest_path), profile_key=key,
                  lat=point["lat"], lon=point["lon"], offset_minutes=0)
    if fault == "manifest_kind": manifest["kind"] = "point-forecast-v1"
    elif fault == "service_hash": manifest["metadata"]["ecmwf_service"]["identity"]["sha256"] = "0"*64
    elif fault == "service_file": manifest["metadata"]["ecmwf_service"]["file"] = "../ecmwf_service.json"
    elif fault == "service_contract": service["contract"] = "unknown"
    elif fault == "key": params["profile_key"] = "ec-native:"+"b"*64
    elif fault == "key_unicode": service["profile_key"] = params["profile_key"] = "ec-native:"+"中"*22
    elif fault == "key_uppercase": service["profile_key"] = params["profile_key"] = "ec-native:"+"A"*64
    elif fault == "key_short": service["profile_key"] = params["profile_key"] = "ec-native:a"
    elif fault == "lat": params["lat"] += .01
    elif fault == "lon": params["lon"] += .01
    elif fault == "offset": params["offset_minutes"] = True
    elif fault == "unknown": params["arbitrary_file"] = "/tmp/unsupported"
    elif fault == "source_file": service["provenance"]["native_profile_source"]["file"] = "/tmp/escape"
    elif fault == "source_hash": service["provenance"]["native_profile_source"]["identity"]["sha256"] = "0"*64
    elif fault == "source_symlink":
        source = Path(cross["source_path"])
        source.rename(tmp_path/"original.json")
        source.symlink_to(tmp_path/"original.json")
    elif fault == "array_hash":
        array = tmp_path/"profiles_000/profiles.npy"
        content = bytearray(array.read_bytes())
        content[-1] ^= 1
        array.write_bytes(content)
    if fault not in ("service_hash",):
        service_path.write_text(json.dumps(service))
        manifest["metadata"]["ecmwf_service"]["identity"] = identity(service_path)
    if fault == "service_symlink":
        service_path.rename(tmp_path/"original-service.json")
        service_path.symlink_to(tmp_path/"original-service.json")
    manifest_path.write_text(json.dumps(manifest))
    params["manifest_identity"] = identity(manifest_path)
    if fault == "manifest_hash": params["manifest_identity"]["sha256"] = "0"*64
    elif fault == "manifest_symlink":
        manifest_path.rename(tmp_path/"original-manifest.json")
        manifest_path.symlink_to(tmp_path/"original-manifest.json")
    elif fault == "manifest_name":
        manifest_path.rename(tmp_path/"other.json")
        params["manifest_path"] = str(tmp_path/"other.json")
    results = rpc(engine_binary, [
        dict(id="read", method="read_ecmwf_service_cross_section", params=params), dict(id="hello", method="hello"),
    ], env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))
    assert results[0]["type"] == "error", results[0]
    assert results[1]["type"] == "result"


def test_preexisting_output_is_not_overwritten(engine_binary, tmp_path, ec_server):
    endpoint, _ = ec_server
    request = recipe(tmp_path, endpoint)
    path, response = plan(engine_binary, tmp_path, request)
    assert response["type"] == "result", response
    seed_cams(request, response["result"])
    request["allow_ec_download"] = False
    path.write_text(json.dumps(request))
    output = tmp_path/"output"
    output.mkdir()
    protected = output/"report.html"
    protected.write_text("user-owned-existing-report")
    result = call(engine_binary, "forecast_ecmwf_service_to_files",
                  dict(path=str(path), output_dir=str(output)))
    assert result["type"] == "error", result
    assert list(output.iterdir()) == [protected]
    assert protected.read_text() == "user-owned-existing-report"
