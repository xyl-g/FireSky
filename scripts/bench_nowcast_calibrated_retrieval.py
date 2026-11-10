#!/usr/bin/env python3
"""Benchmark the unified calibrated-nowcast retrieval boundary.

The pinned fixture uses a real compact HSD tensor bundle and two decoded GFS
sidecars. It separates decoded prior construction, native file-output RPC,
and Python object reconstruction so the results do not imply a pure-Rust
application or a complete nowcast latency.
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
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from firesky.gfs import decoded_grid_cache_path, load_mmap_decoded_grid
from firesky.geometry import GeoPoint, destination_point
from firesky.models import ForecastRequest
from firesky.rust_desktop_data import nowcast_calibrated_retrieval
from firesky.rust_desktop_process import call_engine, close as close_engine
from firesky.satellite import (
    calibrated_satellite_cloud_retrieval,
    satellite_sample_points,
)
from firesky.service import (
    GfsNowcastVerticalPriorProvider,
    _SharedGfsNowcastDomain,
)
from firesky.satellite_cloud_column import CalibratedSatelliteFrame
from firesky.tensor_bundle import load_tensor_bundle


DEFAULT_HSD = (
    ROOT
    / ".cache/himawari-hsd/.rust-bundles"
    / "frame-d8688b4a39853e83c6ef52e489108a7262c08bc906d475c66a8fafad06b547d9"
    / "manifest.json"
)
DEFAULT_LEFT = (
    ROOT
    / ".cache/gfs/2026-11-07/00Z/china/0p25/b47e8ade5e1275031edf.grib2"
)
DEFAULT_RIGHT = (
    ROOT
    / ".cache/gfs/2026-11-07/00Z/china/0p25/383eb147ec4722f589ae.grib2"
)


def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)

    def pick(fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
        return ordered[index]

    return {
        "min": ordered[0],
        "p50": statistics.median(ordered),
        "p90": pick(0.90),
        "max": ordered[-1],
    }


def fixture(hsd_manifest: Path, left_path: Path, right_path: Path):
    missing = [
        str(path)
        for path in (
            hsd_manifest,
            left_path,
            right_path,
            decoded_grid_cache_path(left_path) / "manifest.json",
            decoded_grid_cache_path(right_path) / "manifest.json",
        )
        if not path.is_file()
    ]
    if missing:
        raise RuntimeError(f"pinned nowcast cache is unavailable: {missing}")

    cycle = datetime(2026, 11, 7, tzinfo=timezone.utc)
    valid_time = datetime(2026, 11, 7, 8, 55, tzinfo=timezone.utc)
    center = GeoPoint(30.2741, 120.1551)

    class Client:
        resolution = "0p25"
        _res_grid_deg = 0.25

    provider = GfsNowcastVerticalPriorProvider(Client())
    domains = {
        hour: _SharedGfsNowcastDomain(
            grid=load_mmap_decoded_grid(decoded_grid_cache_path(path)),
            cache_path=str(path),
            source_url=f"pinned-cache:{hour}",
            bbox=(0.0, 0.0, 0.0, 0.0),
        )
        for hour, path in ((8, left_path), (9, right_path))
    }
    provider._snapshot = lambda *_args: domains[_args[-1]]
    request = ForecastRequest(
        target_date=valid_time.date(),
        timezone_name="UTC",
        point=center,
        radius_km=900.0,
        gfs_cycle_override=cycle,
    )
    by_reference = provider.for_request(request)
    native_context = by_reference.native_retrieval_context(valid_time)
    if native_context is None:
        raise RuntimeError("pinned decoded GFS sidecars did not produce native context")
    manifest = load_tensor_bundle(hsd_manifest, kind="hsd-compact-frame-v1")
    metadata = manifest.manifest["metadata"]
    arrays = manifest.arrays
    frame = CalibratedSatelliteFrame(
        valid_time_utc=datetime.fromisoformat(
            str(metadata["valid_time_utc"]).replace("Z", "+00:00")
        ),
        latitudes=arrays["latitudes"],
        longitudes=arrays["longitudes"],
        bands={
            "B07": arrays["band_b07"],
            "B13": arrays["band_b13"],
            "B15": arrays["band_b15"],
        },
        units=dict(metadata["units"]),
        source=str(metadata["source"]),
        quality=str(metadata["quality"]),
        schema_version=int(metadata["frame_schema_version"]),
        range_bytes=int(metadata["range_bytes"]),
        range_blocks=int(metadata["range_blocks"]),
        native_manifest_path=hsd_manifest,
    )
    return provider, by_reference, request, frame, native_context, center, valid_time


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine-bin",
        type=Path,
        default=ROOT / "engine/target/release/firesky-engine",
    )
    parser.add_argument("--hsd-manifest", type=Path, default=DEFAULT_HSD)
    parser.add_argument("--left", type=Path, default=DEFAULT_LEFT)
    parser.add_argument("--right", type=Path, default=DEFAULT_RIGHT)
    parser.add_argument("--trials", type=int, default=9)
    args = parser.parse_args()
    if not 3 <= args.trials <= 30:
        parser.error("--trials must be between 3 and 30")
    engine = args.engine_bin.resolve()
    if not engine.is_file():
        raise RuntimeError(f"Rust engine is unavailable: {engine}")
    os.environ["FIRESKY_RUST_ENGINE_BIN"] = str(engine)
    os.environ["FIRESKY_GFS_PRIOR_BACKEND"] = "rust"
    close_engine()

    (
        provider,
        by_reference,
        request,
        frame,
        context,
        center,
        valid_time,
    ) = fixture(
        args.hsd_manifest.resolve(),
        args.left.resolve(),
        args.right.resolve(),
    )
    samples = satellite_sample_points(center, 25.0, 900.0)
    points = [destination_point(center, bearing, distance) for bearing, distance in samples]

    def prior_rust_run() -> float:
        os.environ["FIRESKY_GFS_PRIOR_BACKEND"] = "rust"
        started = time.perf_counter()
        values = provider._batch(request, points, valid_time)
        elapsed = (time.perf_counter() - started) * 1000.0
        if len(values) != len(points) or not all(value is not None for value in values):
            raise RuntimeError("decoded prior fixture unexpectedly returned unknown samples")
        return elapsed

    def prior_python_run() -> float:
        os.environ["FIRESKY_GFS_PRIOR_BACKEND"] = "python"
        try:
            started = time.perf_counter()
            values = provider._batch(request, points, valid_time)
            elapsed = (time.perf_counter() - started) * 1000.0
        finally:
            os.environ["FIRESKY_GFS_PRIOR_BACKEND"] = "rust"
        if len(values) != len(points) or not all(value is not None for value in values):
            raise RuntimeError("Python decoded prior fixture unexpectedly returned unknown samples")
        return elapsed

    def native_file_run() -> tuple[float, int, dict[str, float]]:
        samples_now = satellite_sample_points(center, 25.0, 900.0)
        output_root = Path(tempfile.mkdtemp(prefix="firesky-retrieval-bench-"))
        output_path = output_root / "retrieval.json"
        params = {
            "manifest_path": str(frame.native_manifest_path.resolve()),
            "valid_time_utc": frame.valid_time_utc.astimezone(timezone.utc).isoformat(),
            "point_lat": float(center.lat),
            "point_lon": float(center.lon),
            "azimuth_deg": 25.0,
            "radius_km": 900.0,
            "motion_dx_px_per_hour": 0.0,
            "motion_dy_px_per_hour": 0.0,
            "cycle_time_utc": context["cycle_time_utc"].astimezone(timezone.utc).isoformat(),
            "left_forecast_hour": int(context["left_forecast_hour"]),
            "right_forecast_hour": int(context["right_forecast_hour"]),
            "source": "gfs-interpolated-vertical-prior",
            "fraction": float(context["fraction"]),
            "local_radius_km": float(context["local_radius_km"]),
            "left_cache_path": str(Path(context["left_cache_path"]).resolve()),
            "right_cache_path": str(Path(context["right_cache_path"]).resolve()),
            "domain_hash": str(context["domain_hash"]),
            "resolution": str(context["resolution"]),
            "output_path": str(output_path),
        }
        started = time.perf_counter()
        receipt = call_engine("desktop_nowcast_calibrated_retrieval", params)
        payload = output_path.read_bytes()
        elapsed = (time.perf_counter() - started) * 1000.0
        output_path.unlink(missing_ok=True)
        output_root.rmdir()
        if receipt.get("bytes") != len(payload):
            raise RuntimeError("native retrieval receipt byte count differs")
        if receipt.get("sha256") != hashlib.sha256(payload).hexdigest():
            raise RuntimeError("native retrieval receipt digest differs")
        value = json.loads(payload)
        if len(value["priors"]) != len(samples_now):
            raise RuntimeError("native retrieval prior count differs")
        return elapsed, len(payload), value["timings_ms"]

    def wrapper_run():
        started = time.perf_counter()
        result = nowcast_calibrated_retrieval(
            frame=frame,
            point=center,
            azimuth_deg=25.0,
            radius_km=900.0,
            retrieval_context=context,
            return_priors=True,
            return_timings=True,
        )
        elapsed = (time.perf_counter() - started) * 1000.0
        return elapsed, result

    def python_fallback_run():
        os.environ["FIRESKY_HSD_BACKEND"] = "python"
        os.environ["FIRESKY_GFS_PRIOR_BACKEND"] = "python"
        try:
            started = time.perf_counter()
            result = calibrated_satellite_cloud_retrieval(
                frame,
                center,
                25.0,
                by_reference,
                radius_km=900.0,
                return_metrics=True,
                return_priors=True,
            )
            elapsed = (time.perf_counter() - started) * 1000.0
        finally:
            os.environ["FIRESKY_HSD_BACKEND"] = "rust"
            os.environ["FIRESKY_GFS_PRIOR_BACKEND"] = "rust"
        if not result[0] or not result[1]:
            raise RuntimeError("Python fallback fixture is incomplete")
        return elapsed

    _, (
        expected_features,
        expected_sets,
        expected_priors,
        _,
        _,
    ) = wrapper_run()
    if not expected_features or not expected_sets or not all(
        prior is not None for _, _, prior in expected_priors
    ):
        raise RuntimeError("unified retrieval fixture is incomplete")
    if not any(
        item.retrieval_quality != "unknown" for item in expected_sets
    ):
        raise RuntimeError("unified retrieval fixture contains no usable set")

    # Warm all three paths and record one cold unified call before trial data.
    prior_rust_run()
    prior_python_run()
    python_fallback_run()
    native_file_run()
    close_engine()
    cold_wrapper_started = time.perf_counter()
    cold_result = wrapper_run()
    cold_wrapper_ms = (time.perf_counter() - cold_wrapper_started) * 1000.0
    if cold_result[1][4] is None:
        raise RuntimeError("unified retrieval returned no timings")

    prior_trials = []
    python_prior_trials = []
    native_trials = []
    wrapper_trials = []
    python_fallback_trials = []
    native_phases = []
    wrapper_phases = []
    result_bytes = None
    for index in range(args.trials):
        order = (
            prior_rust_run,
            prior_python_run,
            native_file_run,
            wrapper_run,
            python_fallback_run,
        )
        if index % 2:
            order = order[::-1]
        values = {}
        for run in order:
            started = time.perf_counter()
            result = run()
            elapsed = (
                result[0]
                if run in {native_file_run, wrapper_run}
                else result
            )
            values[run] = elapsed
            if run is native_file_run:
                result_bytes = result[1]
                native_phases.append(result[2])
            elif run is wrapper_run:
                wrapper_phases.append(result[1][4])
        prior_trials.append(values[prior_rust_run])
        python_prior_trials.append(values[prior_python_run])
        native_trials.append(values[native_file_run])
        wrapper_trials.append(values[wrapper_run])
        python_fallback_trials.append(values[python_fallback_run])

    phase_names = next(iter(native_phases)).keys()
    output = {
        "contract": "firesky-nowcast-calibrated-retrieval-benchmark-v1",
        "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
        "trials": args.trials,
        "samples": len(samples),
        "features": len(expected_features),
        "column_sets": len(expected_sets),
        "unknown_column_sets": sum(
            item.retrieval_quality == "unknown" for item in expected_sets
        ),
        "result_bytes": result_bytes,
        "cold_wrapper_ms": cold_wrapper_ms,
        "decoded_prior_rust_ms": _percentiles(prior_trials),
        "decoded_prior_python_ms": _percentiles(python_prior_trials),
        "native_file_rpc_ms": _percentiles(native_trials),
        "python_wrapper_ms": _percentiles(wrapper_trials),
        "python_fallback_ms": _percentiles(python_fallback_trials),
        "native_object_reconstruction_ms": _percentiles([
            wrapper - native
            for wrapper, native in zip(wrapper_trials, native_trials, strict=True)
        ]),
        "native_phases_ms": {
            name: _percentiles([phase[name] for phase in native_phases])
            for name in phase_names
        },
        "wrapper_header_phases_ms": {
            name: _percentiles([phase[name] for phase in wrapper_phases])
            for name in phase_names
        },
        "scope": (
            "Warm, cache-local 150-point HSD/GFS retrieval. The file RPC excludes "
            "Python object reconstruction; the wrapper includes it."
        ),
        "excluded": [
            "HSD/CAMS/GFS downloads and cache warming",
            "HSD motion/advection and multi-frame orchestration",
            "final NowcastAdjustment construction and event scoring",
            "HTTP/CLI transport and application UI",
        ],
    }
    close_engine()
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
