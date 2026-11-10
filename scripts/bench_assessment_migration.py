#!/usr/bin/env python3
"""Stored raw-input assessment benchmark, not the full public forecast API.

Python uses its reference orchestrator and the existing CPU ray tracer.
Rust includes request parsing, raw decode, profile output and durable bundles.
"""
import argparse
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from test_rust_assessment import python_reference
from test_rust_quality import (
    assessment_quality_rows, assert_object, python_regional_quality, quality_from_bundle,
)
from test_rust_diagnostics import diagnostics_from_bundle
from test_rust_profile_diagnostics import assert_diagnostics
from test_rust_frames import check_payload
from firesky import national_batch as batch
from firesky.models import ForecastRequest, GeoPoint
from firesky.national_point_core import materialize_national_point_core
from firesky.tensor_bundle import load_tensor_bundle


def pinned(model):
    if model == "gfs":
        directory = ROOT / ".cache/gfs/2026-11-07/18Z/0p25"
        frames = {}
        for path in directory.glob("*.grib2.json"):
            metadata = json.loads(path.read_text())
            if metadata["bbox"] == [99.25,141.25,48.5,12.0] and metadata["forecast_hour"] in (14,15,16):
                frames[metadata["forecast_hour"]] = str(path.with_suffix(""))
        cycle, day, cams_hours, bbox = "2026-11-07T18:00:00Z", "2026-11-08", [42,45,48], None
    else:
        directory = ROOT / ".cache/ecmwf/2026-11-07/12Z"
        frames = {h: str(directory / f"ifs-basic-v2-2026110712-f{h:03}.grib2") for h in [42,45,48]}
        cycle, day, cams_hours, bbox = "2026-11-07T12:00:00Z", "2026-11-09", [66,69,72], [118,125,35,28]
    if len(frames) != 3:
        raise RuntimeError("pinned local model inputs are unavailable")
    cams_cycle = datetime(2026,11,6,12,tzinfo=timezone.utc)
    cams = [dict(path=str(ROOT / f".cache/cams/2026-11-06/12Z/china/cams-optics-v2-2026-11-06-1200-{h:03}-0af35cdc1e57.grib"),
                 valid_time=(cams_cycle+timedelta(hours=h)).isoformat()) for h in cams_hours]
    request = dict(model=model, cycle_utc=cycle, date=day, phenomenon="dusk", radius_km=200,
                   frames=[dict(hour=h,path=p) for h,p in sorted(frames.items())], cams=cams, bbox=bbox,
                   points=[dict(lat=31.23,lon=121.47,timezone_name="Asia/Shanghai",elevation_source="model-grid")])
    for record in request["frames"]+request["cams"]:
        if not Path(record["path"]).is_file():
            raise RuntimeError(f"pinned input unavailable: {record['path']}")
    return request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pinned-model", choices=["gfs","ecmwf-basic"], default="gfs")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--engine", type=Path, default=ROOT / "engine/target/release/firesky-engine")
    parser.add_argument("--quality", action="store_true", help="include full event/instant empirical ledgers")
    parser.add_argument("--diagnostics", action="store_true", help="include complete event-quality diagnostics")
    parser.add_argument("--profile-diagnostics", action="store_true", help="include selected-column material/geometry diagnostics")
    parser.add_argument("--frames", action="store_true", help="include complete compact frames and generated single-point core (not materialized forecast)")
    parser.add_argument("--point-forecast", action="store_true", help="validate and materialize one complete stored-input point")
    parser.add_argument("--core-fixture", type=Path, help="export reference request/core for warm component benchmarks (requires --point-forecast)")
    args = parser.parse_args()
    if args.core_fixture and not args.point_forecast:
        parser.error("--core-fixture requires --point-forecast")
    args.frames = args.frames or args.point_forecast
    args.profile_diagnostics = args.profile_diagnostics or args.frames
    args.diagnostics = args.diagnostics or args.profile_diagnostics
    args.quality = args.quality or args.diagnostics
    if not 1 <= args.trials <= 7:
        parser.error("trials must be 1..7")
    request = json.loads(args.request.read_text()) if args.request else pinned(args.pinned_model)
    if args.point_forecast and len(request["points"]) != 1:
        parser.error("--point-forecast requires exactly one point")
    if args.request:
        for record in request["frames"]+request["cams"]:
            record["path"] = str((args.request.parent / record["path"]).resolve())
    if args.frames:
        request["include_point_core"] = len(request["points"]) == 1
        for i, point in enumerate(request["points"]):
            point.setdefault("name", f"Observer {i}")
    for flag in ("ASTRONOMY","CLOUD","CAMS"):
        os.environ[f"FIRESKY_{flag}_BACKEND"] = "python"
    os.environ["FIRESKY_GFS_DECODE_BACKEND"] = "python"
    os.environ["FIRESKY_ECMWF_DECODE_BACKEND"] = "python"
    os.environ["FIRESKY_EMPIRICAL_BACKEND"] = "python"
    python_times, rust_times, phases = [], [], []
    # Warm both workflows, then retain sequential measured trials. Only Rust
    # writes persistent checked bundles, so this is not an equal-I/O comparison.
    with tempfile.TemporaryDirectory(prefix="firesky-assessment-") as temporary:
        root = Path(temporary)
        path = root / "request.json"
        path.write_text(json.dumps(request))
        with pytest.MonkeyPatch.context() as monkeypatch:
            for index in range(args.trials+1):
                start = time.perf_counter_ns()
                prepared, expected = python_reference(request, monkeypatch)
                if args.frames:
                    frame_points = [dict(p) for p in request["points"]]
                    for point in frame_points:
                        if point["elevation_source"] == "model-grid":
                            point["elevation_m"] = float(batch._bilinear_regular_values(
                                prepared.latitudes, prepared.longitudes, prepared.grid[0, :, 24],
                                np.array([point["lat"]]), np.array([point["lon"]]))[0])
                    parts = batch.build_national_frame_payloads(
                        replace(prepared, model_source="gfs" if request["model"] == "gfs" else "ec"),
                        frame_points, date.fromisoformat(request["date"]), request["phenomenon"],
                        datetime.fromisoformat(request["cycle_utc"].replace("Z", "+00:00")),
                        *expected[:6], "cpu", include_point_core=request["include_point_core"],
                        point_spacing_deg=request.get("point_spacing_deg"),
                        analysis_radius_km=request["radius_km"])
                    expected_payload = dict(results=parts[0], point_core=parts[1], frames=parts[2])
                    if args.point_forecast:
                        p = frame_points[0]
                        point_request = ForecastRequest(
                            target_date=date.fromisoformat(request["date"]), timezone_name=p["timezone_name"],
                            point=GeoPoint(p["lat"], p["lon"], p["elevation_m"]), name=p["name"],
                            phenomenon=request["phenomenon"], model_source="gfs" if request["model"] == "gfs" else "ec",
                            radius_km=request["radius_km"],
                            gfs_cycle_override=datetime.fromisoformat(request["cycle_utc"].replace("Z", "+00:00")))
                        expected_point = materialize_national_point_core(point_request, parts[1])
                    # The builder already computes these scores/diagnostics.
                    # Recompute verification-only projections outside the timer.
                    python_ms = (time.perf_counter_ns()-start)/1e6
                    if args.core_fixture and index == 0:
                        fixture_request = asdict(point_request)
                        fixture_request["target_date"] = point_request.target_date.isoformat()
                        fixture_request["gfs_cycle_override"] = point_request.gfs_cycle_override.isoformat()
                        # A requested benchmark artifact, outside measured work.
                        args.core_fixture.write_text(json.dumps(dict(request=fixture_request, core=parts[1])))
                if args.quality:
                    quality_rows = assessment_quality_rows(expected[0], expected[1], request["phenomenon"])
                    expected_events, expected_instant = python_regional_quality(quality_rows, request["radius_km"])
                    if args.diagnostics:
                        expected_diagnostics = []
                        for point, event in enumerate(expected_events):
                            value = batch._fire_cloud_quality(expected[0][point], expected[1][point],
                                quality_rows[point, :, 8], np.flatnonzero(expected[0][point, :, 0] >= .5),
                                precomputed_score=event, analysis_radius_km=request["radius_km"])
                            if request["model"] != "gfs" and value["components"]:
                                value["components"].update(batch.cloud_optics_metadata_for_model("ec"))
                                for key in ["quality_coverage_source","observer_column_quality_source"]:
                                    value["components"][key] = value["components"][key].replace("GFS","EC public TCC/RH proxy")
                            expected_diagnostics.append(value)
                if args.profile_diagnostics:
                    expected_profiles = batch._profile_diagnostic_bundle(
                        prepared, expected[1], batch._horizontal_stencil(prepared, request["points"]))
                if not args.frames:
                    python_ms = (time.perf_counter_ns()-start)/1e6
                output = root / f"result-{index}"
                method = "score_stored_regional_to_files" if args.quality else "assess_stored_regional_to_files"
                if args.diagnostics:
                    method = "diagnose_stored_regional_to_files"
                if args.profile_diagnostics:
                    method = "diagnose_stored_profiles_to_files"
                if args.frames:
                    method = "build_stored_frames_to_files"
                if args.point_forecast:
                    method = "forecast_stored_point_to_files"
                command = dict(id="bench",method=method,
                               params=dict(path=str(path),output_dir=str(output)))
                env = dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python")
                start = time.perf_counter_ns()
                child = subprocess.run([str(args.engine.resolve()),"serve"],
                    input=json.dumps(command)+"\n", text=True,capture_output=True,check=True,timeout=300,env=env)
                rust_ms = (time.perf_counter_ns()-start)/1e6
                response = json.loads(child.stdout)
                if response["type"] != "result":
                    raise RuntimeError(response)
                kind = "regional-quality-v1" if args.quality else "regional-assessment-v1"
                if args.diagnostics:
                    kind = "regional-diagnostics-v1"
                if args.profile_diagnostics:
                    kind = "regional-profile-diagnostics-v1"
                if args.frames:
                    kind = "regional-frames-v1"
                if args.point_forecast:
                    kind = "point-forecast-v1"
                bundle = load_tensor_bundle(output / "manifest.json",kind=kind,source_path=path)
                for name,wanted in zip(["assessment","samples","evaluation_epoch","anchor_epoch"],expected[:4]):
                    np.testing.assert_allclose(bundle.arrays[name],wanted,rtol=4e-5,atol=5e-4,equal_nan=True,err_msg=name)
                np.testing.assert_allclose(bundle.arrays["columns"],prepared.grid,rtol=4e-6,atol=5e-5,equal_nan=True)
                for frame, entry, wanted in zip(
                    request["frames"], bundle.manifest["metadata"]["profile_bundles"], prepared.layer_profiles
                ):
                    manifest = output / entry["manifest"]
                    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == entry["identity"]["sha256"]
                    profile = load_tensor_bundle(
                        manifest, kind="regional-profiles-v1", source_path=frame["path"], mmap=True
                    )
                    np.testing.assert_allclose(
                        profile.arrays["profiles"], wanted, rtol=4e-6, atol=5e-5, equal_nan=True
                    )
                if args.quality:
                    for point, event in enumerate(expected_events):
                        assert_object(quality_from_bundle(bundle, point), event)
                    for field, name in enumerate(bundle.manifest["metadata"]["quality"]["instant_fields"]):
                        np.testing.assert_allclose(
                            bundle.arrays["instant_quality"][:, :, field], expected_instant[name],
                            rtol=3e-11, atol=1e-11,
                        )
                if args.diagnostics:
                    for point, event in enumerate(expected_diagnostics):
                        assert_object(diagnostics_from_bundle(bundle, point), event)
                if args.profile_diagnostics:
                    assert_diagnostics(bundle.arrays["profile_diagnostics"], np.stack(expected_profiles, axis=-1))
                if args.frames:
                    artifact = bundle.manifest["metadata"]["frame_payload"]
                    encoded = (output / artifact["file"]).read_bytes()
                    assert len(encoded) == artifact["identity"]["bytes"]
                    assert hashlib.sha256(encoded).hexdigest() == artifact["identity"]["sha256"]
                    check_payload(json.loads(encoded), expected_payload)
                if args.point_forecast:
                    artifact = bundle.manifest["metadata"]["point_forecast"]
                    encoded = (output / artifact["file"]).read_bytes()
                    assert len(encoded) == artifact["identity"]["bytes"]
                    assert hashlib.sha256(encoded).hexdigest() == artifact["identity"]["sha256"]
                    assert_object(json.loads(encoded), expected_point)
                if index:
                    python_times.append(python_ms); rust_times.append(rust_ms)
                    timing = dict(bundle.manifest["metadata"]["timings_ms"])
                    if args.quality:
                        timing["event_composition"] = bundle.manifest["metadata"]["quality"]["event_composition_ms"]
                        timing["instant_and_quality_output"] = bundle.manifest["metadata"]["quality"]["instant_and_output_ms"]
                    if args.diagnostics:
                        timing["diagnostics_and_output"] = bundle.manifest["metadata"]["diagnostics"]["composition_and_output_ms"]
                    if args.profile_diagnostics:
                        timing["profile_diagnostics"] = bundle.manifest["metadata"]["profile_diagnostics"]
                    if args.frames:
                        timing["frame_payload"] = bundle.manifest["metadata"]["frame_payload"]
                    if args.point_forecast:
                        timing["point_forecast"] = bundle.manifest["metadata"]["point_forecast"]
                    phases.append(timing)
            print(json.dumps(dict(
                scope=("stored raw validated/materialized point; no downloads/selection/production API; Rust includes durable output"
                       if args.point_forecast else "stored raw assessment and frame/core assembly; not validated/materialized public forecast; Rust includes durable output"
                       if args.frames else "stored raw assessment; not full forecast JSON; Rust includes durable output"),
                model=request["model"], points=len(request["points"]),cells=prepared.grid.shape[1],
                complete_empirical_quality=args.quality,
                complete_event_diagnostics=args.diagnostics,
                selected_profile_diagnostics=args.profile_diagnostics,
                frame_and_core_assembly=args.frames,
                point_validation_and_materialization=args.point_forecast,
                frames=len(request["frames"]), python_ms=python_times,rust_ms=rust_times,
                python_median_ms=statistics.median(python_times),rust_median_ms=statistics.median(rust_times),
                rust_phases_ms=phases,
                sources=bundle.manifest["metadata"]["sources"],
                max_assessment_error=float(np.nanmax(np.abs(bundle.arrays["assessment"]-expected[0]))),
            ),indent=2))


if __name__ == "__main__":
    main()
