"""Reproducible warm in-memory CAMS Python/native differential benchmark.

This is a reference harness, not a production dependency or full-forecast test.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import time

import numpy as np

from firesky import aerosol
from firesky.national_batch import _map_cams_optics_to_model_grid


def timed(function, iterations):
    function()
    samples = []
    for _ in range(iterations):
        start = time.perf_counter()
        function()
        samples.append((time.perf_counter() - start) * 1000)
    return {"median_ms": statistics.median(samples), "samples_ms": samples}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("grib", type=Path)
    parser.add_argument("--iterations", type=int, default=7)
    parser.add_argument("--bbox", type=float, nargs=4, default=[80, 135, 55, 10],
                        metavar=("WEST", "EAST", "NORTH", "SOUTH"))
    parser.add_argument("--spacing", type=float, default=.25)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 20 or not 0 < args.spacing <= 10:
        parser.error("iterations must be 1..20 and spacing in (0,10]")
    west, east, north, south = args.bbox
    if not (-90 <= south <= north <= 90 and -180 <= west <= east <= 180):
        parser.error("invalid bbox")
    rows = int(np.floor((north - south) / args.spacing)) + 1
    cols = int(np.floor((east - west) / args.spacing)) + 1
    if rows * cols > 500_000:
        parser.error("target grid exceeds 500000 cells")
    lats, lons = np.meshgrid(south + np.arange(rows) * args.spacing,
                             west + np.arange(cols) * args.spacing, indexing="ij")
    os.environ["FIRESKY_CAMS_BACKEND"] = "python"
    grid = aerosol.load_cams_optics_grid(args.grib)
    expected_map = _map_cams_optics_to_model_grid(grid, lats.ravel(), lons.ravel())
    expected_height = aerosol.effective_aerosol_scale_height_km(grid.aerext532_m_inv)
    results = {}
    for backend in ("python", "rust"):
        os.environ["FIRESKY_CAMS_BACKEND"] = backend
        actual = _map_cams_optics_to_model_grid(grid, lats.ravel(), lons.ravel())
        for a, b in zip(actual, expected_map):
            np.testing.assert_array_equal(a, b)
        np.testing.assert_allclose(
            aerosol.effective_aerosol_scale_height_km(grid.aerext532_m_inv),
            expected_height, rtol=3e-12, atol=3e-12,
        )
        results[backend] = {
            "map": timed(lambda: _map_cams_optics_to_model_grid(grid, lats.ravel(), lons.ravel()), args.iterations),
            "scale_heights": timed(lambda: aerosol.effective_aerosol_scale_height_km(grid.aerext532_m_inv), args.iterations),
        }
    payload = args.grib.read_bytes()
    print(json.dumps({
        "scope": "warm in-memory Python/NumPy versus release native C ABI; excludes decode/startup/IPC/files; not full forecast",
        "source": {"path": str(args.grib), "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()},
        "source_cells": grid.lats.size, "target_cells": lats.size,
        "covered_cells": int(np.count_nonzero(expected_map[2])),
        "bbox": args.bbox, "spacing": args.spacing, "iterations": args.iterations,
        "max_scale_height_abs_error": float(np.max(np.abs(
            aerosol.effective_aerosol_scale_height_km(grid.aerext532_m_inv) - expected_height))),
        "results": results,
    }, indent=2))


if __name__ == "__main__":
    main()
