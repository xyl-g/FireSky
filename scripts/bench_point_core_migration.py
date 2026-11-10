#!/usr/bin/env python3
"""Warm validated materialization of a fixed core, not a full forecast."""
import argparse
from datetime import date, datetime
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from firesky.models import ForecastRequest, GeoPoint
from firesky.national_point_core import materialize_national_point_core
from test_rust_frames import compare
from test_rust_migration import rpc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--engine", type=Path, default=ROOT / "engine/target/release/firesky-engine")
    parser.add_argument("--benchmark", type=Path, default=ROOT / "engine/target/release/examples/bench_point_core")
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--trials", type=int, default=5)
    args = parser.parse_args()
    if not (1 <= args.iterations <= 10000 and 1 <= args.trials <= 15):
        parser.error("bounded iterations/trials required")
    if not args.fixture.is_file() or args.fixture.stat().st_size > 1024 * 1024:
        parser.error("fixture must be at most 1 MiB")
    fixture = json.loads(args.fixture.read_text())
    request = dict(fixture["request"])
    request["point"] = GeoPoint(**request["point"])
    request["target_date"] = date.fromisoformat(request["target_date"])
    for name in ("gfs_cycle_override", "reference_time_utc", "model_cycle_override"):
        if request.get(name) is not None:
            request[name] = datetime.fromisoformat(request[name].replace("Z", "+00:00"))
    request = ForecastRequest(**request)
    os.environ["FIRESKY_ASTRONOMY_BACKEND"] = "python"
    os.environ["FIRESKY_EMPIRICAL_BACKEND"] = "python"
    expected = materialize_national_point_core(request, fixture["core"])
    got = rpc(args.engine.resolve(), [
        dict(id="point", method="materialize_point_core", params=fixture)],
        env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))[0]
    if got["type"] != "result":
        raise RuntimeError(got)
    compare(got["result"], expected)
    times = []
    for _ in range(args.trials + 1):
        start = time.perf_counter_ns()
        for _ in range(args.iterations):
            actual = materialize_national_point_core(request, fixture["core"])
        times.append((time.perf_counter_ns() - start) / 1e6 / args.iterations)
        assert actual == expected
    times.pop(0)
    result = subprocess.run(
        [str(args.benchmark.resolve()), str(args.fixture.resolve()), str(args.iterations), str(args.trials)],
        capture_output=True, text=True, check=True, timeout=300,
        env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python"))
    native = json.loads(result.stdout)
    print(json.dumps(dict(
        scope="warm fixed-core validation/materialization; parsing, serialization, raw decode and I/O excluded",
        iterations=args.iterations, trials=args.trials,
        python=dict(ms_per_point=times, median_ms=statistics.median(times)),
        rust=native, complete_payload_parity=True,
        rust_speed_ratio=statistics.median(times) / native["median_ms"]), indent=2))


if __name__ == "__main__":
    main()
