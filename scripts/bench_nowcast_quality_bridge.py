"""Benchmark the two-call and single-call native nowcast quality contracts.

The synthetic rows exercise the complete event ledger and all instant fields.
This is a warm persistent-engine microbenchmark, not a complete nowcast or
end-to-end application latency claim.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import statistics
import sys
import time
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from firesky import national_batch as batch
from firesky.rust_desktop_data import score_nowcast_fields, score_observer_fields
from firesky.rust_desktop_process import close as close_engine


def fixture(count: int) -> tuple[list[SimpleNamespace], np.ndarray]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    steps = [
        SimpleNamespace(
            valid_time_local=base + timedelta(minutes=index * 5),
            forward_view_cloud_field_amount=0.30 + 0.02 * index,
            forward_burnable_cloud_field_amount=0.15 + 0.015 * index,
            observer_local_cloud_field_amount=0.20,
            forward_burnable_cloud_load=0.25 + 0.03 * index,
            forward_burnable_cloud_material_load=0.35 + 0.03 * index,
            forward_burnable_cloud_incoming_aerosol_load=0.30 + 0.025 * index,
            solar_incoming_block_risk=0.01 * index,
            vividness_score=0.60,
            visibility_score=0.80,
            intensity_score=0.20,
            satellite_retrieval_material_evidence=None,
            top_candidate=None,
            top_boundary=None,
            top_cloud_hole=None,
            selected_observer_path_aod550=None,
            selected_inflow_path_aod550=None,
            selected_aerosol_path_variability_score=0.0,
            observer_return_transmission=None,
        )
        for index in range(count)
    ]
    values = np.zeros((count, batch.OUTPUT_FIELD_COUNT), dtype=np.float32)
    samples = np.zeros((count, batch.NATIONAL_SAMPLE_FIELDS), dtype=np.float32)
    for index, step in enumerate(steps):
        values[index, batch.NATIONAL_VIEW_AMOUNT_OFFSET] = (
            step.forward_view_cloud_field_amount
        )
        values[index, batch.NATIONAL_BURN_AMOUNT_OFFSET] = (
            step.forward_burnable_cloud_field_amount
        )
        values[index, batch.NATIONAL_BURN_LOAD_OFFSET] = (
            step.forward_burnable_cloud_load
        )
        values[index, batch.NATIONAL_MATERIAL_LOAD_OFFSET] = (
            step.forward_burnable_cloud_material_load
        )
        values[index, batch.NATIONAL_INCOMING_AEROSOL_LOAD_OFFSET] = (
            step.forward_burnable_cloud_incoming_aerosol_load
        )
        values[index, batch.NATIONAL_INCOMING_BLOCK_OFFSET] = (
            step.solar_incoming_block_risk
        )
        values[index, 5] = step.visibility_score
        values[index, 4] = step.vividness_score
        values[index, batch.NATIONAL_LOCAL_VIEW_OFFSET] = (
            step.observer_local_cloud_field_amount
        )
        samples[index, 5] = 0.2
    rows = np.zeros((count, 18), dtype=np.float64)
    rows[:, :8] = values[
        :,
        [
            batch.NATIONAL_VIEW_AMOUNT_OFFSET,
            batch.NATIONAL_BURN_AMOUNT_OFFSET,
            batch.NATIONAL_BURN_LOAD_OFFSET,
            batch.NATIONAL_MATERIAL_LOAD_OFFSET,
            batch.NATIONAL_INCOMING_AEROSOL_LOAD_OFFSET,
            batch.NATIONAL_INCOMING_BLOCK_OFFSET,
            5,
            4,
        ],
    ]
    rows[:, 8] = np.arange(count, dtype=np.float64) * 5.0
    rows[:, 9] = samples[:, 5]
    rows[:, 10:15] = values[
        :,
        [
            batch.NATIONAL_LOCAL_VIEW_OFFSET,
            batch.NATIONAL_LOCAL_BURN_OFFSET,
            batch.NATIONAL_LOCAL_LOAD_OFFSET,
            batch.NATIONAL_LOCAL_MATERIAL_LOAD_OFFSET,
            batch.NATIONAL_LOCAL_INCOMING_AEROSOL_LOAD_OFFSET,
        ],
    ]
    rows[:, 15:18] = 1.0
    return steps, rows


def two_call(rows: np.ndarray):
    event = score_observer_fields(
        [rows.tolist()],
        mode="scalar",
        apply_duration=True,
        local_columns=True,
        score_ceiling=False,
        radius_km=batch.NATIONAL_ANALYSIS_RADIUS_KM,
    )
    singles = []
    for index in range(len(rows)):
        single = rows[index : index + 1].copy()
        single[0, 17] = 1.0
        singles.append(single.tolist())
    instant = score_observer_fields(
        singles,
        mode="scalar",
        apply_duration=False,
        local_columns=True,
        score_ceiling=False,
        radius_km=batch.NATIONAL_ANALYSIS_RADIUS_KM,
    )
    return event, instant


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-bin", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=15)
    parser.add_argument("--samples", type=int, nargs="+", default=[1, 5, 20, 60, 128])
    args = parser.parse_args()
    if not 5 <= args.trials <= 100:
        parser.error("--trials must be between 5 and 100")
    if any(not 1 <= count <= 4096 for count in args.samples):
        parser.error("--samples values must be between 1 and 4096")
    os.environ["FIRESKY_RUST_ENGINE_BIN"] = str(args.engine_bin.resolve())
    close_engine()
    output = []
    for count in args.samples:
        _, rows = fixture(count)

        def single_call():
            return score_nowcast_fields(
                rows.tolist(),
                apply_duration=True,
                local_columns=True,
                score_ceiling=False,
                radius_km=batch.NATIONAL_ANALYSIS_RADIUS_KM,
                parallel_instants=True,
            )

        two_call(rows)
        single_call()
        old_times = []
        new_times = []
        for _ in range(args.trials):
            started = time.perf_counter()
            old = two_call(rows)
            old_times.append((time.perf_counter() - started) * 1000.0)
            started = time.perf_counter()
            new = single_call()
            new_times.append((time.perf_counter() - started) * 1000.0)
        if old[0]["events"][0] != new["event"]:
            raise RuntimeError("single-call event ledger differs from the two-call reference")
        projected = [
            [
                item["index"],
                item["components"]["burnable_cloud_coverage_peak"],
                item["components"]["forward_burnable_cloud_field_amount_peak"],
                item["components"]["forward_burnable_cloud_load_peak"],
                item["components"]["forward_burnable_cloud_material_load_peak"],
                item["components"][
                    "forward_burnable_cloud_incoming_aerosol_load_mean"
                ],
                item["components"]["forward_burnable_cloud_ratio_peak"],
                item["components"]["brightness_color"],
                item["components"]["cloud_material"],
                item["components"]["quality_aod550"],
            ]
            for item in old[1]["events"]
        ]
        if projected != new["instant_event_projection"]:
            raise RuntimeError("single-call instant event projection differs from the reference")
        instant_fields = [
            "index",
            "pre_response_index",
            "quality_response_scale",
            "quality_response_exponent",
            "quality_response_adjustment",
            "physically_burnable",
            "burnable_cloud_coverage",
            "brightness_color",
            "cloud_material",
            "material_factor",
            "material_adjustment",
            "incoming_aod_illumination_factor",
            "incoming_aod_adjustment",
            "observer_aod_contrast_factor",
            "observer_aod_adjustment",
            "coverage_base_score",
            "raw_quality_index",
            "quality_aod550",
            "local_aod550",
        ]
        instant_reference = [
            [
                float(item[0][field])
                for field in instant_fields
            ]
            for item in old[1]["instant"]
        ]
        if instant_reference != new["instant"]:
            raise RuntimeError("single-call instant values differ from the reference")
        old_payload_bytes = len(
            json.dumps({"rows": [rows.tolist()]})
        ) + len(
            json.dumps(
                {
                    "rows": [
                        np.concatenate((rows[index : index + 1, :17], [[1.0]]), axis=1).tolist()
                        for index in range(count)
                    ]
                }
            )
        )
        new_payload_bytes = len(json.dumps({"rows": rows.tolist()}))
        old_median = statistics.median(old_times)
        new_median = statistics.median(new_times)
        output.append(
            {
                "samples": count,
                "two_call_median_ms": round(old_median, 4),
                "single_call_median_ms": round(new_median, 4),
                "speedup": round(old_median / new_median, 3),
                "two_call_request_bytes": old_payload_bytes,
                "single_call_request_bytes": new_payload_bytes,
                "exact_match": True,
            }
        )
    close_engine()
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
