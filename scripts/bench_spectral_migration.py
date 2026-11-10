"""Warm expanded-CAMS comparison and reproducible synthetic codec workload.

Fixture creation uses the reference environment. The child benchmark executes
only the standalone Rust binary with Python unavailable.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import tempfile
import time

import numpy as np

from firesky import cams_optics as optics


def synthetic_file(path):
    import eccodes as codes

    latitudes = np.repeat(np.linspace(74., -2., 191), 321)
    longitudes = np.tile(np.linspace(40.4, 168.4, 321), 191)
    factor = 1. + .15 * np.sin(latitudes / 10.) * np.cos(longitudes / 15.)
    records = [(f"aod{w}", 0) for w in optics.SPECTRAL_AOD_WAVELENGTHS_NM]
    records += [(f"aerext{w}", p) for w in optics.SPECTRAL_EXTINCTION_WAVELENGTHS_NM
                for p in optics.SPECTRAL_PRESSURE_LEVELS_HPA]
    records += [("ssa550", 0), ("asymmetry550", 0)]
    with path.open("wb") as stream:
        for name, level in records:
            handle = codes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
            try:
                for key, value in {
                    "centre": "ecmf", "shortName": name,
                    "typeOfLevel": "isobaricInhPa" if name.startswith("aerext") else "surface",
                    "level": level, "Ni": 321, "Nj": 191,
                    "latitudeOfFirstGridPointInDegrees": 74.,
                    "longitudeOfFirstGridPointInDegrees": 40.4,
                    "latitudeOfLastGridPointInDegrees": -2.,
                    "longitudeOfLastGridPointInDegrees": 168.4,
                    "iDirectionIncrementInDegrees": .4, "jDirectionIncrementInDegrees": .4,
                    "dataDate": 20261109, "dataTime": 0, "forecastTime": 0,
                    "packingType": "grid_simple", "bitsPerValue": 24,
                }.items():
                    codes.codes_set(handle, key, value)
                if name.startswith("aod"):
                    values = .2 * (550/int(name[3:]))**1.3 * factor
                elif name.startswith("aerext"):
                    height = optics.standard_pressure_height_m(level)
                    values = .0001 * np.exp(-height / 1700.) * (532/int(name[6:]))**1.2 * factor
                elif name == "ssa550":
                    values = .9 + .01 * factor
                else:
                    values = .6 + .02 * factor
                codes.codes_set_values(handle, values)
                codes.codes_write(handle, stream)
            finally:
                codes.codes_release(handle)


def timer(function, iterations):
    function()
    samples = []
    for _ in range(iterations):
        start = time.perf_counter()
        function()
        samples.append((time.perf_counter() - start) * 1000)
    return {"median_ms": statistics.median(samples), "samples_ms": samples}


def diagnostics(grid):
    point = optics.nearest_cams_spectral_point(grid, 30., 120.)
    result = [point.aod_at(w) for w in (440., 500., 550., 670., 865.)]
    result += [point.profile_optical_depth(w) for w in optics.SPECTRAL_EXTINCTION_WAVELENGTHS_NM]
    result += [optics.angstrom_exponent(point.aod),
               optics.henyey_greenstein_phase_function(point.asymmetry_factor_550, 30.),
               optics.direct_aerosol_transmission(point.aod[550], 5.)]
    middle = optics.middle_layer_extinction_diagnostics(point.extinction[532], point.pressure_levels_hpa)
    return np.array(result + [middle.optical_depth, middle.peak_extinction_km_inv])


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--grib", type=Path)
    source.add_argument("--synthetic", action="store_true")
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--rust-benchmark", type=Path,
                        default=Path(__file__).resolve().parents[1] / "engine/target/release/examples/bench_spectral")
    args = parser.parse_args()
    if not 1 <= args.iterations <= 20:
        parser.error("iterations must be 1..20")
    with tempfile.TemporaryDirectory(prefix="firesky-spectral-comparison-") as directory:
        os.environ["FIRESKY_CAMS_BACKEND"] = "python"
        path = args.grib or Path(directory) / "synthetic-61311.grib"
        if args.synthetic:
            synthetic_file(path)
        grid = optics.load_cams_spectral_optics(path)
        expected = diagnostics(grid)
        results = {}
        for backend in ("python", "rust"):
            os.environ["FIRESKY_CAMS_BACKEND"] = backend
            actual = diagnostics(grid)
            np.testing.assert_allclose(actual, expected, rtol=3e-12, atol=3e-12)
            results[backend] = timer(lambda: diagnostics(grid), args.iterations)
        env = {**os.environ, "PATH": "", "PYTHONPATH": "", "FIRESKY_PYTHON": "/no-python"}
        completed = subprocess.run(
            [str(args.rust_benchmark.resolve()), str(path.resolve()), str(args.iterations), "30", "120"],
            env=env, capture_output=True, text=True, check=True, timeout=300,
        )
        payload = path.read_bytes()
        print(json.dumps({
            "fixture": "synthetic ecCodes GRIB, not observed CAMS weather" if args.synthetic else "caller-supplied stored GRIB",
            "source": {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()},
            "cells": grid.latitudes.size,
            "scope": "in-memory point diagnostics, Python/NumPy versus native C ABI; includes bridge overhead, not decode/IPC/files",
            "max_diagnostic_abs_error": float(np.max(np.abs(actual - expected))),
            "in_memory": results, "standalone": json.loads(completed.stdout),
        }, indent=2))


if __name__ == "__main__":
    main()
