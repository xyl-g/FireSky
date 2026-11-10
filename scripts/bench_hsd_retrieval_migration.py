"""Benchmark the native and Python calibrated-HSD posterior boundary.

The fixture is synthetic and local: it measures deterministic posterior
retrieval plus the persistent JSON-lines bridge, not HSD downloads, Satpy
calibration, GFS interpolation or complete nowcast assembly.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from firesky.geometry import GeoPoint
from firesky.rust_desktop_data import nowcast_hsd_retrieval
from firesky.rust_desktop_process import close as close_engine
from firesky.satellite_cloud_column import (
    CalibratedSatelliteFrame,
    HsdCompactCache,
    RustHsdCompactCacheProvider,
    VerticalPrior,
    VerticalPriorLevel,
    retrieve_cloud_columns,
    sample_calibrated_frame,
)


def fixture(root: Path):
    point = GeoPoint(30.2741, 120.1551)
    valid_time = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
    lat_axis = np.asarray(
        [point.lat - 0.05, point.lat, point.lat + 0.05],
        dtype=np.float32,
    )
    lon_axis = np.asarray(
        [point.lon - 0.05, point.lon, point.lon + 0.05],
        dtype=np.float32,
    )
    latitudes = np.broadcast_to(lat_axis[:, None], (3, 3)).copy()
    longitudes = np.broadcast_to(lon_axis[None, :], (3, 3)).copy()
    frame = CalibratedSatelliteFrame(
        valid_time_utc=valid_time,
        latitudes=latitudes,
        longitudes=longitudes,
        bands={
            "B07": np.full((3, 3), 279.0, dtype=np.float32),
            "B13": np.full((3, 3), 280.0, dtype=np.float32),
            "B15": np.full((3, 3), 281.0, dtype=np.float32),
        },
        units={"B07": "K", "B13": "K", "B15": "K"},
        source="bench-hsd",
        quality="medium",
        range_bytes=1,
        range_blocks=1,
    )
    HsdCompactCache(root / "compact").write(frame, point, 900.0)
    native_frame = RustHsdCompactCacheProvider(root).fetch(
        valid_time + timedelta(minutes=5),
        point,
        radius_km=900.0,
    )
    if native_frame is None:
        raise RuntimeError("native HSD fixture export returned no frame")
    sample = sample_calibrated_frame(native_frame, point.lat, point.lon)
    prior = VerticalPrior(
        valid_time_utc=valid_time,
        cycle_time_utc=valid_time,
        forecast_hour=0,
        source="gfs-test",
        terrain_asl_m=8.0,
        identity="bench-column",
        target_lat=point.lat,
        target_lon=point.lon,
        levels=(
            VerticalPriorLevel(1000, 10.0, 291.0, 50.0, 0.02),
            VerticalPriorLevel(900, 1000.0, 286.0, 80.0, 0.70),
            VerticalPriorLevel(800, 2500.0, 280.0, 85.0, 0.60),
            VerticalPriorLevel(700, 4000.0, 270.0, 60.0, 0.10),
        ),
    )
    return point, native_frame, sample, prior


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-bin", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=25)
    parser.add_argument("--trials", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.samples <= 256:
        parser.error("--samples must be between 1 and 256")
    if not 2 <= args.trials <= 100:
        parser.error("--trials must be between 2 and 100")
    os.environ["FIRESKY_RUST_ENGINE_BIN"] = str(args.engine_bin.resolve())

    with tempfile.TemporaryDirectory(prefix="firesky-hsd-retrieval-bench-") as name:
        point, frame, sample, prior = fixture(Path(name))
        requests = [
            {
                "lat": point.lat,
                "lon": point.lon,
                "distance_km": float(index),
                "bearing_deg": float(index % 360),
                "sample": sample,
                "prior": prior,
                "motion_dx_px_per_hour": 0.25,
                "motion_dy_px_per_hour": -0.5,
            }
            for index in range(args.samples)
        ]

        def native_run():
            return nowcast_hsd_retrieval(frame, requests)

        def python_run():
            return tuple(
                retrieve_cloud_columns(
                    frame,
                    point,
                    float(index),
                    float(index % 360),
                    prior,
                    sample=sample,
                    motion_dx_px_per_hour=0.25,
                    motion_dy_px_per_hour=-0.5,
                )
                for index in range(args.samples)
            )

        # The export call used to prepare the fixture starts the resident
        # child. Close it so the first retrieval number includes native startup.
        close_engine()
        started = time.perf_counter()
        native_run()
        first_native_ms = (time.perf_counter() - started) * 1000.0
        python_run()
        native_times = []
        python_times = []
        for _ in range(args.trials):
            started = time.perf_counter()
            native_run()
            native_times.append((time.perf_counter() - started) * 1000.0)
            started = time.perf_counter()
            python_run()
            python_times.append((time.perf_counter() - started) * 1000.0)

    print(
        json.dumps(
            {
                "contract": "rust-desktop-hsd-retrieval-benchmark-v1",
                "samples": args.samples,
                "trials": args.trials,
                "engine_sha256": hashlib.sha256(
                    args.engine_bin.resolve().read_bytes()
                ).hexdigest(),
                "first_native_ms": first_native_ms,
                "resident_native_median_ms": statistics.median(native_times),
                "python_median_ms": statistics.median(python_times),
                "native_over_python_ratio": (
                    statistics.median(native_times)
                    / max(statistics.median(python_times), 1e-9)
                ),
                "caveats": [
                    "Synthetic local HSD frame and fixed vertical prior.",
                    "Includes persistent JSON-lines request/response and Python object reconstruction.",
                    "Excludes remote HSD listing/range I/O, Satpy calibration and GFS interpolation.",
                    "Release-binary results are not a complete application throughput or P95 claim.",
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
