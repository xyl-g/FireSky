#!/usr/bin/env python3
"""Benchmark decoded GFS sidecar priors against the Python feature reference.

This uses real cached GRIB sidecars when available. It measures only the
vertical-prior batch boundary, including persistent JSON-lines transport and
Python dataclass reconstruction. It excludes HSD decode/retrieval, observation
constraints, final event assembly and HTTP/CLI transport.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from firesky.gfs import decoded_grid_cache_path, load_mmap_decoded_grid
from firesky.geometry import GeoPoint, destination_point
from firesky.models import ForecastRequest
from firesky.rust_desktop_process import close as close_engine
from firesky.satellite import satellite_sample_points
from firesky.service import (
    GfsNowcastVerticalPriorProvider,
    _SharedGfsNowcastDomain,
)


DEFAULT_LEFT = (
    ROOT
    / ".cache/gfs/2026-11-07/00Z/china/0p25/b47e8ade5e1275031edf.grib2"
)
DEFAULT_RIGHT = (
    ROOT
    / ".cache/gfs/2026-11-07/00Z/china/0p25/383eb147ec4722f589ae.grib2"
)


def fixture() -> tuple[
    GfsNowcastVerticalPriorProvider,
    ForecastRequest,
    list[GeoPoint],
    datetime,
]:
    paths = {8: DEFAULT_LEFT, 9: DEFAULT_RIGHT}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise RuntimeError(f"pinned GFS cache is unavailable: {missing}")
    cycle = datetime(2026, 11, 7, tzinfo=timezone.utc)
    valid_time = datetime(2026, 11, 7, 8, 55, tzinfo=timezone.utc)
    center = GeoPoint(30.2741, 120.1551)

    class Client:
        resolution = "0p25"
        _res_grid_deg = 0.25

    provider = GfsNowcastVerticalPriorProvider(Client())
    domains = {}
    for hour, raw_path in paths.items():
        cache_path = decoded_grid_cache_path(raw_path)
        if not (cache_path / "manifest.json").is_file():
            raise RuntimeError(f"decoded GFS sidecar is unavailable: {cache_path}")
        domains[hour] = _SharedGfsNowcastDomain(
            grid=load_mmap_decoded_grid(cache_path),
            cache_path=str(raw_path),
            source_url=f"pinned-cache:{hour}",
            bbox=(0.0, 0.0, 0.0, 0.0),
        )
    provider._snapshot = lambda *_args: domains[_args[-1]]
    samples = satellite_sample_points(center, 25.0, 900.0)
    points = [destination_point(center, bearing, distance) for bearing, distance in samples]
    request = ForecastRequest(
        target_date=valid_time.date(),
        timezone_name="UTC",
        point=center,
        radius_km=900.0,
        gfs_cycle_override=cycle,
    )
    return provider, request, points, valid_time


def run(provider, request, points, valid_time, backend):
    os.environ["FIRESKY_GFS_PRIOR_BACKEND"] = backend
    started = time.perf_counter()
    values = provider._batch(request, points, valid_time)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return elapsed_ms, values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine-bin",
        type=Path,
        default=ROOT / "engine/target/release/firesky-engine",
    )
    parser.add_argument("--trials", type=int, default=9)
    args = parser.parse_args()
    if not 3 <= args.trials <= 30:
        parser.error("--trials must be between 3 and 30")
    engine = args.engine_bin.resolve()
    if not engine.is_file():
        raise RuntimeError(f"Rust engine is unavailable: {engine}")
    os.environ["FIRESKY_RUST_ENGINE_BIN"] = str(engine)
    close_engine()

    provider, request, points, valid_time = fixture()
    run(provider, request, points, valid_time, "python")
    run(provider, request, points, valid_time, "rust")
    _, expected = run(provider, request, points, valid_time, "python")
    _, actual = run(provider, request, points, valid_time, "rust")
    if not all(expected) or not all(actual):
        raise RuntimeError("a benchmark sample unexpectedly has no vertical prior")
    for index, (native, reference) in enumerate(zip(actual, expected, strict=True)):
        if native != reference:
            raise RuntimeError(f"decoded prior differs from Python at sample {index}")

    python_times = []
    rust_times = []
    for trial in range(args.trials):
        if trial % 2 == 0:
            python_ms, _ = run(provider, request, points, valid_time, "python")
            rust_ms, _ = run(provider, request, points, valid_time, "rust")
        else:
            rust_ms, _ = run(provider, request, points, valid_time, "rust")
            python_ms, _ = run(provider, request, points, valid_time, "python")
        python_times.append(python_ms)
        rust_times.append(rust_ms)
    close_engine()

    python_median = statistics.median(python_times)
    rust_median = statistics.median(rust_times)
    fields_per_sample = 2 + 5 * len(actual[0].levels)
    print(
        json.dumps(
            {
                "contract": "firesky-nowcast-gfs-prior-decoded-benchmark-v1",
                "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
                "points": len(points),
                "levels_per_point": len(actual[0].levels),
                "exact_prior_objects": True,
                "compared_scalar_fields": len(points) * fields_per_sample,
                "python_trials_ms": python_times,
                "rust_trials_ms": rust_times,
                "python_median_ms": python_median,
                "rust_median_ms": rust_median,
                "python_min_ms": min(python_times),
                "rust_min_ms": min(rust_times),
                "python_max_ms": max(python_times),
                "rust_max_ms": max(rust_times),
                "speedup": python_median / rust_median,
                "saved_median_ms": python_median - rust_median,
                "scope": (
                    "Warm local decoded GFS sidecar batch; includes persistent "
                    "JSON-lines transport and Python object reconstruction."
                ),
                "excluded": [
                    "HSD decode/retrieval and observation constraints",
                    "final event scoring and result assembly",
                    "HTTP/CLI transport, downloads and cache warming",
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
