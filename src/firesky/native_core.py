from __future__ import annotations

import ctypes
import math
import os
import sys
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from firesky.models import CloudColumn, GridCellFeature


FIELD_COUNT = 20
# Contract `hsd-assessment-feature-row-v2`: the legacy 20-column feature row
# extended with the 2 m surface pair (surface-parcel LCL cloud base), the
# satellite footprint geometry and the satellite cloud-probability pair. The
# layout must stay in lockstep with FEATURE_ROW_V2_* in native/src/lib.rs.
# Absent values travel as NaN and are never backfilled as 0.
FEATURE_ROW_V2_FIELD_COUNT = 27
FEATURE_ROW_V2_CONTRACT = "hsd-assessment-feature-row-v2"
FEATURE_ROW_V2_SURFACE_TEMPERATURE_K = 20
FEATURE_ROW_V2_SURFACE_DEWPOINT_K = 21
FEATURE_ROW_V2_FOOTPRINT_AZIMUTH_WIDTH_DEG = 22
FEATURE_ROW_V2_FOOTPRINT_NEAR_DISTANCE_KM = 23
FEATURE_ROW_V2_FOOTPRINT_FAR_DISTANCE_KM = 24
FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY = 25
FEATURE_ROW_V2_CLOUD_PROBABILITY_GIVEN_CLOUD = 26
FEATURE_ROW_V2_NAMES: tuple[str, ...] = (
    "lat",
    "lon",
    "distance_km",
    "bearing_deg",
    "pressure_hpa",
    "geopotential_height_m",
    "rh_percent",
    "temperature_k",
    "omega_pa_s",
    "u_ms",
    "v_ms",
    "cloud_fraction_percent",
    "cloud_liquid_kg_kg",
    "cloud_ice_kg_kg",
    "source_lat",
    "source_lon",
    "source_distance_km",
    "source_bearing_deg",
    "surface_visibility_m",
    "precip_rate_kg_m2_s",
    "surface_temperature_k",
    "surface_dewpoint_k",
    "footprint_azimuth_width_deg",
    "footprint_near_distance_km",
    "footprint_far_distance_km",
    "horizontal_cloud_probability",
    "cloud_probability_given_cloud",
)
METRIC_COUNT = 9
CANDIDATE_PREFILTER_FIELDS = 7
BOUNDARY_PREFILTER_FIELDS = 3
AGG_COLUMN_FIELDS = 31
COLUMN_FIELDS = 14
SIGNAL_FIELDS = 15
NATIONAL_GRID_FIELDS = 36
NATIONAL_AEROSOL_PROFILE_LEVELS = 11
NATIONAL_OBSERVER_FIELDS = 3
NATIONAL_SAMPLE_FIELDS = 9
NATIONAL_VIEW_DIAGNOSTIC_FIELDS = 34
NATIONAL_VIEW_DIAGNOSTIC_OFFSET = 43
NATIONAL_VETO_LEDGER_OFFSET = NATIONAL_VIEW_DIAGNOSTIC_OFFSET + NATIONAL_VIEW_DIAGNOSTIC_FIELDS
NATIONAL_VETO_CANDIDATE_OFFSET = NATIONAL_VETO_LEDGER_OFFSET
NATIONAL_VETO_TARGET_ELIGIBLE_OFFSET = NATIONAL_VETO_LEDGER_OFFSET + 1
NATIONAL_VETO_GEOMETRY_REACHABLE_OFFSET = NATIONAL_VETO_LEDGER_OFFSET + 2
NATIONAL_VETO_EXTERNAL_PATH_TRANSMISSION_OFFSET = NATIONAL_VETO_LEDGER_OFFSET + 3
NATIONAL_VETO_UNOCCLUDED_BURN_UPPER_OFFSET = NATIONAL_VETO_LEDGER_OFFSET + 4
NATIONAL_OUTPUT_FIELDS = NATIONAL_VETO_LEDGER_OFFSET + 5
NATIVE_ABI_VERSION = 3
PATH_AUDIT_FIELDS = 36
PATH_AUDIT_SCHEMA_VERSION = 2.0
PATH_AUDIT_FIELD_OFFSETS = {
    "schema_version": 0,
    "external_tau_visible_mean": 1,
    "target_self_tau_visible_mean": 2,
    "pre_peak_external_transmitted_field": 3,
    "peak_transmission_factor_visible_mean": 4,
    "trace_steps_max": 5,
    "unique_cells_max": 6,
    "nonconsecutive_revisited_cells_max": 7,
    "domain_break_count": 8,
    "ray_height_break_count": 9,
    "opaque_cutoff_count": 10,
    "family_tau_high_visible_mean": 11,
    "family_tau_mid_visible_mean": 12,
    "family_tau_low_visible_mean": 13,
    "target_extension_cells_max": 14,
    "visible_solid_angle_weight": 15,
    "pb_loss_total": 16,
    "pb_decomposition_error": 17,
    "dominant_loss_field": 18,
    "dominant_share": 19,
    "dominant_valid": 20,
    "dominant_target_cell_index": 21,
    "dominant_family_code": 22,
    "dominant_distance_km": 23,
    "dominant_bearing_deg": 24,
    "dominant_amount": 25,
    "dominant_base_asl_m": 26,
    "dominant_top_asl_m": 27,
    "dominant_terrain_asl_m": 28,
    "dominant_material_quality": 29,
    "dominant_target_opacity": 30,
    "dominant_target_deep_risk": 31,
    "dominant_effective_self_transmission": 32,
    "dominant_target_deck_connected": 33,
    "dominant_target_deck_attenuated_fraction": 34,
    "omitted_loss_field": 35,
}
TARGET_AUDIT_FIELDS = 28
TARGET_AUDIT_MAX_RECORDS = 128
TARGET_AUDIT_SCHEMA_VERSION = 1.0
TARGET_AUDIT_FIELD_OFFSETS = {
    "schema_version": 0,
    "valid": 1,
    "target_cell_index": 2,
    "family_code": 3,
    "distance_km": 4,
    "bearing_deg": 5,
    "amount": 6,
    "candidate_overlap_field": 7,
    "visible_overlap_field": 8,
    "burn_contribution_field": 9,
    "target_eligible": 10,
    "geometry_reachable": 11,
    "external_path_transmission": 12,
    "target_self_transmission": 13,
    "external_tau": 14,
    "target_self_tau": 15,
    "base_asl_m": 16,
    "top_asl_m": 17,
    "terrain_asl_m": 18,
    "base_above_observer_m": 19,
    "top_above_observer_m": 20,
    "material_quality": 21,
    "target_opacity": 22,
    "target_deep_risk": 23,
    "deck_connected": 24,
    "deck_attenuated_fraction": 25,
    "clear_gap_count": 26,
    "path_audit_available": 27,
}
_library = None
_load_attempted = False
_load_error: Optional[str] = None
_library_load_lock = threading.Lock()
_national_scoring_libraries: dict[str, ctypes.CDLL] = {}
COMPUTE_BACKENDS = ("auto", "cpu", "gpu")
GPU_AUTO_MIN_FEATURES = max(1, int(os.environ.get("FIRESKY_GPU_MIN_FEATURES", "100000")))
GPU_REDUCTION_MIN_TARGETS = max(
    1,
    int(os.environ.get("FIRESKY_GPU_REDUCTION_MIN_TARGETS", "2048")),
)
_environment_backend = os.environ.get("FIRESKY_COMPUTE_BACKEND", "auto").strip().lower()
if _environment_backend not in COMPUTE_BACKENDS:
    _environment_backend = "auto"
_requested_backend: ContextVar[str] = ContextVar(
    "firesky_compute_backend",
    default=_environment_backend,
)


@dataclass
class ComputeBackendTrace:
    requested: str
    used: set[str] = field(default_factory=set)

    @property
    def actual(self) -> str:
        if self.used == {"cache"}:
            return "cache"
        if self.used == {"gpu"}:
            return "gpu"
        if self.used == {"cpu"}:
            return "cpu"
        if self.used:
            return "hybrid"
        return "cpu"


_backend_trace: ContextVar[ComputeBackendTrace | None] = ContextVar(
    "firesky_compute_backend_trace", default=None
)


def normalize_compute_backend(value: object) -> str:
    backend = str(value or "auto").strip().lower()
    if backend not in COMPUTE_BACKENDS:
        raise ValueError("compute_backend must be auto, cpu, or gpu")
    return backend


@contextmanager
def compute_backend_scope(backend: object):
    requested = normalize_compute_backend(backend)
    trace = ComputeBackendTrace(requested=requested)
    backend_token = _requested_backend.set(requested)
    trace_token = _backend_trace.set(trace)
    try:
        yield trace
    finally:
        _backend_trace.reset(trace_token)
        _requested_backend.reset(backend_token)


def current_compute_backend() -> str:
    return normalize_compute_backend(_requested_backend.get())


def _record_compute_backend(backend: str) -> None:
    trace = _backend_trace.get()
    if trace is not None:
        trace.used.add(backend)


def record_compute_backend(backend: str) -> None:
    if backend not in {"cpu", "gpu", "cache"}:
        raise ValueError("invalid actual compute backend")
    _record_compute_backend(backend)


def _library_name() -> str:
    if sys.platform == "darwin":
        return "libfiresky_native.dylib"
    if os.name == "nt":
        return "firesky_native.dll"
    return "libfiresky_native.so"


def _variant_artifact_name(variant: str) -> str:
    """Name of a build variant's cdylib artifact (variant "" = base build).

    cargo names the cdylib per platform: libfiresky_native.{dylib,so} on
    unix but firesky_native.dll on Windows, so variant file names must be
    derived from the platform base name instead of a hardcoded prefix.
    """
    base = _library_name()
    if not variant:
        return base
    stem = Path(base).stem
    return f"{stem}_{variant}{Path(base).suffix}"


def _library_candidates() -> list[Path]:
    explicit_path = os.environ.get("FIRESKY_NATIVE_LIBRARY_PATH", "").strip()
    if explicit_path:
        return [Path(explicit_path).expanduser()]

    name = _library_name()
    variant = os.environ.get("FIRESKY_NATIVE_VARIANT", "auto").strip().lower()
    if variant not in {"auto", "cpu", "gpu", "cpu-pgo", "gpu-pgo"}:
        variant = "auto"
    if variant in {"cpu", "gpu"}:
        names = [_variant_artifact_name(variant), name]
    elif variant in {"cpu-pgo", "gpu-pgo"}:
        names = [_variant_artifact_name(variant.replace("-", "_"))]
    else:
        names = [name, _variant_artifact_name("gpu"), _variant_artifact_name("cpu")]
    release_dir = Path(__file__).resolve().parents[2] / "native" / "target" / "release"
    candidates = [release_dir / candidate for candidate in names]
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        candidates.insert(0, Path(bundle_dir) / "firesky" / "native" / name)
    return candidates


def native_library():
    global _library, _load_attempted
    if _load_attempted:
        return _library
    with _library_load_lock:
        if _load_attempted:
            return _library
        _library = _load_native_library()
        _load_attempted = True
        return _library


def _native_source_mtime_ns() -> int:
    """Return the newest native source timestamp used by release artifacts."""
    native_root = Path(__file__).resolve().parents[2] / "native"
    paths = [native_root / "Cargo.toml", native_root / "Cargo.lock"]
    source_root = native_root / "src"
    if source_root.is_dir():
        paths.extend(source_root.rglob("*.rs"))
    mtimes: list[int] = []
    for path in paths:
        try:
            mtimes.append(path.stat().st_mtime_ns)
        except OSError:
            continue
    return max(mtimes, default=0)


def _native_artifact_is_current(path: Path) -> bool:
    """Avoid silently selecting a PGO artifact older than native sources."""
    try:
        return path.stat().st_mtime_ns >= _native_source_mtime_ns()
    except OSError:
        return False


def national_scoring_library(variant_override: str | None = None):
    """Load the requested native national scorer variant."""
    default_library = native_library()
    if default_library is None:
        return None

    explicit_path = os.environ.get("FIRESKY_NATIVE_NATIONAL_LIBRARY_PATH", "").strip()
    variant = (variant_override or os.environ.get("FIRESKY_NATIVE_NATIONAL_VARIANT", "auto")).strip().lower()
    if variant not in {"auto", "default", "cpu", "cpu-pgo", "gpu", "gpu-pgo"}:
        variant = "auto"
    if explicit_path:
        candidate = Path(explicit_path).expanduser()
    elif variant == "default":
        return default_library
    else:
        release_dir = Path(__file__).resolve().parents[2] / "native" / "target" / "release"
        candidate_name = {
            "auto": _variant_artifact_name("cpu_pgo"),
            "cpu": _variant_artifact_name("cpu"),
            "cpu-pgo": _variant_artifact_name("cpu_pgo"),
            "gpu": _variant_artifact_name("gpu"),
            "gpu-pgo": _variant_artifact_name("gpu_pgo"),
        }[variant]
        candidate = release_dir / candidate_name
        if not candidate.is_file():
            if variant in {"auto", "gpu"}:
                return default_library
            raise RuntimeError(f"national CPU scorer library not found: {candidate}")
        if variant == "auto" and not _native_artifact_is_current(candidate):
            return default_library

    cache_key = str(candidate.resolve())
    cached = _national_scoring_libraries.get(cache_key)
    if cached is not None:
        return cached
    if not candidate.is_file():
        raise RuntimeError(f"national scorer library not found: {candidate}")

    with _library_load_lock:
        cached = _national_scoring_libraries.get(cache_key)
        if cached is not None:
            return cached
        try:
            library = ctypes.CDLL(str(candidate))
            _require_native_abi_version(library)
            symbols = (
                "firesky_national_assess_batch",
                "firesky_national_audit_selected",
            )
            for symbol in symbols:
                function = getattr(library, symbol)
                reference = getattr(default_library, symbol)
                function.argtypes = reference.argtypes
                function.restype = reference.restype
            backend_function = getattr(library, "firesky_national_assess_batch_backend", None)
            backend_reference = getattr(
                default_library, "firesky_national_assess_batch_backend", None
            )
            if backend_function is not None and backend_reference is not None:
                backend_function.argtypes = backend_reference.argtypes
                backend_function.restype = backend_reference.restype
        except (OSError, AttributeError, RuntimeError) as exc:
            if variant == "auto" and not explicit_path:
                return default_library
            raise RuntimeError(
                f"failed to load national scorer library {candidate}: {exc}"
            ) from exc
        _national_scoring_libraries[cache_key] = library
        return library


def _require_native_abi_version(library) -> None:
    try:
        version_function = library.firesky_native_abi_version
    except AttributeError as exc:
        raise RuntimeError(
            f"native ABI version symbol is missing; expected {NATIVE_ABI_VERSION}"
        ) from exc
    version_function.argtypes = []
    version_function.restype = ctypes.c_uint32
    try:
        actual_version = int(version_function())
    except Exception as exc:
        raise RuntimeError(
            f"native ABI version lookup failed; expected {NATIVE_ABI_VERSION}"
        ) from exc
    if actual_version != NATIVE_ABI_VERSION:
        raise RuntimeError(
            f"native ABI version {actual_version} is incompatible; "
            f"expected {NATIVE_ABI_VERSION}"
        )


def _load_native_library():
    global _library, _load_error
    errors: list[str] = []
    _load_error = None
    for candidate in _library_candidates():
        if not candidate.is_file():
            if os.environ.get("FIRESKY_NATIVE_LIBRARY_PATH", "").strip():
                errors.append(f"{candidate}: file not found")
            continue
        try:
            library = ctypes.CDLL(str(candidate))
            _require_native_abi_version(library)
            function = library.firesky_interpolate_features
            function.argtypes = [
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_double),
            ]
            function.restype = ctypes.c_ssize_t
            indexed_function = library.firesky_interpolate_features_indexed
            indexed_function.argtypes = [
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
                ctypes.c_void_p,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_double),
            ]
            indexed_function.restype = ctypes.c_ssize_t
            grid_index_create = library.firesky_grid_index_create
            grid_index_create.argtypes = [ctypes.POINTER(ctypes.c_double), ctypes.c_size_t]
            grid_index_create.restype = ctypes.c_void_p
            grid_index_free = library.firesky_grid_index_free
            grid_index_free.argtypes = [ctypes.c_void_p]
            grid_index_free.restype = None
            output_len_function = library.firesky_interpolation_output_len
            output_len_function.argtypes = [
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
                ctypes.c_double,
                ctypes.c_double,
            ]
            output_len_function.restype = ctypes.c_ssize_t
            metrics_function = library.firesky_feature_metrics_batch
            metrics_function.argtypes = [
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
                ctypes.c_double,
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
            ]
            metrics_function.restype = ctypes.c_ssize_t
            assessment_metrics_function = getattr(
                library, "firesky_feature_metrics_batch_v2", None
            )
            if assessment_metrics_function is not None:
                assessment_metrics_function.argtypes = [
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.c_size_t,
                    ctypes.c_double,
                    ctypes.c_double,
                    ctypes.POINTER(ctypes.c_double),
                    ctypes.c_size_t,
                ]
                assessment_metrics_function.restype = ctypes.c_ssize_t
            gpu_available_function = getattr(library, "firesky_gpu_available", None)
            if gpu_available_function is not None:
                gpu_available_function.argtypes = []
                gpu_available_function.restype = ctypes.c_int
            gpu_metrics_function = getattr(library, "firesky_feature_metrics_gpu", None)
            if gpu_metrics_function is not None:
                gpu_metrics_function.argtypes = metrics_function.argtypes
                gpu_metrics_function.restype = ctypes.c_ssize_t
            candidate_function = library.firesky_candidate_prefilter_batch
            candidate_function.argtypes = [
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
            ]
            candidate_function.restype = ctypes.c_ssize_t
            gpu_candidate_function = getattr(library, "firesky_candidate_prefilter_gpu", None)
            if gpu_candidate_function is not None:
                gpu_candidate_function.argtypes = candidate_function.argtypes
                gpu_candidate_function.restype = ctypes.c_ssize_t
            boundary_prefilter_function = library.firesky_boundary_prefilter_batch
            boundary_prefilter_function.argtypes = [
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
                ctypes.c_double,
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
            ]
            boundary_prefilter_function.restype = ctypes.c_ssize_t
            gpu_boundary_prefilter_function = getattr(
                library,
                "firesky_boundary_prefilter_gpu",
                None,
            )
            if gpu_boundary_prefilter_function is not None:
                gpu_boundary_prefilter_function.argtypes = boundary_prefilter_function.argtypes
                gpu_boundary_prefilter_function.restype = ctypes.c_ssize_t
            columns_function = library.firesky_cloud_columns_batch
            columns_function.argtypes = [
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
                ctypes.c_double,
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
            ]
            columns_function.restype = ctypes.c_ssize_t
            scoring_function = library.firesky_path_clearance_batch
            scoring_function.argtypes = [
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_size_t),
                ctypes.c_size_t,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
            ]
            scoring_function.restype = ctypes.c_ssize_t
            gpu_scoring_function = getattr(library, "firesky_path_clearance_gpu", None)
            if gpu_scoring_function is not None:
                gpu_scoring_function.argtypes = scoring_function.argtypes
                gpu_scoring_function.restype = ctypes.c_ssize_t
            boundary_function = library.firesky_boundary_clearance_batch
            boundary_function.argtypes = [
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_size_t),
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
            ]
            boundary_function.restype = ctypes.c_ssize_t
            gpu_boundary_function = getattr(library, "firesky_boundary_clearance_gpu", None)
            if gpu_boundary_function is not None:
                gpu_boundary_function.argtypes = boundary_function.argtypes
                gpu_boundary_function.restype = ctypes.c_ssize_t
            boundary_context_function = library.firesky_boundary_context_batch
            boundary_context_function.argtypes = [
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_size_t),
                ctypes.c_size_t,
                ctypes.c_double,
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
            ]
            boundary_context_function.restype = ctypes.c_ssize_t
            gpu_boundary_context_function = getattr(library, "firesky_boundary_context_gpu", None)
            if gpu_boundary_context_function is not None:
                gpu_boundary_context_function.argtypes = boundary_context_function.argtypes
                gpu_boundary_context_function.restype = ctypes.c_ssize_t
            aerosol_function = library.firesky_aerosol_transmission_samples
            aerosol_function.argtypes = [
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
            ]
            aerosol_function.restype = ctypes.c_ssize_t
            aerosol_batch_function = library.firesky_aerosol_transmission_batch
            aerosol_batch_function.argtypes = [
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
            ]
            aerosol_batch_function.restype = ctypes.c_ssize_t
            cloud_hole_function = library.firesky_cloud_hole_batch
            cloud_hole_function.argtypes = [
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
                ctypes.c_double,
                ctypes.c_double,
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_size_t,
            ]
            cloud_hole_function.restype = ctypes.c_ssize_t
            national_function = library.firesky_national_assess_batch
            national_function.argtypes = [
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_size_t,
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_size_t,
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_size_t,
                ctypes.c_size_t,
                ctypes.c_size_t,
                ctypes.c_size_t,
                ctypes.c_float,
                ctypes.c_float,
                ctypes.c_float,
                ctypes.c_float,
                ctypes.POINTER(ctypes.c_float),
            ]
            national_function.restype = ctypes.c_int
            national_backend_function = getattr(
                library, "firesky_national_assess_batch_backend", None
            )
            if national_backend_function is not None:
                national_backend_function.argtypes = [
                    *national_function.argtypes,
                    ctypes.c_uint32,
                    ctypes.POINTER(ctypes.c_uint32),
                ]
                national_backend_function.restype = ctypes.c_int
            audit_function = library.firesky_national_audit_selected
            audit_function.argtypes = [
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_size_t,
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_size_t,
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_size_t,
                ctypes.c_size_t,
                ctypes.c_size_t,
                ctypes.c_size_t,
                ctypes.c_float,
                ctypes.c_float,
                ctypes.c_float,
                ctypes.c_float,
                ctypes.POINTER(ctypes.c_size_t),
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_float),
            ]
            audit_function.restype = ctypes.c_int
            target_audit_function = library.firesky_national_audit_selected_targets
            target_audit_function.argtypes = [
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_size_t,
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_size_t,
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_size_t,
                ctypes.c_size_t,
                ctypes.c_size_t,
                ctypes.c_size_t,
                ctypes.c_float,
                ctypes.c_float,
                ctypes.c_float,
                ctypes.c_float,
                ctypes.POINTER(ctypes.c_size_t),
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_float),
            ]
            target_audit_function.restype = ctypes.c_int
            low_target_distance_function = (
                library.firesky_national_min_remote_low_target_distance_km
            )
            low_target_distance_function.argtypes = []
            low_target_distance_function.restype = ctypes.c_float
            _library = library
            return library
        except (OSError, AttributeError, RuntimeError) as exc:
            errors.append(f"{candidate.name}: {exc}")
    if errors:
        _load_error = "native library unavailable; " + "; ".join(errors)
    else:
        _load_error = "native library not found"
    return None


def native_available() -> bool:
    return native_library() is not None


def native_gpu_available() -> bool:
    library = native_library()
    function = getattr(library, "firesky_gpu_available", None) if library is not None else None
    return function is not None and int(function()) == 1


def native_status() -> dict[str, object]:
    requested = current_compute_backend()
    gpu = native_gpu_available()
    return {
        "available": native_available(),
        "error": _load_error,
        "cpu_available": native_available(),
        "gpu_available": gpu,
        "requested_backend": requested,
        "default_backend": "gpu" if requested == "gpu" or requested == "auto" and gpu else "cpu",
        "gpu_auto_min_features": GPU_AUTO_MIN_FEATURES,
        "gpu_reduction_min_targets": GPU_REDUCTION_MIN_TARGETS,
    }


class NativeFeatureBuffer:
    """Buffer of legacy 20-column feature rows (`hsd-native-feature-row`).

    Rows cannot carry satellite probability columns: `feature_to_row` refuses
    them, so a legacy buffer proves the features it was built from carry none.
    """

    ROW_FIELD_COUNT = FIELD_COUNT
    has_satellite_probability = False

    def __init__(self, features: Sequence[GridCellFeature]) -> None:
        self.field_count = self.ROW_FIELD_COUNT
        self.length = len(features)
        if self.length:
            self._storage = np.fromiter(
                chain.from_iterable(
                    self.row_for_feature(feature) for feature in features
                ),
                dtype=np.float64,
                count=self.length * self.ROW_FIELD_COUNT,
            )
        else:
            self._storage = np.empty(1, dtype=np.float64)
        self.values = self._storage.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        self._grid_index = None
        self._grid_index_lock = threading.Lock()

    def row_for_feature(self, feature: GridCellFeature) -> tuple[float, ...]:
        return feature_to_row(feature)

    @classmethod
    def from_ctypes(cls, storage, length: int) -> NativeFeatureBuffer:
        buffer = cls.__new__(cls)
        buffer.field_count = cls.ROW_FIELD_COUNT
        buffer.length = length
        buffer._storage = np.ctypeslib.as_array(storage)[: length * cls.ROW_FIELD_COUNT]
        buffer.values = ctypes.cast(storage, ctypes.POINTER(ctypes.c_double))
        buffer._grid_index = None
        buffer._grid_index_lock = threading.Lock()
        return buffer

    @classmethod
    def from_numpy(cls, rows: np.ndarray) -> NativeFeatureBuffer:
        values = np.asarray(rows, dtype=np.float64)
        row_field_count = cls.ROW_FIELD_COUNT
        if values.ndim == 2:
            if values.shape[1] != row_field_count:
                raise ValueError(f"native feature rows must have {row_field_count} columns")
            length = values.shape[0]
        elif values.ndim == 1:
            if values.size % row_field_count != 0:
                raise ValueError(
                    f"native feature buffer size must be divisible by {row_field_count}"
                )
            length = values.size // row_field_count
        else:
            raise ValueError("native feature rows must be a one- or two-dimensional array")
        buffer = cls.__new__(cls)
        buffer.field_count = row_field_count
        buffer.length = int(length)
        buffer._storage = (
            np.ascontiguousarray(values, dtype=np.float64).reshape(-1)
            if length
            else np.empty(1, dtype=np.float64)
        )
        buffer.values = buffer._storage.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        buffer._grid_index = None
        buffer._grid_index_lock = threading.Lock()
        return buffer

    def grid_index(self):
        if self._grid_index is None:
            if self.field_count != FIELD_COUNT:
                raise ValueError(
                    "the native grid index consumes legacy 20-column rows; "
                    f"{self.field_count}-column buffers need their own contract"
                )
            with self._grid_index_lock:
                if self._grid_index is None:
                    library = native_library()
                    if library is None:
                        raise RuntimeError(_load_error or "native library unavailable")
                    handle = library.firesky_grid_index_create(self.values, self.length)
                    if not handle:
                        raise RuntimeError("native grid-index creation failed")
                    self._grid_index = handle
        return self._grid_index


class NativeAssessmentFeatureBuffer(NativeFeatureBuffer):
    """Buffer of `hsd-assessment-feature-row-v2` rows (27 columns).

    The v2 row extends the legacy 20 columns with the 2 m surface pair, the
    satellite footprint geometry and `horizontal_cloud_probability` /
    `cloud_probability_given_cloud` (None packed as NaN). Declaring
    `has_satellite_probability=True` keeps these rows out of every legacy
    20-column consumer so the probability columns cannot be dropped silently.
    """

    ROW_FIELD_COUNT = FEATURE_ROW_V2_FIELD_COUNT
    has_satellite_probability = True
    row_contract = FEATURE_ROW_V2_CONTRACT

    def row_for_feature(self, feature: GridCellFeature) -> tuple[float, ...]:
        return feature_to_row_v2(feature)

    def close(self) -> None:
        handle = getattr(self, "_grid_index", None)
        if handle:
            library = _library
            if library is not None:
                library.firesky_grid_index_free(handle)
            self._grid_index = None

    def __del__(self) -> None:
        self.close()


class NativeFeatureList(list):
    def __init__(self, features: Sequence[GridCellFeature], native_buffer: NativeFeatureBuffer) -> None:
        super().__init__(features)
        self.native_buffer = native_buffer


class NativeSnapshotFeatures(Sequence[GridCellFeature]):
    """A native snapshot buffer that materializes Python feature objects only on demand."""

    DECODE_FIELD_COUNT = FIELD_COUNT
    has_satellite_probability = False

    def __init__(self, native_buffer: NativeFeatureBuffer) -> None:
        buffer_field_count = int(getattr(native_buffer, "field_count", FIELD_COUNT))
        if buffer_field_count != self.DECODE_FIELD_COUNT:
            raise ValueError(
                "snapshot features decode "
                f"{self.DECODE_FIELD_COUNT}-column rows; got a {buffer_field_count}-column buffer"
            )
        self.native_buffer = native_buffer
        # The legacy row layout cannot carry satellite horizontal/conditional
        # probabilities.  Keep this contract explicit so callers do not infer
        # safety merely from the presence of a native buffer.
        self.has_satellite_probability = type(self).has_satellite_probability
        self._materialized: Optional[list[GridCellFeature]] = None
        self._rows: dict[int, GridCellFeature] = {}
        self._materialize_lock = threading.Lock()

    @property
    def is_materialized(self) -> bool:
        return self._materialized is not None

    @staticmethod
    def _decode_row(values, offset: int) -> GridCellFeature:
        return row_to_feature(values, offset)

    def materialize(self) -> list[GridCellFeature]:
        if self._materialized is None:
            with self._materialize_lock:
                if self._materialized is None:
                    values = self.native_buffer._storage
                    self._materialized = []
                    for index in range(self.native_buffer.length):
                        feature = self._rows.get(index)
                        if feature is None:
                            feature = self._decode_row(values, index * self.DECODE_FIELD_COUNT)
                            self._rows[index] = feature
                        self._materialized.append(feature)
        return self._materialized

    def feature_at(self, index: int) -> GridCellFeature:
        length = self.native_buffer.length
        normalized = index + length if index < 0 else index
        if normalized < 0 or normalized >= length:
            raise IndexError("native feature index out of range")
        feature = self._rows.get(normalized)
        if feature is not None:
            return feature
        with self._materialize_lock:
            feature = self._rows.get(normalized)
            if feature is None:
                feature = self._decode_row(
                    self.native_buffer._storage,
                    normalized * self.DECODE_FIELD_COUNT,
                )
                self._rows[normalized] = feature
            return feature

    def __len__(self) -> int:
        return self.native_buffer.length

    def __getitem__(self, index):
        if isinstance(index, slice):
            return self.materialize()[index]
        return self.feature_at(index)

    def __iter__(self):
        return iter(self.materialize())


class NativeAssessmentSnapshotFeatures(NativeSnapshotFeatures):
    """On-demand feature view over `hsd-assessment-feature-row-v2` rows.

    Mirrors `NativeSnapshotFeatures` for the 27-column assessment layout: the
    satellite probability columns survive decode (NaN -> None) and the declared
    `has_satellite_probability=True` contract lets scoring route these
    snapshots to the v2 native metrics instead of the Python path.
    """

    DECODE_FIELD_COUNT = FEATURE_ROW_V2_FIELD_COUNT
    has_satellite_probability = True

    @staticmethod
    def _decode_row(values, offset: int) -> GridCellFeature:
        return row_to_feature_v2(values, offset)


def optional_value(value: Optional[float]) -> float:
    return math.nan if value is None else float(value)


def feature_to_row(feature: GridCellFeature) -> tuple[float, ...]:
    if (
        feature.horizontal_cloud_probability is not None
        or feature.cloud_probability_given_cloud is not None
    ):
        raise ValueError(
            "satellite cloud-probability features cannot be packed into the legacy native buffer"
        )
    return (
        feature.lat,
        feature.lon,
        feature.distance_km,
        feature.bearing_deg,
        float(feature.pressure_hpa),
        feature.geopotential_height_m,
        feature.rh_percent,
        optional_value(feature.temperature_k),
        optional_value(feature.omega_pa_s),
        optional_value(feature.u_ms),
        optional_value(feature.v_ms),
        optional_value(feature.cloud_fraction_percent),
        optional_value(feature.cloud_liquid_kg_kg),
        optional_value(feature.cloud_ice_kg_kg),
        optional_value(feature.source_lat),
        optional_value(feature.source_lon),
        optional_value(feature.source_distance_km),
        optional_value(feature.source_bearing_deg),
        optional_value(feature.surface_visibility_m),
        optional_value(feature.precip_rate_kg_m2_s),
    )


def row_to_feature(values, offset: int) -> GridCellFeature:
    temperature = values[offset + 7]
    omega = values[offset + 8]
    u_wind = values[offset + 9]
    v_wind = values[offset + 10]
    cloud_fraction = values[offset + 11]
    cloud_liquid = values[offset + 12]
    cloud_ice = values[offset + 13]
    source_lat = values[offset + 14]
    source_lon = values[offset + 15]
    source_distance = values[offset + 16]
    source_bearing = values[offset + 17]
    visibility = values[offset + 18]
    precipitation = values[offset + 19]
    return GridCellFeature(
        lat=values[offset],
        lon=values[offset + 1],
        distance_km=values[offset + 2],
        bearing_deg=values[offset + 3],
        pressure_hpa=int(values[offset + 4]),
        geopotential_height_m=values[offset + 5],
        rh_percent=values[offset + 6],
        temperature_k=None if temperature != temperature else temperature,
        omega_pa_s=None if omega != omega else omega,
        u_ms=None if u_wind != u_wind else u_wind,
        v_ms=None if v_wind != v_wind else v_wind,
        cloud_fraction_percent=None if cloud_fraction != cloud_fraction else cloud_fraction,
        cloud_liquid_kg_kg=None if cloud_liquid != cloud_liquid else cloud_liquid,
        cloud_ice_kg_kg=None if cloud_ice != cloud_ice else cloud_ice,
        source_lat=None if source_lat != source_lat else source_lat,
        source_lon=None if source_lon != source_lon else source_lon,
        source_distance_km=None if source_distance != source_distance else source_distance,
        source_bearing_deg=None if source_bearing != source_bearing else source_bearing,
        surface_visibility_m=None if visibility != visibility else visibility,
        precip_rate_kg_m2_s=None if precipitation != precipitation else precipitation,
    )


def feature_to_row_v2(feature: GridCellFeature) -> tuple[float, ...]:
    """Pack one `hsd-assessment-feature-row-v2` row (27 columns).

    Columns 0..19 keep the legacy `feature_to_row` semantics verbatim; the
    seven v2 columns append the 2 m surface pair, the satellite footprint
    geometry and the satellite probability pair. None packs as NaN and NaN
    unpacks as None — unknown never becomes 0.
    """
    return (
        feature.lat,
        feature.lon,
        feature.distance_km,
        feature.bearing_deg,
        float(feature.pressure_hpa),
        feature.geopotential_height_m,
        feature.rh_percent,
        optional_value(feature.temperature_k),
        optional_value(feature.omega_pa_s),
        optional_value(feature.u_ms),
        optional_value(feature.v_ms),
        optional_value(feature.cloud_fraction_percent),
        optional_value(feature.cloud_liquid_kg_kg),
        optional_value(feature.cloud_ice_kg_kg),
        optional_value(feature.source_lat),
        optional_value(feature.source_lon),
        optional_value(feature.source_distance_km),
        optional_value(feature.source_bearing_deg),
        optional_value(feature.surface_visibility_m),
        optional_value(feature.precip_rate_kg_m2_s),
        optional_value(feature.surface_temperature_k),
        optional_value(feature.surface_dewpoint_k),
        optional_value(feature.footprint_azimuth_width_deg),
        optional_value(feature.footprint_near_distance_km),
        optional_value(feature.footprint_far_distance_km),
        optional_value(feature.horizontal_cloud_probability),
        optional_value(feature.cloud_probability_given_cloud),
    )


def row_to_feature_v2(values, offset: int) -> GridCellFeature:
    """Unpack one 27-column `hsd-assessment-feature-row-v2` row."""
    surface_temperature = values[offset + FEATURE_ROW_V2_SURFACE_TEMPERATURE_K]
    surface_dewpoint = values[offset + FEATURE_ROW_V2_SURFACE_DEWPOINT_K]
    footprint_width = values[offset + FEATURE_ROW_V2_FOOTPRINT_AZIMUTH_WIDTH_DEG]
    footprint_near = values[offset + FEATURE_ROW_V2_FOOTPRINT_NEAR_DISTANCE_KM]
    footprint_far = values[offset + FEATURE_ROW_V2_FOOTPRINT_FAR_DISTANCE_KM]
    horizontal_probability = values[offset + FEATURE_ROW_V2_HORIZONTAL_CLOUD_PROBABILITY]
    conditional_probability = values[offset + FEATURE_ROW_V2_CLOUD_PROBABILITY_GIVEN_CLOUD]
    legacy = row_to_feature(values, offset)
    return GridCellFeature(
        lat=legacy.lat,
        lon=legacy.lon,
        distance_km=legacy.distance_km,
        bearing_deg=legacy.bearing_deg,
        pressure_hpa=legacy.pressure_hpa,
        geopotential_height_m=legacy.geopotential_height_m,
        rh_percent=legacy.rh_percent,
        temperature_k=legacy.temperature_k,
        omega_pa_s=legacy.omega_pa_s,
        u_ms=legacy.u_ms,
        v_ms=legacy.v_ms,
        cloud_fraction_percent=legacy.cloud_fraction_percent,
        cloud_liquid_kg_kg=legacy.cloud_liquid_kg_kg,
        cloud_ice_kg_kg=legacy.cloud_ice_kg_kg,
        source_lat=legacy.source_lat,
        source_lon=legacy.source_lon,
        source_distance_km=legacy.source_distance_km,
        source_bearing_deg=legacy.source_bearing_deg,
        surface_visibility_m=legacy.surface_visibility_m,
        precip_rate_kg_m2_s=legacy.precip_rate_kg_m2_s,
        surface_temperature_k=None if surface_temperature != surface_temperature else surface_temperature,
        surface_dewpoint_k=None if surface_dewpoint != surface_dewpoint else surface_dewpoint,
        footprint_azimuth_width_deg=None if footprint_width != footprint_width else footprint_width,
        footprint_near_distance_km=None if footprint_near != footprint_near else footprint_near,
        footprint_far_distance_km=None if footprint_far != footprint_far else footprint_far,
        horizontal_cloud_probability=(
            None if horizontal_probability != horizontal_probability else horizontal_probability
        ),
        cloud_probability_given_cloud=(
            None if conditional_probability != conditional_probability else conditional_probability
        ),
    )


def interpolate_features(
    left: NativeFeatureBuffer,
    right: NativeFeatureBuffer,
    left_valid_seconds: float,
    right_valid_seconds: float,
    evaluation_seconds: float,
    relevant_azimuth_deg: Optional[float],
    local_radius_km: float,
) -> tuple[Sequence[GridCellFeature], float, float]:
    library = native_library()
    if library is None:
        raise RuntimeError(_load_error or "native library unavailable")
    for buffer in (left, right):
        if int(getattr(buffer, "field_count", FIELD_COUNT)) != FIELD_COUNT:
            raise ValueError(
                "feature interpolation consumes legacy 20-column rows; "
                f"{int(getattr(buffer, 'field_count', FIELD_COUNT))}-column buffers need their own contract"
            )
    azimuth = math.nan if relevant_azimuth_deg is None else relevant_azimuth_deg
    output_length = library.firesky_interpolation_output_len(
        left.values,
        left.length,
        azimuth,
        local_radius_km,
    )
    if output_length < 0:
        raise RuntimeError(f"native interpolation sizing failed with code {output_length}")
    output_type = ctypes.c_double * max(1, output_length * FIELD_COUNT)
    output = output_type()
    motion = (ctypes.c_double * 2)()
    result = library.firesky_interpolate_features_indexed(
        left.values,
        left.length,
        left.grid_index(),
        right.values,
        right.length,
        right.grid_index(),
        left_valid_seconds,
        right_valid_seconds,
        evaluation_seconds,
        azimuth,
        local_radius_km,
        output,
        output_length,
        motion,
    )
    if result < 0:
        raise RuntimeError(f"native interpolation failed with code {result}")
    native_buffer = NativeFeatureBuffer.from_ctypes(output, result)
    features = NativeSnapshotFeatures(native_buffer)
    return features, float(motion[0]), float(motion[1])


def feature_metrics(
    feature_buffer: NativeFeatureBuffer,
    diagnostic_radius_km: float,
    observer_elevation_m: float = 0.0,
) -> np.ndarray:
    field_count = int(getattr(feature_buffer, "field_count", FIELD_COUNT))
    if field_count == FEATURE_ROW_V2_FIELD_COUNT:
        return assessment_feature_metrics(
            feature_buffer,
            diagnostic_radius_km,
            observer_elevation_m,
        )
    if observer_elevation_m != 0.0:
        raise ValueError(
            "legacy 20-column feature metrics have no observer-elevation input; "
            "pack hsd-assessment-feature-row-v2 rows instead"
        )
    library = native_library()
    if library is None:
        raise RuntimeError(_load_error or "native library unavailable")
    requested = current_compute_backend()
    use_gpu = requested == "gpu" or (
        requested == "auto"
        and feature_buffer.length >= GPU_AUTO_MIN_FEATURES
        and native_gpu_available()
    )
    output = np.empty((feature_buffer.length, METRIC_COUNT), dtype=np.float64)
    function = (
        getattr(library, "firesky_feature_metrics_gpu", None)
        if use_gpu
        else library.firesky_feature_metrics_batch
    )
    if use_gpu and (function is None or not native_gpu_available()):
        if requested == "gpu":
            raise RuntimeError("GPU compute backend is unavailable")
        function = library.firesky_feature_metrics_batch
        use_gpu = False
    result = function(
        feature_buffer.values,
        feature_buffer.length,
        diagnostic_radius_km,
        output.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        feature_buffer.length,
    )
    if result < 0:
        if requested == "auto" and use_gpu:
            result = library.firesky_feature_metrics_batch(
                feature_buffer.values,
                feature_buffer.length,
                diagnostic_radius_km,
                output.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
                feature_buffer.length,
            )
            use_gpu = False
        if result < 0:
            backend = "GPU" if use_gpu else "CPU"
            raise RuntimeError(f"native {backend} feature metrics failed with code {result}")
    _record_compute_backend("gpu" if use_gpu else "cpu")
    return output[:result]


def assessment_feature_metrics(
    feature_buffer: NativeFeatureBuffer,
    diagnostic_radius_km: float,
    observer_elevation_m: float = 0.0,
) -> np.ndarray:
    """Assessment metrics over `hsd-assessment-feature-row-v2` rows.

    Calls `firesky_feature_metrics_batch_v2`: the same 9-metric layout as the
    legacy batch, plus the satellite probability semantics and the
    observer-elevation aware surface-LCL cloud base / apparent top. The GPU
    backend only implements the legacy 20-column kernel, so an explicit GPU
    request fails closed instead of silently reading the wrong row stride.
    """
    library = native_library()
    if library is None:
        raise RuntimeError(_load_error or "native library unavailable")
    if int(getattr(feature_buffer, "field_count", FIELD_COUNT)) != FEATURE_ROW_V2_FIELD_COUNT:
        raise ValueError(
            f"assessment feature metrics require {FEATURE_ROW_V2_FIELD_COUNT}-column "
            f"{FEATURE_ROW_V2_CONTRACT} rows"
        )
    requested = current_compute_backend()
    if requested == "gpu":
        raise RuntimeError("GPU compute backend has no assessment feature-metrics kernel")
    function = getattr(library, "firesky_feature_metrics_batch_v2", None)
    if function is None:
        raise RuntimeError(
            "native library lacks firesky_feature_metrics_batch_v2; rebuild native/"
        )
    output = np.empty((feature_buffer.length, METRIC_COUNT), dtype=np.float64)
    result = function(
        feature_buffer.values,
        feature_buffer.length,
        diagnostic_radius_km,
        observer_elevation_m,
        output.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        feature_buffer.length,
    )
    if result < 0:
        raise RuntimeError(f"native assessment feature metrics failed with code {result}")
    _record_compute_backend("cpu")
    return output[:result]


def candidate_prefilter(
    feature_buffer: NativeFeatureBuffer,
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
) -> np.ndarray:
    library = native_library()
    if library is None:
        raise RuntimeError(_load_error or "native library unavailable")
    requested = current_compute_backend()
    use_gpu = requested == "gpu" or (
        requested == "auto"
        and feature_buffer.length >= GPU_AUTO_MIN_FEATURES
        and native_gpu_available()
    )
    function = (
        getattr(library, "firesky_candidate_prefilter_gpu", None)
        if use_gpu
        else library.firesky_candidate_prefilter_batch
    )
    if use_gpu and (function is None or not native_gpu_available()):
        if requested == "gpu":
            raise RuntimeError("GPU candidate prefilter is unavailable")
        function = library.firesky_candidate_prefilter_batch
        use_gpu = False
    output = np.empty(
        (feature_buffer.length, CANDIDATE_PREFILTER_FIELDS),
        dtype=np.float64,
    )
    result = function(
        feature_buffer.values,
        feature_buffer.length,
        solar_elevation_deg,
        solar_azimuth_deg,
        output.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        feature_buffer.length,
    )
    if result < 0 and requested == "auto" and use_gpu:
        result = library.firesky_candidate_prefilter_batch(
            feature_buffer.values,
            feature_buffer.length,
            solar_elevation_deg,
            solar_azimuth_deg,
            output.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            feature_buffer.length,
        )
        use_gpu = False
    if result < 0:
        backend = "GPU" if use_gpu else "CPU"
        raise RuntimeError(f"native {backend} candidate prefilter failed with code {result}")
    _record_compute_backend("gpu" if use_gpu else "cpu")
    return output[:result]


def boundary_prefilter(
    feature_buffer: NativeFeatureBuffer,
    solar_azimuth_deg: float,
) -> np.ndarray:
    library = native_library()
    if library is None:
        raise RuntimeError(_load_error or "native library unavailable")
    requested = current_compute_backend()
    use_gpu = requested == "gpu" or (
        requested == "auto"
        and feature_buffer.length >= GPU_AUTO_MIN_FEATURES
        and native_gpu_available()
    )
    function = (
        getattr(library, "firesky_boundary_prefilter_gpu", None)
        if use_gpu
        else library.firesky_boundary_prefilter_batch
    )
    if use_gpu and (function is None or not native_gpu_available()):
        if requested == "gpu":
            raise RuntimeError("GPU boundary prefilter is unavailable")
        function = library.firesky_boundary_prefilter_batch
        use_gpu = False
    output = np.empty(
        (feature_buffer.length, BOUNDARY_PREFILTER_FIELDS),
        dtype=np.float64,
    )
    result = function(
        feature_buffer.values,
        feature_buffer.length,
        solar_azimuth_deg,
        output.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        feature_buffer.length,
    )
    if result < 0 and requested == "auto" and use_gpu:
        result = library.firesky_boundary_prefilter_batch(
            feature_buffer.values,
            feature_buffer.length,
            solar_azimuth_deg,
            output.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
            feature_buffer.length,
        )
        use_gpu = False
    if result < 0:
        backend = "GPU" if use_gpu else "CPU"
        raise RuntimeError(f"native {backend} boundary prefilter failed with code {result}")
    _record_compute_backend("gpu" if use_gpu else "cpu")
    return output[:result]


def aggregate_cloud_columns(
    feature_buffer: NativeFeatureBuffer,
    max_distance_km: float,
) -> np.ndarray:
    library = native_library()
    if library is None:
        raise RuntimeError(_load_error or "native library unavailable")
    output = np.empty((feature_buffer.length, AGG_COLUMN_FIELDS), dtype=np.float64)
    result = library.firesky_cloud_columns_batch(
        feature_buffer.values,
        feature_buffer.length,
        max_distance_km,
        output.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        feature_buffer.length,
    )
    if result < 0:
        raise RuntimeError(f"native cloud-column aggregation failed with code {result}")
    return output[:result]


def path_clearance_weights(
    feature_buffer: NativeFeatureBuffer,
    candidate_indices: Sequence[int],
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
) -> list[float]:
    if not candidate_indices:
        return []
    library = native_library()
    if library is None:
        raise RuntimeError(_load_error or "native library unavailable")
    index_type = ctypes.c_size_t * len(candidate_indices)
    indices = index_type(*candidate_indices)
    output_type = ctypes.c_double * len(candidate_indices)
    output = output_type()
    requested = current_compute_backend()
    use_gpu = (
        requested == "gpu"
        and len(candidate_indices) >= GPU_REDUCTION_MIN_TARGETS
        and native_gpu_available()
    )
    function = (
        getattr(library, "firesky_path_clearance_gpu", None)
        if use_gpu
        else library.firesky_path_clearance_batch
    )
    if function is None:
        if requested == "gpu":
            raise RuntimeError("GPU path-clearance kernel is unavailable")
        function = library.firesky_path_clearance_batch
        use_gpu = False
    result = function(
        feature_buffer.values,
        feature_buffer.length,
        indices,
        len(candidate_indices),
        solar_elevation_deg,
        solar_azimuth_deg,
        output,
        len(candidate_indices),
    )
    if result < 0:
        if requested == "auto" and use_gpu:
            result = library.firesky_path_clearance_batch(
                feature_buffer.values,
                feature_buffer.length,
                indices,
                len(candidate_indices),
                solar_elevation_deg,
                solar_azimuth_deg,
                output,
                len(candidate_indices),
            )
            use_gpu = False
        if result < 0:
            raise RuntimeError(f"native path-clearance scoring failed with code {result}")
    _record_compute_backend("gpu" if use_gpu else "cpu")
    return [float(output[index]) for index in range(result)]


def boundary_clearance_weights(
    feature_buffer: NativeFeatureBuffer,
    target_indices: Sequence[int],
    target_heights_m: Sequence[float],
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
) -> list[float]:
    if len(target_indices) != len(target_heights_m):
        raise ValueError("target indices and heights must have the same length")
    if not target_indices:
        return []
    library = native_library()
    if library is None:
        raise RuntimeError(_load_error or "native library unavailable")
    index_type = ctypes.c_size_t * len(target_indices)
    indices = index_type(*target_indices)
    height_type = ctypes.c_double * len(target_heights_m)
    heights = height_type(*target_heights_m)
    output_type = ctypes.c_double * len(target_indices)
    output = output_type()
    requested = current_compute_backend()
    use_gpu = (
        requested == "gpu"
        and len(target_indices) >= GPU_REDUCTION_MIN_TARGETS
        and native_gpu_available()
    )
    function = (
        getattr(library, "firesky_boundary_clearance_gpu", None)
        if use_gpu
        else library.firesky_boundary_clearance_batch
    )
    if function is None:
        if requested == "gpu":
            raise RuntimeError("GPU boundary-clearance kernel is unavailable")
        function = library.firesky_boundary_clearance_batch
        use_gpu = False
    result = function(
        feature_buffer.values,
        feature_buffer.length,
        indices,
        heights,
        len(target_indices),
        solar_elevation_deg,
        solar_azimuth_deg,
        output,
        len(target_indices),
    )
    if result < 0:
        raise RuntimeError(f"native boundary-clearance scoring failed with code {result}")
    _record_compute_backend("gpu" if use_gpu else "cpu")
    return [float(output[index]) for index in range(result)]


def boundary_context_scores(
    feature_buffer: NativeFeatureBuffer,
    target_indices: Sequence[int],
    solar_azimuth_deg: float,
) -> list[tuple[float, float]]:
    if not target_indices:
        return []
    library = native_library()
    if library is None:
        raise RuntimeError(_load_error or "native library unavailable")
    index_type = ctypes.c_size_t * len(target_indices)
    indices = index_type(*target_indices)
    output = np.empty((len(target_indices), 2), dtype=np.float64)
    requested = current_compute_backend()
    use_gpu = (
        requested == "gpu"
        and len(target_indices) >= GPU_REDUCTION_MIN_TARGETS
        and native_gpu_available()
    )
    function = (
        getattr(library, "firesky_boundary_context_gpu", None)
        if use_gpu
        else library.firesky_boundary_context_batch
    )
    if function is None:
        if requested == "gpu":
            raise RuntimeError("GPU boundary-context kernel is unavailable")
        function = library.firesky_boundary_context_batch
        use_gpu = False
    result = function(
        feature_buffer.values,
        feature_buffer.length,
        indices,
        len(target_indices),
        solar_azimuth_deg,
        output.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        len(target_indices),
    )
    if result < 0:
        raise RuntimeError(f"native boundary-context scoring failed with code {result}")
    _record_compute_backend("gpu" if use_gpu else "cpu")
    return [(float(row[0]), float(row[1])) for row in output[:result]]


def aerosol_transmission_samples(
    aod550: float,
    observer_aod550: float,
    inflow_aod550: float,
    target_distance_km: float,
    target_height_m: float,
    apparent_elevation_deg: float,
    solar_elevation_deg: float,
    observer_elevation_m: float,
) -> list[float]:
    library = native_library()
    if library is None:
        raise RuntimeError(_load_error or "native library unavailable")
    output = (ctypes.c_double * 3)()
    result = library.firesky_aerosol_transmission_samples(
        aod550,
        observer_aod550,
        inflow_aod550,
        target_distance_km,
        target_height_m,
        apparent_elevation_deg,
        solar_elevation_deg,
        observer_elevation_m,
        output,
        3,
    )
    if result < 0:
        raise RuntimeError(f"native aerosol scoring failed with code {result}")
    return [float(output[index]) for index in range(result)]


def aerosol_transmission_batch(inputs: np.ndarray) -> np.ndarray:
    rows = np.ascontiguousarray(inputs, dtype=np.float64)
    if rows.ndim != 2 or rows.shape[1] != 8:
        raise ValueError("aerosol batch inputs must have shape (n, 8)")
    if len(rows) == 0:
        return np.empty((0, 5), dtype=np.float64)
    library = native_library()
    if library is None:
        raise RuntimeError(_load_error or "native library unavailable")
    output = np.empty((len(rows), 5), dtype=np.float64)
    result = library.firesky_aerosol_transmission_batch(
        rows.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        len(rows),
        output.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        len(rows),
    )
    if result < 0:
        raise RuntimeError(f"native aerosol batch scoring failed with code {result}")
    return output[:result]


def _national_native_tensors(
    grid: np.ndarray,
    aerosol_profiles: np.ndarray,
    observers: np.ndarray,
    samples: np.ndarray,
    *,
    rows: int,
    cols: int,
    validate_aerosol: bool = True,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    grid_values = np.ascontiguousarray(grid, dtype=np.float32)
    aerosol_values = np.ascontiguousarray(aerosol_profiles, dtype=np.float32)
    observer_values = np.ascontiguousarray(observers, dtype=np.float32)
    sample_values = np.ascontiguousarray(samples, dtype=np.float32)
    if grid_values.ndim != 3 or grid_values.shape[2] != NATIONAL_GRID_FIELDS:
        raise ValueError(
            f"national grid must have shape (time, cell, {NATIONAL_GRID_FIELDS})"
        )
    if observer_values.ndim != 2 or observer_values.shape[1] != NATIONAL_OBSERVER_FIELDS:
        raise ValueError("national observers must have shape (point, 3)")
    if sample_values.ndim != 3 or sample_values.shape[2] != NATIONAL_SAMPLE_FIELDS:
        raise ValueError("national samples must have shape (point, step, 9)")
    if sample_values.shape[0] != observer_values.shape[0]:
        raise ValueError("national observer and sample counts differ")
    if rows * cols != grid_values.shape[1]:
        raise ValueError("national grid dimensions do not match cell count")
    expected_aerosol_shape = (
        grid_values.shape[0],
        grid_values.shape[1],
        NATIONAL_AEROSOL_PROFILE_LEVELS,
    )
    if aerosol_values.shape != expected_aerosol_shape:
        raise ValueError(
            "national aerosol profiles must have shape "
            f"{expected_aerosol_shape}"
        )
    if validate_aerosol:
        if not np.isfinite(aerosol_values).all() or np.any(aerosol_values < 0.0):
            raise ValueError("national aerosol profiles must be finite and non-negative")
        aerosol_aod = grid_values[:, :, 25]
        if (
            not np.isfinite(aerosol_aod).all()
            or np.any(aerosol_aod < 0.0)
            or np.any(aerosol_aod > 10.0)
        ):
            raise ValueError("national aerosol AOD must be finite and between 0 and 10")
        if np.any((aerosol_aod > 0.0) & (np.sum(aerosol_values, axis=2) <= 0.0)):
            raise ValueError("positive national aerosol AOD requires a non-zero profile")
    return grid_values, aerosol_values, observer_values, sample_values


def national_assess_batch(
    grid: np.ndarray,
    aerosol_profiles: np.ndarray,
    observers: np.ndarray,
    samples: np.ndarray,
    *,
    rows: int,
    cols: int,
    lat0: float,
    lon0: float,
    spacing: float,
    radius_km: float,
    compute_backend: str | None = None,
) -> np.ndarray:
    requested = normalize_compute_backend(compute_backend or current_compute_backend())
    library = national_scoring_library(requested if requested in {"cpu", "gpu"} else None)
    if library is None:
        raise RuntimeError(_load_error or "native library unavailable")
    validation_version = getattr(
        library, "firesky_national_aerosol_validation_version", None
    )
    native_validates_aerosol = False
    if validation_version is not None:
        validation_version.argtypes = []
        validation_version.restype = ctypes.c_uint32
        native_validates_aerosol = validation_version() >= 1
    grid_values, aerosol_values, observer_values, sample_values = (
        _national_native_tensors(
            grid,
            aerosol_profiles,
            observers,
            samples,
            rows=rows,
            cols=cols,
            validate_aerosol=not native_validates_aerosol,
        )
    )
    output = np.empty(
        (observer_values.shape[0], sample_values.shape[1], NATIONAL_OUTPUT_FIELDS),
        dtype=np.float32,
    )
    call_args = (
        grid_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        grid_values.shape[0],
        grid_values.shape[1],
        aerosol_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        aerosol_values.size,
        aerosol_values.shape[2],
        observer_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        observer_values.shape[0],
        sample_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        sample_values.shape[0] * sample_values.shape[1],
        sample_values.shape[1],
        rows,
        cols,
        float(lat0),
        float(lon0),
        float(spacing),
        float(radius_km),
        output.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
    )
    backend_actual = ctypes.c_uint32(0)
    backend_function = getattr(library, "firesky_national_assess_batch_backend", None)
    if backend_function is not None:
        request_gpu = requested == "gpu" or (
            requested == "auto"
            and "FIRESKY_NATIVE_ENABLE_NATIONAL_PROFILE_GPU" in os.environ
        )
        result = backend_function(
            *call_args, ctypes.c_uint32(int(request_gpu)), ctypes.byref(backend_actual)
        )
    else:
        result = library.firesky_national_assess_batch(*call_args)
    if result == -4:
        raise ValueError(
            "national aerosol profiles and AOD must be finite, non-negative, "
            "within range, and consistent"
        )
    if result != 0:
        raise RuntimeError(f"native national assessment failed with code {result}")
    # Contract note: unlike feature_metrics/candidate_prefilter (which
    # fail-fast on an explicit gpu request), the national batch intentionally
    # accepts a CPU fallback when Metal is unavailable — the Rust ABI reports
    # backend_actual=0 as a *valid* CPU fallback (lib.rs ABI docs) and the
    # scalar-parity probe guarantees identical results. Callers that must
    # observe the real backend read compute_backend_trace/national_gpu_fallback.
    if requested == "cpu" and backend_actual.value != 0:
        raise RuntimeError("native scorer executed GPU despite an explicit CPU request")
    _record_compute_backend("gpu" if backend_actual.value == 1 else "cpu")
    return output


def national_path_audit_selected(
    grid: np.ndarray,
    aerosol_profiles: np.ndarray,
    observers: np.ndarray,
    samples: np.ndarray,
    audit_indices: Sequence[int],
    *,
    rows: int,
    cols: int,
    lat0: float,
    lon0: float,
    spacing: float,
    radius_km: float,
) -> np.ndarray:
    """Return offline path diagnostics for selected flattened sample rows.

    The native routine reuses the observer-field scorer but does not return or
    alter current national frame output. ``audit_indices`` indexes the flattened
    ``(observer, step)`` sample rows and the result has one 36-field v2 row per
    requested index.
    """
    grid_values, aerosol_values, observer_values, sample_values = (
        _national_native_tensors(
            grid,
            aerosol_profiles,
            observers,
            samples,
            rows=rows,
            cols=cols,
        )
    )
    try:
        raw_indices = list(audit_indices)
    except TypeError as exc:
        raise TypeError("national path audit indices must be an iterable of integers") from exc
    for value in raw_indices:
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
            raise TypeError("national path audit indices must contain integers, not bool/float")
        if int(value) < 0:
            raise ValueError("national path audit indices must be non-negative")
    indices = np.asarray(raw_indices, dtype=object)
    if not raw_indices:
        return np.empty((0, PATH_AUDIT_FIELDS), dtype=np.float32)
    max_index = sample_values.shape[0] * sample_values.shape[1]
    if any(int(value) >= max_index for value in raw_indices):
        raise ValueError("national path audit index is outside the sample buffer")
    indices = np.ascontiguousarray(indices, dtype=np.uintp)
    library = native_library()
    if library is None:
        raise RuntimeError(_load_error or "native library unavailable")
    output = np.empty((len(indices), PATH_AUDIT_FIELDS), dtype=np.float32)
    result = library.firesky_national_audit_selected(
        grid_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        grid_values.shape[0],
        grid_values.shape[1],
        aerosol_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        aerosol_values.size,
        aerosol_values.shape[2],
        observer_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        observer_values.shape[0],
        sample_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        sample_values.shape[0] * sample_values.shape[1],
        sample_values.shape[1],
        rows,
        cols,
        float(lat0),
        float(lon0),
        float(spacing),
        float(radius_km),
        indices.ctypes.data_as(ctypes.POINTER(ctypes.c_size_t)),
        len(indices),
        output.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
    )
    if result != 0:
        raise RuntimeError(f"native national path audit failed with code {result}")
    if not np.isfinite(output).all():
        raise RuntimeError("native national path audit returned non-finite values")
    schema_offset = PATH_AUDIT_FIELD_OFFSETS["schema_version"]
    schema_values = output[:, schema_offset]
    expected_schema = np.float32(PATH_AUDIT_SCHEMA_VERSION)
    if not np.all(schema_values == expected_schema):
        mismatch = np.flatnonzero(schema_values != expected_schema)[0]
        actual_schema = float(schema_values[mismatch])
        raise RuntimeError(
            f"native national path audit schema {actual_schema:g} is obsolete; "
            f"expected {PATH_AUDIT_SCHEMA_VERSION:g}"
        )
    return output


def national_target_audit_selected(
    grid: np.ndarray,
    aerosol_profiles: np.ndarray,
    observers: np.ndarray,
    samples: np.ndarray,
    audit_indices: Sequence[int],
    *,
    rows: int,
    cols: int,
    lat0: float,
    lon0: float,
    spacing: float,
    radius_km: float,
) -> np.ndarray:
    """Return top per-target diagnostics for offline selected sample rows.

    The ABI reuses the normal projection/compositor and solar path once. It is
    deliberately absent from national frame assembly and has a strict schema.
    """
    grid_values, aerosol_values, observer_values, sample_values = (
        _national_native_tensors(
            grid,
            aerosol_profiles,
            observers,
            samples,
            rows=rows,
            cols=cols,
        )
    )
    try:
        raw_indices = list(audit_indices)
    except TypeError as exc:
        raise TypeError("national target audit indices must be an iterable") from exc
    for value in raw_indices:
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
            raise TypeError("national target audit indices must contain integers")
        if int(value) < 0:
            raise ValueError("national target audit indices must be non-negative")
    if not raw_indices:
        return np.empty((0, TARGET_AUDIT_MAX_RECORDS, TARGET_AUDIT_FIELDS), dtype=np.float32)
    max_index = sample_values.shape[0] * sample_values.shape[1]
    if any(int(value) >= max_index for value in raw_indices):
        raise ValueError("national target audit index is outside the sample buffer")
    indices = np.ascontiguousarray(np.asarray(raw_indices, dtype=np.uintp))
    library = native_library()
    if library is None:
        raise RuntimeError(_load_error or "native library unavailable")
    output = np.empty(
        (len(indices), TARGET_AUDIT_MAX_RECORDS, TARGET_AUDIT_FIELDS), dtype=np.float32
    )
    result = library.firesky_national_audit_selected_targets(
        grid_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        grid_values.shape[0],
        grid_values.shape[1],
        aerosol_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        aerosol_values.size,
        aerosol_values.shape[2],
        observer_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        observer_values.shape[0],
        sample_values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        sample_values.shape[0] * sample_values.shape[1],
        sample_values.shape[1],
        rows,
        cols,
        float(lat0),
        float(lon0),
        float(spacing),
        float(radius_km),
        indices.ctypes.data_as(ctypes.POINTER(ctypes.c_size_t)),
        len(indices),
        output.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
    )
    if result != 0:
        raise RuntimeError(f"native national target audit failed with code {result}")
    if not np.isfinite(output).all():
        raise RuntimeError("native national target audit returned non-finite values")
    schemas = output[:, :, TARGET_AUDIT_FIELD_OFFSETS["schema_version"]]
    valid = output[:, :, TARGET_AUDIT_FIELD_OFFSETS["valid"]] > 0.5
    if np.any(schemas[valid] != np.float32(TARGET_AUDIT_SCHEMA_VERSION)):
        raise RuntimeError(
            f"native national target audit schema is obsolete; expected "
            f"{TARGET_AUDIT_SCHEMA_VERSION:g}"
        )
    if np.any((output[:, :, TARGET_AUDIT_FIELD_OFFSETS["valid"]] < 0.0)):
        raise RuntimeError("native national target audit has invalid valid flags")
    # A target record is a marginal contribution from the authoritative
    # observer-facing compositor. Never publish a record whose burn is not
    # supported by the same target's eligibility, solar geometry, and two
    # transmission factors. This also detects stale/misaligned native
    # libraries instead of silently producing a misleading sidecar.
    burn = output[:, :, TARGET_AUDIT_FIELD_OFFSETS["burn_contribution_field"]]
    visible = output[:, :, TARGET_AUDIT_FIELD_OFFSETS["visible_overlap_field"]]
    eligible = output[:, :, TARGET_AUDIT_FIELD_OFFSETS["target_eligible"]] > 0.5
    geometry = output[:, :, TARGET_AUDIT_FIELD_OFFSETS["geometry_reachable"]] > 0.5
    external = output[:, :, TARGET_AUDIT_FIELD_OFFSETS["external_path_transmission"]]
    target_self = output[:, :, TARGET_AUDIT_FIELD_OFFSETS["target_self_transmission"]]
    active = valid & (burn > 1e-6)
    if np.any(active & (~eligible | ~geometry | (external <= 1e-6) | (target_self <= 1e-6))):
        raise RuntimeError(
            "native national target audit violates burn eligibility/geometry/transmission invariant"
        )
    if np.any(valid & (burn > visible + 1e-6)):
        raise RuntimeError("native national target audit violates burn-visible ordering")
    return output


def national_min_remote_low_target_distance_km() -> float:
    """Return the native scorer's low-pressure-family distance cutoff."""
    library = native_library()
    if library is None:
        raise RuntimeError(_load_error or "native library unavailable")
    return float(library.firesky_national_min_remote_low_target_distance_km())


def cloud_hole_columns_to_buffer(columns: Sequence[CloudColumn]) -> np.ndarray:
    """Pack a sequence of CloudColumn objects into a flat float64 buffer for the native FFI."""
    data = np.empty(len(columns) * COLUMN_FIELDS, dtype=np.float64)
    for i, col in enumerate(columns):
        offset = i * COLUMN_FIELDS
        data[offset + 0] = col.distance_km
        data[offset + 1] = col.bearing_deg
        data[offset + 2] = col.high_presence
        data[offset + 3] = col.mid_presence
        data[offset + 4] = col.low_presence
        data[offset + 5] = col.deep_cloud_risk
        data[offset + 6] = col.thickness_risk
        data[offset + 7] = col.burnability_score
        data[offset + 8] = col.cloud_base_m
        data[offset + 9] = col.cloud_top_m
        data[offset + 10] = col.explicit_cloud_evidence
        data[offset + 11] = col.ice_presence
        data[offset + 12] = col.liquid_presence
        data[offset + 13] = col.cloud_top_m
    return data


def cloud_hole_lightpath_signals_native(
    columns: Sequence[CloudColumn],
    solar_elevation_deg: float,
    solar_azimuth_deg: float,
    max_signals: int = 256,
) -> list[tuple[float, ...]]:
    """Call the native firesky_cloud_hole_batch FFI and return raw signal tuples.

    Each tuple has 15 fields matching the Rust SIGNAL_FIELDS layout:
    (hole_distance, hole_bearing, sector_delta, clear_slot, edge_contrast,
     target_distance, target_bearing, target_score, alignment, clearance,
     rear_blocking, score, burnability, deep_risk, thickness_risk)
    """
    library = native_library()
    if library is None:
        raise RuntimeError(_load_error or "native library unavailable")
    if not columns:
        return []

    buf = cloud_hole_columns_to_buffer(columns)
    col_ptr = buf.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
    output_type = ctypes.c_double * (max_signals * SIGNAL_FIELDS)
    output = output_type()
    result = library.firesky_cloud_hole_batch(
        col_ptr,
        len(columns),
        solar_elevation_deg,
        solar_azimuth_deg,
        output,
        max_signals,
    )
    if result < 0:
        raise RuntimeError(f"native cloud-hole scoring failed with code {result}")
    signals: list[tuple[float, ...]] = []
    for i in range(result):
        offset = i * SIGNAL_FIELDS
        signals.append(tuple(float(output[offset + j]) for j in range(SIGNAL_FIELDS)))
    return signals
