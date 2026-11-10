#!/usr/bin/env python3
"""Warm component benchmark; neither a full forecast nor a deployment test."""

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from firesky import geometry, rust_astronomy
from firesky.models import GeoPoint


def measure(function, trials):
    function()
    values = []
    for _ in range(trials):
        start = time.perf_counter_ns()
        function()
        values.append((time.perf_counter_ns() - start) / 1e6)
    return {"median_ms": statistics.median(values), "trials_ms": values}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=7)
    parser.add_argument("--samples", type=int, default=10000)
    parser.add_argument("--windows", type=int, default=10)
    args = parser.parse_args()
    if not (1 <= args.trials <= 20 and 1 <= args.samples <= 100000 and 1 <= args.windows <= 100):
        parser.error("bounded trials/samples/windows required")
    point = GeoPoint(31.23,121.47,50)
    tz = "Asia/Shanghai"
    times = [datetime(2026,11,9,8,tzinfo=timezone.utc) + timedelta(seconds=i % 7200,microseconds=123456)
             for i in range(args.samples)]
    os.environ["FIRESKY_ASTRONOMY_BACKEND"] = "python"
    reference = [geometry.solar_snapshot(point,value,tz) for value in times]
    anchor = geometry.solar_event_time_local(date(2026,11,9),point,tz,"dusk")
    expected_window = geometry.typical_deck_glow_window(point,anchor,tz,"dusk",80,6000)
    os.environ["FIRESKY_ASTRONOMY_BACKEND"] = "rust"
    rust_astronomy._position_function()
    actual = [geometry.solar_snapshot(point,value,tz) for value in times]
    max_error = max(max(abs(a.elevation_deg-b.elevation_deg),abs(a.azimuth_deg-b.azimuth_deg))
                    for a,b in zip(actual,reference))
    if max_error > 2e-9:
        raise RuntimeError(f"solar numerical parity failed: {max_error}")
    window = geometry.typical_deck_glow_window(point,anchor,tz,"dusk",80,6000)
    if window != expected_window:
        raise RuntimeError("window parity failed")
    output = {"scope": "warm component calls, not full forecasts",
              "rust_transport": "C ABI for snapshots and UTC window core",
              "samples": args.samples, "windows": args.windows,
              "max_position_error_deg": max_error, "window_exact": True}
    for backend in ("python","rust"):
        os.environ["FIRESKY_ASTRONOMY_BACKEND"] = backend
        output[f"{backend}_snapshots"] = measure(
            lambda: [geometry.solar_snapshot(point,value,tz) for value in times],args.trials)
        output[f"{backend}_windows"] = measure(
            lambda: [geometry.typical_deck_glow_window(point,anchor,tz,"dusk",80,6000)
                     for _ in range(args.windows)],args.trials)
    print(json.dumps(output,indent=2))


if __name__ == "__main__":
    main()
