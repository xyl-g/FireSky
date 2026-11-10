"""Warm offline L137 reference comparison; synthetic input is not real IFS."""

import argparse
from dataclasses import fields
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import tempfile
import time

import numpy as np

from firesky import ecmwf_ifs as ec
from firesky.rust_ecmwf import decode_full_tensor


def synthetic_file(path):
    import eccodes as codes

    lat = np.repeat(np.linspace(60., 0., 61), 101)
    lon = np.tile(np.linspace(60., 160., 101), 61)
    factor = 1. + .1 * np.sin(lat / 10.) * np.cos(lon / 15.)
    pv = np.concatenate((np.linspace(0., 1000., 138), np.linspace(0., 1., 138)))
    records = [("isobaricInhPa", p, name) for p in ec.ECMWF_IFS_PRESSURE_LEVELS
               for name in ec.ECMWF_IFS_PUBLIC_PRESSURE_PARAMS]
    records += [("hybrid", p, name) for p in range(1, 138) for name in ("cc", "clwc", "ciwc")]
    records += [("hybrid", 0, "lnsp"), ("surface", 0, "z"),
                ("heightAboveGround", 2, "2t"), ("heightAboveGround", 2, "2d")]
    with path.open("wb") as stream:
        for kind, level, name in records:
            handle = codes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
            try:
                for key, value in {
                    "centre": "ecmf", "setLocalDefinition": 1, "localDefinitionNumber": 1,
                    "marsType": "fc", "marsStream": "oper", "Ni": 101, "Nj": 61,
                    "latitudeOfFirstGridPointInDegrees": 60., "latitudeOfLastGridPointInDegrees": 0.,
                    "longitudeOfFirstGridPointInDegrees": 60., "longitudeOfLastGridPointInDegrees": 160.,
                    "iDirectionIncrementInDegrees": 1., "jDirectionIncrementInDegrees": 1.,
                    "shortName": name, "typeOfLevel": kind, "level": level,
                    "dataDate": 20261109, "dataTime": 0, "step": 0 if name == "z" else 6,
                    "packingType": "grid_simple", "bitsPerValue": 24,
                }.items():
                    codes.codes_set(handle, key, value)
                if kind == "hybrid" and level > 0:
                    codes.codes_set(handle, "PVPresent", 1)
                    codes.codes_set_array(handle, "pv", pv)
                    value = .5 if name == "cc" else (1e-5 if name == "clwc" else 2e-5)
                    values = value * factor * (1. + level / 300.)
                else:
                    value = {
                        "gh": 44330. * (1. - (level / 1013.25)**.190263),
                        "r": 80., "t": 260., "w": -.01, "u": 10., "v": -3.,
                        "lnsp": 11.4, "z": 980.665, "2t": 290., "2d": 285.,
                    }[name]
                    values = value * factor
                codes.codes_set_values(handle, values)
                codes.codes_write(handle, stream)
            finally:
                codes.codes_release(handle)


def compare(actual, expected):
    count = 0
    for attribute in fields(ec.EcmwfIfsInputTensor):
        a, b = getattr(actual, attribute.name), getattr(expected, attribute.name)
        if isinstance(b, np.ndarray):
            np.testing.assert_array_equal(a.view("u4"), b.view("u4"), err_msg=attribute.name)
            assert a.flags.writeable == b.flags.writeable, attribute.name
            count += 1
        elif attribute.name == "raw_fields":
            assert a.keys() == b.keys()
            for key in b:
                np.testing.assert_array_equal(a[key].view("u4"), b[key].view("u4"))
                count += 1
        else:
            assert a == b, attribute.name
    return count


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--synthetic", action="store_true")
    source.add_argument("--grib", type=Path)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--rust-benchmark", type=Path,
                        default=Path(__file__).resolve().parents[1] / "engine/target/release/examples/bench_ec_full")
    args = parser.parse_args()
    if not 1 <= args.iterations <= 20:
        parser.error("iterations must be 1..20")
    with tempfile.TemporaryDirectory(prefix="firesky-full-comparison-") as directory:
        os.environ["FIRESKY_ECMWF_DECODE_BACKEND"] = "python"
        path = args.grib or Path(directory) / "synthetic-l137.grib"
        if args.synthetic:
            synthetic_file(path)
        expected = ec.decode_ecmwf_ifs_full_catalog_tensor(path)
        actual = decode_full_tensor(path)
        checked_arrays = compare(actual, expected)
        del actual, expected
        samples = []
        # The parity decode above also warms ecCodes/filesystem.
        for _ in range(args.iterations):
            start = time.perf_counter()
            tensor = ec.decode_ecmwf_ifs_full_catalog_tensor(path)
            samples.append((time.perf_counter() - start) * 1000)
            del tensor
        env = {**os.environ, "PATH": "", "PYTHONPATH": "", "FIRESKY_PYTHON": "/no-python"}
        completed = subprocess.run(
            [str(args.rust_benchmark.resolve()), str(path.resolve()), str(args.iterations)],
            capture_output=True, text=True, env=env, check=True, timeout=300,
        )
        print(json.dumps({
            "fixture": "synthetic ecCodes GRIB, not real IFS weather" if args.synthetic else "caller-supplied stored GRIB",
            "source": {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
            "checked_arrays_bitwise": checked_arrays,
            "python_decode": {"median_ms": statistics.median(samples), "samples_ms": samples},
            "standalone": json.loads(completed.stdout),
            "comparison_scope": "warm complete input decode only; compare python_decode to Rust decode_ms, not Rust total; no full forecast",
        }, indent=2))


if __name__ == "__main__":
    main()
