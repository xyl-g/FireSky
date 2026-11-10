"""Transport and native-call bridge for expanded CAMS physical diagnostics."""

import ctypes
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
import tempfile

import numpy as np

from firesky.rust_grid import call_engine
from firesky.tensor_bundle import load_tensor_bundle


def decode_spectral(path):
    from firesky.cams_optics import (
        CamsSpectralOptics, SPECTRAL_AOD_WAVELENGTHS_NM,
        SPECTRAL_EXTINCTION_WAVELENGTHS_NM, SPECTRAL_PRESSURE_LEVELS_HPA,
        SPECTRAL_OPTICS_SCHEMA_VERSION,
    )

    path = Path(path).resolve()
    try:
        with tempfile.TemporaryDirectory(prefix="firesky-rust-spectral-") as temporary:
            output = Path(temporary) / "tensors"
            result = call_engine("decode_cams_spectral_to_files", {
                "path": str(path), "output_dir": str(output),
            })
            if result.get("manifest_path") != str(output / "manifest.json"):
                raise ValueError("Rust spectral output identity mismatch")
            bundle = load_tensor_bundle(result["manifest_path"], kind="cams-spectral-v1", source_path=path)
        arrays, metadata = bundle.arrays, bundle.manifest["metadata"]
        waves = metadata["aod_wavelengths_nm"]
        cells = arrays["latitudes"].size
        valid_time = metadata["valid_time_utc"]
        valid_time = None if valid_time is None else datetime.fromisoformat(valid_time)
        if (
            metadata["schema_version"] != SPECTRAL_OPTICS_SCHEMA_VERSION
            or metadata["decoder_backend"] != "rust-cams-spectral-v1"
            or metadata["score_consumed"] is not False
            or len(waves) != 5 or set(waves) != set(SPECTRAL_AOD_WAVELENGTHS_NM)
            or tuple(metadata["extinction_wavelengths_nm"]) != SPECTRAL_EXTINCTION_WAVELENGTHS_NM
            or tuple(metadata["pressure_levels_hpa"]) != SPECTRAL_PRESSURE_LEVELS_HPA
            or metadata["phase_fields"] != ["ssa550", "asymmetry550"]
            or (valid_time is not None and (
                valid_time.tzinfo is None or valid_time.utcoffset() != timedelta(0)))
            or not 1 <= cells <= 5_000_000
            or set(arrays) != {"latitudes", "longitudes", "aod", "ssa550", "asymmetry550",
                              "aerext355", "aerext532", "aerext1064"}
            or any(arrays[key].shape != (cells,) for key in (
                "latitudes", "longitudes", "ssa550", "asymmetry550"))
            or arrays["aod"].shape != (5, cells)
            or any(arrays[f"aerext{w}"].shape != (8, cells) for w in SPECTRAL_EXTINCTION_WAVELENGTHS_NM)
            or any(value.dtype != np.dtype("<f8") for value in arrays.values())
        ):
            raise ValueError("Rust expanded CAMS tensor contract mismatch")
        for value in arrays.values():
            value.setflags(write=True)
        return CamsSpectralOptics(
            latitudes=arrays["latitudes"], longitudes=arrays["longitudes"],
            aod={w: arrays["aod"][i] for i, w in enumerate(waves)},
            extinction={w: arrays[f"aerext{w}"] for w in SPECTRAL_EXTINCTION_WAVELENGTHS_NM},
            single_scattering_albedo_550=arrays["ssa550"], asymmetry_factor_550=arrays["asymmetry550"],
            pressure_levels_hpa=tuple(metadata["pressure_levels_hpa"]), valid_time=valid_time,
        )
    except (RuntimeError, OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"Rust expanded CAMS decoder rejected input: {exc}") from exc


@lru_cache(maxsize=1)
def _functions():
    from firesky.native_core import native_library

    library = native_library()
    if library is None:
        raise RuntimeError("Rust spectral backend requires scripts/build_native_variants.sh")
    names = ("scalar", "aod", "profile", "nearest", "normalize")
    try:
        functions = {name: getattr(library, f"firesky_spectral_{name}_v1") for name in names}
    except AttributeError as exc:
        raise RuntimeError("Rust spectral symbols missing; rebuild native variants") from exc
    doubles = ctypes.POINTER(ctypes.c_double)
    size = ctypes.c_size_t
    functions["scalar"].argtypes = [ctypes.c_uint32, doubles, doubles]
    functions["aod"].argtypes = [doubles, doubles, size, ctypes.c_double, doubles]
    functions["profile"].argtypes = [
        doubles, doubles, size, ctypes.c_uint32, ctypes.c_double, ctypes.c_double, doubles,
    ]
    functions["nearest"].argtypes = [
        doubles, doubles, size, ctypes.c_double, ctypes.c_double, ctypes.POINTER(size),
    ]
    functions["normalize"].argtypes = [doubles, size, doubles]
    for function in functions.values():
        function.restype = ctypes.c_int32
    return functions


def _pointer(values):
    return values.ctypes.data_as(ctypes.POINTER(ctypes.c_double))


def scalar(operation, *values):
    args = (ctypes.c_double * 4)(*map(float, values))
    output = ctypes.c_double()
    if _functions()["scalar"](operation, args, ctypes.byref(output)) != 0:
        raise ValueError("Rust spectral scalar rejected physical parameters")
    return output.value


def interpolate_aod(aod, wavelength):
    points = list(aod.items())
    if len(points) > 1024:
        raise ValueError("Rust spectral AOD point limit exceeded")
    waves = np.ascontiguousarray([float(key) for key, _ in points], dtype=np.float64)
    values = np.ascontiguousarray([float(value) for _, value in points], dtype=np.float64)
    output = ctypes.c_double()
    if _functions()["aod"](_pointer(waves), _pointer(values), waves.size, float(wavelength), ctypes.byref(output)) != 0:
        raise ValueError("Rust wavelength is outside the positive CAMS AOD range or parameters are invalid")
    return output.value


def profile(values, levels, *, lower=None, upper=None):
    values = np.ascontiguousarray(values, dtype=np.float64)
    levels = np.ascontiguousarray(levels, dtype=np.float64)
    if values.ndim != 1 or levels.shape != values.shape or values.size > 256:
        raise ValueError("extinction and pressure-level arrays must be matching bounded vectors")
    output = np.empty(5, dtype=np.float64)
    mode = 0 if lower is None else 1
    status = _functions()["profile"](
        _pointer(values), _pointer(levels), values.size, mode,
        0.0 if lower is None else float(lower), 0.0 if upper is None else float(upper),
        _pointer(output),
    )
    if status != 0:
        raise ValueError("Rust extinction profile has invalid levels, bounds or insufficient physical coverage")
    if mode == 0:
        return float(output[0])
    from firesky.cams_optics import MiddleLayerExtinctionDiagnostics
    return MiddleLayerExtinctionDiagnostics(
        level_count=int(output[0]), peak_extinction_km_inv=float(output[1]),
        optical_depth=float(output[2]), seasoning_level_fraction=float(output[3]),
        overloaded_level_fraction=float(output[4]),
    )


def nearest(optics, lat, lon):
    from firesky.cams_optics import CamsSpectralPoint

    lats = np.ascontiguousarray(optics.latitudes, dtype=np.float64)
    lons = np.ascontiguousarray(optics.longitudes, dtype=np.float64)
    if lats.ndim != 1 or lons.shape != lats.shape or not 1 <= lats.size <= 5_000_000:
        raise ValueError("nearest CAMS point requires matching bounded coordinates")
    output = ctypes.c_size_t()
    if _functions()["nearest"](
        _pointer(lats), _pointer(lons), lats.size, float(lat), float(lon), ctypes.byref(output),
    ) != 0:
        raise ValueError("Rust nearest spectral point rejected grid dimensions")
    index = output.value
    return CamsSpectralPoint(
        lat=float(optics.latitudes[index]), lon=float(optics.longitudes[index]),
        aod={w: float(v[index]) for w, v in optics.aod.items()},
        extinction={w: tuple(float(v) for v in values[:, index]) for w, values in optics.extinction.items()},
        single_scattering_albedo_550=float(optics.single_scattering_albedo_550[index]),
        asymmetry_factor_550=float(optics.asymmetry_factor_550[index]),
        pressure_levels_hpa=optics.pressure_levels_hpa,
    )


def normalize(values):
    array = np.asarray(values, dtype=np.float64)
    values = np.ascontiguousarray(array)
    if values.size > 5_000_000:
        raise ValueError("Rust longitude normalization cell limit exceeded")
    output = np.empty(array.shape, dtype=np.float64)
    if _functions()["normalize"](_pointer(values), values.size, _pointer(output)) != 0:
        raise ValueError("Rust longitude normalization rejected dimensions")
    return output[()] if array.ndim == 0 else output
