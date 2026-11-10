"""Transport-only bridge to strict Rust EC basic/offline-full decoders."""

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
from types import MappingProxyType

import numpy as np

from firesky.rust_grid import call_engine
from firesky.tensor_bundle import load_tensor_bundle


def decode_basic_tensor(
    path, *, bbox=None, expected_cycle_utc=None, expected_forecast_hour=None, resolution="0p25",
):
    return _decode_tensor(
        path, bbox=bbox, expected_cycle_utc=expected_cycle_utc,
        expected_forecast_hour=expected_forecast_hour, resolution=resolution,
    )


def decode_full_tensor(
    path, *, bbox=None, expected_cycle_utc=None, expected_forecast_hour=None, model_levels=None,
    resolution="0p25",
):
    from firesky.ecmwf_ifs import _normalise_model_levels

    levels = _normalise_model_levels(model_levels)
    if len(levels) > (4096 - 58) // 3:
        raise ValueError("Rust EC full catalog exceeds the 4096-message budget")
    return _decode_tensor(
        path, bbox=bbox, expected_cycle_utc=expected_cycle_utc,
        expected_forecast_hour=expected_forecast_hour, resolution=resolution, model_levels=levels,
    )


def _decode_tensor(
    path, *, bbox=None, expected_cycle_utc=None, expected_forecast_hour=None, resolution="0p25",
    model_levels=None,
):
    from firesky.ecmwf_ifs import (
        ECMWF_IFS_BASIC_CONTRACT, ECMWF_IFS_FULL_CONTRACT, ECMWF_IFS_PRESSURE_LEVELS, EcmwfIfsCapabilityError,
        EcmwfIfsInputTensor, _normalise_expected_cycle, _validate_forecast_hour,
    )
    if resolution != "0p25":
        raise ValueError("strict ECMWF IFS decoder supports only 0p25")
    cycle = _normalise_expected_cycle(expected_cycle_utc)
    if expected_forecast_hour is not None:
        _validate_forecast_hour(expected_forecast_hour)
    path = Path(path).resolve()
    full = model_levels is not None
    contract = ECMWF_IFS_FULL_CONTRACT if full else ECMWF_IFS_BASIC_CONTRACT
    try:
        with tempfile.TemporaryDirectory(prefix="firesky-rust-ec-") as temporary:
            output = Path(temporary) / "tensors"
            params = {
                "path": str(path), "output_dir": str(output),
                "bbox": list(bbox) if bbox is not None else None,
                "expected_cycle_utc": cycle.isoformat() if cycle is not None else None,
                "expected_forecast_hour": expected_forecast_hour,
            }
            if full:
                params["model_levels"] = list(model_levels)
            response = call_engine(
                "decode_ecmwf_full_to_files" if full else "decode_ecmwf_basic_to_files", params,
            )
            if response.get("manifest_path") != str(output / "manifest.json"):
                raise ValueError("EC tensor output identity mismatch")
            bundle = load_tensor_bundle(
                response["manifest_path"], kind="ecmwf-full-v1" if full else "ecmwf-basic-v1", source_path=path,
            )
        metadata, arrays = bundle.manifest["metadata"], bundle.arrays
        decoded_cycle = datetime.fromisoformat(metadata["cycle_utc"])
        valid_time = datetime.fromisoformat(metadata["valid_time_utc"])
        hour = metadata["forecast_hour"]
        levels = tuple(metadata["pressure_levels"])
        shape = tuple(metadata["native_grid_shape"])
        cells = arrays["latitudes"].size
        if (
            metadata["contract"] != contract
            or metadata["resolution"] != resolution or metadata["grid_type"] != "regular_ll"
            or decoded_cycle.tzinfo is None or decoded_cycle.utcoffset() != timedelta(0)
            or valid_time != decoded_cycle + timedelta(hours=hour)
            or (cycle is not None and decoded_cycle != cycle)
            or (expected_forecast_hour is not None and hour != expected_forecast_hour)
            or levels != ECMWF_IFS_PRESSURE_LEVELS
            or len(shape) != 2 or not all(type(size) is int and size > 0 for size in shape)
            or cells == 0 or arrays["latitudes"].shape != (cells,)
            or arrays["longitudes"].shape != (cells,)
            or arrays["native_latitudes"].shape != (shape[0] * shape[1],)
            or arrays["native_longitudes"].shape != arrays["native_latitudes"].shape
            or any(value.dtype != np.dtype("<f4") for value in arrays.values())
        ):
            raise ValueError("EC tensor metadata contract mismatch")
        pressure_names = {
            "gh": "geopotential_height_m", "r": "relative_humidity_percent", "t": "temperature_k",
            "w": "omega_pa_s", "u": "u_ms", "v": "v_ms",
        }
        diagnostics = metadata["value_diagnostics"]
        if (
            set(diagnostics) != {"relative_humidity_below_zero_count",
                                 "relative_humidity_above_100_count", "relative_humidity_clipped_count"}
            or not all(type(count) is int and count >= 0 for count in diagnostics.values())
            or diagnostics["relative_humidity_clipped_count"]
            != diagnostics["relative_humidity_below_zero_count"] + diagnostics["relative_humidity_above_100_count"]
        ):
            raise ValueError("EC tensor diagnostics mismatch")
        kwargs = {}
        for parameter, attribute in pressure_names.items():
            values = arrays[parameter]
            if values.shape != (len(levels), cells):
                raise ValueError("EC tensor pressure shape mismatch")
            # Match the reference tensor's mutability contract, not transport storage.
            values.setflags(write=parameter != "r" or diagnostics["relative_humidity_clipped_count"] == 0)
            kwargs[attribute] = values
        for name in ("surface_geopotential", "terrain") + (
            ("lnsp", "surface_temperature", "surface_dewpoint") if full else ("total_cloud",)
        ):
            if arrays[name].shape != (cells,):
                raise ValueError("EC tensor surface shape mismatch")
        raw_keys = metadata["raw_keys"]
        matrix = arrays["raw_fields"]
        required_raw_count = 58 + 3 * len(model_levels) if full else 56
        if len(raw_keys) != required_raw_count or matrix.shape != (len(raw_keys), cells):
            raise ValueError("EC tensor raw-field shape mismatch")
        raw_fields = {}
        for identity, values in zip(raw_keys, matrix):
            if (
                not isinstance(identity, list) or len(identity) != 3
                or type(identity[0]) is not str or type(identity[1]) is not int
                or type(identity[2]) is not str or tuple(identity) in raw_fields
            ):
                raise ValueError("EC tensor raw-field identity mismatch")
            raw_fields[tuple(identity)] = values
        if full:
            if (
                tuple(metadata["model_level_numbers"]) != model_levels
                or metadata["decoder_backend"] != "rust-ec-full-v1"
                or metadata["unsupported_reasons"] != []
                or any(arrays[f"model_{name}"].shape != (len(model_levels), cells) for name in ("cc", "clwc", "ciwc"))
                or any(arrays[name].shape != (len(model_levels) + 1,) for name in ("hybrid_a", "hybrid_b"))
            ):
                raise ValueError("EC tensor hybrid/model-level contract mismatch")
            for name in ("cc", "clwc", "ciwc"):
                arrays[f"model_{name}"].setflags(write=True)
        return EcmwfIfsInputTensor(
            contract=metadata["contract"], cycle_utc=decoded_cycle, valid_time_utc=valid_time,
            forecast_hour=hour, resolution=resolution, grid_type=metadata["grid_type"],
            native_grid_shape=shape, latitudes=arrays["latitudes"], longitudes=arrays["longitudes"],
            native_latitudes=arrays["native_latitudes"], native_longitudes=arrays["native_longitudes"],
            pressure_levels_hpa=levels, **kwargs,
            cloud_fraction_total_percent=None if full else arrays["total_cloud"], cloud_fraction_high_percent=None,
            cloud_fraction_middle_percent=None, cloud_fraction_low_percent=None,
            model_level_numbers=model_levels if full else (),
            model_cloud_fraction=arrays["model_cc"] if full else None,
            model_cloud_liquid_kg_kg=arrays["model_clwc"] if full else None,
            model_cloud_ice_kg_kg=arrays["model_ciwc"] if full else None,
            hybrid_a_pa=arrays["hybrid_a"] if full else None,
            hybrid_b=arrays["hybrid_b"] if full else None, lnsp=arrays["lnsp"] if full else None,
            surface_temperature_k=arrays["surface_temperature"] if full else None,
            surface_dewpoint_k=arrays["surface_dewpoint"] if full else None,
            surface_geopotential_m2_s2=arrays["surface_geopotential"], terrain_elevation_m=arrays["terrain"],
            raw_fields=MappingProxyType(raw_fields), source_file=str(path),
            value_diagnostics=MappingProxyType(diagnostics),
        )
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        raise EcmwfIfsCapabilityError(f"Rust EC {'full' if full else 'basic'} decoder rejected input: {exc}") from exc
