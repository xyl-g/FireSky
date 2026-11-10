"""Explicit opt-in bridge to the standalone Rust engine."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

import numpy as np

from firesky.tensor_bundle import load_tensor_bundle


class EngineOperationError(RuntimeError):
    """A validated engine response rejected an operation, not its transport."""


def engine_binary():
    root = Path(__file__).resolve().parents[2]
    executable = "firesky-engine.exe" if os.name == "nt" else "firesky-engine"
    binary = Path(os.environ.get(
        "FIRESKY_RUST_ENGINE_BIN", root / "engine" / "target" / "release" / executable,
    )).expanduser().resolve()
    if not binary.is_file():
        raise RuntimeError("Rust engine unavailable; run bash scripts/build_rust_engine.sh")
    return binary


def call_engine(method, params, *, timeout_seconds=180):
    binary = engine_binary()
    request = {"id": "gfs", "method": method, "params": params}
    try:
        completed = subprocess.run(
            [str(binary), "serve"], input=json.dumps(request, allow_nan=False) + "\n",
            capture_output=True, text=True, encoding="utf-8", timeout=timeout_seconds, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Rust engine failed: {exc}") from exc
    if completed.returncode != 0:
        raise RuntimeError(f"Rust engine exited {completed.returncode}: {completed.stderr[-2000:]}")
    try:
        message = json.loads(completed.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Rust engine returned an invalid response") from exc
    if not isinstance(message, dict) or message.get("id") != "gfs":
        raise RuntimeError("Rust engine response identity mismatch")
    if message.get("type") != "result":
        raise EngineOperationError(str(message.get("error", {}).get("message") or "Rust engine operation failed"))
    return message["result"]


def decode_gfs_grid(path):
    from firesky.gfs import _DecodedGfsGrid

    path = Path(path).resolve()
    with tempfile.TemporaryDirectory(prefix="firesky-rust-grid-") as temporary:
        output = Path(temporary) / "tensors"
        result = call_engine("decode_gfs_to_files", {"path": str(path), "output_dir": str(output)})
        if result.get("manifest_path") != str(output / "manifest.json"):
            raise RuntimeError("Rust GFS tensor output identity mismatch")
        bundle = load_tensor_bundle(result["manifest_path"], kind="gfs-decoded-v1", source_path=path)
    latitudes = bundle.arrays["latitudes"]
    longitudes = bundle.arrays["longitudes"]
    if latitudes.ndim != 1 or longitudes.shape != latitudes.shape:
        raise RuntimeError("Rust GFS coordinate dimensions are invalid")
    keys = bundle.manifest["metadata"]["field_keys"]
    matrix = bundle.arrays["fields"]
    if matrix.shape != (len(keys), latitudes.size):
        raise RuntimeError("Rust GFS field dimensions are invalid")
    fields = {}
    for identity, values in zip(keys, matrix):
        if (
            not isinstance(identity, list) or len(identity) != 3
            or not isinstance(identity[0], str) or type(identity[1]) is not int
            or not isinstance(identity[2], str)
        ):
            raise RuntimeError("Rust GFS field identity is invalid")
        key = tuple(identity)
        if key in fields:
            raise RuntimeError("Rust GFS duplicate field identity")
        values.setflags(write=False)
        fields[key] = values
    latitudes.setflags(write=False)
    longitudes.setflags(write=False)
    return _DecodedGfsGrid(latitudes, longitudes, fields)


def derive_gfs_tensors(path, output_dir, *, mmap=True):
    """Run GRIB decode and full cloud derivation in Rust, returning owned NPY files."""
    path = Path(path).resolve()
    output = Path(output_dir).absolute()
    result = call_engine("derive_gfs_to_files", {"path": str(path), "output_dir": str(output)})
    if result.get("manifest_path") != str(output / "manifest.json"):
        raise RuntimeError("Rust GFS tensor output identity mismatch")
    bundle = load_tensor_bundle(
        result["manifest_path"], kind="gfs-derived-v1", source_path=path, mmap=mmap,
    )
    metadata = bundle.manifest["metadata"]
    levels = metadata.get("pressure_levels")
    cells = bundle.arrays["latitudes"].size
    if (
        not isinstance(levels, list) or not levels
        or not all(type(level) is int for level in levels)
        or metadata.get("column_fields") != 36 or metadata.get("profile_fields") != 24
        or bundle.arrays["latitudes"].shape != (cells,)
        or bundle.arrays["longitudes"].shape != (cells,)
        or bundle.arrays["columns"].shape != (cells, 36)
        or bundle.arrays["profiles"].shape != (cells, len(levels), 24)
    ):
        raise RuntimeError("Rust GFS derived tensor dimensions are invalid")
    return bundle


def native_snapshot(path, observer, radius_km):
    from firesky.native_core import FIELD_COUNT, NativeFeatureBuffer, NativeSnapshotFeatures

    result = call_engine("prepare_gfs_features", {
        "path": str(Path(path).resolve()), "observer": [observer.lat, observer.lon],
        "radius_km": float(radius_km),
    })
    if result.get("field_count") != FIELD_COUNT:
        raise RuntimeError("Rust GFS feature layout is incompatible")
    rows = np.asarray(result["features"], dtype=np.float64)
    if rows.size == 0:
        rows = np.empty((0, FIELD_COUNT), dtype=np.float64)
    if rows.ndim != 2 or rows.shape[1] != FIELD_COUNT:
        raise RuntimeError("Rust GFS feature dimensions are invalid")
    return NativeSnapshotFeatures(NativeFeatureBuffer.from_numpy(rows))
