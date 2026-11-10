#!/usr/bin/env python3
"""Warm regional timeline/sample preparation, not a full forecast benchmark."""

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import statistics
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from firesky import national_batch, rust_astronomy
from firesky.web_server import national_points_from_payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=7)
    args = parser.parse_args()
    if not 1 <= args.trials <= 20:
        parser.error("trials must be 1..20")
    points = national_points_from_payload({
        "regions": ["china"], "model_source": "gfs",
        "gfs_resolution": "0p25", "spacing_deg": .25,
    })
    if len(points) != 36685:
        raise RuntimeError("production point domain changed")
    day = date(2026, 11, 9)
    cycle = datetime(2026, 11, 9, tzinfo=timezone.utc)
    hours = np.arange(-24, 49, 3, dtype=np.float64)
    output = {"scope": "warm regional preparation only; no weather or scoring",
              "points": len(points), "steps": 20, "phenomenon": "dusk",
              "date": day.isoformat(), "transport": "C ABI"}
    results = {}
    for backend in ("python", "rust"):
        os.environ["FIRESKY_ASTRONOMY_BACKEND"] = backend
        if backend == "rust":
            rust_astronomy._regional_function()
        def run():
            return national_batch.prepare_solar_samples(points, day, "dusk", cycle, hours)
        results[backend] = run()
        trials = []
        for _ in range(args.trials):
            start = time.perf_counter_ns()
            run()
            trials.append((time.perf_counter_ns() - start) / 1e6)
        output[backend] = {"median_ms": statistics.median(trials), "trials_ms": trials}
    actual, expected = results["rust"], results["python"]
    for i, (a, b) in enumerate(zip(actual, expected)):
        np.testing.assert_allclose(a, b, rtol=0, atol=5e-4 if i == 1 else 5e-6)
    np.testing.assert_array_equal(actual[1][:, :, [2, 3, 6]], expected[1][:, :, [2, 3, 6]])
    output["max_sample_error"] = float(np.max(np.abs(actual[1] - expected[1])))
    output["max_epoch_error_seconds"] = float(np.max(np.abs(actual[2] - expected[2])))
    output["speedup"] = output["python"]["median_ms"] / output["rust"]["median_ms"]
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
