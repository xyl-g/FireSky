#!/usr/bin/env python3
"""Profile the benchmark's per-scan assessment phase (assess()) with cProfile.

Reuses scripts/bench_nowcast_assessment fixture setup, then profiles:
  1. assess()                          - both per-scan physical assessments
  2. constrain(base)                   - radar/path constraints
  3. native_batch()                    - native batch assessment path
"""
from __future__ import annotations

import cProfile
import io
import os
import pstats
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

os.environ.setdefault(
    "FIRESKY_RUST_ENGINE_BIN", str(ROOT / "engine/target/release/firesky-engine")
)
os.environ.setdefault("FIRESKY_GFS_PRIOR_BACKEND", "rust")
os.environ.setdefault("FIRESKY_HSD_BACKEND", "rust")

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


def main() -> None:
    older_manifest = (DEFAULT_CENTERS[0] / DEFAULT_FIRST_SUFFIX / "manifest.json").resolve()
    latest_manifest = (DEFAULT_CENTERS[0] / DEFAULT_SECOND_SUFFIX / "manifest.json").resolve()
    _, wrapped, request, older, latest = fixture(
        older_manifest, latest_manifest, DEFAULT_LEFT.resolve(), DEFAULT_RIGHT.resolve()
    )
    target = older.valid_time_utc + (latest.valid_time_utc - older.valid_time_utc) / 2
    nowcast = calibrated_only_nowcast_adjustment(
        request, target, 25.0, latest, wrapped,
        source_times_utc=(older.valid_time_utc, latest.valid_time_utc),
        observation_frames=(older, latest),
    )
    aerosol = AerosolReading(
        0.15, "profile", "measured", request.point.lat, request.point.lon
    )
    offsets = sorted(nowcast.observed_features_by_offset)

    def assess():
        return [
            satellite_nowcast_assessment(
                request, target, nowcast, aerosol, offset_minutes=offset
            )
            for offset in offsets
        ]

    base = assess()  # warm

    def constrain(steps):
        constrained = []
        for index, step in enumerate(steps):
            value, _ = apply_nowcast_observation_constraints(
                step, nowcast, step.valid_time_utc,
                path_azimuth_deg=step.solar_azimuth_deg,
                observer_elevation_m=request.point.elevation_m,
                diagnose_nowcast=index == 0,
            )
            constrained.append(value)
        return constrained

    local_event_time = target.astimezone(__import__("zoneinfo").ZoneInfo(request.timezone_name))

    def score(steps):
        return apply_unified_event_quality(
            steps, aerosol.aod550, event_time_local=local_event_time,
            core_window_minutes=15, apply_duration=True,
        )

    from firesky.service import nowcast_observation_assessments

    def native_batch():
        steps, _ = nowcast_observation_assessments(request, nowcast, aerosol, backend="rust")
        return steps

    phases = {
        "assess": lambda: assess(),
        "constrain": lambda: constrain(base),
        "score": lambda: score(base),
        "native_batch": lambda: native_batch(),
    }
    for name, fn in phases.items():
        fn()  # warm each phase
    close_engine()
    for name, fn in phases.items():
        profiler = cProfile.Profile()
        profiler.enable()
        fn()
        profiler.disable()
        stream = io.StringIO()
        stats = pstats.Stats(profiler, stream=stream)
        stats.sort_stats("cumulative")
        stats.print_stats(28)
        text = stream.getvalue()
        # Trim the header boilerplate
        lines = text.splitlines()
        keep = []
        header_seen = False
        for line in lines:
            if "ncalls" in line:
                header_seen = True
            if header_seen:
                keep.append(line)
        print(f"===== PHASE {name} =====")
        print("\n".join(keep[:34]))
        print()
    close_engine()


if __name__ == "__main__":
    main()
