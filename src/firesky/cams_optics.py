"""Strict readers and physical diagnostics for expanded CAMS optical fields.

The production forecast currently consumes the compact AOD550/profile cache.  This
module is intentionally separate: an expanded cache must contain the spectral
fields explicitly requested from CAMS.  Missing fields are an error instead of a
silent fallback, so an A/B replay cannot accidentally mix optical schemas.

The functions in this module return radiative quantities (optical depth,
transmission, SSA and asymmetry factor).  They do not convert transmission to a
human-perceived brightness score.  That conversion needs an observation model and
must remain a separate, calibrated layer.
"""

from __future__ import annotations

import math
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
from eccodes import codes_get, codes_get_array, codes_grib_new_from_file, codes_release


SPECTRAL_AOD_WAVELENGTHS_NM = (355, 440, 550, 670, 865)
SPECTRAL_EXTINCTION_WAVELENGTHS_NM = (355, 532, 1064)
SPECTRAL_PRESSURE_LEVELS_HPA = (1000, 925, 850, 700, 600, 500, 400, 300)
SPECTRAL_OPTICS_SCHEMA_VERSION = "cams-expanded-optics-v1"
SPECTRAL_OPTICS_CACHE_PREFIX = "cams-spectral-optics-v1"
SPECTRAL_OPTICS_CACHE_FORMAT = "grib"
SPECTRAL_OPTICS_REQUEST_VARIABLES = (
    "total_aerosol_optical_depth_355nm",
    "total_aerosol_optical_depth_440nm",
    "total_aerosol_optical_depth_550nm",
    "total_aerosol_optical_depth_670nm",
    "total_aerosol_optical_depth_865nm",
    "aerosol_extinction_coefficient_355nm",
    "aerosol_extinction_coefficient_532nm",
    "aerosol_extinction_coefficient_1064nm",
    "aerosol_single_scattering_albedo_550nm",
    "aerosol_asymmetry_factor_550nm",
)
SPECTRAL_OPTICS_DATASET = "cams-global-atmospheric-composition-forecasts"


def _spectral_optics_area(area: Sequence[float]) -> list[float]:
    values = [float(value) for value in area]
    if len(values) != 4 or not all(math.isfinite(value) for value in values):
        raise ValueError("CAMS spectral cache area must contain four finite values")
    north, west, south, east = values
    if not (-90.0 <= south < north <= 90.0 and -180.0 <= west < east <= 180.0):
        raise ValueError("CAMS spectral cache area is invalid")
    return values


def build_spectral_optics_request(
    area: Sequence[float] | None = None,
) -> dict[str, object]:
    """Build the explicit CDS request for the expanded diagnostic schema."""
    query: dict[str, object] = {
        "variable": list(SPECTRAL_OPTICS_REQUEST_VARIABLES),
        "pressure_level": [str(level) for level in SPECTRAL_PRESSURE_LEVELS_HPA],
        "type": ["forecast"],
        "data_format": SPECTRAL_OPTICS_CACHE_FORMAT,
    }
    if area is not None:
        query["area"] = _spectral_optics_area(area)
    return query


def spectral_optics_cache_identity(
    run_time_utc: datetime,
    lead_hours: int,
    area: Sequence[float],
) -> dict[str, object]:
    """Return the immutable identity for one expanded CAMS diagnostic file."""
    if run_time_utc.tzinfo is None or run_time_utc.utcoffset() is None:
        raise ValueError("CAMS spectral cache run time must be timezone-aware")
    run_time = run_time_utc.astimezone(timezone.utc).replace(microsecond=0)
    lead = int(lead_hours)
    if lead < 0:
        raise ValueError("CAMS spectral cache lead hour must be non-negative")
    return {
        "schema_version": SPECTRAL_OPTICS_SCHEMA_VERSION,
        "format": SPECTRAL_OPTICS_CACHE_FORMAT,
        "source": "cams",
        "dataset": SPECTRAL_OPTICS_DATASET,
        "run_time_utc": run_time.isoformat().replace("+00:00", "Z"),
        "lead_hours": lead,
        "valid_time_utc": (run_time + timedelta(hours=lead))
        .isoformat()
        .replace("+00:00", "Z"),
        "area": _spectral_optics_area(area),
        "request_variables": list(SPECTRAL_OPTICS_REQUEST_VARIABLES),
        "aod_wavelengths_nm": list(SPECTRAL_AOD_WAVELENGTHS_NM),
        "extinction_wavelengths_nm": list(SPECTRAL_EXTINCTION_WAVELENGTHS_NM),
        "pressure_levels_hpa": list(SPECTRAL_PRESSURE_LEVELS_HPA),
        "phase_fields": ["ssa550", "asymmetry550"],
        "score_consumed": False,
    }


def spectral_optics_cache_key(
    run_time_utc: datetime,
    lead_hours: int,
    area: Sequence[float],
) -> str:
    identity = spectral_optics_cache_identity(run_time_utc, lead_hours, area)
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def spectral_optics_cache_filename(
    run_time_utc: datetime,
    lead_hours: int,
    area: Sequence[float],
) -> str:
    run_time = run_time_utc.astimezone(timezone.utc)
    return (
        f"{SPECTRAL_OPTICS_CACHE_PREFIX}-{run_time:%Y-%m-%d-%H%M}-"
        f"{int(lead_hours):03d}-{spectral_optics_cache_key(run_time, lead_hours, area)}.grib"
    )


def spectral_optics_cache_metadata_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".json")


def _validate_spectral_optics_identity(identity: Mapping[str, object]) -> None:
    required_keys = {
        "schema_version",
        "format",
        "source",
        "dataset",
        "run_time_utc",
        "lead_hours",
        "valid_time_utc",
        "area",
        "request_variables",
        "aod_wavelengths_nm",
        "extinction_wavelengths_nm",
        "pressure_levels_hpa",
        "phase_fields",
        "score_consumed",
    }
    if set(identity) != required_keys:
        raise ValueError("CAMS spectral cache identity fields are incomplete")
    if (
        identity["schema_version"] != SPECTRAL_OPTICS_SCHEMA_VERSION
        or identity["format"] != SPECTRAL_OPTICS_CACHE_FORMAT
        or identity["source"] != "cams"
        or identity["dataset"] != SPECTRAL_OPTICS_DATASET
        or identity["request_variables"] != list(SPECTRAL_OPTICS_REQUEST_VARIABLES)
        or identity["aod_wavelengths_nm"] != list(SPECTRAL_AOD_WAVELENGTHS_NM)
        or identity["extinction_wavelengths_nm"]
        != list(SPECTRAL_EXTINCTION_WAVELENGTHS_NM)
        or identity["pressure_levels_hpa"] != list(SPECTRAL_PRESSURE_LEVELS_HPA)
        or identity["phase_fields"] != ["ssa550", "asymmetry550"]
        or identity["score_consumed"] is not False
    ):
        raise ValueError("CAMS spectral cache identity does not match the schema")
    lead_hours = identity["lead_hours"]
    if isinstance(lead_hours, bool) or not isinstance(lead_hours, int) or lead_hours < 0:
        raise ValueError("CAMS spectral cache identity has an invalid lead hour")
    _spectral_optics_area(identity["area"])
    try:
        run_time = datetime.fromisoformat(
            str(identity["run_time_utc"]).replace("Z", "+00:00")
        )
        valid_time = datetime.fromisoformat(
            str(identity["valid_time_utc"]).replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("CAMS spectral cache identity has invalid times") from exc
    if (
        run_time.tzinfo is None
        or run_time.utcoffset() is None
        or valid_time.tzinfo is None
        or valid_time.utcoffset() is None
    ):
        raise ValueError("CAMS spectral cache identity times must be timezone-aware")
    run_time = run_time.astimezone(timezone.utc)
    valid_time = valid_time.astimezone(timezone.utc)
    if valid_time != run_time + timedelta(hours=lead_hours):
        raise ValueError("CAMS spectral cache identity valid time is inconsistent")


def write_spectral_optics_cache_metadata(
    path: Path,
    identity: Mapping[str, object],
) -> Path:
    """Write a content-bound sidecar for an expanded CAMS diagnostic file."""
    _validate_spectral_optics_identity(identity)
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError("CAMS spectral cache file is missing or empty")
    metadata = {
        "schema_version": SPECTRAL_OPTICS_SCHEMA_VERSION,
        "identity": dict(identity),
        "file": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    target = spectral_optics_cache_metadata_path(path)
    target.write_text(
        json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return target


def read_spectral_optics_cache_metadata(path: Path) -> dict[str, object]:
    metadata_path = spectral_optics_cache_metadata_path(path)
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"CAMS spectral cache metadata is unreadable: {metadata_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("CAMS spectral cache metadata must be an object")
    return payload


def validate_spectral_optics_cache_metadata(
    path: Path,
    expected_identity: Mapping[str, object],
) -> dict[str, object]:
    """Validate schema, immutable identity, and content fingerprint."""
    metadata = read_spectral_optics_cache_metadata(path)
    if metadata.get("schema_version") != SPECTRAL_OPTICS_SCHEMA_VERSION:
        raise ValueError("CAMS spectral cache schema version mismatch")
    actual_identity = metadata.get("identity")
    if not isinstance(actual_identity, dict):
        raise ValueError("CAMS spectral cache metadata has no identity object")
    _validate_spectral_optics_identity(expected_identity)
    _validate_spectral_optics_identity(actual_identity)
    if actual_identity != dict(expected_identity):
        raise ValueError("CAMS spectral cache identity mismatch")
    if metadata.get("file") != path.name:
        raise ValueError("CAMS spectral cache file identity mismatch")
    try:
        identity_run_time = datetime.fromisoformat(
            str(expected_identity["run_time_utc"]).replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("CAMS spectral cache identity has invalid run time") from exc
    if path.name != spectral_optics_cache_filename(
        identity_run_time,
        expected_identity["lead_hours"],
        expected_identity["area"],
    ):
        raise ValueError("CAMS spectral cache filename does not match identity")
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError("CAMS spectral cache file is missing or empty")
    size_bytes = metadata.get("size_bytes")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int):
        raise ValueError("CAMS spectral cache metadata size is invalid")
    if size_bytes != path.stat().st_size:
        raise ValueError("CAMS spectral cache size mismatch")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if not isinstance(metadata.get("sha256"), str) or metadata["sha256"] != digest:
        raise ValueError("CAMS spectral cache checksum mismatch")
    return metadata


@dataclass(frozen=True)
class CamsSpectralOptics:
    """Expanded CAMS fields on one regular grid.

    ``aod`` maps wavelength in nm to a flattened surface grid.  ``extinction``
    maps wavelength in nm to an array with shape ``(pressure, grid_point)`` and
    units of inverse metres.  SSA and asymmetry are 550 nm surface fields.
    """

    latitudes: np.ndarray
    longitudes: np.ndarray
    aod: Mapping[int, np.ndarray]
    extinction: Mapping[int, np.ndarray]
    single_scattering_albedo_550: np.ndarray
    asymmetry_factor_550: np.ndarray
    pressure_levels_hpa: tuple[int, ...] = SPECTRAL_PRESSURE_LEVELS_HPA
    valid_time: datetime | None = None


@dataclass(frozen=True)
class CamsSpectralPoint:
    """Nearest-grid-point optical values used by an offline replay."""

    lat: float
    lon: float
    aod: Mapping[int, float]
    extinction: Mapping[int, tuple[float, ...]]
    single_scattering_albedo_550: float
    asymmetry_factor_550: float
    pressure_levels_hpa: tuple[int, ...]

    def aod_at(self, wavelength_nm: float) -> float:
        return interpolate_log_aod(self.aod, wavelength_nm)

    def profile_optical_depth(self, wavelength_nm: int) -> float:
        profile = self.extinction.get(int(wavelength_nm))
        if profile is None:
            raise KeyError(f"CAMS extinction is not available at {wavelength_nm} nm")
        return integrate_pressure_level_extinction(
            profile,
            self.pressure_levels_hpa,
        )


@dataclass(frozen=True)
class MiddleLayerExtinctionDiagnostics:
    """Direct diagnostics from a CAMS ``aerext532`` pressure profile.

    Peak extinction is converted from CAMS' native inverse-metres to the
    tutorial-friendly inverse-kilometres.  ``seasoning_level_fraction`` is a
    discrete pressure-level diagnostic, not an optical-depth correction.
    """

    level_count: int
    peak_extinction_km_inv: float
    optical_depth: float
    seasoning_level_fraction: float
    overloaded_level_fraction: float


def load_cams_spectral_optics(path: Path) -> CamsSpectralOptics:
    """Read the complete expanded CAMS optical schema from one GRIB file.

    The parser deliberately does not accept the compact cache schema.  This
    makes missing wavelength/phase diagnostics visible during an A/B run.
    """

    latitudes: np.ndarray | None = None
    longitudes: np.ndarray | None = None
    aod: dict[int, np.ndarray] = {}
    extinction: dict[int, dict[int, np.ndarray]] = {
        wavelength: {} for wavelength in SPECTRAL_EXTINCTION_WAVELENGTHS_NM
    }
    ssa: np.ndarray | None = None
    asymmetry: np.ndarray | None = None
    valid_time: datetime | None = None

    with path.open("rb") as handle:
        while True:
            gid = codes_grib_new_from_file(handle)
            if gid is None:
                break
            try:
                short_name = str(codes_get(gid, "shortName"))
                level = int(codes_get(gid, "level"))
                field_kind: tuple[str, int] | None = None
                if short_name.startswith("aod"):
                    try:
                        wavelength = int(short_name[3:])
                    except ValueError:
                        wavelength = -1
                    if wavelength in SPECTRAL_AOD_WAVELENGTHS_NM:
                        field_kind = ("aod", wavelength)
                elif short_name.startswith("aerext"):
                    try:
                        wavelength = int(short_name[6:])
                    except ValueError:
                        wavelength = -1
                    if wavelength in SPECTRAL_EXTINCTION_WAVELENGTHS_NM:
                        field_kind = ("extinction", wavelength)
                elif short_name == "ssa550":
                    field_kind = ("ssa", 550)
                elif short_name == "asymmetry550":
                    field_kind = ("asymmetry", 550)
                if field_kind is None:
                    continue

                message_valid_time: datetime | None = None
                try:
                    validity_date = str(codes_get(gid, "validityDate"))
                    validity_time = int(codes_get(gid, "validityTime"))
                    message_valid_time = datetime.strptime(
                        f"{validity_date}{validity_time:04d}", "%Y%m%d%H%M"
                    ).replace(tzinfo=timezone.utc)
                except KeyError:
                    # Synthetic/offline fixtures may not carry GRIB validity
                    # keys; the cache contract validates this separately.
                    pass
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"CAMS optical message has an invalid valid time: {path}") from exc
                if message_valid_time is not None:
                    if valid_time is None:
                        valid_time = message_valid_time
                    elif valid_time != message_valid_time:
                        raise ValueError(f"CAMS optical messages use different valid times: {path}")

                kind, wavelength = field_kind
                try:
                    level_type = str(codes_get(gid, "typeOfLevel"))
                    number_of_points = int(codes_get(gid, "numberOfPoints"))
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(
                        f"CAMS optical message is missing grid metadata: {path}"
                    ) from exc
                if kind == "extinction":
                    try:
                        units = str(codes_get(gid, "units"))
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ValueError(
                            f"CAMS extinction message is missing units: {path}"
                        ) from exc
                    if level_type != "isobaricInhPa" or units != "m**-1":
                        raise ValueError(
                            f"CAMS extinction level/unit contract is invalid: {path}"
                        )
                elif level_type != "surface" or level != 0:
                    raise ValueError(
                        f"CAMS {kind} must use surface level 0: {path}"
                    )

                message_lats = np.ascontiguousarray(
                    codes_get_array(gid, "latitudes"), dtype=np.float64
                )
                message_lons = np.ascontiguousarray(
                    normalize_longitudes(codes_get_array(gid, "longitudes")),
                    dtype=np.float64,
                )
                values = np.ascontiguousarray(
                    codes_get_array(gid, "values"), dtype=np.float64
                )
                if not (
                    message_lats.ndim == message_lons.ndim == values.ndim == 1
                    and len(message_lats) == len(message_lons) == len(values)
                    and len(values) == number_of_points
                    and len(values) > 0
                ):
                    raise ValueError(f"CAMS optical message has inconsistent grid lengths: {path}")
                if not (
                    np.all(np.isfinite(message_lats))
                    and np.all(np.isfinite(message_lons))
                    and np.all(np.isfinite(values))
                ):
                    raise ValueError(f"CAMS optical message contains non-finite values: {path}")
                if latitudes is None:
                    latitudes = message_lats
                    longitudes = message_lons
                elif not (
                    np.array_equal(latitudes, message_lats)
                    and np.array_equal(longitudes, message_lons)
                ):
                    raise ValueError(f"CAMS optical messages use different grids: {path}")

                if kind == "aod":
                    if wavelength in aod:
                        raise ValueError(f"Duplicate CAMS AOD field {wavelength}: {path}")
                    if np.any((values < 0.0) | (values > 10.0)):
                        raise ValueError(f"CAMS AOD is outside 0..10: {path}")
                    aod[wavelength] = values
                elif kind == "extinction":
                    if wavelength not in extinction:
                        raise ValueError(
                            f"Unexpected CAMS extinction wavelength {wavelength}: {path}"
                        )
                    if level not in SPECTRAL_PRESSURE_LEVELS_HPA:
                        raise ValueError(
                            f"Unexpected CAMS extinction pressure level {level}: {path}"
                        )
                    if level in extinction[wavelength]:
                        raise ValueError(
                            f"Duplicate CAMS extinction level {wavelength}/{level}: {path}"
                        )
                    if np.any(values < 0.0):
                        raise ValueError(f"CAMS extinction contains negative values: {path}")
                    extinction[wavelength][level] = values
                elif kind == "ssa":
                    if ssa is not None:
                        raise ValueError(f"Duplicate CAMS SSA field: {path}")
                    if np.any((values < 0.0) | (values > 1.0)):
                        raise ValueError(f"CAMS SSA is outside 0..1: {path}")
                    ssa = values
                else:
                    if asymmetry is not None:
                        raise ValueError(f"Duplicate CAMS asymmetry field: {path}")
                    if np.any((values < -1.0) | (values > 1.0)):
                        raise ValueError(f"CAMS asymmetry is outside -1..1: {path}")
                    asymmetry = values
            finally:
                codes_release(gid)

    missing_aod = sorted(set(SPECTRAL_AOD_WAVELENGTHS_NM) - set(aod))
    missing_ext = {
        wavelength: sorted(set(SPECTRAL_PRESSURE_LEVELS_HPA) - set(levels))
        for wavelength, levels in extinction.items()
        if set(levels) != set(SPECTRAL_PRESSURE_LEVELS_HPA)
    }
    missing = []
    if missing_aod:
        missing.append(f"aod={missing_aod}")
    if missing_ext:
        missing.append(f"extinction={missing_ext}")
    if ssa is None:
        missing.append("ssa550")
    if asymmetry is None:
        missing.append("asymmetry550")
    if latitudes is None or longitudes is None:
        missing.append("grid")
    if missing:
        raise ValueError(f"incomplete expanded CAMS optical file {path}: " + ", ".join(missing))

    stacked_extinction = {
        wavelength: np.ascontiguousarray(
            np.stack(
                [levels[level] for level in SPECTRAL_PRESSURE_LEVELS_HPA],
                axis=0,
            ),
            dtype=np.float64,
        )
        for wavelength, levels in extinction.items()
    }
    return CamsSpectralOptics(
        latitudes=latitudes,
        longitudes=longitudes,
        aod={key: np.ascontiguousarray(value, dtype=np.float64) for key, value in aod.items()},
        extinction=stacked_extinction,
        single_scattering_albedo_550=np.ascontiguousarray(ssa, dtype=np.float64),
        asymmetry_factor_550=np.ascontiguousarray(asymmetry, dtype=np.float64),
        valid_time=valid_time,
    )


def load_spectral_optics_cache(
    path: Path,
    expected_identity: Mapping[str, object],
) -> CamsSpectralOptics:
    """Load a diagnostic file only after its versioned cache contract passes."""
    validate_spectral_optics_cache_metadata(path, expected_identity)
    optics = load_cams_spectral_optics(path)
    expected_valid_time = expected_identity.get("valid_time_utc")
    if optics.valid_time is None:
        raise ValueError("CAMS spectral GRIB has no valid time")
    if expected_valid_time is None:
        raise ValueError("CAMS spectral cache identity has no valid time")
    try:
        parsed_expected = datetime.fromisoformat(
            str(expected_valid_time).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except ValueError as exc:
        raise ValueError("CAMS spectral cache identity has an invalid valid time") from exc
    if optics.valid_time != parsed_expected:
        raise ValueError("CAMS spectral GRIB valid time does not match cache identity")
    return optics


def nearest_cams_spectral_point(
    optics: CamsSpectralOptics,
    lat: float,
    lon: float,
) -> CamsSpectralPoint:
    """Return a deterministic nearest grid point without spatial smoothing."""

    delta_lon = np.abs((optics.longitudes - float(lon) + 180.0) % 360.0 - 180.0)
    index = int(np.argmin(np.abs(optics.latitudes - float(lat)) + delta_lon))
    return CamsSpectralPoint(
        lat=float(optics.latitudes[index]),
        lon=float(optics.longitudes[index]),
        aod={wavelength: float(values[index]) for wavelength, values in optics.aod.items()},
        extinction={
            wavelength: tuple(float(value) for value in values[:, index])
            for wavelength, values in optics.extinction.items()
        },
        single_scattering_albedo_550=float(optics.single_scattering_albedo_550[index]),
        asymmetry_factor_550=float(optics.asymmetry_factor_550[index]),
        pressure_levels_hpa=optics.pressure_levels_hpa,
    )


def normalize_longitudes(values: np.ndarray) -> np.ndarray:
    return (np.asarray(values, dtype=np.float64) + 180.0) % 360.0 - 180.0


def interpolate_log_aod(aod: Mapping[int, float], wavelength_nm: float) -> float:
    """Interpolate AOD in log-wavelength/log-AOD space.

    Interpolation is only allowed within the requested CAMS wavelength range;
    extrapolation would manufacture aerosol spectral behavior not supplied by
    the model.
    """

    wavelength = float(wavelength_nm)
    if not math.isfinite(wavelength) or wavelength <= 0.0:
        raise ValueError("wavelength must be finite and positive")
    points = sorted((float(key), float(value)) for key, value in aod.items())
    usable = [(key, value) for key, value in points if value > 0.0 and math.isfinite(value)]
    if len(usable) < 2 or wavelength < usable[0][0] or wavelength > usable[-1][0]:
        raise ValueError("wavelength is outside the positive CAMS AOD range")
    for left, right in zip(usable, usable[1:]):
        if left[0] <= wavelength <= right[0]:
            if wavelength == left[0]:
                return left[1]
            if wavelength == right[0]:
                return right[1]
            fraction = (math.log(wavelength) - math.log(left[0])) / (
                math.log(right[0]) - math.log(left[0])
            )
            return math.exp(
                math.log(left[1]) + fraction * (math.log(right[1]) - math.log(left[1]))
            )
    raise ValueError("could not bracket wavelength")


def angstrom_exponent(aod: Mapping[int, float], wavelength_a_nm: int = 440, wavelength_b_nm: int = 865) -> float:
    """Calculate the observed Angstrom exponent from two CAMS AOD fields."""

    first = float(aod[wavelength_a_nm])
    second = float(aod[wavelength_b_nm])
    if first <= 0.0 or second <= 0.0 or not math.isfinite(first + second):
        raise ValueError("AOD values must be finite and positive")
    return -math.log(first / second) / math.log(wavelength_a_nm / wavelength_b_nm)


def standard_pressure_height_m(pressure_hpa: float) -> float:
    """Approximate geopotential height for a pressure level.

    This is only a geometry helper for integrating a CAMS extinction profile;
    production cloud geometry must continue to use model geopotential height.
    """

    pressure = float(pressure_hpa)
    if not 0.0 < pressure <= 1013.25:
        raise ValueError("pressure must be within the standard-atmosphere range")
    return 44330.0 * (1.0 - (pressure / 1013.25) ** 0.190263)


def integrate_pressure_level_extinction(
    extinction_m_inv: tuple[float, ...] | np.ndarray,
    pressure_levels_hpa: tuple[int, ...] = SPECTRAL_PRESSURE_LEVELS_HPA,
) -> float:
    """Integrate pressure-level extinction over standard-atmosphere height."""

    values = np.asarray(extinction_m_inv, dtype=np.float64)
    levels = np.asarray(pressure_levels_hpa, dtype=np.float64)
    if values.shape != levels.shape:
        raise ValueError("extinction and pressure-level arrays must have the same shape")
    heights = np.asarray([standard_pressure_height_m(level) for level in levels])
    order = np.argsort(heights)
    finite = np.isfinite(values[order]) & (values[order] >= 0.0)
    if np.count_nonzero(finite) < 2:
        raise ValueError("extinction profile has fewer than two valid levels")
    return float(np.trapezoid(values[order][finite], heights[order][finite]))


def middle_layer_extinction_diagnostics(
    extinction_m_inv: tuple[float, ...] | np.ndarray,
    pressure_levels_hpa: tuple[int, ...] | np.ndarray,
    *,
    lower_height_m: float = 2_000.0,
    upper_height_m: float = 8_000.0,
) -> MiddleLayerExtinctionDiagnostics:
    """Summarize the 2-8 km portion of a CAMS ``aerext532`` profile.

    The tutorial's 0.01-0.02 and 0.05 thresholds are expressed in km^-1;
    CAMS supplies ``aerext532`` in m^-1.  This function performs that unit
    conversion explicitly and does not turn the result into a forecast score.
    """

    values = np.asarray(extinction_m_inv, dtype=np.float64)
    levels = np.asarray(pressure_levels_hpa, dtype=np.float64)
    if values.shape != levels.shape or values.ndim != 1:
        raise ValueError("extinction and pressure-level arrays must be matching vectors")
    if not math.isfinite(lower_height_m + upper_height_m) or not (
        0.0 <= lower_height_m < upper_height_m
    ):
        raise ValueError("middle-layer height bounds are invalid")

    heights = np.asarray([standard_pressure_height_m(level) for level in levels])
    physical = np.isfinite(values) & (values >= 0.0)
    selected = physical & (heights >= lower_height_m) & (heights <= upper_height_m)
    if np.count_nonzero(selected) < 2:
        raise ValueError("middle layer has fewer than two valid pressure levels")

    selected_heights = heights[selected]
    selected_values = values[selected]
    physical_order = np.argsort(heights[physical])
    physical_heights = heights[physical][physical_order]
    physical_values = values[physical][physical_order]
    if (
        physical_heights[0] > lower_height_m
        or physical_heights[-1] < upper_height_m
    ):
        raise ValueError("extinction profile does not bracket the middle layer")
    integration_heights = np.concatenate(
        (
            np.asarray([lower_height_m]),
            np.sort(
                selected_heights[
                    (selected_heights > lower_height_m)
                    & (selected_heights < upper_height_m)
                ]
            ),
            np.asarray([upper_height_m]),
        )
    )
    integration_values = np.interp(
        integration_heights,
        physical_heights,
        physical_values,
    )
    extinction_km_inv = selected_values * 1_000.0
    seasoning = (extinction_km_inv >= 0.01) & (extinction_km_inv <= 0.02)
    overloaded = extinction_km_inv > 0.05
    return MiddleLayerExtinctionDiagnostics(
        level_count=int(selected_values.size),
        peak_extinction_km_inv=float(np.max(extinction_km_inv)),
        optical_depth=float(
            np.trapezoid(integration_values, integration_heights)
        ),
        seasoning_level_fraction=float(np.mean(seasoning)),
        overloaded_level_fraction=float(np.mean(overloaded)),
    )


def relative_airmass(solar_elevation_deg: float) -> float:
    """Kasten-Young relative optical air mass for a visible solar ray."""

    elevation = float(solar_elevation_deg)
    if not math.isfinite(elevation) or elevation <= -5.0:
        return math.inf
    clipped = min(90.0, max(-5.0, elevation))
    return 1.0 / (
        math.sin(math.radians(clipped))
        + 0.50572 * (clipped + 6.079) ** -1.6364
    )


def direct_aerosol_transmission(aod: float, solar_elevation_deg: float) -> float:
    """Beer-Lambert direct transmission through an aerosol column."""

    optical_depth = max(0.0, float(aod))
    air_mass = relative_airmass(solar_elevation_deg)
    if not math.isfinite(air_mass):
        return 0.0
    return math.exp(-optical_depth * air_mass)


def henyey_greenstein_phase_function(asymmetry_factor: float, scattering_angle_deg: float) -> float:
    """Return the normalized HG phase-function approximation from CAMS ``g``.

    CAMS supplies the first phase-function moment (the asymmetry factor), not a
    full angle-resolved aerosol phase function.  This reconstruction is useful
    for diagnostics but must not be presented as an observed phase function.
    """

    g = max(-0.999, min(0.999, float(asymmetry_factor)))
    cosine = math.cos(math.radians(float(scattering_angle_deg)))
    denominator = (1.0 + g * g - 2.0 * g * cosine) ** 1.5
    return (1.0 - g * g) / (4.0 * math.pi * denominator)
