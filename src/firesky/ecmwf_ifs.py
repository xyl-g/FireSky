"""Strict, source-independent ECMWF IFS numeric input contracts.

This module has two deliberately separate contracts:

* public-open-data/basic is runnable from the public Open Data product. It
  uses only public pressure-level thermodynamics/winds, public layer cloud
  cover, and surface geopotential.
* full-catalog/offline-bundle is an enhancement contract for an externally
  obtained IFS bundle. It requires model-level cloud water/ice/fraction,
  hybrid A/B coordinates, lnsp, and surface geopotential.

Neither contract imports GFS types or fills a missing IFS field from another
model. The explicit EC point path may consume the basic contract, while the
default GFS and national scoring paths remain separate.
"""

from __future__ import annotations

import json
import math
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np
from eccodes import codes_get, codes_get_array, codes_grib_new_from_file, codes_release

from firesky.storage import cycle_directory
from firesky.bounded_lock import bounded_file_lock


ECMWF_IFS_ADAPTER_VERSION = "ifs-basic-v2"
ECMWF_IFS_CACHE_SCHEMA_VERSION = 2
ECMWF_IFS_MODEL = "ifs"
ECMWF_IFS_RESOLUTION = "0p25"
ECMWF_IFS_FORECAST_CADENCE_HOURS = 3
# The public open-data product publishes 0..144 h every 3 h, then 150..360 h
# every 6 h. Steps like 147 or 153 do not exist and requesting them fails.
ECMWF_IFS_FORECAST_CADENCE_HIGH_HOURS = 6
ECMWF_IFS_FORECAST_CADENCE_HIGH_START_HOUR = 144
ECMWF_IFS_FORECAST_MAX_HOUR = 360


def is_published_ecmwf_ifs_step(hour: int) -> bool:
    """Return whether *hour* exists in the public open-data step grid."""
    if hour < 0 or hour > ECMWF_IFS_FORECAST_MAX_HOUR:
        return False
    if hour <= ECMWF_IFS_FORECAST_CADENCE_HIGH_START_HOUR:
        return hour % ECMWF_IFS_FORECAST_CADENCE_HOURS == 0
    return (
        (hour - ECMWF_IFS_FORECAST_CADENCE_HIGH_START_HOUR)
        % ECMWF_IFS_FORECAST_CADENCE_HIGH_HOURS
        == 0
    )
ECMWF_IFS_BASIC_CONTRACT = "public-open-data/basic"
ECMWF_IFS_PUBLIC_OPEN_DATA_CONTRACT = "public-open-data"
ECMWF_IFS_FULL_CONTRACT = "full-catalog/offline-bundle"
ECMWF_IFS_BASIC_CACHE_SCHEMA = "firesky-ecmwf-ifs-basic-v2"
ECMWF_IFS_FULL_CACHE_SCHEMA = "firesky-ecmwf-ifs-full-catalog-v2"
ECMWF_IFS_REQUEST_TIMEOUT_SECONDS = 90.0
ECMWF_IFS_CACHE_LOCK_TIMEOUT_SECONDS = 300.0
ECMWF_IFS_TENSOR_CACHE_BYTES = 512 * 1024 * 1024
ECMWF_IFS_MAX_RETRIES = 3
ECMWF_IFS_RETRY_AFTER_SECONDS = (1, 5, 2)
ECMWF_IFS_PUBLIC_UNSUPPORTED_REASON = (
    "unsupported_missing_model_level_cloud_fields"
)
ECMWF_IFS_PUBLIC_LAYER_CLOUD_REASON = (
    "public_open_data_only_total_cloud_cover"
)
ECMWF_IFS_GRAVITY_M_S2 = 9.80665

ECMWF_IFS_PRESSURE_LEVELS = (
    200,
    250,
    300,
    400,
    500,
    600,
    700,
    850,
    925,
)

# Public Open Data pressure-level parameters. Cloud liquid/ice/fraction are
# not pressure-level parameters in the IFS public product.
ECMWF_IFS_PUBLIC_PRESSURE_PARAMS = ("gh", "r", "t", "w", "u", "v")
ECMWF_IFS_PRESSURE_PARAMS = ECMWF_IFS_PUBLIC_PRESSURE_PARAMS
"""Cloud fields actually indexed by the official public Open Data product.

The public IFS files expose total cloud cover (TCC) at the surface level. The
HCC/MCC/LCC layer fields are not public Open Data requestable fields, so they
must not appear in a public request or basic required-field contract.
"""
ECMWF_IFS_PUBLIC_CLOUD_PARAMS = ("tcc",)
ECMWF_IFS_CLOUD_PARAMS = ECMWF_IFS_PUBLIC_CLOUD_PARAMS
ECMWF_IFS_MODEL_LEVEL_PARAMS = ("cc", "clwc", "ciwc")
ECMWF_IFS_SURFACE_PARAMS = ("z",)
ECMWF_IFS_DOWNLOAD_PARAMS = (
    ECMWF_IFS_PUBLIC_PRESSURE_PARAMS
    + ECMWF_IFS_PUBLIC_CLOUD_PARAMS
    + ECMWF_IFS_SURFACE_PARAMS
)

# IFS L137 is the default full-catalog contract. Tests and controlled offline
# bundles may pass a smaller complete model-level fixture explicitly.
ECMWF_IFS_MODEL_LEVELS = tuple(range(1, 138))
ECMWF_IFS_MODEL_LEVEL_TYPE = "hybrid"

# These URLs describe public basic data only; none is claimed to expose the
# full ML cloud bundle.
ECMWF_IFS_OPEN_DATA_SOURCES = {
    "aws": "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
    "google": "https://storage.googleapis.com/ecmwf-open-data",
    "ecmwf": "https://data.ecmwf.int/forecasts",
}

FieldKey = tuple[str, int, str]

_CLOUD_LEVEL_TYPE_ALIASES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "tcc": frozenset(
            {"entireAtmosphere", "atmosphere", "entireAtmosphereSingleLayer"}
        ),
        "hcc": frozenset({"highCloudLayer"}),
        "mcc": frozenset({"middleCloudLayer"}),
        "lcc": frozenset({"lowCloudLayer"}),
    }
)
_CLOUD_CANONICAL_LEVEL_TYPES: Mapping[str, str] = MappingProxyType(
    {
        "tcc": "entireAtmosphere",
        "hcc": "highCloudLayer",
        "mcc": "middleCloudLayer",
        "lcc": "lowCloudLayer",
    }
)
_MODEL_LEVEL_TYPES = frozenset({"hybrid", "hybridLevel"})
_SURFACE_LEVEL_TYPES = frozenset({"surface"})
_PRESSURE_LEVEL_TYPES = frozenset({"isobaricInhPa"})


def _basic_required_field_keys() -> tuple[FieldKey, ...]:
    pressure = [
        ("isobaricInhPa", level, parameter)
        for level in ECMWF_IFS_PRESSURE_LEVELS
        for parameter in ECMWF_IFS_PUBLIC_PRESSURE_PARAMS
    ]
    clouds = [
        (_CLOUD_CANONICAL_LEVEL_TYPES[parameter], 0, parameter)
        for parameter in ECMWF_IFS_PUBLIC_CLOUD_PARAMS
    ]
    return tuple(pressure + clouds + [("surface", 0, "z")])


def _full_required_field_keys(model_levels: Sequence[int]) -> tuple[FieldKey, ...]:
    pressure = [
        ("isobaricInhPa", level, parameter)
        for level in ECMWF_IFS_PRESSURE_LEVELS
        for parameter in ECMWF_IFS_PUBLIC_PRESSURE_PARAMS
    ]
    model = [
        (ECMWF_IFS_MODEL_LEVEL_TYPE, level, parameter)
        for level in model_levels
        for parameter in ECMWF_IFS_MODEL_LEVEL_PARAMS
    ]
    return tuple(
        pressure
        + model
        + [
            ("surface", 0, "lnsp"),
            ("surface", 0, "z"),
        ]
    )


ECMWF_IFS_BASIC_REQUIRED_FIELD_KEYS = _basic_required_field_keys()
ECMWF_IFS_BASIC_REQUIRED_FIELDS = frozenset(ECMWF_IFS_BASIC_REQUIRED_FIELD_KEYS)
ECMWF_IFS_FULL_REQUIRED_FIELD_KEYS = _full_required_field_keys(
    ECMWF_IFS_MODEL_LEVELS
)
ECMWF_IFS_FULL_REQUIRED_FIELDS = frozenset(ECMWF_IFS_FULL_REQUIRED_FIELD_KEYS)

# Backwards-compatible names now refer to the runnable public/basic contract.
ECMWF_IFS_REQUIRED_FIELD_KEYS = ECMWF_IFS_BASIC_REQUIRED_FIELD_KEYS
ECMWF_IFS_REQUIRED_FIELDS = ECMWF_IFS_BASIC_REQUIRED_FIELDS

# Surface z is geopotential (m2/s2), never an orography field assumed to be in
# metres. Equivalent ecCodes spellings are accepted explicitly.
ECMWF_IFS_FIELD_UNITS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "gh": frozenset({"gpm", "m"}),
        "z": frozenset(
            {"m**2 s**-2", "m2 s-2", "m^2 s^-2", "m**2 s^-2"}
        ),
        "r": frozenset({"%"}),
        "t": frozenset({"K"}),
        "w": frozenset({"Pa s**-1", "Pa s-1", "Pa s^-1", "Pa/s"}),
        "u": frozenset({"m s**-1", "m s-1", "m s^-1", "m/s"}),
        "v": frozenset({"m s**-1", "m s-1", "m s^-1", "m/s"}),
        "tcc": frozenset({"%", "(0 - 1)", "0 - 1", "1"}),
        "hcc": frozenset({"%", "(0 - 1)", "0 - 1", "1"}),
        "mcc": frozenset({"%", "(0 - 1)", "0 - 1", "1"}),
        "lcc": frozenset({"%", "(0 - 1)", "0 - 1", "1"}),
        "cc": frozenset({"(0 - 1)", "0 - 1", "%", "1"}),
        "clwc": frozenset({"kg kg**-1", "kg kg-1", "kg/kg"}),
        "ciwc": frozenset({"kg kg**-1", "kg kg-1", "kg/kg"}),
        "lnsp": frozenset({"Numeric", "ln(Pa)", "1"}),
    }
)


def _ifs_values_with_bitmap(gid) -> np.ndarray:
    """Decode one IFS value array without converting bitmap gaps to data.

    eccodes returns the product's missingValue sentinel (9999 by default) for
    bitmap gaps, which the previous |v|>1e20 guard never caught. Uses this
    module's codes_* references so test doubles keep working.
    """
    values = np.asarray(codes_get_array(gid, "values"), dtype=np.float32)
    try:
        bitmap_present = bool(int(codes_get(gid, "bitmapPresent")))
    except (KeyError, TypeError, ValueError):
        bitmap_present = False
    if not bitmap_present:
        return values
    try:
        bitmap = np.asarray(codes_get_array(gid, "bitmap"))
    except Exception as exc:
        raise EcmwfIfsCapabilityError(
            "ECMWF IFS GRIB bitmap is declared but cannot be decoded"
        ) from exc
    if bitmap.shape != values.shape:
        raise EcmwfIfsCapabilityError(
            "ECMWF IFS GRIB bitmap and values have different shapes"
        )
    decoded = np.array(values, dtype=np.float32, copy=True)
    decoded[bitmap == 0] = np.nan
    return decoded


def _field_label(key: FieldKey) -> str:
    level_type, level, short_name = key
    return f"{level_type}/{level}/{short_name}"


class EcmwfIfsCapabilityError(RuntimeError):
    """Raised when an IFS file cannot satisfy a strict numeric contract."""


@dataclass(frozen=True)
class EcmwfIfsCapability:
    """Metadata-only result of probing one IFS GRIB or offline bundle."""

    supported: bool
    missing_fields: tuple[str, ...]
    invalid_fields: tuple[str, ...]
    available_fields: tuple[str, ...]
    cycle_utc: datetime | None
    valid_time_utc: datetime | None
    forecast_hour: int | None
    grid_type: str | None
    native_grid_shape: tuple[int, int] | None
    contract: str = ECMWF_IFS_BASIC_CONTRACT
    unsupported_reasons: tuple[str, ...] = ()

    def require(self) -> "EcmwfIfsCapability":
        if self.supported:
            return self
        parts: list[str] = []
        if self.unsupported_reasons:
            parts.append("unsupported=" + ",".join(self.unsupported_reasons))
        if self.missing_fields:
            parts.append("missing fields=" + ",".join(self.missing_fields))
        if self.invalid_fields:
            parts.append("invalid=" + ",".join(self.invalid_fields))
        detail = "; ".join(parts) or "unknown contract failure"
        raise EcmwfIfsCapabilityError(
            "ECMWF IFS strict numeric input contract rejected: " + detail
        )


@dataclass(frozen=True)
class EcmwfIfsInputTensor:
    """Independent IFS tensor at the native EC grid.

    Basic tensors expose public layer cloud cover. Full tensors expose
    model-level cloud fields and hybrid coordinates. Optional fields are
    None when the selected contract does not provide them; no field is
    synthesized to make the other contract appear complete.
    """

    contract: str
    cycle_utc: datetime
    valid_time_utc: datetime
    forecast_hour: int
    resolution: str
    grid_type: str
    native_grid_shape: tuple[int, int]
    latitudes: np.ndarray
    longitudes: np.ndarray
    native_latitudes: np.ndarray
    native_longitudes: np.ndarray
    pressure_levels_hpa: tuple[int, ...]
    geopotential_height_m: np.ndarray
    relative_humidity_percent: np.ndarray
    temperature_k: np.ndarray
    omega_pa_s: np.ndarray
    u_ms: np.ndarray
    v_ms: np.ndarray
    cloud_fraction_total_percent: np.ndarray | None
    cloud_fraction_high_percent: np.ndarray | None
    cloud_fraction_middle_percent: np.ndarray | None
    cloud_fraction_low_percent: np.ndarray | None
    model_level_numbers: tuple[int, ...]
    model_cloud_fraction: np.ndarray | None
    model_cloud_liquid_kg_kg: np.ndarray | None
    model_cloud_ice_kg_kg: np.ndarray | None
    hybrid_a_pa: np.ndarray | None
    hybrid_b: np.ndarray | None
    lnsp: np.ndarray | None
    surface_geopotential_m2_s2: np.ndarray
    terrain_elevation_m: np.ndarray
    raw_fields: Mapping[FieldKey, np.ndarray]
    source_file: str
    value_diagnostics: Mapping[str, int] = MappingProxyType({})

    @property
    def fields(self) -> Mapping[FieldKey, np.ndarray]:
        """Raw field map, provided for inspection and provenance only."""

        return self.raw_fields

    @property
    def cloud_fraction_percent(self) -> np.ndarray:
        """Public total cloud cover; unavailable on ML-only full tensors."""

        if self.cloud_fraction_total_percent is None:
            raise EcmwfIfsCapabilityError(
                "ECMWF IFS tensor has no public total cloud-cover field"
            )
        return self.cloud_fraction_total_percent

    @property
    def cloud_fraction(self) -> np.ndarray:
        """Public total cloud fraction in the unit interval."""

        return self.cloud_fraction_percent / 100.0

    @property
    def cloud_liquid_kg_kg(self) -> np.ndarray:
        if self.model_cloud_liquid_kg_kg is None:
            raise EcmwfIfsCapabilityError(
                "ECMWF IFS basic tensor has no model-level cloud liquid water"
            )
        return self.model_cloud_liquid_kg_kg

    @property
    def cloud_ice_kg_kg(self) -> np.ndarray:
        if self.model_cloud_ice_kg_kg is None:
            raise EcmwfIfsCapabilityError(
                "ECMWF IFS basic tensor has no model-level cloud ice water"
            )
        return self.model_cloud_ice_kg_kg

    @property
    def cloud_layer_fields(self) -> Mapping[str, np.ndarray]:
        """Available EC layer-cloud semantics, without vertical invention."""

        fields: dict[str, np.ndarray] = {}
        for name, values in (
            ("tcc", self.cloud_fraction_total_percent),
            ("hcc", self.cloud_fraction_high_percent),
            ("mcc", self.cloud_fraction_middle_percent),
            ("lcc", self.cloud_fraction_low_percent),
        ):
            if values is not None:
                fields[name] = values
        return MappingProxyType(fields)

    @property
    def model_cloud_fields(self) -> Mapping[str, np.ndarray]:
        fields: dict[str, np.ndarray] = {}
        for name, values in (
            ("cc", self.model_cloud_fraction),
            ("clwc", self.model_cloud_liquid_kg_kg),
            ("ciwc", self.model_cloud_ice_kg_kg),
        ):
            if values is not None:
                fields[name] = values
        return MappingProxyType(fields)

    @property
    def pressure_fields(self) -> Mapping[str, np.ndarray]:
        """Thermodynamic/wind vocabulary for an explicit future integration."""

        fields: dict[str, np.ndarray] = {
            "gh": self.geopotential_height_m,
            "r": self.relative_humidity_percent,
            "t": self.temperature_k,
            "w": self.omega_pa_s,
            "u": self.u_ms,
            "v": self.v_ms,
        }
        if self.cloud_fraction_total_percent is not None:
            fields["tcc"] = self.cloud_fraction_total_percent
        return MappingProxyType(fields)


@dataclass(frozen=True)
class _MessageMetadata:
    key: FieldKey
    raw_key: FieldKey
    raw_short_name: str
    units: str
    cycle_utc: datetime
    valid_time_utc: datetime
    step_hours: int
    grid_type: str
    native_grid_shape: tuple[int, int]
    hybrid_pv: tuple[float, ...] | None


@dataclass(frozen=True)
class _FieldRecord:
    metadata: _MessageMetadata
    values: np.ndarray


_MISSING = object()


def _get_optional(gid: Any, key: str) -> Any:
    try:
        return codes_get(gid, key)
    except Exception:
        return _MISSING


def _get_optional_array(gid: Any, key: str) -> np.ndarray | None:
    try:
        values = np.asarray(codes_get_array(gid, key), dtype=np.float64)
    except Exception:
        return None
    if values.ndim != 1 or values.size == 0 or np.any(~np.isfinite(values)):
        return None
    return values


def _hybrid_pv(gid: Any) -> tuple[float, ...] | None:
    pv = _get_optional_array(gid, "pv")
    if pv is not None:
        return tuple(float(value) for value in pv)
    a = _get_optional_array(gid, "hybridA")
    b = _get_optional_array(gid, "hybridB")
    if a is None or b is None or a.size != b.size:
        return None
    return tuple(float(value) for value in np.concatenate((a, b)))


def _parse_utc_datetime(date_value: Any, time_value: Any) -> datetime | None:
    if date_value is _MISSING or time_value is _MISSING:
        return None
    try:
        date_text = str(int(date_value))
        if len(date_text) != 8:
            return None
        date_part = datetime.strptime(date_text, "%Y%m%d")
        raw_time = int(time_value)
        if raw_time < 24:
            hour, minute = raw_time, 0
        else:
            hour, minute = divmod(raw_time, 100)
        if hour > 23 or minute > 59:
            return None
        return date_part.replace(hour=hour, minute=minute, tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _message_cycle(gid: Any) -> datetime | None:
    data_date = _get_optional(gid, "dataDate")
    data_time = _get_optional(gid, "dataTime")
    if data_date is _MISSING:
        data_date = _get_optional(gid, "date")
    if data_time is _MISSING:
        data_time = _get_optional(gid, "time")
    return _parse_utc_datetime(data_date, data_time)


def _message_step(gid: Any) -> int | None:
    raw_step = _get_optional(gid, "step")
    raw_units = _get_optional(gid, "stepUnits")
    if raw_step is _MISSING or raw_units is _MISSING:
        return None
    try:
        step = float(raw_step)
        units = int(raw_units)
    except (TypeError, ValueError, OverflowError):
        return None
    # GRIB code 1 is hour. Do not reinterpret days/minutes as hours.
    if units != 1 or not math.isfinite(step) or step < 0.0 or not step.is_integer():
        return None
    return int(step)


def _message_valid_time(gid: Any) -> datetime | None:
    return _parse_utc_datetime(
        _get_optional(gid, "validityDate"),
        _get_optional(gid, "validityTime"),
    )


def _normalise_model_levels(
    model_levels: Sequence[int] | None,
) -> tuple[int, ...]:
    levels = tuple(ECMWF_IFS_MODEL_LEVELS if model_levels is None else model_levels)
    if not levels or any(
        isinstance(level, bool) or not isinstance(level, int) or level <= 0
        for level in levels
    ):
        raise ValueError("ECMWF IFS model levels must be positive integers")
    if levels != tuple(range(1, len(levels) + 1)):
        raise ValueError(
            "ECMWF IFS model levels must be the complete contiguous sequence 1..N"
        )
    return levels


def _canonical_key(
    level_type: Any,
    level: int,
    short_name: Any,
) -> tuple[FieldKey | None, str]:
    raw_type = str(level_type)
    raw_short = str(short_name)
    if raw_type in _PRESSURE_LEVEL_TYPES and level > 0:
        if raw_short in ECMWF_IFS_PUBLIC_PRESSURE_PARAMS:
            return ("isobaricInhPa", level, raw_short), raw_short
        # Some official IFS products expose pressure geopotential as z. It
        # has the same semantic slot as gh after z/g conversion.
        if raw_short == "z":
            return ("isobaricInhPa", level, "gh"), raw_short
        return None, raw_short
    if raw_type in _SURFACE_LEVEL_TYPES and level == 0:
        if raw_short in {"z", "lnsp"}:
            return ("surface", 0, raw_short), raw_short
        return None, raw_short
    if level == 0 and raw_short in ECMWF_IFS_PUBLIC_CLOUD_PARAMS:
        for parameter, aliases in _CLOUD_LEVEL_TYPE_ALIASES.items():
            if raw_short == parameter and raw_type in aliases:
                return (
                    _CLOUD_CANONICAL_LEVEL_TYPES[parameter],
                    0,
                    parameter,
                ), raw_short
    if raw_type in _MODEL_LEVEL_TYPES and level > 0:
        if raw_short in ECMWF_IFS_MODEL_LEVEL_PARAMS:
            return ("hybrid", level, raw_short), raw_short
    return None, raw_short


def _contract_required_keys(
    contract: str,
    model_levels: Sequence[int],
) -> tuple[FieldKey, ...]:
    if contract in {ECMWF_IFS_BASIC_CONTRACT, ECMWF_IFS_PUBLIC_OPEN_DATA_CONTRACT}:
        return ECMWF_IFS_BASIC_REQUIRED_FIELD_KEYS
    if contract == ECMWF_IFS_FULL_CONTRACT:
        return _full_required_field_keys(model_levels)
    raise ValueError(f"unknown ECMWF IFS contract: {contract}")


def _metadata_for_message(
    gid: Any,
    *,
    contract: str,
) -> tuple[_MessageMetadata | None, list[str]]:
    invalid: list[str] = []
    level_type = _get_optional(gid, "typeOfLevel")
    level_value = _get_optional(gid, "level")
    short_name = _get_optional(gid, "shortName")
    if level_type is _MISSING or level_value is _MISSING or short_name is _MISSING:
        return None, ["message identity"]
    try:
        level = int(level_value)
    except (TypeError, ValueError, OverflowError):
        return None, ["message identity"]
    key, raw_short = _canonical_key(level_type, level, short_name)
    if key is None:
        return None, invalid
    required = set(
        ECMWF_IFS_BASIC_REQUIRED_FIELD_KEYS
        if contract in {ECMWF_IFS_BASIC_CONTRACT, ECMWF_IFS_PUBLIC_OPEN_DATA_CONTRACT}
        else _full_required_field_keys(ECMWF_IFS_MODEL_LEVELS)
    )
    if contract in {ECMWF_IFS_BASIC_CONTRACT, ECMWF_IFS_PUBLIC_OPEN_DATA_CONTRACT}:
        if key not in required:
            return None, invalid
    elif key[0] == "hybrid":
        # Full-level membership is checked by the probe after all messages
        # have been read, so a controlled fixture can select its level count.
        if key[2] not in ECMWF_IFS_MODEL_LEVEL_PARAMS:
            return None, invalid
    elif key not in required:
        return None, invalid

    units_value = _get_optional(gid, "units")
    units = "" if units_value is _MISSING else str(units_value)
    accepted_units = ECMWF_IFS_FIELD_UNITS.get(raw_short, frozenset())
    if units not in accepted_units:
        invalid.append(f"{_field_label(key)} units={units!r}")

    cycle_utc = _message_cycle(gid)
    step_hours = _message_step(gid)
    if cycle_utc is None:
        invalid.append(f"{_field_label(key)} cycle")
    if step_hours is None:
        invalid.append(f"{_field_label(key)} step")

    message_type = _get_optional(gid, "marsType")
    if message_type is _MISSING:
        message_type = _get_optional(gid, "type")
    if message_type is _MISSING or str(message_type) != "fc":
        invalid.append(f"{_field_label(key)} type")
    stream = _get_optional(gid, "marsStream")
    if stream is _MISSING:
        stream = _get_optional(gid, "stream")
    if stream is _MISSING or str(stream) != "oper":
        invalid.append(f"{_field_label(key)} stream")

    grid_type = _get_optional(gid, "gridType")
    ni = _get_optional(gid, "Ni")
    nj = _get_optional(gid, "Nj")
    try:
        native_shape = (int(nj), int(ni))
    except (TypeError, ValueError, OverflowError):
        native_shape = (0, 0)
    if grid_type is _MISSING or str(grid_type) != "regular_ll":
        invalid.append("grid type")
    if native_shape[0] <= 0 or native_shape[1] <= 0:
        invalid.append("grid shape")

    if cycle_utc is None or step_hours is None:
        return None, invalid
    valid_time_utc = _message_valid_time(gid)
    if valid_time_utc is None:
        invalid.append(f"{_field_label(key)} valid time")
        valid_time_utc = cycle_utc + timedelta(hours=step_hours)
    return (
        _MessageMetadata(
            key=key,
            raw_key=(str(level_type), level, raw_short),
            raw_short_name=raw_short,
            units=units,
            cycle_utc=cycle_utc,
            valid_time_utc=valid_time_utc,
            step_hours=step_hours,
            grid_type=str(grid_type) if grid_type is not _MISSING else "",
            native_grid_shape=native_shape,
            hybrid_pv=_hybrid_pv(gid) if key[0] == "hybrid" else None,
        ),
        invalid,
    )


def _normalise_expected_cycle(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("ECMWF IFS cycle must be aware UTC")
    return value.astimezone(timezone.utc)


def _validate_forecast_hour(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("ECMWF IFS forecast hour must be a non-negative integer")
    if not is_published_ecmwf_ifs_step(value):
        # Fail closed: off-grid steps (e.g. 147 after the 3 h -> 6 h cadence
        # switch at f144) are not published and would 404 on every source.
        raise ValueError(
            "ECMWF IFS forecast hour is not on the published open-data step "
            f"grid (0-144 h every 3 h, then every 6 h up to "
            f"{ECMWF_IFS_FORECAST_MAX_HOUR}): {value}"
        )
    return value


def _probe_file(
    path: Path | str,
    *,
    contract: str,
    model_levels: Sequence[int] | None,
    expected_cycle_utc: datetime | None,
    expected_forecast_hour: int | None,
    resolution: str,
) -> EcmwfIfsCapability:
    if resolution != ECMWF_IFS_RESOLUTION:
        raise ValueError(
            f"strict ECMWF IFS capability probe supports only {ECMWF_IFS_RESOLUTION}"
        )
    levels = _normalise_model_levels(model_levels) if contract == ECMWF_IFS_FULL_CONTRACT else ()
    required_keys = _contract_required_keys(contract, levels)
    file_path = Path(path)
    expected_cycle = _normalise_expected_cycle(expected_cycle_utc)
    if expected_forecast_hour is not None:
        _validate_forecast_hour(expected_forecast_hour)

    available: set[FieldKey] = set()
    invalid: list[str] = []
    messages: list[_MessageMetadata] = []
    try:
        with file_path.open("rb") as handle:
            while True:
                gid = codes_grib_new_from_file(handle)
                if gid is None:
                    break
                try:
                    metadata, message_invalid = _metadata_for_message(
                        gid, contract=contract
                    )
                    invalid.extend(message_invalid)
                    if metadata is None:
                        continue
                    if metadata.key in available:
                        invalid.append(f"duplicate {_field_label(metadata.key)}")
                    available.add(metadata.key)
                    messages.append(metadata)
                finally:
                    codes_release(gid)
    except (OSError, RuntimeError, ValueError) as exc:
        invalid.append(f"read error={exc}")

    cycles = {item.cycle_utc for item in messages}
    if len(cycles) != 1:
        invalid.append("messages do not share one cycle")
    cycle_utc = next(iter(cycles), None)
    if expected_cycle is not None and cycle_utc != expected_cycle:
        invalid.append(f"cycle expected={expected_cycle.isoformat()}")

    forecast_messages = [
        item
        for item in messages
        if not (item.key[0] == "surface" and item.key[2] == "z")
    ]
    forecast_steps = {item.step_hours for item in forecast_messages}
    forecast_hour = (
        next(iter(forecast_steps)) if len(forecast_steps) == 1 else None
    )
    if len(forecast_steps) != 1:
        invalid.append("forecast messages do not share one forecast hour")
    if expected_forecast_hour is not None and forecast_hour != expected_forecast_hour:
        invalid.append(f"forecast hour expected={expected_forecast_hour}")
    valid_times = {item.valid_time_utc for item in forecast_messages}
    valid_time_utc = next(iter(valid_times), None) if len(valid_times) == 1 else None
    if len(valid_times) != 1:
        invalid.append("forecast messages do not share one valid time")
    if cycle_utc is not None and forecast_hour is not None:
        computed_valid_time = cycle_utc + timedelta(hours=forecast_hour)
        if valid_time_utc != computed_valid_time:
            invalid.append("forecast valid time does not equal cycle plus step")
    for item in messages:
        if item.key[0] == "surface" and item.key[2] == "z":
            if item.step_hours not in {0, forecast_hour}:
                invalid.append("surface geopotential step is not static or forecast step")
        elif forecast_hour is not None and item.step_hours != forecast_hour:
            invalid.append(f"{_field_label(item.key)} step differs from forecast")

    grid_types = {item.grid_type for item in messages}
    grid_shapes = {item.native_grid_shape for item in messages}
    grid_type = next(iter(grid_types), None) if len(grid_types) == 1 else None
    native_shape = next(iter(grid_shapes), None) if len(grid_shapes) == 1 else None
    if len(grid_types) != 1:
        invalid.append("messages do not share one grid type")
    if len(grid_shapes) != 1:
        invalid.append("messages do not share one grid shape")

    if contract == ECMWF_IFS_FULL_CONTRACT:
        model_messages = [item for item in messages if item.key[0] == "hybrid"]
        expected_pv_length = 2 * (len(levels) + 1)
        pvs = {item.hybrid_pv for item in model_messages if item.hybrid_pv is not None}
        if not model_messages or len(pvs) != 1:
            invalid.append("missing hybrid A/B coordinates")
        elif len(next(iter(pvs))) != expected_pv_length:
            invalid.append(
                "hybrid A/B coordinate length "
                f"expected={expected_pv_length}"
            )

    missing = tuple(
        _field_label(key) for key in required_keys if key not in available
    )
    unsupported_reasons: list[str] = []
    if contract in {
        ECMWF_IFS_BASIC_CONTRACT,
        ECMWF_IFS_PUBLIC_OPEN_DATA_CONTRACT,
    }:
        # Basic is runnable, but its full ML-cloud enhancement is explicitly
        # unavailable from public Open Data and must never be inferred.
        unsupported_reasons.extend(
            (
                ECMWF_IFS_PUBLIC_UNSUPPORTED_REASON,
                ECMWF_IFS_PUBLIC_LAYER_CLOUD_REASON,
            )
        )
    elif any(key[0] == "hybrid" for key in required_keys) and any(
        key not in available for key in required_keys if key[0] == "hybrid"
    ):
        unsupported_reasons.append(ECMWF_IFS_PUBLIC_UNSUPPORTED_REASON)

    supported = not missing and not invalid
    return EcmwfIfsCapability(
        supported=supported,
        missing_fields=missing,
        invalid_fields=tuple(dict.fromkeys(invalid)),
        available_fields=tuple(sorted(_field_label(key) for key in available)),
        cycle_utc=cycle_utc,
        valid_time_utc=valid_time_utc,
        forecast_hour=forecast_hour,
        grid_type=grid_type,
        native_grid_shape=native_shape,
        contract=contract,
        unsupported_reasons=tuple(dict.fromkeys(unsupported_reasons)),
    )


def probe_ecmwf_ifs_basic_capability(
    path: Path | str,
    *,
    expected_cycle_utc: datetime | None = None,
    expected_forecast_hour: int | None = None,
    resolution: str = ECMWF_IFS_RESOLUTION,
) -> EcmwfIfsCapability:
    """Probe the runnable public Open Data/basic contract."""

    return _probe_file(
        path,
        contract=ECMWF_IFS_BASIC_CONTRACT,
        model_levels=None,
        expected_cycle_utc=expected_cycle_utc,
        expected_forecast_hour=expected_forecast_hour,
        resolution=resolution,
    )


def probe_ecmwf_ifs_capability(
    path: Path | str,
    *,
    expected_cycle_utc: datetime | None = None,
    expected_forecast_hour: int | None = None,
    resolution: str = ECMWF_IFS_RESOLUTION,
    contract: str = ECMWF_IFS_BASIC_CONTRACT,
    model_levels: Sequence[int] | None = None,
) -> EcmwfIfsCapability:
    """Probe an IFS file without decoding value arrays.

    The default is the runnable public/basic contract. Passing
    full-catalog/offline-bundle switches to the strict model-level
    enhancement contract.
    """

    return _probe_file(
        path,
        contract=contract,
        model_levels=model_levels,
        expected_cycle_utc=expected_cycle_utc,
        expected_forecast_hour=expected_forecast_hour,
        resolution=resolution,
    )


def probe_ecmwf_ifs_full_catalog_capability(
    path: Path | str,
    *,
    expected_cycle_utc: datetime | None = None,
    expected_forecast_hour: int | None = None,
    model_levels: Sequence[int] | None = None,
    resolution: str = ECMWF_IFS_RESOLUTION,
) -> EcmwfIfsCapability:
    """Probe an externally obtained full-catalog/offline bundle."""

    return _probe_file(
        path,
        contract=ECMWF_IFS_FULL_CONTRACT,
        model_levels=model_levels,
        expected_cycle_utc=expected_cycle_utc,
        expected_forecast_hour=expected_forecast_hour,
        resolution=resolution,
    )


def require_ecmwf_ifs_capability(
    path: Path | str,
    *,
    expected_cycle_utc: datetime | None = None,
    expected_forecast_hour: int | None = None,
    resolution: str = ECMWF_IFS_RESOLUTION,
    contract: str = ECMWF_IFS_BASIC_CONTRACT,
    model_levels: Sequence[int] | None = None,
) -> EcmwfIfsCapability:
    """Probe and raise before any value decoding or scoring can occur."""

    return probe_ecmwf_ifs_capability(
        path,
        expected_cycle_utc=expected_cycle_utc,
        expected_forecast_hour=expected_forecast_hour,
        resolution=resolution,
        contract=contract,
        model_levels=model_levels,
    ).require()


def require_ecmwf_ifs_full_catalog_capability(
    path: Path | str,
    *,
    expected_cycle_utc: datetime | None = None,
    expected_forecast_hour: int | None = None,
    model_levels: Sequence[int] | None = None,
    resolution: str = ECMWF_IFS_RESOLUTION,
) -> EcmwfIfsCapability:
    return probe_ecmwf_ifs_full_catalog_capability(
        path,
        expected_cycle_utc=expected_cycle_utc,
        expected_forecast_hour=expected_forecast_hour,
        model_levels=model_levels,
        resolution=resolution,
    ).require()


def _readonly(values: np.ndarray) -> np.ndarray:
    result = np.ascontiguousarray(values, dtype=np.float32)
    result.setflags(write=False)
    return result


def _bbox_indices(
    latitudes: np.ndarray,
    longitudes: np.ndarray,
    bbox: tuple[float, float, float, float] | None,
) -> np.ndarray:
    if bbox is None:
        return np.arange(latitudes.size, dtype=np.int64)
    west, east, north, south = (float(value) for value in bbox)
    if not (
        -180.0 <= west <= 180.0
        and -180.0 <= east <= 180.0
        and -90.0 <= south <= 90.0
        and -90.0 <= north <= 90.0
        and south <= north
    ):
        raise ValueError("ECMWF IFS bbox is invalid")
    normalised_lons = (longitudes + 180.0) % 360.0 - 180.0
    mask = (latitudes >= south) & (latitudes <= north)
    if west <= east:
        mask &= (normalised_lons >= west) & (normalised_lons <= east)
    else:
        mask &= (normalised_lons >= west) | (normalised_lons <= east)
    indices = np.flatnonzero(mask)
    if indices.size == 0:
        raise EcmwfIfsCapabilityError(
            f"ECMWF IFS native grid has no cells inside bbox {bbox}"
        )
    return indices


def load_ecmwf_ifs_grid_coordinates(
    path: Path | str,
    *,
    bbox: tuple[float, float, float, float] | None = None,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Read the regular-grid coordinates without decoding field values."""

    file_path = Path(path)
    try:
        with file_path.open("rb") as handle:
            gid = codes_grib_new_from_file(handle)
            if gid is None:
                raise EcmwfIfsCapabilityError("ECMWF IFS file has no messages")
            try:
                if str(codes_get(gid, "gridType")) != "regular_ll":
                    raise EcmwfIfsCapabilityError(
                        "ECMWF IFS cache coordinate grid is not regular_ll"
                    )
                latitudes = np.asarray(
                    codes_get_array(gid, "latitudes"), dtype=np.float32
                )
                longitudes = np.asarray(
                    codes_get_array(gid, "longitudes"), dtype=np.float32
                )
                ni = int(codes_get(gid, "Ni"))
                nj = int(codes_get(gid, "Nj"))
            finally:
                codes_release(gid)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        if isinstance(exc, EcmwfIfsCapabilityError):
            raise
        raise EcmwfIfsCapabilityError(
            f"ECMWF IFS cache coordinate read failed: {file_path}"
        ) from exc
    if (
        latitudes.ndim != 1
        or longitudes.shape != latitudes.shape
        or ni <= 0
        or nj <= 0
        or latitudes.size != ni * nj
    ):
        raise EcmwfIfsCapabilityError("ECMWF IFS cache coordinates have invalid shape")
    indices = _bbox_indices(latitudes, longitudes, bbox)
    selected_latitudes = latitudes[indices]
    selected_longitudes = ((longitudes[indices] + 180.0) % 360.0) - 180.0
    order = np.lexsort((selected_longitudes, selected_latitudes))
    ordered_latitudes = np.asarray(selected_latitudes[order], dtype=np.float32)
    ordered_longitudes = np.asarray(selected_longitudes[order], dtype=np.float32)
    latitude_axis = np.unique(ordered_latitudes)
    longitude_axis = np.unique(ordered_longitudes)
    if latitude_axis.size * longitude_axis.size != ordered_latitudes.size:
        raise EcmwfIfsCapabilityError(
            "ECMWF IFS cache coordinate bbox is not rectangular"
        )
    spacing_candidates = [
        float(np.diff(axis)[0])
        for axis in (latitude_axis, longitude_axis)
        if axis.size > 1
    ]
    if not spacing_candidates:
        raise EcmwfIfsCapabilityError(
            "ECMWF IFS cache coordinates need a two-dimensional grid"
        )
    spacing = spacing_candidates[0]
    if any(
        not np.allclose(np.diff(axis), spacing, rtol=0.0, atol=1e-6)
        for axis in (latitude_axis, longitude_axis)
        if axis.size > 1
    ) or any(
        not math.isclose(value, spacing, rel_tol=0.0, abs_tol=1e-4)
        for value in spacing_candidates[1:]
    ):
        raise EcmwfIfsCapabilityError("ECMWF IFS cache coordinates are not regular")
    return latitude_axis, longitude_axis, float(spacing)


def _validate_native_grid_coordinates(
    latitudes: np.ndarray,
    longitudes: np.ndarray,
    expected_size: int,
) -> None:
    if (
        latitudes.ndim != 1
        or longitudes.ndim != 1
        or latitudes.size != longitudes.size
        or latitudes.size != expected_size
        or np.any(~np.isfinite(latitudes))
        or np.any(~np.isfinite(longitudes))
    ):
        raise EcmwfIfsCapabilityError("ECMWF IFS native coordinate array is invalid")
    normalised_lons = (longitudes + 180.0) % 360.0 - 180.0
    latitude_count = np.unique(latitudes).size
    longitude_count = np.unique(normalised_lons).size
    coordinate_count = np.unique(
        np.column_stack((latitudes, normalised_lons)), axis=0
    ).shape[0]
    if (
        latitude_count * longitude_count != expected_size
        or coordinate_count != expected_size
    ):
        raise EcmwfIfsCapabilityError(
            "ECMWF IFS native coordinate grid has missing or duplicated cells"
        )


def _convert_values(
    metadata: _MessageMetadata,
    values: np.ndarray,
) -> np.ndarray:
    result = np.asarray(values, dtype=np.float32)
    raw_short = metadata.raw_short_name
    units = metadata.units
    if raw_short == "z" and metadata.key[0] == "isobaricInhPa":
        return result / ECMWF_IFS_GRAVITY_M_S2
    if raw_short in ECMWF_IFS_PUBLIC_CLOUD_PARAMS and units != "%":
        return result * 100.0
    if raw_short == "cc" and units == "%":
        return result / 100.0
    return result


def _read_records(
    path: Path | str,
    *,
    contract: str,
    model_levels: Sequence[int] | None,
    bbox: tuple[float, float, float, float] | None = None,
) -> tuple[dict[FieldKey, _FieldRecord], np.ndarray, np.ndarray, np.ndarray]:
    levels = _normalise_model_levels(model_levels) if contract == ECMWF_IFS_FULL_CONTRACT else ()
    required = set(_contract_required_keys(contract, levels))
    records: dict[FieldKey, _FieldRecord] = {}
    native_latitudes: np.ndarray | None = None
    native_longitudes: np.ndarray | None = None
    selected_indices: np.ndarray | None = None
    file_path = Path(path)
    try:
        with file_path.open("rb") as handle:
            while True:
                gid = codes_grib_new_from_file(handle)
                if gid is None:
                    break
                try:
                    metadata, _ = _metadata_for_message(gid, contract=contract)
                    if metadata is None or metadata.key not in required:
                        continue
                    latitudes = np.asarray(
                        codes_get_array(gid, "latitudes"), dtype=np.float32
                    )
                    longitudes = np.asarray(
                        codes_get_array(gid, "longitudes"), dtype=np.float32
                    )
                    expected_size = (
                        metadata.native_grid_shape[0]
                        * metadata.native_grid_shape[1]
                    )
                    if native_latitudes is None:
                        _validate_native_grid_coordinates(
                            latitudes, longitudes, expected_size
                        )
                        native_latitudes = latitudes
                        native_longitudes = longitudes
                        selected_indices = _bbox_indices(
                            native_latitudes, native_longitudes, bbox
                        )
                    elif (
                        latitudes.shape != native_latitudes.shape
                        or longitudes.shape != native_longitudes.shape
                        or not np.array_equal(native_latitudes, latitudes)
                        or not np.array_equal(native_longitudes, longitudes)
                    ):
                        raise EcmwfIfsCapabilityError(
                            "ECMWF IFS messages do not share one native coordinate grid"
                        )
                    # Bitmap-masked decode: eccodes returns the 9999
                    # missingValue sentinel for bitmap gaps, which the old
                    # |v|>1e20 guard never caught.
                    values = _ifs_values_with_bitmap(gid)
                    if values.ndim != 1 or values.size != expected_size:
                        raise EcmwfIfsCapabilityError(
                            f"ECMWF IFS {_field_label(metadata.key)} has invalid value array shape"
                        )
                    if np.any(~np.isfinite(values)):
                        raise EcmwfIfsCapabilityError(
                            f"ECMWF IFS {_field_label(metadata.key)} contains missing values"
                        )
                    if metadata.key in records:
                        raise EcmwfIfsCapabilityError(
                            f"duplicate {_field_label(metadata.key)}"
                        )
                    # ecCodes has already materialized this message's global
                    # values. Keep only the requested cells before retaining
                    # the record so many pressure fields do not accumulate
                    # full-domain arrays in memory.
                    assert selected_indices is not None
                    records[metadata.key] = _FieldRecord(
                        metadata, np.ascontiguousarray(values[selected_indices])
                    )
                finally:
                    codes_release(gid)
    except EcmwfIfsCapabilityError:
        raise
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        raise EcmwfIfsCapabilityError(
            f"ECMWF IFS strict tensor decode failed: {exc}"
        ) from exc
    if native_latitudes is None or native_longitudes is None:
        raise EcmwfIfsCapabilityError("ECMWF IFS file has no native coordinate grid")
    missing = sorted(_field_label(key) for key in required.difference(records))
    if missing:
        raise EcmwfIfsCapabilityError(
            "ECMWF IFS strict tensor decode lost required fields: "
            + ",".join(missing)
        )
    assert selected_indices is not None
    return records, native_latitudes, native_longitudes, selected_indices


def _validate_common_values(
    pressure_arrays: Mapping[str, np.ndarray],
    terrain: np.ndarray,
) -> Mapping[str, int]:
    if np.any(~np.isfinite(terrain)) or np.any(
        (terrain < -500.0) | (terrain > 9000.0)
    ):
        raise EcmwfIfsCapabilityError(
            "ECMWF IFS surface geopotential produces invalid terrain elevation"
        )
    humidity = pressure_arrays["r"]
    if np.any(~np.isfinite(humidity)):
        raise EcmwfIfsCapabilityError("ECMWF IFS relative humidity is invalid")
    low_count = int(np.count_nonzero(humidity < 0.0))
    high_count = int(np.count_nonzero(humidity > 100.0))
    if low_count or high_count:
        # GRIB providers occasionally encode tiny numerical underflow or
        # supersaturation. Keep the raw field for provenance, but expose a
        # physically bounded value to the common scorer.
        pressure_arrays["r"] = _readonly(np.clip(humidity, 0.0, 100.0))
    temperature = pressure_arrays["t"]
    if np.any(~np.isfinite(temperature)) or np.any(
        (temperature < 120.0) | (temperature > 380.0)
    ):
        raise EcmwfIfsCapabilityError("ECMWF IFS temperature is invalid")
    return MappingProxyType(
        {
            "relative_humidity_below_zero_count": low_count,
            "relative_humidity_above_100_count": high_count,
            "relative_humidity_clipped_count": low_count + high_count,
        }
    )


def _base_tensor_kwargs(
    capability: EcmwfIfsCapability,
    *,
    resolution: str,
    file_path: Path,
    native_latitudes: np.ndarray,
    native_longitudes: np.ndarray,
    indices: np.ndarray,
    pressure_arrays: Mapping[str, np.ndarray],
    terrain: np.ndarray,
    surface_geopotential: np.ndarray,
    raw_fields: Mapping[FieldKey, np.ndarray],
    value_diagnostics: Mapping[str, int],
) -> dict[str, Any]:
    selected_lats = _readonly(native_latitudes[indices])
    selected_native_lons = _readonly(native_longitudes[indices])
    selected_lons = _readonly(
        (native_longitudes[indices] + 180.0) % 360.0 - 180.0
    )
    return {
        "contract": capability.contract,
        "cycle_utc": capability.cycle_utc,
        "valid_time_utc": capability.valid_time_utc,
        "forecast_hour": int(capability.forecast_hour),
        "resolution": resolution,
        "grid_type": str(capability.grid_type),
        "native_grid_shape": capability.native_grid_shape,
        "latitudes": selected_lats,
        "longitudes": selected_lons,
        "native_latitudes": _readonly(native_latitudes),
        "native_longitudes": _readonly(native_longitudes),
        "pressure_levels_hpa": ECMWF_IFS_PRESSURE_LEVELS,
        "geopotential_height_m": pressure_arrays["gh"],
        "relative_humidity_percent": pressure_arrays["r"],
        "temperature_k": pressure_arrays["t"],
        "omega_pa_s": pressure_arrays["w"],
        "u_ms": pressure_arrays["u"],
        "v_ms": pressure_arrays["v"],
        "surface_geopotential_m2_s2": surface_geopotential,
        "terrain_elevation_m": terrain,
        "raw_fields": MappingProxyType(dict(raw_fields)),
        "source_file": str(file_path.resolve()),
        "value_diagnostics": MappingProxyType(dict(value_diagnostics)),
    }


def _raw_fields(
    records: Mapping[FieldKey, _FieldRecord],
) -> Mapping[FieldKey, np.ndarray]:
    fields: dict[FieldKey, np.ndarray] = {}
    for record in records.values():
        fields[record.metadata.raw_key] = _readonly(record.values)
    return fields


def decode_ecmwf_ifs_basic_tensor(
    path: Path | str,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    expected_cycle_utc: datetime | None = None,
    expected_forecast_hour: int | None = None,
    resolution: str = ECMWF_IFS_RESOLUTION,
) -> EcmwfIfsInputTensor:
    """Decode the runnable EC-only public/basic tensor."""

    capability = require_ecmwf_ifs_capability(
        path,
        expected_cycle_utc=expected_cycle_utc,
        expected_forecast_hour=expected_forecast_hour,
        resolution=resolution,
        contract=ECMWF_IFS_BASIC_CONTRACT,
    )
    file_path = Path(path)
    records, native_latitudes, native_longitudes, indices = _read_records(
        file_path,
        contract=ECMWF_IFS_BASIC_CONTRACT,
        model_levels=None,
        bbox=bbox,
    )

    def field(key: FieldKey) -> np.ndarray:
        record = records[key]
        return _readonly(_convert_values(record.metadata, record.values))

    pressure_arrays = {
        parameter: np.stack(
            [
                field(("isobaricInhPa", level, parameter))
                for level in ECMWF_IFS_PRESSURE_LEVELS
            ],
            axis=0,
        )
        for parameter in ECMWF_IFS_PUBLIC_PRESSURE_PARAMS
    }
    cloud_arrays = {
        parameter: field(
            (_CLOUD_CANONICAL_LEVEL_TYPES[parameter], 0, parameter)
        )
        for parameter in ECMWF_IFS_PUBLIC_CLOUD_PARAMS
    }
    surface_geopotential = field(("surface", 0, "z"))
    terrain = _readonly(surface_geopotential / ECMWF_IFS_GRAVITY_M_S2)
    value_diagnostics = _validate_common_values(pressure_arrays, terrain)
    for parameter, values in cloud_arrays.items():
        if np.any(~np.isfinite(values)) or np.any(
            (values < 0.0) | (values > 100.0)
        ):
            raise EcmwfIfsCapabilityError(
                f"ECMWF IFS public {parameter} cloud cover is invalid"
            )
    kwargs = _base_tensor_kwargs(
        capability,
        resolution=resolution,
        file_path=file_path,
        native_latitudes=native_latitudes,
        native_longitudes=native_longitudes,
        indices=indices,
        pressure_arrays=pressure_arrays,
        terrain=terrain,
        surface_geopotential=surface_geopotential,
        raw_fields=_raw_fields(records),
        value_diagnostics=value_diagnostics,
    )
    kwargs.update(
        {
            "cloud_fraction_total_percent": cloud_arrays["tcc"],
            "cloud_fraction_high_percent": None,
            "cloud_fraction_middle_percent": None,
            "cloud_fraction_low_percent": None,
            "model_level_numbers": (),
            "model_cloud_fraction": None,
            "model_cloud_liquid_kg_kg": None,
            "model_cloud_ice_kg_kg": None,
            "hybrid_a_pa": None,
            "hybrid_b": None,
            "lnsp": None,
        }
    )
    return EcmwfIfsInputTensor(**kwargs)


def decode_ecmwf_ifs_full_catalog_tensor(
    path: Path | str,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    expected_cycle_utc: datetime | None = None,
    expected_forecast_hour: int | None = None,
    model_levels: Sequence[int] | None = None,
    resolution: str = ECMWF_IFS_RESOLUTION,
) -> EcmwfIfsInputTensor:
    """Decode an externally obtained full-catalog/offline IFS bundle."""

    levels = _normalise_model_levels(model_levels)
    capability = require_ecmwf_ifs_full_catalog_capability(
        path,
        expected_cycle_utc=expected_cycle_utc,
        expected_forecast_hour=expected_forecast_hour,
        model_levels=levels,
        resolution=resolution,
    )
    file_path = Path(path)
    records, native_latitudes, native_longitudes, indices = _read_records(
        file_path,
        contract=ECMWF_IFS_FULL_CONTRACT,
        model_levels=levels,
        bbox=bbox,
    )

    def field(key: FieldKey) -> np.ndarray:
        record = records[key]
        return _readonly(_convert_values(record.metadata, record.values))

    pressure_arrays = {
        parameter: np.stack(
            [
                field(("isobaricInhPa", level, parameter))
                for level in ECMWF_IFS_PRESSURE_LEVELS
            ],
            axis=0,
        )
        for parameter in ECMWF_IFS_PUBLIC_PRESSURE_PARAMS
    }
    model_arrays = {
        parameter: np.stack(
            [field(("hybrid", level, parameter)) for level in levels],
            axis=0,
        )
        for parameter in ECMWF_IFS_MODEL_LEVEL_PARAMS
    }
    surface_geopotential = field(("surface", 0, "z"))
    terrain = _readonly(surface_geopotential / ECMWF_IFS_GRAVITY_M_S2)
    value_diagnostics = _validate_common_values(pressure_arrays, terrain)
    cloud_fraction = model_arrays["cc"]
    if np.any(~np.isfinite(cloud_fraction)) or np.any(
        (cloud_fraction < 0.0) | (cloud_fraction > 1.0)
    ):
        raise EcmwfIfsCapabilityError("ECMWF IFS model-level cloud fraction is invalid")
    for parameter in ("clwc", "ciwc"):
        values = model_arrays[parameter]
        if np.any(~np.isfinite(values)) or np.any(values < 0.0):
            raise EcmwfIfsCapabilityError(
                f"ECMWF IFS model-level {parameter} is invalid"
            )

    pv_values = {
        records[("hybrid", level, "cc")].metadata.hybrid_pv for level in levels
    }
    if len(pv_values) != 1 or None in pv_values:
        raise EcmwfIfsCapabilityError("ECMWF IFS model-level hybrid A/B is invalid")
    pv = np.asarray(next(iter(pv_values)), dtype=np.float32)
    half = pv.size // 2
    hybrid_a = _readonly(pv[:half])
    hybrid_b = _readonly(pv[half:])
    lnsp = field(("surface", 0, "lnsp"))
    if np.any(~np.isfinite(lnsp)):
        raise EcmwfIfsCapabilityError("ECMWF IFS lnsp contains invalid values")

    kwargs = _base_tensor_kwargs(
        capability,
        resolution=resolution,
        file_path=file_path,
        native_latitudes=native_latitudes,
        native_longitudes=native_longitudes,
        indices=indices,
        pressure_arrays=pressure_arrays,
        terrain=terrain,
        surface_geopotential=surface_geopotential,
        raw_fields=_raw_fields(records),
        value_diagnostics=value_diagnostics,
    )
    kwargs.update(
        {
            "cloud_fraction_total_percent": None,
            "cloud_fraction_high_percent": None,
            "cloud_fraction_middle_percent": None,
            "cloud_fraction_low_percent": None,
            "model_level_numbers": levels,
            "model_cloud_fraction": model_arrays["cc"],
            "model_cloud_liquid_kg_kg": model_arrays["clwc"],
            "model_cloud_ice_kg_kg": model_arrays["ciwc"],
            "hybrid_a_pa": hybrid_a,
            "hybrid_b": hybrid_b,
            "lnsp": lnsp,
        }
    )
    return EcmwfIfsInputTensor(**kwargs)


def decode_ecmwf_ifs_tensor(
    path: Path | str,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    expected_cycle_utc: datetime | None = None,
    expected_forecast_hour: int | None = None,
    resolution: str = ECMWF_IFS_RESOLUTION,
    contract: str = ECMWF_IFS_BASIC_CONTRACT,
    model_levels: Sequence[int] | None = None,
) -> EcmwfIfsInputTensor:
    """Decode either explicit contract; default is runnable public/basic."""

    if contract == ECMWF_IFS_FULL_CONTRACT:
        return decode_ecmwf_ifs_full_catalog_tensor(
            path,
            bbox=bbox,
            expected_cycle_utc=expected_cycle_utc,
            expected_forecast_hour=expected_forecast_hour,
            model_levels=model_levels,
            resolution=resolution,
        )
    return decode_ecmwf_ifs_basic_tensor(
        path,
        bbox=bbox,
        expected_cycle_utc=expected_cycle_utc,
        expected_forecast_hour=expected_forecast_hour,
        resolution=resolution,
    )


def _cache_path(cache_dir: Path, cycle_utc: datetime, forecast_hour: int) -> Path:
    target_dir = cycle_directory(cache_dir, cycle_utc)
    return target_dir / (
        f"ifs-basic-v{ECMWF_IFS_CACHE_SCHEMA_VERSION}-"
        f"{cycle_utc:%Y%m%d%H}-f{forecast_hour:03d}.grib2"
    )


def full_catalog_cache_path(
    cache_dir: Path | str,
    cycle_utc: datetime,
    forecast_hour: int,
) -> Path:
    """Return the separate offline-bundle cache name; it is never downloaded."""

    cycle = _normalise_expected_cycle(cycle_utc)
    if cycle is None:
        raise ValueError("ECMWF IFS cycle is required")
    _validate_forecast_hour(forecast_hour)
    return cycle_directory(Path(cache_dir), cycle) / (
        f"ifs-full-catalog-v{ECMWF_IFS_CACHE_SCHEMA_VERSION}-"
        f"{cycle:%Y%m%d%H}-f{forecast_hour:03d}.grib2"
    )


def _cache_metadata_path(path: Path) -> Path:
    return path.with_name(path.name + ".json")


def _configure_bounded_ecmwf_client(client: Any) -> None:
    """Bound index and GRIB requests made by one Open Data client."""
    import functools
    from multiurl import download as multiurl_download

    timeout = float(ECMWF_IFS_REQUEST_TIMEOUT_SECONDS)
    original_request = client.session.request

    @functools.wraps(original_request)
    def bounded_request(method: str, url: str, **kwargs: Any):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = timeout
        return original_request(method, url, **kwargs)

    client.session.request = bounded_request
    client.maximum_retries = ECMWF_IFS_MAX_RETRIES
    client.retry_after = ECMWF_IFS_RETRY_AFTER_SECONDS
    client.use_server_retry_after = False

    def bounded_download(result: Any) -> Any:
        if client.use_sas_token:
            result.urls = client._apply_sas_to_urls(result.urls)
        result.size = multiurl_download(
            result.urls,
            target=result.target,
            verify=client.verify,
            session=client.session,
            accept_ranges=client.source.accept_ranges,
            accept_multiple_ranges=client.source.accept_multiple_ranges,
            maximum_retries=ECMWF_IFS_MAX_RETRIES,
            retry_after=ECMWF_IFS_RETRY_AFTER_SECONDS,
            timeout=timeout,
        )
        return result

    client._download = bounded_download


class EcmwfIfsAdapter:
    """Runnable public/basic EC-only adapter.

    It downloads three valid public requests (PL fields, forecast layer cloud,
    and static surface geopotential), concatenates them into a versioned cache,
    and probes the basic contract before returning it. It never requests ML
    cc/clwc/ciwc fields and has no public path for the full bundle.
    """

    model_name = "ec"
    member_name = "ecmwf-ifs-basic"
    contract = ECMWF_IFS_BASIC_CONTRACT

    def __init__(
        self,
        cache_dir: Path | str = ".cache/ecmwf",
        source: str = "ecmwf",
        resolution: str = ECMWF_IFS_RESOLUTION,
        *,
        tensor_cache_bytes: int = ECMWF_IFS_TENSOR_CACHE_BYTES,
    ) -> None:
        if source not in ECMWF_IFS_OPEN_DATA_SOURCES:
            raise ValueError(f"unsupported ECMWF Open Data source: {source}")
        if resolution != ECMWF_IFS_RESOLUTION:
            raise ValueError(
                f"strict ECMWF IFS adapter supports only {ECMWF_IFS_RESOLUTION}"
            )
        if type(tensor_cache_bytes) is not int or tensor_cache_bytes < 0:
            raise ValueError("EC tensor cache byte limit must be a nonnegative integer")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.source = source
        self.resolution = resolution
        self.tensor_cache_bytes = tensor_cache_bytes

    @staticmethod
    def cache_path(
        cache_dir: Path | str,
        cycle_utc: datetime,
        forecast_hour: int,
    ) -> Path:
        cycle = _normalise_expected_cycle(cycle_utc)
        if cycle is None:
            raise ValueError("ECMWF IFS cycle is required")
        _validate_forecast_hour(forecast_hour)
        return _cache_path(Path(cache_dir), cycle, forecast_hour)

    def _common_request(
        self,
        cycle_utc: datetime,
        forecast_hour: int,
    ) -> dict[str, Any]:
        cycle = _normalise_expected_cycle(cycle_utc)
        if cycle is None:
            raise ValueError("ECMWF IFS cycle is required")
        _validate_forecast_hour(forecast_hour)
        return {
            "date": cycle.strftime("%Y%m%d"),
            "time": cycle.hour,
            "type": "fc",
            "stream": "oper",
        }

    def build_pressure_request(
        self,
        cycle_utc: datetime,
        forecast_hour: int,
    ) -> dict[str, Any]:
        request = self._common_request(cycle_utc, forecast_hour)
        request.update(
            {
                "step": forecast_hour,
                "param": list(ECMWF_IFS_PUBLIC_PRESSURE_PARAMS),
                "levelist": list(ECMWF_IFS_PRESSURE_LEVELS),
                "levtype": "pl",
            }
        )
        return request

    def build_cloud_request(
        self,
        cycle_utc: datetime,
        forecast_hour: int,
    ) -> dict[str, Any]:
        request = self._common_request(cycle_utc, forecast_hour)
        request.update(
            {
                "step": forecast_hour,
                "param": list(ECMWF_IFS_PUBLIC_CLOUD_PARAMS),
                "levtype": "sfc",
            }
        )
        return request

    def build_surface_request(
        self,
        cycle_utc: datetime,
        forecast_hour: int,
    ) -> dict[str, Any]:
        request = self._common_request(cycle_utc, forecast_hour)
        request.update(
            {
                "step": 0,
                "param": list(ECMWF_IFS_SURFACE_PARAMS),
                "levtype": "sfc",
            }
        )
        return request

    def probe(
        self,
        path: Path | str,
        cycle_utc: datetime,
        forecast_hour: int,
    ) -> EcmwfIfsCapability:
        return probe_ecmwf_ifs_basic_capability(
            path,
            expected_cycle_utc=cycle_utc,
            expected_forecast_hour=forecast_hour,
            resolution=self.resolution,
        )

    def download(self, cycle_utc: datetime, forecast_hour: int) -> Path:
        cycle = _normalise_expected_cycle(cycle_utc)
        if cycle is None:
            raise ValueError("ECMWF IFS cycle is required")
        _validate_forecast_hour(forecast_hour)
        target = _cache_path(self.cache_dir, cycle, forecast_hour)
        target.parent.mkdir(parents=True, exist_ok=True)
        lock_path = target.with_suffix(target.suffix + ".lock")
        with lock_path.open("a+b") as lock_handle:
            with bounded_file_lock(
                lock_handle,
                timeout_seconds=ECMWF_IFS_CACHE_LOCK_TIMEOUT_SECONDS,
                description="ECMWF IFS 缓存锁",
            ):
                if target.is_file() and target.stat().st_size > 0:
                    self._validate_cache_metadata(target, cycle, forecast_hour)
                    self.probe(target, cycle, forecast_hour).require()
                    return target
                self._download_bundle_unlocked(target, cycle, forecast_hour)
                try:
                    self.probe(target, cycle, forecast_hour).require()
                except Exception:
                    target.unlink(missing_ok=True)
                    _cache_metadata_path(target).unlink(missing_ok=True)
                    raise
                self._write_cache_metadata(target, cycle, forecast_hour)
        return target

    def load_tensor(
        self,
        cycle_utc: datetime,
        forecast_hour: int,
        *,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> EcmwfIfsInputTensor:
        path = self.download(cycle_utc, forecast_hour)
        from firesky.ecmwf_tensor_cache import load_or_decode_basic_tensor

        return load_or_decode_basic_tensor(
            path,
            cache_root=self.cache_dir / "derived" / "basic-tensors-v1",
            cache_bytes=self.tensor_cache_bytes,
            decoder=decode_ecmwf_ifs_basic_tensor,
            bbox=bbox,
            expected_cycle_utc=cycle_utc,
            expected_forecast_hour=forecast_hour,
            resolution=self.resolution,
        )

    def _download_bundle_unlocked(
        self,
        target: Path,
        cycle_utc: datetime,
        forecast_hour: int,
    ) -> None:
        try:
            from ecmwf.opendata import Client
        except ImportError as exc:
            raise RuntimeError("ecmwf-opendata is required for EC basic input") from exc

        pressure_temporary = target.with_suffix(
            target.suffix + f".pl-tmp-{os.getpid()}"
        )
        cloud_temporary = target.with_suffix(
            target.suffix + f".cloud-tmp-{os.getpid()}"
        )
        surface_temporary = target.with_suffix(
            target.suffix + f".sfc-tmp-{os.getpid()}"
        )
        bundle_temporary = target.with_suffix(target.suffix + f".tmp-{os.getpid()}")
        try:
            client = Client(
                source=self.source,
                model=ECMWF_IFS_MODEL,
                resol=self.resolution,
                verify=True,
            )
            _configure_bounded_ecmwf_client(client)
            client.retrieve(
                target=str(pressure_temporary),
                **self.build_pressure_request(cycle_utc, forecast_hour),
            )
            client.retrieve(
                target=str(cloud_temporary),
                **self.build_cloud_request(cycle_utc, forecast_hour),
            )
            client.retrieve(
                target=str(surface_temporary),
                **self.build_surface_request(cycle_utc, forecast_hour),
            )
            with bundle_temporary.open("wb") as output:
                for part in (
                    pressure_temporary,
                    cloud_temporary,
                    surface_temporary,
                ):
                    if not part.is_file() or part.stat().st_size <= 0:
                        raise RuntimeError(
                            f"ECMWF IFS request produced no file: {part.name}"
                        )
                    with part.open("rb") as input_handle:
                        shutil.copyfileobj(input_handle, output)
                output.flush()
                os.fsync(output.fileno())
            bundle_temporary.replace(target)
        finally:
            pressure_temporary.unlink(missing_ok=True)
            cloud_temporary.unlink(missing_ok=True)
            surface_temporary.unlink(missing_ok=True)
            bundle_temporary.unlink(missing_ok=True)

    def _metadata(
        self,
        cycle_utc: datetime,
        forecast_hour: int,
    ) -> dict[str, Any]:
        cycle = cycle_utc.astimezone(timezone.utc)
        return {
            "schema": ECMWF_IFS_BASIC_CACHE_SCHEMA,
            "schema_version": ECMWF_IFS_CACHE_SCHEMA_VERSION,
            "contract": ECMWF_IFS_BASIC_CONTRACT,
            "model": ECMWF_IFS_MODEL,
            "member": self.member_name,
            "source": self.source,
            "resolution": self.resolution,
            "cycle_utc": cycle.isoformat().replace("+00:00", "Z"),
            "forecast_hour": int(forecast_hour),
            "valid_time_utc": (
                cycle + timedelta(hours=forecast_hour)
            ).isoformat().replace("+00:00", "Z"),
            "pressure_levels_hpa": list(ECMWF_IFS_PRESSURE_LEVELS),
            "pressure_params": list(ECMWF_IFS_PUBLIC_PRESSURE_PARAMS),
            "cloud_params": list(ECMWF_IFS_PUBLIC_CLOUD_PARAMS),
            "surface_params": list(ECMWF_IFS_SURFACE_PARAMS),
            "terrain_step_hour": 0,
        }

    def _write_cache_metadata(
        self,
        target: Path,
        cycle_utc: datetime,
        forecast_hour: int,
    ) -> None:
        metadata_path = _cache_metadata_path(target)
        temporary = metadata_path.with_name(metadata_path.name + f".tmp-{os.getpid()}")
        temporary.write_text(
            json.dumps(
                self._metadata(cycle_utc, forecast_hour),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        temporary.replace(metadata_path)

    def _validate_cache_metadata(
        self,
        target: Path,
        cycle_utc: datetime,
        forecast_hour: int,
    ) -> None:
        metadata_path = _cache_metadata_path(target)
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EcmwfIfsCapabilityError(
                "ECMWF IFS cached basic bundle has no valid metadata sidecar"
            ) from exc
        if metadata != self._metadata(cycle_utc, forecast_hour):
            raise EcmwfIfsCapabilityError(
                "ECMWF IFS cached basic bundle metadata does not match "
                "the requested contract"
            )
