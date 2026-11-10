"""Complete native EC service refresh versus integrity-verified snapshot reuse."""
import argparse
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
from test_rust_ecmwf_data import ec_server, ec_grib_fixture
from test_rust_ecmwf_point import recipe, seed_cams
from test_rust_response_cache import arrays, same_arrays
from bench_ecmwf_service_migration import invoke


class Session:
    def __init__(self, binary):
        self.process = subprocess.Popen(
            ["/usr/bin/time", "-l", str(binary), "serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
            env=dict(os.environ, PATH="", PYTHONPATH="", FIRESKY_PYTHON="/no/python",
                     FIRESKY_ECCODES_LIBRARY="/no/ecCodes/library"))
        try:
            self.call("hello", {})
        except BaseException:
            self.process.kill()
            self.process.communicate()
            raise

    def call(self, method, params):
        started = time.perf_counter()
        self.process.stdin.write(json.dumps(dict(id="persistent", method=method, params=params))+"\n")
        self.process.stdin.flush()
        response = json.loads(self.process.stdout.readline())
        elapsed = (time.perf_counter()-started)*1000
        if response["type"] != "result":
            raise RuntimeError(response)
        return response["result"], elapsed

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            self.process.stdout.close()
            self.process.stderr.close()
            raise
        stderr = self.process.stderr.read()
        self.process.stdout.close()
        self.process.stderr.close()
        if self.process.returncode:
            raise RuntimeError(stderr)
        rss = re.search(r"(\d+)\s+maximum resident set size", stderr)
        return int(rss[1]) if rss else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--engine-bin", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.trials <= 20:
        parser.error("trials must be 1..20")
    root = args.cache_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    binary = args.engine_bin.resolve()
    fixture = ec_server.__wrapped__(root, ec_grib_fixture.__wrapped__())
    endpoint, state = next(fixture)
    records = []
    try:
        request = recipe(root, endpoint)
        path = root/"request.json"
        path.write_text(json.dumps(request))
        planned, _, _ = invoke(binary, "plan_ecmwf_inputs", dict(path=str(path)))
        assert planned["inputs_located"] is False  # CAMS is deliberately seeded next.
        seed_cams(request, planned)
        request["allow_ec_download"] = False
        path.write_text(json.dumps(request))
        params = dict(path=str(path), cache_dir=str(root/"responses"))
        first, elapsed, rss = invoke(binary, "forecast_ecmwf_service_cached", params)
        baseline = arrays(first)
        baseline_report = dict(first["service"]["report"])
        baseline_report.pop("html_report_path")
        records.append(dict(trial=-1, operation="initial-miss", total_ms=elapsed,
                            process_peak_rss_bytes=rss,
                            native_lookup_ms=first["response_cache"]["elapsed_ms"]))
        before = dict(state.calls)
        for trial in range(args.trials):
            operations = ("forced-refresh", "snapshot-hit")
            if trial % 2:
                operations = tuple(reversed(operations))
            for operation in operations:
                cached, elapsed, rss = invoke(binary, "forecast_ecmwf_service_cached",
                    dict(params, force_refresh=operation == "forced-refresh"))
                expected = "refresh" if operation == "forced-refresh" else "hit"
                assert cached["response_cache"]["status"] == expected
                same_arrays(arrays(cached), baseline)
                report = dict(cached["service"]["report"])
                report.pop("html_report_path")
                assert report == baseline_report
                assert cached["service"]["native_quality"] == first["service"]["native_quality"]
                assert cached["service"]["response"]["cross_section"] == first["service"]["response"]["cross_section"]
                records.append(dict(trial=trial, operation=operation, total_ms=elapsed,
                                    native_lookup_ms=cached["response_cache"]["elapsed_ms"],
                                    process_peak_rss_bytes=rss))
        assert dict(state.calls) == before
        for trial in range(args.trials):
            point = first["service"]["observer"]
            profile, elapsed, rss = invoke(binary, "read_cached_ecmwf_service_cross_section",
                dict(cache_dir=params["cache_dir"], response_key=first["response_key"],
                     lat=point["lat"], lon=point["lon"], offset_minutes=0))
            assert len(profile["distances_km"]) == 41
            records.append(dict(trial=trial, operation="registered-profile", total_ms=elapsed,
                                process_peak_rss_bytes=rss))
        session = Session(binary)
        try:
            for trial in range(args.trials):
                cached, elapsed = session.call("forecast_ecmwf_service_cached", params)
                assert cached["response_cache"]["status"] == "hit"
                same_arrays(arrays(cached), baseline)
                records.append(dict(trial=trial, operation="persistent-snapshot-hit", total_ms=elapsed,
                                    native_lookup_ms=cached["response_cache"]["elapsed_ms"],
                                    process_peak_rss_bytes=None))
        finally:
            persistent_rss = session.close()
        # The first generation remains registered after every refresh.
        verified = {name: dict(shape=list(value.shape), dtype=str(value.dtype),
                    sha256=hashlib.sha256(value.tobytes()).hexdigest()) for name, value in baseline.items()}
    finally:
        next(fixture, None)
    print(json.dumps(dict(contract="ecmwf-response-cache-benchmark-v1",
        engine_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        trials=records, verified_arrays=verified, cells=6, pressure_levels=9,
        event_steps=20, cross_section_distances=41,
        median_ms={op: statistics.median(r["total_ms"] for r in records if r["operation"] == op)
                   for op in sorted({r["operation"] for r in records})},
        persistent_session_peak_rss_bytes=persistent_rss,
        median_peak_rss_bytes={op: statistics.median(r["process_peak_rss_bytes"] for r in records
            if r["operation"] == op and r["process_peak_rss_bytes"] is not None)
            for op in sorted({r["operation"] for r in records if r["process_peak_rss_bytes"] is not None})},
        caveats=[
            "Raw EC and CAMS are seeded before measurement; initial-miss means no native response snapshot.",
            "Fresh-child timings include startup and full JSON response parsing; persistent-snapshot-hit is a separate resident session after hello.",
            "Every engine process has Python unavailable; the persistent session also has ecCodes unavailable.",
            "All sixteen main arrays and every per-hour 24-field profile are checked bitwise outside timed code.",
            "Hits verify the entire stored generation but intentionally do not revalidate historical raw GRIB inputs.",
            "Refresh and hit share the same cached-service response contract; their freshness semantics differ.",
            "This six-cell synthetic local fixture is not national-domain, live-provider, CPU/P95/throughput or deployment evidence.",
            "Production HTTP, authorization, cancellation, bounded retention and client integration remain unfinished.",
        ]), indent=2))


if __name__ == "__main__":
    main()
