"""Offline EC point cold/warm workflow and split/batched native execution."""
import argparse
from collections import Counter
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
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
from firesky import ecmwf_point as reference, ecmwf_ifs as ec, geometry
from test_rust_ecmwf_data import ec_server, ec_grib_fixture, params as data_params, CYCLE
from test_rust_ecmwf_point import recipe, request_object, seed_cams
from test_rust_frames import compare


def invoke(binary, method, params):
    completed = subprocess.run([str(binary), "serve"],
        input=json.dumps(dict(id="bench", method=method, params=params))+"\n",
        capture_output=True, text=True, check=True, timeout=120,
        env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))
    response = json.loads(completed.stdout)
    if response["type"] != "result":
        raise RuntimeError(response)
    return response["result"]


def snapshot(paths):
    return {p.name: dict(bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
            for p in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--engine-bin", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--extra-index-rows", type=int, default=10000)
    parser.add_argument("--index-delay", type=float, default=.01)
    args = parser.parse_args()
    if not 1 <= args.trials <= 20 or not 0 <= args.extra_index_rows <= 90000 or not 0 <= args.index_delay <= 1:
        parser.error("invalid bounded trial/index configuration")
    root = args.cache_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    binary = args.engine_bin.resolve()
    fixture = ec_server.__wrapped__(root, ec_grib_fixture.__wrapped__())
    endpoint, state = next(fixture)
    trials = []
    expected = None
    try:
        with MonkeyPatch.context() as patch:
            for name in ("ECMWF_DATA", "ECMWF_DECODE", "ASTRONOMY", "CLOUD", "CAMS", "EMPIRICAL"):
                patch.setenv(f"FIRESKY_{name}_BACKEND", "python")
            patch.setenv("NO_PROXY", "*")
            patch.setenv("no_proxy", "*")
            patch.setattr(ecmwf.opendata, "Client", lambda **kw: ActualClient(
                source=endpoint, model=kw["model"], resol=kw["resol"], verify=True,
                source_accept_ranges=True, source_accept_multiple_ranges=False))
            base_request = recipe(root, endpoint, "dawn")
            req = request_object(base_request)
            hours = reference.required_ecmwf_forecast_hours(CYCLE, reference.ecmwf_point_evaluation_times(req))
            domain = reference._ecmwf_point_domain_geometry(req.radius_km)
            bbox = list(geometry.bounding_box(req.point, domain[2]))
            state.extra_index_rows = args.extra_index_rows
            state.index_delay = args.index_delay
            for hour in (0, *hours):
                state.asset(CYCLE, hour)
            for trial in range(args.trials):
                record = {}
                for backend in ("python", "rust-split", "rust-batch"):
                    directory = root/f"trial-{trial}-{backend}"
                    directory.mkdir()
                    request = recipe(directory, endpoint, "dawn")
                    request_path = directory/"request.json"
                    request_path.write_text(json.dumps(request))
                    # Fixture model geometry is 2x3 at 1 degree. CAMS source
                    # setup is outside timed code; actual selection/decode is inside.
                    optical_times = [(CYCLE+timedelta(hours=h)).isoformat() for h in hours]
                    seed_cams(request, dict(cams_area=[1.6, 99.4, -.6, 102.6], cams_valid_times=optical_times))
                    per_backend = {}
                    for cache_state in ("cold", "warm"):
                        state.calls = Counter()
                        started = time.perf_counter()
                        if backend == "python":
                            result = reference.run_ecmwf_basic_point_forecast(
                                req, cache_dir=request["ec"]["cache_dir"],
                                cams_cache_dir=request["cams_cache_dir"], cycle_override=CYCLE)
                            report = result.report.to_dict()
                            raw_paths = [ec.EcmwfIfsAdapter.cache_path(
                                request["ec"]["cache_dir"], CYCLE, hour) for hour in hours]
                            timings = result.provenance["timing"]
                        else:
                            output = directory/cache_state
                            if backend == "rust-split":
                                resolved = [invoke(binary, "ecmwf_data", data_params(
                                    directory, endpoint, hours=[hour])) for hour in hours]
                                stored = dict(model="ecmwf-basic", date=request["date"], phenomenon="dawn",
                                    cycle_utc=CYCLE.isoformat(), radius_km=domain[0], bbox=bbox,
                                    points=[request["point"]], include_point_core=True,
                                    frames=[dict(hour=v["forecast_hour"], path=v["path"])
                                            for r in resolved for v in r["files"]],
                                    cams=[dict(valid_time=t, path=str(p)) for t, p in zip(optical_times,
                                        sorted(Path(request["cams_cache_dir"]).glob("*.grib")))])
                                stored_path = directory/f"{cache_state}-stored.json"
                                stored_path.write_text(json.dumps(stored))
                                response = invoke(binary, "forecast_stored_point_to_files",
                                    dict(path=str(stored_path), output_dir=str(output)))
                            else:
                                response = invoke(binary, "forecast_cached_ecmwf_point_to_files",
                                    dict(path=str(request_path), output_dir=str(output)))
                            manifest_path = Path(response["manifest_path"])
                            manifest = json.loads(manifest_path.read_text())
                            point = json.loads((output/"ecmwf_point.json").read_text())
                            report = point["report"]
                            raw_paths = [ec.EcmwfIfsAdapter.cache_path(
                                request["ec"]["cache_dir"], CYCLE, hour) for hour in hours]
                            timings = manifest["metadata"]["timings_ms"]
                            if backend == "rust-batch":
                                plan = json.loads((output/"input_plan.json").read_text())
                                timings = dict(timings, input_plan=plan["timings_ms"],
                                               raw_admission=plan["ec_resolution"]["timings_ms"])
                        elapsed = (time.perf_counter()-started)*1000
                        identities = snapshot(raw_paths)
                        current = dict(report=report, raw_files=identities)
                        if expected is None:
                            expected = current
                        compare(current, expected)
                        per_backend[cache_state] = dict(total_ms=elapsed, timings=timings,
                            requests={k: v for k, v in state.calls.items() if isinstance(k, str)})
                    record[backend] = per_backend
                trials.append(record)
    finally:
        next(fixture, None)
    encoded = json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    print(json.dumps(dict(scope="offline-EC-point-raw-cache-to-independent-report",
        live_provider=False, model_cells=6, pressure_levels=9, event_steps=20,
        forecast_hours=list(hours), trials=trials,
        medians_ms={backend: {state: statistics.median(row[backend][state]["total_ms"] for row in trials)
                             for state in ("cold", "warm")}
                    for backend in ("python", "rust-split", "rust-batch")},
        stable_report_and_files=dict(bytes=len(encoded), sha256=hashlib.sha256(encoded).hexdigest()),
        caveats=["Python is warm in-process; Rust starts fresh children.",
                 "Python warm reuses durable derived caches; native currently redecodes.",
                 "All native output/profile/hash/sync work is timed; Python returns an in-memory report.",
                 "Synthetic loopback six-cell input is not full-domain or live-source acceptance."]),
        indent=2))


if __name__ == "__main__":
    main()
