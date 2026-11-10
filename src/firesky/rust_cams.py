"""Explicit CAMS Rust transport and native-array bridge; no Python optics math."""

import ctypes
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
import tempfile

import numpy as np

from firesky.rust_grid import call_engine
from firesky.tensor_bundle import load_tensor_bundle


def decode_optics(path):
    from firesky.aerosol import CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS, CamsOpticsGrid

    original = Path(path)
    source = original.resolve()
    with tempfile.TemporaryDirectory(prefix="firesky-rust-cams-") as temporary:
        output = Path(temporary) / "tensors"
        result = call_engine("decode_cams_to_files", {
            "path": str(source), "output_dir": str(output),
        })
        if result.get("manifest_path") != str(output / "manifest.json"):
            raise RuntimeError("Rust CAMS tensor output identity mismatch")
        bundle = load_tensor_bundle(result["manifest_path"], kind="cams-optics-v1", source_path=source)
    arrays, metadata = bundle.arrays, bundle.manifest["metadata"]
    valid_time = datetime.fromisoformat(metadata["valid_time_utc"])
    cells = arrays["latitudes"].size
    if (
        tuple(metadata["pressure_levels"]) != CAMS_AEROSOL_PROFILE_PRESSURE_LEVELS
        or metadata["decoder_backend"] != "rust-cams-v1"
        or valid_time.tzinfo is None or valid_time.utcoffset() != timedelta(0)
        or not 1 <= cells <= 5_000_000
        or set(arrays) != {"latitudes", "longitudes", "aod550", "aerext532"}
        or any(arrays[name].shape != (cells,) for name in ("latitudes", "longitudes", "aod550"))
        or arrays["aerext532"].shape != (11, cells)
        or any(value.dtype != np.dtype("<f8") for value in arrays.values())
    ):
        raise RuntimeError("Rust CAMS tensor contract mismatch")
    for value in arrays.values():
        value.setflags(write=True)
    return CamsOpticsGrid(
        path=original, valid_time=valid_time, lats=arrays["latitudes"],
        lons=arrays["longitudes"], aod550=arrays["aod550"],
        aerext532_m_inv=arrays["aerext532"],
    )


@lru_cache(maxsize=1)
def _functions():
    from firesky.native_core import native_library

    library = native_library()
    if library is None:
        raise RuntimeError("Rust CAMS backend requires scripts/build_native_variants.sh")
    try:
        mapping = library.firesky_cams_map_v1
        blending = library.firesky_cams_blend_v1
        heights = library.firesky_cams_scale_heights_v1
    except AttributeError as exc:
        raise RuntimeError("Rust CAMS symbols missing; rebuild native variants") from exc
    doubles = ctypes.POINTER(ctypes.c_double)
    floats = ctypes.POINTER(ctypes.c_float)
    masks = ctypes.POINTER(ctypes.c_uint8)
    size = ctypes.c_size_t
    mapping.argtypes = [doubles] * 4 + [size, doubles, doubles, size, floats, floats, masks]
    blending.argtypes = [floats, floats, size, ctypes.c_float, floats]
    heights.argtypes = [doubles, size, doubles]
    for function in (mapping, blending, heights):
        function.restype = ctypes.c_int32
    return mapping, blending, heights


def _pointer(value, dtype):
    return value.ctypes.data_as(ctypes.POINTER(dtype))


def map_optics(optics, target_lats, target_lons):
    inputs = [np.ascontiguousarray(value, dtype=np.float64) for value in (
        optics.lats, optics.lons, optics.aod550, optics.aerext532_m_inv,
        target_lats, target_lons,
    )]
    lats, lons, aod, extinction, targets, target_longitudes = inputs
    if (
        lats.ndim != 1 or not 1 <= lats.size <= 5_000_000
        or lons.shape != lats.shape or aod.shape != lats.shape
        or extinction.shape != (11, lats.size)
        or targets.ndim != 1 or target_longitudes.shape != targets.shape
        or targets.size > 500_000
    ):
        raise ValueError("Rust CAMS grid dimensions or cell limit exceeded")
    mapped = np.empty(targets.shape, dtype=np.float32)
    profiles = np.empty((targets.size, 11), dtype=np.float32)
    covered = np.empty(targets.shape, dtype=np.uint8)
    status = _functions()[0](
        *[_pointer(value, ctypes.c_double) for value in inputs[:4]], lats.size,
        _pointer(targets, ctypes.c_double), _pointer(target_longitudes, ctypes.c_double),
        targets.size, _pointer(mapped, ctypes.c_float), _pointer(profiles, ctypes.c_float),
        _pointer(covered, ctypes.c_uint8),
    )
    if status != 0:
        raise ValueError("Rust CAMS sampling rejected grid coordinates or dimensions")
    return mapped, profiles, covered.astype(bool)


def blend(left, right, weight):
    left = np.ascontiguousarray(left, dtype=np.float32)
    right = np.ascontiguousarray(right, dtype=np.float32)
    if left.shape != right.shape or left.size > 6_000_000:
        raise ValueError("Rust CAMS temporal tensors must have matching bounded shapes")
    output = np.empty_like(left)
    status = _functions()[1](
        _pointer(left, ctypes.c_float), _pointer(right, ctypes.c_float),
        left.size, float(weight), _pointer(output, ctypes.c_float),
    )
    if status != 0:
        raise ValueError("Rust CAMS temporal interpolation rejected weight or dimensions")
    return output


def scale_heights(values):
    values = np.ascontiguousarray(values, dtype=np.float64)
    if values.ndim < 2 or values.shape[0] != 11:
        raise ValueError("CAMS extinction profile has an unexpected shape")
    cells = values.size // 11
    if cells > 500_000:
        raise ValueError("Rust CAMS scale-height cell limit exceeded")
    output = np.empty(values.shape[1:], dtype=np.float64)
    status = _functions()[2](
        _pointer(values, ctypes.c_double), cells, _pointer(output, ctypes.c_double),
    )
    if status != 0:
        raise ValueError("Rust CAMS scale-height fit rejected dimensions")
    return output
