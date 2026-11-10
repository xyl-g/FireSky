"""Offline EC point vs provenance/HTML/cross-section service-output costs."""
import argparse
from collections import Counter
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
sys.path.insert(0, str(ROOT/"tests"))
from pytest import MonkeyPatch
import ecmwf.opendata
from ecmwf.opendata import Client as ActualClient
from firesky import ecmwf_point as reference, web_server
from test_rust_ecmwf_data import ec_server, ec_grib_fixture, CYCLE
from test_rust_ecmwf_point import recipe, request_object, seed_cams
from test_rust_frames import compare


def invoke(binary, method, params):
    started = time.perf_counter()
    completed = subprocess.run(
        ["/usr/bin/time", "-l", str(binary), "serve"],
        input=json.dumps(dict(id="bench", method=method, params=params))+"\n",
        capture_output=True, text=True, check=True, timeout=120,
        env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))
    elapsed = (time.perf_counter()-started)*1000
    response = json.loads(completed.stdout)
    if response["type"] != "result":
        raise RuntimeError(response)
    rss = re.search(r"(\d+)\s+maximum resident set size", completed.stderr)
    return response["result"], elapsed, int(rss[1]) if rss else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--engine-bin", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--derived-cache", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.trials <= 20:
        parser.error("trials must be 1..20")
    root = args.cache_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    binary = args.engine_bin.resolve()
    fixture = ec_server.__wrapped__(root, ec_grib_fixture.__wrapped__())
    endpoint, state = next(fixture)
    trials = []
    canonical = None
    try:
        with MonkeyPatch.context() as patch:
            for name in ("ECMWF_DATA", "ECMWF_DECODE", "ASTRONOMY", "CLOUD", "CAMS", "EMPIRICAL"):
                patch.setenv(f"FIRESKY_{name}_BACKEND", "python")
            patch.setenv("NO_PROXY", "*")
            patch.setenv("no_proxy", "*")
            patch.setattr(ecmwf.opendata, "Client", lambda **kw: ActualClient(
                source=endpoint, model=kw["model"], resol=kw["resol"], verify=True,
                source_accept_ranges=True, source_accept_multiple_ranges=False))
            req = request_object(recipe(root, endpoint, "dawn"))
            hours = reference.required_ecmwf_forecast_hours(CYCLE, reference.ecmwf_point_evaluation_times(req))
            for hour in (0, *hours):
                state.asset(CYCLE, hour)
            for trial in range(args.trials):
                backends = ("python-service", "rust-point", "rust-service")
                if args.derived_cache:
                    backends += ("rust-service-derived",)
                if trial % 2:
                    backends = tuple(reversed(backends))
                for backend in backends:
                    directory = root/f"trial-{trial}-{backend}"
                    directory.mkdir()
                    request = recipe(directory, endpoint, "dawn")
                    request["derived_cache"] = {} if backend == "rust-service-derived" else None
                    request_path = directory/"request.json"
                    request_path.write_text(json.dumps(request))
                    seed_cams(request, dict(cams_area=[1.6, 99.4, -.6, 102.6],
                        cams_valid_times=[(CYCLE+timedelta(hours=h)).isoformat() for h in hours]))
                    config = web_server.WebServerConfig(
                        report_dir=directory/"reports", ecmwf_cache_dir=Path(request["ec"]["cache_dir"]),
                        cams_cache_dir=Path(request["cams_cache_dir"]))
                    payload = dict(request["point"], target_date=request["date"], phenomenon="dawn",
                        model_source="ec", forecast_mode="model", radius_km=request["radius_km"],
                        ec_cycle_override=CYCLE.isoformat())
                    for cache_state in ("cold", "warm"):
                        state.calls = Counter()
                        rss = None
                        service_ms = None
                        started = time.perf_counter()
                        if backend == "python-service":
                            # Force recomputation, not the durable response-cache shortcut.
                            result = web_server._run_ecmwf_point_payload(payload, config, force_refresh=True)
                            elapsed = (time.perf_counter()-started)*1000
                            report = {key: result[key] for key in (
                                "observer_name", "phenomenon", "model_source", "target_date",
                                "overall_score", "burn_score", "best_time_local", "summary")}
                            timings = result["ecmwf_provenance"]["timing"]
                            assert result["cross_section"]["model_source"] == "ec"
                        else:
                            is_service = backend.startswith("rust-service")
                            method = ("forecast_ecmwf_service_to_files" if is_service
                                      else "forecast_cached_ecmwf_point_to_files")
                            output = directory/cache_state
                            result, elapsed, rss = invoke(binary, method, dict(
                                path=str(request_path), output_dir=str(output)))
                            manifest = json.loads(Path(result["manifest_path"]).read_text())
                            filename = "ecmwf_service.json" if is_service else "ecmwf_point.json"
                            point = json.loads((output/filename).read_text())
                            report = {key: point["report"][key] for key in (
                                "observer_name", "phenomenon", "model_source", "target_date",
                                "overall_score", "burn_score", "best_time_local", "summary")}
                            timings = manifest["metadata"]["timings_ms"]
                            planned = json.loads((output/"input_plan.json").read_text())
                            timings = dict(timings, input_plan=planned["timings_ms"],
                                           raw_admission=planned["ec_resolution"]["timings_ms"],
                                           derived_cache=manifest["metadata"].get("derived_cache"))
                            if is_service:
                                service_ms = manifest["metadata"]["ecmwf_service"]["service_assembly_serialization_and_output_ms"]
                                assert point["response"]["cross_section"]["model_source"] == "ec"
                                assert (output/"report.html").stat().st_size > 10000
                        if canonical is None:
                            canonical = report
                        compare(report, canonical)
                        trials.append(dict(trial=trial, backend=backend, cache_state=cache_state,
                            total_ms=elapsed, timings=timings, service_assembly_ms=service_ms,
                            process_peak_rss_bytes=rss,
                            requests={k:v for k,v in state.calls.items() if isinstance(k,str)}))
    finally:
        next(fixture, None)
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    print(json.dumps(dict(contract="ecmwf-service-benchmark-v1", trials=trials,
        median_ms={b:{s:statistics.median(v["total_ms"] for v in trials
            if v["backend"]==b and v["cache_state"]==s) for s in ("cold","warm")}
            for b in sorted({v["backend"] for v in trials})},
        engine_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        selected_report_sha256=hashlib.sha256(encoded).hexdigest(),
        model_cells=6, pressure_levels=9, event_steps=20, cross_section_distances=41,
        scope="loopback raw-cache cold/warm; recomputed point vs provenance+HTML+cross-section",
        caveats=[
            "Cold means empty raw/derived caches, not cold OS or ecCodes.",
            "Python is warm in-process and force-refreshes the full existing Web service including response-cache writes.",
            "Rust starts fresh children and durably writes all tensors, profiles, hashes and service artifacts.",
            "Native client-side parsing of service artifact files after the manifest acknowledgement is outside timed code.",
            "Rust does not implement the production response-cache/HTTP route; this is not an equal production-workflow speed ratio.",
            "Only selected common report fields are benchmark parity guards; complete HTML/provenance/cross-section parity is tested separately.",
            "Six synthetic cells do not establish national/full-domain, P95, live-provider or deployment performance.",
        ]), indent=2))


if __name__ == "__main__":
    main()
