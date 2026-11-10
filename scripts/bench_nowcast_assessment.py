#!/usr/bin/env python3
"""Benchmark the post-retrieval nowcast assessment on pinned local caches.

This measures the Python per-scan physical assessment + radar/path constraint
path separately from the opt-in native batch assessment operation
(FIRESKY_NOWCAST_ASSESSMENT_BACKEND=rust) and the already-native event/instant
quality ledger. It is not a download-to-report or cross-platform benchmark.
"""

from __future__ import annotations

import argparse
from datetime import timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from scripts.bench_nowcast_adjustment import (
    DEFAULT_CENTERS,
    DEFAULT_FIRST_SUFFIX,
    DEFAULT_LEFT,
    DEFAULT_RIGHT,
    DEFAULT_SECOND_SUFFIX,
    fixture,
)
from firesky.models import AerosolReading
from firesky.rust_desktop_process import close as close_engine
from firesky.satellite import calibrated_only_nowcast_adjustment
from firesky.service import (
    apply_nowcast_observation_constraints,
    apply_unified_event_quality,
    satellite_nowcast_assessment,
)


def summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)

    def pick(fraction: float) -> float:
        index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
        return ordered[index]

    return {
        "min_ms": ordered[0],
        "p50_ms": statistics.median(ordered),
        "p90_ms": pick(0.90),
        "max_ms": ordered[-1],
    }


def measure(function, trials: int) -> dict[str, float]:
    function()
    samples = []
    for _ in range(trials):
        started = time.perf_counter_ns()
        function()
        samples.append((time.perf_counter_ns() - started) / 1_000_000.0)
    return summary(samples)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine-bin",
        type=Path,
        default=ROOT / "engine/target/release/firesky-engine",
    )
    parser.add_argument("--trials", type=int, default=7)
    parser.add_argument("--hsd-root", type=Path, default=DEFAULT_CENTERS[0])
    parser.add_argument("--left", type=Path, default=DEFAULT_LEFT)
    parser.add_argument("--right", type=Path, default=DEFAULT_RIGHT)
    args = parser.parse_args()
    if not 3 <= args.trials <= 30:
        parser.error("--trials must be between 3 and 30")
    engine = args.engine_bin.resolve()
    if not engine.is_file():
        raise RuntimeError(f"Rust engine is unavailable: {engine}")

    os.environ["FIRESKY_RUST_ENGINE_BIN"] = str(engine)
    os.environ["FIRESKY_GFS_PRIOR_BACKEND"] = "rust"
    os.environ["FIRESKY_HSD_BACKEND"] = "rust"
    close_engine()
    older_manifest = (
        args.hsd_root / DEFAULT_FIRST_SUFFIX / "manifest.json"
    ).resolve()
    latest_manifest = (
        args.hsd_root / DEFAULT_SECOND_SUFFIX / "manifest.json"
    ).resolve()
    _, wrapped, request, older, latest = fixture(
        older_manifest,
        latest_manifest,
        args.left.resolve(),
        args.right.resolve(),
    )
    target = older.valid_time_utc + (latest.valid_time_utc - older.valid_time_utc) / 2
    nowcast = calibrated_only_nowcast_adjustment(
        request,
        target,
        25.0,
        latest,
        wrapped,
        source_times_utc=(older.valid_time_utc, latest.valid_time_utc),
        observation_frames=(older, latest),
    )
    if nowcast is None:
        raise RuntimeError("pinned nowcast adjustment is unavailable")

    aerosol = AerosolReading(
        0.15,
        "pinned-nowcast-assessment-benchmark",
        "measured",
        request.point.lat,
        request.point.lon,
    )
    offsets = sorted(nowcast.observed_features_by_offset)

    def assess():
        return [
            satellite_nowcast_assessment(
                request,
                target,
                nowcast,
                aerosol,
                offset_minutes=offset,
            )
            for offset in offsets
        ]

    base = assess()

    def constrain(steps):
        constrained = []
        for index, step in enumerate(steps):
            value, diagnosed = apply_nowcast_observation_constraints(
                step,
                nowcast,
                step.valid_time_utc,
                path_azimuth_deg=step.solar_azimuth_deg,
                observer_elevation_m=request.point.elevation_m,
                diagnose_nowcast=index == 0,
            )
            constrained.append(value)
        return constrained

    local_event_time = target.astimezone(ZoneInfo(request.timezone_name))

    def score(steps):
        return apply_unified_event_quality(
            steps,
            aerosol.aod550,
            event_time_local=local_event_time,
            core_window_minutes=15,
            apply_duration=True,
        )

    def constrained_score(steps):
        return score(constrain(steps))

    def post_retrieval():
        return constrained_score(assess())

    # Opt-in native batch assessment (FIRESKY_NOWCAST_ASSESSMENT_BACKEND=rust):
    # one engine call scores every scan and applies the radar constraints with
    # the same field semantics; only the event ledger stays a separate call.
    from firesky.service import nowcast_observation_assessments

    def native_batch():
        steps, diagnosed = nowcast_observation_assessments(
            request, nowcast, aerosol, backend="rust"
        )
        return steps, diagnosed

    def native_post_retrieval():
        steps, _ = native_batch()
        return score(steps)

    assessment_ms = measure(assess, args.trials)
    constraints_ms = measure(lambda: constrain(base), args.trials)
    event_quality_ms = measure(lambda: score(base), args.trials)
    post_retrieval_ms = measure(post_retrieval, args.trials)
    native_batch_ms = measure(lambda: native_batch()[0], args.trials)
    native_post_retrieval_ms = measure(native_post_retrieval, args.trials)
    # The native and python paths must agree on the scored steps; assert the
    # structural equality once so the timings compare the same work.
    native_steps, _ = native_batch()
    python_steps = constrain(assess())
    if native_steps != python_steps:
        raise RuntimeError(
            "native batch assessment does not match the python reference on "
            "the pinned fixture"
        )
    close_engine()
    print(
        json.dumps(
            {
                "contract": "firesky-nowcast-assessment-benchmark-v1",
                "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
                "trials": args.trials,
                "target_time_utc": target.astimezone(timezone.utc).isoformat(),
                "point": {
                    "lat": request.point.lat,
                    "lon": request.point.lon,
                    "elevation_m": request.point.elevation_m,
                },
                "observed_offsets_minutes": offsets,
                "features_by_offset": {
                    str(offset): len(nowcast.observed_features_by_offset[offset])
                    for offset in offsets
                },
                "candidate_counts": {
                    str(offset): step.candidate_count
                    for offset, step in zip(offsets, base, strict=True)
                },
                "diagnostic_reason_counts": {
                    str(offset): len(step.reasons)
                    for offset, step in zip(offsets, base, strict=True)
                },
                "timings": {
                    "all_per_scan_assessments": assessment_ms,
                    "all_observation_constraints": constraints_ms,
                    "native_event_quality_wrapper": event_quality_ms,
                    "post_retrieval_total": post_retrieval_ms,
                    "native_batch_assessment": native_batch_ms,
                    "native_post_retrieval_total": native_post_retrieval_ms,
                },
                "scope": (
                    "Warm local HSD/GFS cache; includes post-retrieval per-scan "
                    "assessment, radar/path constraints and event quality, plus "
                    "the opt-in native batch assessment path. Excludes discovery, "
                    "downloads, calibration, warming, HTTP/report serialization "
                    "and packaging."
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
