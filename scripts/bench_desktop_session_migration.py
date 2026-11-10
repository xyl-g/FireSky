"""Benchmark the native desktop session authority boundary.

This benchmark uses a local synthetic report and no provider/network I/O. It
measures session lifecycle and JSON transport overhead, not complete forecast
throughput, cache readiness or GUI behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from firesky import desktop_rpc
from firesky.rust_desktop_process import close as close_engine


def synthetic_report() -> dict:
    requirements = [
        {
            "source": source,
            "ready": True,
            "request_cycle": "2026-11-08 00Z",
            "latest_local_cycle": "2026-11-08 00Z",
            "latest_available_cycle": "2026-11-08 00Z",
            "latest_available_cycle_utc": "2026-11-08T00:00:00+00:00",
            "verified": True,
            "is_latest": True,
            "missing_count": 0,
            "missing_hours": list(range(0, 48, 3)),
            "missing_valid_times": [
                f"2026-11-08T{hour:02d}:00:00+00:00" for hour in range(0, 24, 3)
            ],
            "missing_keys": [f"{source}:key-{index:02d}" for index in range(16)],
        }
        for source in ("gfs", "ecmwf", "cams")
    ]
    return {
        "ready": True,
        "model_cycles": {
            "gfs": "2026-11-08T00:00:00+00:00",
            "ec": "2026-11-08T00:00:00+00:00",
        },
        "datasets": [
            {
                "source": source,
                "ready": True,
                "verified": False,
                "request_cycle": "2026-11-08 00Z",
                "missing_count": 0,
            }
            for source in ("gfs", "ecmwf", "cams")
        ],
        "model_windows": {
            "gfs": "2026-11-08T00:00:00+00:00",
            "ec": "2026-11-08T00:00:00+00:00",
        },
        "bbox": [-180.0, 180.0, -90.0, 90.0],
        "requirements": requirements,
        "notes": ["synthetic-local-session-benchmark"] * 8,
    }


def request(child, sequence: int, method: str, params: dict) -> dict:
    child.stdin.write(
        json.dumps(
            {"id": str(sequence), "method": method, "params": params},
            allow_nan=False,
        )
        + "\n"
    )
    child.stdin.flush()
    message = json.loads(child.stdout.readline())
    if message.get("type") != "result":
        raise RuntimeError(message)
    return message["result"]


def direct_native(binary: Path, cycles: int) -> tuple[float, float, float]:
    child = subprocess.Popen(
        [str(binary), "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    owner = "bench-native-session"
    sequence = 0
    try:
        def call(method, params):
            nonlocal sequence
            sequence += 1
            return request(child, sequence, method, params)

        def cycle():
            payload = {"model_source": "both", "workers": 1, "lat": 30.25, "lon": 120.25}
            report = synthetic_report()
            token = call(
                "desktop_session",
                {
                    "action": "create",
                    "owner": owner,
                    "kind": "point",
                    "payload": payload,
                    "report": report,
                },
            )["session"]
            call(
                "desktop_session",
                {"action": "validate", "owner": owner, "session": token},
            )
            call(
                "desktop_session",
                {
                    "action": "update",
                    "owner": owner,
                    "session": token,
                    "report": report,
                },
            )
            call(
                "desktop_session",
                {"action": "consume", "owner": owner, "session": token},
            )

        cycle()
        started = time.perf_counter()
        for _ in range(cycles):
            cycle()
        elapsed = (time.perf_counter() - started) * 1000.0

        payload = {"model_source": "both", "workers": 1}
        report = synthetic_report()
        token = call(
            "desktop_session",
            {
                "action": "create",
                "owner": owner,
                "kind": "point",
                "payload": payload,
                "report": report,
            },
        )["session"]
        started = time.perf_counter()
        for _ in range(cycles):
            call(
                "desktop_session",
                {"action": "validate", "owner": owner, "session": token},
            )
        validate_elapsed = (time.perf_counter() - started) * 1000.0
        started = time.perf_counter()
        for _ in range(cycles):
            call("desktop_session", {"action": "get", "owner": owner, "session": token})
        snapshot_elapsed = (time.perf_counter() - started) * 1000.0
    finally:
        child.stdin.close()
        child.wait(timeout=10)
    return elapsed / cycles, validate_elapsed / cycles, snapshot_elapsed / cycles


def service_cycles(backend: str, cycles: int) -> float:
    os.environ["FIRESKY_DESKTOP_DATA_BACKEND"] = backend
    service = desktop_rpc.DesktopService()
    service._check = lambda *args, **kwargs: synthetic_report()
    service._forecast = lambda kind, payload, progress: {"ok": True}

    def cycle():
        token = service.dispatch(
            "check",
            {"kind": "point", "payload": {"lat": 30.25, "lon": 120.25}},
        )["session"]
        service.dispatch("forecast", {"session": token, "confirmed": True})

    cycle()
    started = time.perf_counter()
    for _ in range(cycles):
        cycle()
    return (time.perf_counter() - started) * 1000.0 / cycles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-bin", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=100)
    parser.add_argument("--trials", type=int, default=5)
    args = parser.parse_args()
    if args.cycles < 2 or args.cycles > 10_000:
        parser.error("--cycles must be between 2 and 10000")
    if args.trials < 2 or args.trials > 30:
        parser.error("--trials must be between 2 and 30")
    binary = args.engine_bin.resolve()
    if not binary.is_file():
        parser.error("engine binary does not exist")
    os.environ["FIRESKY_RUST_ENGINE_BIN"] = str(binary)

    native_times = []
    native_validate_times = []
    native_snapshot_times = []
    python_times = []
    bridge_times = []
    for _ in range(args.trials):
        close_engine()
        native, validate, snapshot = direct_native(binary, args.cycles)
        native_times.append(native)
        native_validate_times.append(validate)
        native_snapshot_times.append(snapshot)
        close_engine()
        python_times.append(service_cycles("python", args.cycles))
        close_engine()
        bridge_times.append(service_cycles("rust", args.cycles))
        close_engine()

    print(
        json.dumps(
            {
                "contract": "rust-desktop-session-benchmark-v1",
                "cycles": args.cycles,
                "trials": args.trials,
                "engine_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "direct_native_cycle_median_ms": statistics.median(native_times),
                "direct_native_validate_median_ms": statistics.median(
                    native_validate_times
                ),
                "direct_native_get_snapshot_median_ms": statistics.median(
                    native_snapshot_times
                ),
                "python_reference_cycle_median_ms": statistics.median(python_times),
                "production_rust_bridge_cycle_median_ms": statistics.median(bridge_times),
                "production_rust_bridge_over_python_ratio": (
                    statistics.median(bridge_times)
                    / max(statistics.median(python_times), 1e-9)
                ),
                "caveats": [
                    "Synthetic local report/payload; no provider, cache or forecast work.",
                    "Cycle includes create, validate, update and consume.",
                    "The snapshot line measures full get response size/transfer; production entry uses validate.",
                    "Results are component microbenchmarks, not end-to-end forecast throughput or P95.",
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
