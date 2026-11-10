#!/usr/bin/env python3
"""Benchmark one- and two-scan native nowcast adjustment assembly.

The pinned fixture uses real compact HSD and decoded GFS caches. It measures
file RPC, Python object reconstruction, the complete wrapper, and the Python
reference path separately; these are not complete download-to-HTTP latencies.
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
from firesky.geometry import GeoPoint
from firesky.models import ForecastRequest
from firesky.rust_desktop_data import (
    nowcast_adjustment,
    nowcast_adjustment_as_model,
)
from firesky.rust_desktop_process import call_engine, close as close_engine
from firesky.satellite import calibrated_only_nowcast_adjustment
from firesky.satellite_cloud_column import CalibratedSatelliteFrame
from firesky.service import (
    GfsNowcastVerticalPriorProvider,
    _SharedGfsNowcastDomain,
)
from firesky.tensor_bundle import load_tensor_bundle


DEFAULT_CENTERS = (
    ROOT / ".cache/himawari-hsd/.rust-bundles",
)
DEFAULT_FIRST_SUFFIX = "frame-d8688b4a39853e83c6ef52e489108a7262c08bc906d475c66a8fafad06b547d9"
DEFAULT_SECOND_SUFFIX = "frame-6a035154d9ea2444cedfbd24e39bc245b75e53604f645122be57e26fd16d4c4c"
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


def _frame(manifest_path: Path) -> CalibratedSatelliteFrame:
    manifest = load_tensor_bundle(manifest_path, kind="hsd-compact-frame-v1")
    metadata = manifest.manifest["metadata"]
    arrays = manifest.arrays
    return CalibratedSatelliteFrame(
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
        native_manifest_path=manifest_path,
    )


def fixture(
    first_manifest: Path,
    second_manifest: Path,
    left_path: Path,
    right_path: Path,
):
    required = (
        first_manifest,
        second_manifest,
        left_path,
        right_path,
        decoded_grid_cache_path(left_path) / "manifest.json",
        decoded_grid_cache_path(right_path) / "manifest.json",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"pinned nowcast cache is unavailable: {missing}")
    frames = sorted(
        (_frame(first_manifest), _frame(second_manifest)),
        key=lambda value: value.valid_time_utc,
    )
    older, latest = frames
    if older.valid_time_utc >= latest.valid_time_utc:
        raise RuntimeError("pinned HSD bundle identities are not unique")

    cycle = datetime(2026, 11, 7, tzinfo=timezone.utc)
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
    # The cache fixture is a 08:55/09:05 pair. Bind both scans to the same
    # decoded f008/f009 interval so this assembly benchmark does not wait on
    # an unrelated f010 grid whose absence would test cache planning instead.
    provider._snapshot = lambda _request, _cycle, hour: domains[min(hour, 9)]
    request = ForecastRequest(
        target_date=latest.valid_time_utc.date(),
        timezone_name="Asia/Shanghai",
        point=center,
        radius_km=900.0,
        gfs_cycle_override=cycle,
    )
    wrapped = provider.for_request(request)
    if wrapped.native_retrieval_context(latest.valid_time_utc) is None:
        raise RuntimeError("pinned decoded GFS sidecars have no native context")
    return provider, wrapped, request, older, latest


def _wrapper_inputs(*, wrapped, request, latest, older, scans):
    frames = (older, latest) if scans == 2 else (latest,)
    return {
        "target_time_utc": older.valid_time_utc
        + (latest.valid_time_utc - older.valid_time_utc) / 2,
        "timezone_name": request.timezone_name,
        "point": request.point,
        "azimuth_deg": 25.0,
        "radius_km": float(request.radius_km),
        "display_frame": latest,
        "frames": frames,
        "retrieval_context_provider": wrapped.native_retrieval_context,
        "source_times_utc": tuple(frame.valid_time_utc for frame in reversed(frames)),
        "motion": None,
        "advection": None,
        "single_historical_observation": False,
    }


def _file_rpc(samples, *, scans: int, engine: Path) -> float:
    import tempfile

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="firesky-nowcast-adjustment-bench-") as root:
        request_value = _wrapper_inputs(**samples, scans=scans)
        frames = []
        for value in request_value["frames"]:
            valid_time = value.valid_time_utc
            frames.append(
                {
                    "manifest_path": str(Path(value.native_manifest_path).resolve()),
                    "valid_time_utc": valid_time.astimezone(timezone.utc).isoformat(),
                    "retrieval": _encoded_context(
                        request_value["retrieval_context_provider"](valid_time)
                    ),
                }
            )
        display = request_value["display_frame"]
        payload = json.dumps(
            {
                "target_time_utc": request_value["target_time_utc"]
                .astimezone(timezone.utc)
                .isoformat(),
                "timezone_name": request_value["timezone_name"],
                "point_lat": float(request_value["point"].lat),
                "point_lon": float(request_value["point"].lon),
                "radius_km": float(request_value["radius_km"]),
                "azimuth_deg": float(request_value["azimuth_deg"]),
                "display_frame": {
                    "manifest_path": str(Path(display.native_manifest_path).resolve()),
                    "valid_time_utc": display.valid_time_utc.astimezone(
                        timezone.utc
                    ).isoformat(),
                    "retrieval": _encoded_context(
                        request_value["retrieval_context_provider"](
                            display.valid_time_utc
                        )
                    ),
                },
                "frames": frames,
                "source_times_utc": [
                    value.astimezone(timezone.utc).isoformat()
                    for value in request_value["source_times_utc"]
                ],
                "motion_dx_px_per_hour": 0.0,
                "motion_dy_px_per_hour": 0.0,
                "motion_correlation": 0.0,
                "motion_quality": "unknown",
                "motion_age_minutes": None,
                "advection": None,
                "single_historical_observation": False,
            },
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        request_path = Path(root) / "request.json"
        output_path = Path(root) / "adjustment.json"
        request_path.write_bytes(payload)
        receipt = call_engine(
            "desktop_nowcast_adjustment",
            {"request_path": str(request_path), "output_path": str(output_path)},
        )
        encoded = output_path.read_bytes()
        if (
            receipt.get("bytes") != len(encoded)
            or receipt.get("sha256") != hashlib.sha256(encoded).hexdigest()
            or receipt.get("request_sha256") != hashlib.sha256(payload).hexdigest()
        ):
            raise RuntimeError("adjustment benchmark receipt differs from its files")
        json.loads(encoded)
    return (time.perf_counter() - started) * 1000.0


def _encoded_context(value):
    return {
        "cycle_time_utc": value["cycle_time_utc"].astimezone(timezone.utc).isoformat(),
        "left_forecast_hour": int(value["left_forecast_hour"]),
        "right_forecast_hour": int(value["right_forecast_hour"]),
        "fraction": float(value["fraction"]),
        "left_cache_path": str(Path(value["left_cache_path"]).resolve()),
        "right_cache_path": str(Path(value["right_cache_path"]).resolve()),
        "local_radius_km": float(value["local_radius_km"]),
        "domain_hash": str(value["domain_hash"]),
        "resolution": str(value["resolution"]),
    }


def _measure(samples, *, scans: int, trials: int, engine: Path):
    inputs = _wrapper_inputs(**samples, scans=scans)

    def wrapper_run():
        started = time.perf_counter()
        value = nowcast_adjustment(**inputs)
        return (time.perf_counter() - started) * 1000.0, value

    def model_run(raw):
        started = time.perf_counter()
        model = nowcast_adjustment_as_model(raw)
        return (time.perf_counter() - started) * 1000.0, model

    def python_fallback_run():
        os.environ["FIRESKY_HSD_BACKEND"] = "python"
        os.environ["FIRESKY_GFS_PRIOR_BACKEND"] = "python"
        try:
            started = time.perf_counter()
            value = calibrated_only_nowcast_adjustment(
                samples["request"],
                inputs["target_time_utc"],
                inputs["azimuth_deg"],
                inputs["display_frame"],
                samples["wrapped"],
                source_times_utc=inputs["source_times_utc"],
                observation_frames=inputs["frames"],
            )
            elapsed = (time.perf_counter() - started) * 1000.0
        finally:
            os.environ["FIRESKY_HSD_BACKEND"] = "rust"
            os.environ["FIRESKY_GFS_PRIOR_BACKEND"] = "rust"
        if value is None:
            raise RuntimeError("Python fallback fixture produced no adjustment")
        return elapsed

    wrapper_run()
    _, first_value = wrapper_run()
    model_run(first_value)
    python_fallback_run()
    rpc_trials = []
    wrapper_trials = []
    model_trials = []
    fallback_trials = []
    output_bytes = None
    for index in range(trials):
        if index % 2 == 0:
            rpc_ms = _file_rpc(samples, scans=scans, engine=engine)
            wrapper_ms, raw = wrapper_run()
        else:
            wrapper_ms, raw = wrapper_run()
            rpc_ms = _file_rpc(samples, scans=scans, engine=engine)
        model_ms, _ = model_run(raw)
        fallback_ms = python_fallback_run()
        output_bytes = len(
            json.dumps(raw, ensure_ascii=False, allow_nan=False).encode()
        )
        rpc_trials.append(rpc_ms)
        wrapper_trials.append(wrapper_ms)
        model_trials.append(model_ms)
        fallback_trials.append(fallback_ms)

    close_engine()
    started = time.perf_counter()
    _, cold_value = wrapper_run()
    cold_wrapper_ms = (time.perf_counter() - started) * 1000.0
    return {
        "scans": scans,
        "observed_features": sum(
            len(value) for value in cold_value["observed_features_by_offset"].values()
        ),
        "column_sets": len(cold_value["cloud_column_sets"]),
        "output_bytes": output_bytes,
        "cold_wrapper_ms": cold_wrapper_ms,
        "file_rpc_ms": _percentiles(rpc_trials),
        "python_wrapper_ms": _percentiles(wrapper_trials),
        "python_object_reconstruction_ms": _percentiles(model_trials),
        "python_fallback_ms": _percentiles(fallback_trials),
        "coverage": first_value["calibrated_sample_coverage_ratio"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine-bin",
        type=Path,
        default=ROOT / "engine/target/release/firesky-engine",
    )
    parser.add_argument("--hsd-root", type=Path, default=DEFAULT_CENTERS[0])
    parser.add_argument("--hsd-first", type=Path)
    parser.add_argument("--hsd-second", type=Path)
    parser.add_argument("--left", type=Path, default=DEFAULT_LEFT)
    parser.add_argument("--right", type=Path, default=DEFAULT_RIGHT)
    parser.add_argument("--trials", type=int, default=7)
    args = parser.parse_args()
    if not 3 <= args.trials <= 30:
        parser.error("--trials must be between 3 and 30")
    engine = args.engine_bin.resolve()
    if not engine.is_file():
        raise RuntimeError(f"Rust engine is unavailable: {engine}")
    first = (args.hsd_first or args.hsd_root / DEFAULT_FIRST_SUFFIX / "manifest.json").resolve()
    second = (args.hsd_second or args.hsd_root / DEFAULT_SECOND_SUFFIX / "manifest.json").resolve()
    os.environ["FIRESKY_RUST_ENGINE_BIN"] = str(engine)
    os.environ["FIRESKY_GFS_PRIOR_BACKEND"] = "rust"
    os.environ["FIRESKY_HSD_BACKEND"] = "rust"
    close_engine()
    samples = fixture(first, second, args.left.resolve(), args.right.resolve())
    provider, wrapped, request, older_frame, latest_frame = samples
    samples = {
        "wrapped": wrapped,
        "request": request,
        "latest": latest_frame,
        "older": older_frame,
    }
    results = {
        "one_scan": _measure(samples, scans=1, trials=args.trials, engine=engine),
        "two_scans": _measure(samples, scans=2, trials=args.trials, engine=engine),
    }
    output = {
        "contract": "firesky-nowcast-adjustment-benchmark-v1",
        "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
        "trials": args.trials,
        "target_time_utc": (
            older_frame.valid_time_utc
            + (latest_frame.valid_time_utc - older_frame.valid_time_utc) / 2
        ).isoformat(),
        "latest_time_utc": latest_frame.valid_time_utc.isoformat(),
        "older_time_utc": older_frame.valid_time_utc.isoformat(),
        "point": {"lat": request.point.lat, "lon": request.point.lon},
        "radius_km": request.radius_km,
        "results": results,
        "scope": (
            "Warm cache-local calibrated HSD/GFS adjustment assembly. File RPC "
            "excludes Python adaptation; the wrapper includes it."
        ),
        "excluded": [
            "HSD/CAMS/GFS downloads and cache warming",
            "motion estimation and advected-frame generation",
            "final event scoring and HTTP/CLI/UI transport",
        ],
    }
    close_engine()
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
