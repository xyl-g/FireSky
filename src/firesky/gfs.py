from __future__ import annotations

import hashlib
import http.client
import json
import fcntl
import errno
import math
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np
from eccodes import codes_get, codes_get_array, codes_grib_new_from_file, codes_release

from firesky.analysis_constants import DEFAULT_ANALYSIS_RADIUS_KM
from firesky.geometry import EARTH_RADIUS_KM, bounding_box, choose_cycle, haversine_km, initial_bearing_deg, normalize_lon
from firesky.feature_cache import load_or_build_feature_cache
from firesky.models import DomainSnapshot, ForecastRequest, GeoPoint, GridCellFeature
from firesky.native_core import FIELD_COUNT, NativeFeatureBuffer, NativeSnapshotFeatures
from firesky.storage import cycle_directory, derived_cache_dir

PRESSURE_LEVELS = (200, 250, 300, 400, 500, 600, 700, 750, 800, 850, 925)
VARIABLES = (
    "RH",
    "HGT",
    "TMP",
    "VVEL",
    "UGRD",
    "VGRD",
    "TCDC",
    "HCDC",
    "MCDC",
    "LCDC",
    "CLWMR",
    "ICMR",
    "DPT",
    "PRES",
    "VIS",
    "PRATE",
)
# The production scorer intentionally keeps its historical pressure/field
# contract above.  These additional values are requested only by the explicit
# diagnostic path below; their presence in a decoded grid must not change the
# production feature extraction loops.
GFS_DIAGNOSTIC_PRESSURE_LEVELS = (1000, 975, 950, 900)
GFS_DIAGNOSTIC_HYDROMETEOR_VARIABLES = ("RWMR", "SNMR", "GRLE")
GFS_DIAGNOSTIC_REQUEST_SCHEMA_VERSION = "gfs-diagnostic-input-v1"
GFS_DIAGNOSTIC_PRESSURE_LEVEL_UNITS = "hPa"
GFS_DIAGNOSTIC_HYDROMETEOR_UNITS = "kg kg^-1"
GFS_DIAGNOSTIC_REQUEST_PRESSURE_LEVELS = tuple(
    dict.fromkeys((*PRESSURE_LEVELS, *GFS_DIAGNOSTIC_PRESSURE_LEVELS))
)
GFS_DIAGNOSTIC_REQUEST_VARIABLES = tuple(
    dict.fromkeys((*VARIABLES, *GFS_DIAGNOSTIC_HYDROMETEOR_VARIABLES))
)
CLOUD_BOUNDARY_LEVEL_TYPES = frozenset(
    {
        "highCloudBottom",
        "highCloudTop",
        "middleCloudBottom",
        "middleCloudTop",
        "lowCloudBottom",
        "lowCloudTop",
    }
)
LIGHTPATH_FETCH_RADIUS_KM = DEFAULT_ANALYSIS_RADIUS_KM
# Keep the generic East-Asia helper bounded. Production national model calls
# pass the full candidate-plus-incoming-trace radius explicitly.
GFS_OPERATIONAL_PADDING_KM = 1000.0
EAST_ASIA_OPERATIONAL_BBOX = (63.0, 145.0, 60.0, 12.0)
GFS_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
_GFS_METADATA_INDEX_FORMAT = "firesky-gfs-metadata-index-v1"
_GFS_METADATA_INDEX_DIR_NAME = ".gfs-metadata-index-v1"
_GFS_METADATA_INDEX_NAME = "index.json"
_GFS_METADATA_INDEX_LOCK_NAME = "index.lock"


class GfsInputUnavailableError(RuntimeError):
    """Raised when a GFS input cannot support a numeric forecast."""


def _gfs_request_schema(url: str) -> tuple[tuple[str, str], ...]:
    """Return the field/level request, excluding the spatial subset bounds."""
    if not url:
        return ()
    parsed = urllib.parse.urlsplit(url)
    values = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    excluded = {"leftlon", "rightlon", "toplat", "bottomlat", "subregion"}
    return tuple(sorted((key, value) for key, value in values if key not in excluded))


@dataclass(frozen=True)
class _GfsCacheMetadataEntry:
    metadata_path: str
    metadata_size: int
    metadata_mtime_ns: int
    cycle: str
    forecast_hour: int
    file: str
    bbox: tuple[float, ...] | None
    global_cache: bool
    spatially_unbounded: bool
    request_schema: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _GfsCacheDirectoryStamp:
    path: str
    device: int
    inode: int
    mtime_ns: int


@dataclass(frozen=True)
class _GfsCacheMetadataIndex:
    entries: tuple[_GfsCacheMetadataEntry, ...]
    directories: tuple[_GfsCacheDirectoryStamp, ...]


_GFS_METADATA_INDEX_CACHE: dict[
    str,
    tuple[tuple[int, int], _GfsCacheMetadataIndex],
] = {}
_GFS_METADATA_INDEX_CACHE_LOCK = threading.Lock()


def _gfs_metadata_index_paths(cache_dir: Path) -> tuple[Path, Path]:
    index_dir = cache_dir / _GFS_METADATA_INDEX_DIR_NAME
    index_dir.mkdir(parents=True, exist_ok=True)
    return (
        index_dir / _GFS_METADATA_INDEX_NAME,
        index_dir / _GFS_METADATA_INDEX_LOCK_NAME,
    )


def _gfs_cache_directory_stamps(
    cache_dir: Path,
) -> tuple[_GfsCacheDirectoryStamp, ...]:
    stamps: list[_GfsCacheDirectoryStamp] = []
    for root, directory_names, _file_names in os.walk(cache_dir):
        directory_names[:] = sorted(
            name
            for name in directory_names
            if name != _GFS_METADATA_INDEX_DIR_NAME
        )
        path = Path(root)
        try:
            stat = path.stat()
        except OSError:
            continue
        stamps.append(
            _GfsCacheDirectoryStamp(
                path=path.relative_to(cache_dir).as_posix(),
                device=stat.st_dev,
                inode=stat.st_ino,
                mtime_ns=stat.st_mtime_ns,
            )
        )
    return tuple(sorted(stamps, key=lambda stamp: stamp.path))


def _gfs_cache_directory_stamps_match(
    cache_dir: Path,
    stamps: Sequence[_GfsCacheDirectoryStamp],
) -> bool:
    for stamp in stamps:
        path = cache_dir if stamp.path == "." else cache_dir / stamp.path
        try:
            stat = path.stat()
        except OSError:
            return False
        if (
            stat.st_dev != stamp.device
            or stat.st_ino != stamp.inode
            or stat.st_mtime_ns != stamp.mtime_ns
        ):
            return False
    return True


def _metadata_index_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _read_gfs_cache_metadata_entry(
    cache_dir: Path,
    metadata_path: Path,
) -> _GfsCacheMetadataEntry | None:
    try:
        before = metadata_path.stat()
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        after = metadata_path.stat()
        if (before.st_size, before.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ):
            return None
        raw_bbox = metadata.get("bbox")
        bbox = (
            tuple(float(value) for value in raw_bbox)
            if raw_bbox is not None
            else None
        )
        if bbox is not None and len(bbox) != 4:
            return None
        relative_path = metadata_path.relative_to(cache_dir).as_posix()
        cached_query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(str(metadata.get("url") or "")).query,
            keep_blank_values=True,
        )
        spatial_keys = {
            "subregion",
            "leftlon",
            "rightlon",
            "toplat",
            "bottomlat",
        }
        return _GfsCacheMetadataEntry(
            metadata_path=relative_path,
            metadata_size=after.st_size,
            metadata_mtime_ns=after.st_mtime_ns,
            cycle=str(metadata.get("cycle") or ""),
            forecast_hour=int(metadata.get("forecast_hour", -1)),
            file=str(metadata["file"]),
            bbox=bbox,
            global_cache=bool(metadata.get("global")),
            spatially_unbounded=not bool(spatial_keys.intersection(cached_query)),
            request_schema=_gfs_request_schema(str(metadata.get("url") or "")),
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _serialize_gfs_metadata_index_entry(
    entry: _GfsCacheMetadataEntry,
) -> dict[str, object]:
    return {
        "metadata_path": entry.metadata_path,
        "metadata_size": entry.metadata_size,
        "metadata_mtime_ns": entry.metadata_mtime_ns,
        "cycle": entry.cycle,
        "forecast_hour": entry.forecast_hour,
        "file": entry.file,
        "bbox": list(entry.bbox) if entry.bbox is not None else None,
        "global": entry.global_cache,
        "spatially_unbounded": entry.spatially_unbounded,
        "request_schema": [list(item) for item in entry.request_schema],
    }


def _deserialize_gfs_metadata_index_entry(
    raw: object,
) -> _GfsCacheMetadataEntry:
    if not isinstance(raw, dict):
        raise ValueError("invalid GFS metadata index entry")
    raw_bbox = raw.get("bbox")
    bbox = (
        tuple(float(value) for value in raw_bbox)
        if raw_bbox is not None
        else None
    )
    if bbox is not None and len(bbox) != 4:
        raise ValueError("invalid GFS metadata index bbox")
    raw_schema = raw.get("request_schema")
    if not isinstance(raw_schema, list):
        raise ValueError("invalid GFS metadata index request schema")
    if any(not isinstance(item, list) or len(item) != 2 for item in raw_schema):
        raise ValueError("invalid GFS metadata index request schema item")
    metadata_path = Path(str(raw["metadata_path"]))
    if metadata_path.is_absolute() or ".." in metadata_path.parts:
        raise ValueError("invalid GFS metadata index path")
    return _GfsCacheMetadataEntry(
        metadata_path=metadata_path.as_posix(),
        metadata_size=int(raw["metadata_size"]),
        metadata_mtime_ns=int(raw["metadata_mtime_ns"]),
        cycle=str(raw["cycle"]),
        forecast_hour=int(raw["forecast_hour"]),
        file=str(raw["file"]),
        bbox=bbox,
        global_cache=bool(raw.get("global")),
        spatially_unbounded=bool(raw.get("spatially_unbounded")),
        request_schema=tuple(
            (str(item[0]), str(item[1]))
            for item in raw_schema
        ),
    )


def _publish_gfs_metadata_index(
    cache_dir: Path,
    entries: Sequence[_GfsCacheMetadataEntry],
    directories: Sequence[_GfsCacheDirectoryStamp],
) -> _GfsCacheMetadataIndex:
    index_path, _ = _gfs_metadata_index_paths(cache_dir)
    ordered = tuple(sorted(entries, key=lambda entry: entry.metadata_path))
    ordered_directories = tuple(sorted(directories, key=lambda stamp: stamp.path))
    payload = {
        "format": _GFS_METADATA_INDEX_FORMAT,
        "cache_root": str(cache_dir.resolve()),
        "entries": [
            _serialize_gfs_metadata_index_entry(entry) for entry in ordered
        ],
        "directories": [
            {
                "path": stamp.path,
                "device": stamp.device,
                "inode": stamp.inode,
                "mtime_ns": stamp.mtime_ns,
            }
            for stamp in ordered_directories
        ],
    }
    temporary = index_path.with_name(
        f"{index_path.name}.tmp-{os.getpid()}-{threading.get_ident()}"
    )
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(index_path)
    signature = _metadata_index_signature(index_path)
    if signature is None:
        raise RuntimeError("GFS metadata index was not published")
    index = _GfsCacheMetadataIndex(
        entries=ordered,
        directories=ordered_directories,
    )
    key = str(cache_dir.resolve())
    with _GFS_METADATA_INDEX_CACHE_LOCK:
        _GFS_METADATA_INDEX_CACHE[key] = (signature, index)
    return index


def _read_gfs_metadata_index(
    cache_dir: Path,
    *,
    validate_directories: bool = True,
) -> _GfsCacheMetadataIndex | None:
    index_path, _ = _gfs_metadata_index_paths(cache_dir)
    signature = _metadata_index_signature(index_path)
    if signature is None:
        return None
    key = str(cache_dir.resolve())
    with _GFS_METADATA_INDEX_CACHE_LOCK:
        cached = _GFS_METADATA_INDEX_CACHE.get(key)
        if cached is not None and cached[0] == signature:
            index = cached[1]
            if not validate_directories or _gfs_cache_directory_stamps_match(
                cache_dir,
                index.directories,
            ):
                return index
            return None
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        if payload.get("format") != _GFS_METADATA_INDEX_FORMAT:
            return None
        if payload.get("cache_root") != key:
            return None
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list):
            return None
        raw_directories = payload.get("directories")
        if not isinstance(raw_directories, list):
            return None
        entries = tuple(
            sorted(
                (_deserialize_gfs_metadata_index_entry(raw) for raw in raw_entries),
                key=lambda entry: entry.metadata_path,
            )
        )
        directories = tuple(
            sorted(
                (
                    _GfsCacheDirectoryStamp(
                        path=str(raw["path"]),
                        device=int(raw["device"]),
                        inode=int(raw["inode"]),
                        mtime_ns=int(raw["mtime_ns"]),
                    )
                    for raw in raw_directories
                    if isinstance(raw, dict)
                ),
                key=lambda stamp: stamp.path,
            )
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    index = _GfsCacheMetadataIndex(entries=entries, directories=directories)
    if (
        not any(stamp.path == "." for stamp in directories)
        or any(
            Path(stamp.path).is_absolute() or ".." in Path(stamp.path).parts
            for stamp in directories
        )
    ):
        return None
    if validate_directories and not _gfs_cache_directory_stamps_match(
        cache_dir,
        index.directories,
    ):
        return None
    with _GFS_METADATA_INDEX_CACHE_LOCK:
        _GFS_METADATA_INDEX_CACHE[key] = (signature, index)
    return index


def _rebuild_gfs_metadata_index(
    cache_dir: Path,
) -> _GfsCacheMetadataIndex:
    cache_dir.mkdir(parents=True, exist_ok=True)
    index_path, lock_path = _gfs_metadata_index_paths(cache_dir)
    with lock_path.open("a+b") as lock_handle:
        _acquire_gfs_file_lock(
            lock_handle,
            timeout_seconds=GFS_FILE_LOCK_TIMEOUT_SECONDS,
        )
        entries = [
            entry
            for metadata_path in sorted(cache_dir.rglob("*.grib2.json"))
            if (
                entry := _read_gfs_cache_metadata_entry(cache_dir, metadata_path)
            )
            is not None
        ]
        return _publish_gfs_metadata_index(
            cache_dir,
            entries,
            _gfs_cache_directory_stamps(cache_dir),
        )


def _load_or_build_gfs_metadata_index(
    cache_dir: Path,
) -> _GfsCacheMetadataIndex:
    index = _read_gfs_metadata_index(cache_dir)
    if index is not None:
        return index
    return _rebuild_gfs_metadata_index(cache_dir)


def _upsert_gfs_metadata_index_entry_locked(
    cache_dir: Path,
    metadata_path: Path,
    index_path: Path,
) -> None:
    if not index_path.is_file():
        return
    index = _read_gfs_metadata_index(
        cache_dir,
        validate_directories=False,
    )
    entry = _read_gfs_cache_metadata_entry(cache_dir, metadata_path)
    if index is None or entry is None:
        index_path.unlink(missing_ok=True)
        with _GFS_METADATA_INDEX_CACHE_LOCK:
            _GFS_METADATA_INDEX_CACHE.pop(str(cache_dir.resolve()), None)
        return
    by_path = {item.metadata_path: item for item in index.entries}
    by_path[entry.metadata_path] = entry
    _publish_gfs_metadata_index(
        cache_dir,
        tuple(by_path.values()),
        _gfs_cache_directory_stamps(cache_dir),
    )

# Supported GFS resolutions: key → (product suffix, grid degrees, NOMADS filter script)
GFS_RESOLUTIONS = {
    "0p25": ("0p25", 0.25, "filter_gfs_0p25.pl"),
    "0p50": ("0p50", 0.50, "filter_gfs_0p50.pl"),
}



GFS_AVAILABILITY_LAG_HOURS = 4
GFS_FILE_LOCK_TIMEOUT_SECONDS = 300.0


def _acquire_gfs_file_lock(lock_handle, *, timeout_seconds: float) -> None:
    """Acquire a cache lock without waiting forever on another worker."""
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            if time.monotonic() >= deadline:
                raise TimeoutError("等待 GFS 文件锁超时。") from exc
            time.sleep(0.05)


def choose_gfs_cycle(
    reference_utc: datetime,
    availability_lag_hours: int = GFS_AVAILABILITY_LAG_HOURS,
) -> datetime:
    """Select the newest operational GFS cycle past its publication lag."""
    return choose_cycle(reference_utc, (0, 6, 12, 18), availability_lag_hours)


@dataclass
class _MutableCell:
    lat: float
    lon: float
    level: int
    values: Dict[str, float]


@dataclass(frozen=True)
class _DecodedGfsGrid:
    latitudes: np.ndarray
    longitudes: np.ndarray
    fields: Dict[Tuple[str, int, str], np.ndarray]


_SHARED_GRID_CACHE_LIMIT = 4
_DECODED_GRID_CACHE_VERSION = 7
_DECODED_GRID_DISK_LIMIT = 4
SHARED_GRID_DECODE_WAIT_TIMEOUT_SECONDS = 180.0
_SHARED_GRID_CACHE: OrderedDict[Tuple[str, int, int], _DecodedGfsGrid] = OrderedDict()
_SHARED_GRID_INFLIGHT: Dict[Tuple[str, int, int], threading.Event] = {}
_SHARED_GRID_LOCK = threading.Lock()


def _close_decoded_grid(grid: _DecodedGfsGrid) -> None:
    """Release mmap file descriptors held by a decoded grid.

    numpy memmap arrays keep the underlying file descriptor open until
    garbage-collected.  In multiprocessing workers that process many GRIB
    files, relying on GC causes FD accumulation that eventually triggers
    [Errno 24] Too many open files. Close unique mmap handles on eviction;
    active views fall back to normal GC cleanup.
    """
    arrays = [getattr(grid, "latitudes", None), getattr(grid, "longitudes", None)]
    fields = getattr(grid, "fields", None)
    if isinstance(fields, dict):
        arrays.extend(fields.values())
    closed: set[int] = set()
    for array in arrays:
        mmap_handle = getattr(array, "_mmap", None)
        if mmap_handle is None or id(mmap_handle) in closed:
            continue
        try:
            mmap_handle.close()
        except (BufferError, OSError):
            # A native/NumPy view may still be finishing; GC will close it once
            # the last view is released.
            continue
        closed.add(id(mmap_handle))


class GfsNomadsClient:
    model_name = "gfs"

    def __init__(
        self,
        cache_dir: Path | str = ".cache/gfs",
        timeout_seconds: int = 90,
        fixed_bbox: Tuple[float, float, float, float] | None = None,
        feature_cache_enabled: bool = True,
        shared_decode_enabled: bool = False,
        shared_decode_cache_limit: int | None = None,
        resolution: str = "0p25",
        region: str | None = None,
    ) -> None:
        if resolution not in GFS_RESOLUTIONS:
            raise ValueError(f"不支持的 GFS 精度: {resolution}，可选: {list(GFS_RESOLUTIONS.keys())}")
        self.resolution = resolution
        self._res_product, self._res_grid_deg, self._res_filter = GFS_RESOLUTIONS[resolution]
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds
        self.fixed_bbox = canonical_subset_bbox(fixed_bbox, self._res_grid_deg) if fixed_bbox is not None else None
        self.feature_cache_enabled = feature_cache_enabled
        self.shared_decode_enabled = shared_decode_enabled
        self.shared_decode_cache_limit = shared_decode_cache_limit
        # Optional region tag used purely for storage layout: files are written
        # under .cache/gfs/<date>/<HH>Z/<region>/0p25/ instead of the cycle root.
        self.region = region
        self.download_attempts = max(
            1, int(os.environ.get("FIRESKY_GFS_DOWNLOAD_ATTEMPTS", "4"))
        )
        self._resolved_paths: Dict[tuple[str, int, tuple[float, float, float, float]], Path] = {}
        self._resolved_paths_lock = threading.Lock()
        if shared_decode_cache_limit is not None:
            ensure_shared_decoded_grid_cache_capacity(shared_decode_cache_limit)

    def fetch_snapshot(
        self,
        request: ForecastRequest,
        init_time_utc: datetime,
        forecast_hour: int,
        *,
        include_terrain: bool = True,
    ) -> DomainSnapshot:
        fetch_radius_km = max(request.radius_km, LIGHTPATH_FETCH_RADIUS_KM)
        requested_bbox = bounding_box(request.point, fetch_radius_km)
        bbox = (
            self.fixed_bbox
            if self.fixed_bbox is not None and bbox_contains(self.fixed_bbox, requested_bbox)
            else requested_bbox
        )
        url = self.build_subset_url(init_time_utc, forecast_hour, bbox)
        cache_path = self._resolve_snapshot_path(init_time_utc, forecast_hour, bbox, url)
        valid_time = init_time_utc + timedelta(hours=forecast_hour)
        features = self.parse_snapshot(cache_path, request.point, fetch_radius_km)
        terrain_elevation_m = (
            terrain_elevation_at_point(cache_path, request.point)
            if include_terrain
            else None
        )
        identity = (
            f"gfs:{init_time_utc.astimezone(timezone.utc):%Y%m%d%H}:"
            f"f{forecast_hour:03d}:{self.resolution}:{hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]}"
        )
        return DomainSnapshot(
            model_name=self.model_name,
            forecast_hour=forecast_hour,
            init_time_utc=init_time_utc,
            valid_time_utc=valid_time,
            features=features,
            source_url=url,
            cache_path=str(cache_path),
            terrain_elevation_m=terrain_elevation_m,
            vertical_prior_identity=identity,
        )

    def load_decoded_subset(
        self,
        init_time_utc: datetime,
        forecast_hour: int,
        bbox: Tuple[float, float, float, float],
        *,
        include_diagnostic_fields: bool = False,
        cache_only: bool = False,
    ) -> tuple[_DecodedGfsGrid, Path]:
        """Return a decoded regular grid without creating observer features.

        ``cache_only`` is used by synchronous nowcast scoring.  The default
        retains the download-on-miss behavior required by explicit prefetch
        and model preparation jobs.
        """
        canonical_bbox = canonical_subset_bbox(bbox, self._res_grid_deg)
        url = self.build_subset_url(
            init_time_utc,
            forecast_hour,
            canonical_bbox,
            include_diagnostic_fields=include_diagnostic_fields,
        )
        if cache_only:
            cache_path = self._resolve_snapshot_path(
                init_time_utc,
                forecast_hour,
                canonical_bbox,
                url,
                allow_download=False,
            )
        else:
            cache_path = self._resolve_snapshot_path(
                init_time_utc,
                forecast_hour,
                canonical_bbox,
                url,
            )
        grid = load_shared_decoded_grid(cache_path)
        # Regional batch requests use the same fixed bbox that produced the
        # cached GRIB. Re-slicing every field here would copy the complete
        # decoded grid once per hour before tensor derivation. Reuse the
        # shared mmap only when the canonical bbox contains every decoded
        # coordinate; narrower requests still take the existing copy path.
        if isinstance(grid, _DecodedGfsGrid):
            leftlon, rightlon, toplat, bottomlat = canonical_bbox
            if (
                grid.latitudes.size > 0
                and grid.longitudes.size == grid.latitudes.size
                and float(np.min(grid.longitudes)) >= leftlon
                and float(np.max(grid.longitudes)) <= rightlon
                and float(np.min(grid.latitudes)) >= bottomlat
                and float(np.max(grid.latitudes)) <= toplat
            ):
                return grid, cache_path
        return subset_decoded_grid(grid, canonical_bbox), cache_path

    def build_subset_url(
        self,
        init_time_utc: datetime,
        forecast_hour: int,
        bbox: Tuple[float, float, float, float] | None,
        *,
        include_diagnostic_fields: bool = False,
    ) -> str:
        """Build NOMADS filter URL. When bbox is None, the subregion params are
        omitted entirely — NOMADS then returns the full global grid.

        The diagnostic fields are opt-in so an existing production request keeps
        the exact historical selector set.  ``GFS_DIAGNOSTIC_REQUEST_*`` only
        contains fields that have a local request/parse contract; an absent
        message is surfaced by :func:`gfs_diagnostic_availability`.
        """
        from firesky import rust_gfs_data
        if rust_gfs_data.enabled():
            return rust_gfs_data.build_url(
                self, init_time_utc, forecast_hour, bbox, include_diagnostic_fields
            )
        cycle = init_time_utc.astimezone(timezone.utc)
        product = self._res_product
        params: dict[str, str] = {
            "file": f"gfs.t{cycle:%H}z.pgrb2.{product}.f{forecast_hour:03d}",
            "dir": f"/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos",
        }
        if bbox is not None:
            leftlon, rightlon, toplat, bottomlat = canonical_subset_bbox(bbox, self._res_grid_deg)
            params["subregion"] = ""
            params["leftlon"] = self._format_lon(leftlon)
            params["rightlon"] = self._format_lon(rightlon)
            params["toplat"] = f"{toplat:.3f}"
            params["bottomlat"] = f"{bottomlat:.3f}"
        pressure_levels = (
            GFS_DIAGNOSTIC_REQUEST_PRESSURE_LEVELS
            if include_diagnostic_fields
            else PRESSURE_LEVELS
        )
        variables = (
            GFS_DIAGNOSTIC_REQUEST_VARIABLES
            if include_diagnostic_fields
            else VARIABLES
        )
        for level in pressure_levels:
            params[f"lev_{level}_mb"] = "on"
        params["lev_low_cloud_layer"] = "on"
        params["lev_middle_cloud_layer"] = "on"
        params["lev_high_cloud_layer"] = "on"
        params["lev_low_cloud_bottom_level"] = "on"
        params["lev_low_cloud_top_level"] = "on"
        params["lev_middle_cloud_bottom_level"] = "on"
        params["lev_middle_cloud_top_level"] = "on"
        params["lev_high_cloud_bottom_level"] = "on"
        params["lev_high_cloud_top_level"] = "on"
        params["lev_surface"] = "on"
        for variable in variables:
            params[f"var_{variable}"] = "on"
        query = urllib.parse.urlencode(params)
        return f"https://nomads.ncep.noaa.gov/cgi-bin/{self._res_filter}?{query}"

    def build_diagnostic_subset_url(
        self,
        init_time_utc: datetime,
        forecast_hour: int,
        bbox: Tuple[float, float, float, float] | None,
    ) -> str:
        """Build the versioned GFS diagnostic request without changing scoring."""
        return self.build_subset_url(
            init_time_utc,
            forecast_hour,
            bbox,
            include_diagnostic_fields=True,
        )

    def download(
        self,
        url: str,
        init_time_utc: datetime | None = None,
        forecast_hour: int | None = None,
        bbox: Tuple[float, float, float, float] | None = None,
    ) -> Path:
        from firesky import rust_gfs_data
        if rust_gfs_data.enabled():
            return rust_gfs_data.download(self, url, init_time_utc, forecast_hour, bbox)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        target_dir = (
            cycle_directory(self.cache_dir, init_time_utc, self.resolution, region=self.region)
            if init_time_utc is not None
            else self.cache_dir / "undated"
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        cache_path = target_dir / f"{digest}.grib2"
        if cache_path.exists() and cache_path.stat().st_size > 0:
            self._write_metadata_if_possible(cache_path, url, init_time_utc, forecast_hour, bbox)
            return cache_path
        lock_path = cache_path.with_suffix(cache_path.suffix + ".lock")
        with lock_path.open("a+b") as lock_handle:
            _acquire_gfs_file_lock(
                lock_handle,
                timeout_seconds=GFS_FILE_LOCK_TIMEOUT_SECONDS,
            )
            if cache_path.exists() and cache_path.stat().st_size > 0:
                self._write_metadata_if_possible(cache_path, url, init_time_utc, forecast_hour, bbox)
                return cache_path
            temporary = cache_path.with_suffix(
                cache_path.suffix + f".tmp-{os.getpid()}-{threading.get_ident()}"
            )
            last_error: BaseException | None = None
            for attempt in range(1, self.download_attempts + 1):
                temporary.unlink(missing_ok=True)
                try:
                    self._download_once(url, temporary)
                    temporary.replace(cache_path)
                    last_error = None
                    break
                except (
                    http.client.IncompleteRead,
                    urllib.error.URLError,
                    TimeoutError,
                    ConnectionError,
                    OSError,
                    RuntimeError,
                ) as exc:
                    last_error = exc
                    temporary.unlink(missing_ok=True)
                    if attempt < self.download_attempts:
                        time.sleep(min(12.0, float(2 ** (attempt - 1))))
            if last_error is not None:
                raise RuntimeError(
                    f"GFS download failed after {self.download_attempts} attempts: {last_error}"
                ) from last_error
            self._write_metadata_if_possible(cache_path, url, init_time_utc, forecast_hour, bbox)
        return cache_path

    def _download_once(self, url: str, temporary: Path) -> None:
        request = urllib.request.Request(
            url,
            headers={
                "Accept-Encoding": "identity",
                "User-Agent": "firesky/0.1 burning-sunset-engine",
            },
        )
        expected_bytes: int | None = None
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    expected_bytes = int(content_length)
                except ValueError:
                    expected_bytes = None
            with temporary.open("wb") as handle:
                while True:
                    chunk = response.read(GFS_DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())

        actual_bytes = temporary.stat().st_size
        if expected_bytes is not None and actual_bytes != expected_bytes:
            raise RuntimeError(
                f"incomplete GFS response: expected {expected_bytes} bytes, received {actual_bytes}"
            )
        if actual_bytes < 16:
            raise RuntimeError(f"GFS response is too small: {actual_bytes} bytes")
        with temporary.open("rb") as handle:
            if handle.read(4) != b"GRIB":
                raise RuntimeError("GFS response is not a GRIB file")
            handle.seek(-4, os.SEEK_END)
            if handle.read(4) != b"7777":
                raise RuntimeError("GFS response has no GRIB end marker")

    def prefetch_subset(
        self,
        init_time_utc: datetime,
        forecast_hour: int,
        bbox: Tuple[float, float, float, float] | None,
        *,
        include_diagnostic_fields: bool = False,
    ) -> Path:
        if bbox is None:
            url = self.build_subset_url(
                init_time_utc,
                forecast_hour,
                None,
                include_diagnostic_fields=include_diagnostic_fields,
            )
            return self._resolve_snapshot_path(init_time_utc, forecast_hour, None, url)
        canonical_bbox = canonical_subset_bbox(bbox, self._res_grid_deg)
        url = self.build_subset_url(
            init_time_utc,
            forecast_hour,
            canonical_bbox,
            include_diagnostic_fields=include_diagnostic_fields,
        )
        return self._resolve_snapshot_path(init_time_utc, forecast_hour, canonical_bbox, url)

    def prefetch_diagnostic_subset(
        self,
        init_time_utc: datetime,
        forecast_hour: int,
        bbox: Tuple[float, float, float, float] | None,
    ) -> Path:
        """Download/resolve a GFS diagnostic subset with its own URL identity."""
        return self.prefetch_subset(
            init_time_utc,
            forecast_hour,
            bbox,
            include_diagnostic_fields=True,
        )

    def preload_snapshots(
        self,
        init_time_utc: datetime,
        forecast_hours: Sequence[int],
        bbox: Tuple[float, float, float, float] | None = None,
    ) -> list[Path]:
        """Resolve and mmap every regional model hour before observer scoring starts."""
        if not self.shared_decode_enabled:
            raise ValueError("preload_snapshots requires shared_decode_enabled")
        target_bbox = canonical_subset_bbox(
            bbox or self.fixed_bbox or EAST_ASIA_OPERATIONAL_BBOX,
            self._res_grid_deg,
        )
        hours = sorted({int(hour) for hour in forecast_hours})
        ensure_shared_decoded_grid_cache_capacity(
            max(len(hours), self.shared_decode_cache_limit or 0)
        )
        paths: list[Path] = []
        for hour in hours:
            path = self.prefetch_subset(init_time_utc, hour, target_bbox)
            paths.append(path)
        decode_workers = max(
            1,
            int(os.environ.get("FIRESKY_GFS_DECODE_WORKERS", "2")),
        )
        with ThreadPoolExecutor(
            max_workers=min(decode_workers, max(1, len(paths))),
            thread_name_prefix="firesky-gfs-decode",
        ) as executor:
            list(executor.map(load_shared_decoded_grid, paths))
        return paths

    def _resolve_snapshot_path(
        self,
        init_time_utc: datetime,
        forecast_hour: int,
        bbox: Tuple[float, float, float, float] | None,
        url: str,
        *,
        allow_download: bool = True,
    ) -> Path:
        cycle = init_time_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        canonical_bbox = (
            canonical_subset_bbox(bbox, self._res_grid_deg)
            if bbox is not None
            else None
        )
        key = (cycle, int(forecast_hour), canonical_bbox)
        if allow_download:
            with self._resolved_paths_lock:
                cached = self._resolved_paths.get(key)
                if cached is not None and cached.exists() and cached.stat().st_size > 0:
                    return cached
        cache_path = self.find_covering_cache(
            init_time_utc,
            forecast_hour,
            canonical_bbox,
            url=url,
        )
        if cache_path is None:
            if not allow_download:
                raise FileNotFoundError(
                    "GFS cached subset is unavailable for the requested cycle, hour, and domain"
                )
            cache_path = self.download(
                url,
                init_time_utc=init_time_utc,
                forecast_hour=forecast_hour,
                bbox=canonical_bbox,
            )
        with self._resolved_paths_lock:
            self._resolved_paths[key] = cache_path
        return cache_path

    def find_covering_cache(
        self,
        init_time_utc: datetime,
        forecast_hour: int,
        bbox: Tuple[float, float, float, float] | None,
        url: str | None = None,
    ) -> Path | None:
        from firesky import rust_gfs_data
        if rust_gfs_data.enabled():
            return rust_gfs_data.find(self, init_time_utc, forecast_hour, bbox, url)
        required = (
            canonical_subset_bbox(bbox, self._res_grid_deg)
            if bbox is not None
            else None
        )
        cycle = init_time_utc.astimezone(timezone.utc)
        cycle_key = cycle.strftime("%Y-%m-%dT%H:%M:%SZ")
        requested_schema = _gfs_request_schema(url or "") if url is not None else None
        entries = _load_or_build_gfs_metadata_index(self.cache_dir).entries
        for attempt in range(2):
            stale_metadata = False
            for entry in entries:
                if entry.cycle != cycle_key or entry.forecast_hour != forecast_hour:
                    continue
                metadata_path = self.cache_dir / entry.metadata_path
                try:
                    metadata_stat = metadata_path.stat()
                except OSError:
                    stale_metadata = True
                    break
                if (
                    metadata_stat.st_size != entry.metadata_size
                    or metadata_stat.st_mtime_ns != entry.metadata_mtime_ns
                ):
                    stale_metadata = True
                    break
                cache_path = metadata_path.parent / entry.file
                if not cache_path.exists():
                    cache_path = self.cache_dir / entry.file
                try:
                    if cache_path.stat().st_size == 0:
                        continue
                except OSError:
                    continue
                if requested_schema is not None and entry.request_schema != requested_schema:
                    continue
                if entry.global_cache:
                    if entry.bbox != (-180.0, 180.0, 90.0, -90.0):
                        continue
                    if not entry.spatially_unbounded:
                        continue
                    # A validated global GRIB covers any required bbox.
                    return cache_path
                if entry.bbox is None or required is None:
                    continue
                if bbox_contains(entry.bbox, required):
                    return cache_path
            if stale_metadata and attempt == 0:
                entries = _rebuild_gfs_metadata_index(self.cache_dir).entries
                continue
            break
        return None

    def _write_metadata_if_possible(
        self,
        cache_path: Path,
        url: str,
        init_time_utc: datetime | None,
        forecast_hour: int | None,
        bbox: Tuple[float, float, float, float] | None,
    ) -> None:
        if init_time_utc is None or forecast_hour is None:
            return
        cycle = init_time_utc.astimezone(timezone.utc)
        metadata: dict[str, object] = {
            "model": self.model_name,
            "cycle": cycle.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "forecast_hour": int(forecast_hour),
            "file": cache_path.name,
            "url": url,
        }
        if bbox is None:
            # Global download: mark as covering the whole globe.
            metadata["global"] = True
            metadata["bbox"] = [-180.0, 180.0, 90.0, -90.0]
        else:
            metadata["bbox"] = list(
                canonical_subset_bbox(bbox, self._res_grid_deg)
            )
        metadata_path = cache_path.with_suffix(cache_path.suffix + ".json")
        index_path, index_lock_path = _gfs_metadata_index_paths(self.cache_dir)
        with index_lock_path.open("a+b") as index_lock_handle:
            _acquire_gfs_file_lock(
                index_lock_handle,
                timeout_seconds=GFS_FILE_LOCK_TIMEOUT_SECONDS,
            )
            temporary = metadata_path.with_name(
                f"{metadata_path.name}.tmp-{os.getpid()}-{threading.get_ident()}"
            )
            temporary.write_text(
                json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2),
                encoding="utf-8",
            )
            temporary.replace(metadata_path)
            _upsert_gfs_metadata_index_entry_locked(
                self.cache_dir,
                metadata_path,
                index_path,
            )

    def parse_snapshot(self, path: Path, observer: GeoPoint, radius_km: float) -> Sequence[GridCellFeature]:
        if _gfs_decode_backend() == "rust":
            from firesky.rust_grid import native_snapshot
            return native_snapshot(path, observer, radius_km)
        if self.shared_decode_enabled:
            return native_features_from_decoded_grid(load_shared_decoded_grid(path), observer, radius_km)
        if not self.feature_cache_enabled:
            return self._parse_snapshot_uncached(path, observer, radius_km)
        return load_or_build_feature_cache(
            path,
            observer,
            radius_km,
            lambda: self._parse_snapshot_uncached(path, observer, radius_km),
        )

    def _parse_snapshot_uncached(self, path: Path, observer: GeoPoint, radius_km: float) -> List[GridCellFeature]:
        cells: Dict[Tuple[float, float, int], _MutableCell] = {}
        high_cloud_cover: Dict[Tuple[float, float], float] = {}
        leftlon, rightlon, toplat, bottomlat = bounding_box(observer, radius_km)
        with path.open("rb") as handle:
            while True:
                gid = codes_grib_new_from_file(handle)
                if gid is None:
                    break
                short_name = str(codes_get(gid, "shortName"))
                level_type = str(codes_get(gid, "typeOfLevel"))
                level = int(codes_get(gid, "level"))
                lats = codes_get_array(gid, "latitudes")
                lons = codes_get_array(gid, "longitudes")
                values = _decoded_values_with_bitmap(gid)
                for lat, lon, value in zip(lats, lons, values):
                    lat_key = round(float(lat), 4)
                    lon_key = round(normalize_lon(float(lon)), 4)
                    if lat_key > toplat or lat_key < bottomlat:
                        continue
                    if leftlon <= rightlon:
                        if lon_key < leftlon or lon_key > rightlon:
                            continue
                    elif lon_key > rightlon and lon_key < leftlon:
                        continue
                    if level_type in CLOUD_BOUNDARY_LEVEL_TYPES:
                        continue
                    if short_name in {"hcc", "hcdc"} or (
                        level_type == "highCloudLayer" and short_name in {"tcc", "tcdc"}
                    ):
                        high_cloud_cover[(lat_key, lon_key)] = float(value)
                        continue
                    key = (lat_key, lon_key, level)
                    cell = cells.setdefault(key, _MutableCell(lat=lat_key, lon=lon_key, level=level, values={}))
                    cell.values[short_name] = float(value)
                codes_release(gid)
        features: List[GridCellFeature] = []
        surface_values = {
            (cell.lat, cell.lon): cell.values
            for cell in cells.values()
            if cell.level == 0
        }
        for cell in cells.values():
            if cell.level not in PRESSURE_LEVELS:
                continue
            # Same fail-closed contract as the decoded-grid path
            # (_validate_vertical_prior_fields): a pressure level without
            # geopotential or humidity must not silently become 0 m / 0 %
            # pseudo data on this legacy path.
            missing = [
                name
                for name, value in (
                    ("geopotential", cell.values.get("gh")),
                    ("relative humidity", cell.values.get("r")),
                )
                if value is None or not math.isfinite(float(value))
            ]
            if missing:
                raise ValueError(
                    "GFS vertical prior field missing or non-finite: "
                    + ", ".join(missing)
                    + f" at {cell.level} hPa"
                )
            point = GeoPoint(lat=cell.lat, lon=cell.lon)
            surface = surface_values.get((cell.lat, cell.lon), {})
            pressure_cloud_cover = cell.values.get("tcc", cell.values.get("tcdc"))
            if cell.level == 300 and (
                pressure_cloud_cover is None
                or not math.isfinite(float(pressure_cloud_cover))
            ):
                pressure_cloud_cover = high_cloud_cover.get((cell.lat, cell.lon))
            features.append(
                GridCellFeature(
                    lat=cell.lat,
                    lon=cell.lon,
                    distance_km=haversine_km(observer, point),
                    bearing_deg=initial_bearing_deg(observer, point),
                    pressure_hpa=cell.level,
                    geopotential_height_m=cell.values.get("gh", 0.0),
                    rh_percent=cell.values.get("r", 0.0),
                    temperature_k=cell.values.get("t"),
                    omega_pa_s=cell.values.get("w"),
                    u_ms=cell.values.get("u"),
                    v_ms=cell.values.get("v"),
                    cloud_fraction_percent=pressure_cloud_cover,
                    cloud_liquid_kg_kg=cell.values.get("clwmr"),
                    cloud_ice_kg_kg=cell.values.get("icmr"),
                    surface_visibility_m=surface.get("vis"),
                    precip_rate_kg_m2_s=surface.get("prate"),
                    surface_temperature_k=surface.get("t"),
                    surface_dewpoint_k=surface.get("dpt"),
                )
            )
        return features

    @staticmethod
    def _format_lon(value: float) -> str:
        if value < 0:
            value = 360.0 + value
        return f"{value:.3f}"


def load_shared_decoded_grid(path: Path) -> _DecodedGfsGrid:
    resolved = path.resolve()
    stat = resolved.stat()
    backend = _gfs_decode_backend()
    identity = str(resolved) if backend == "python" else f"rust-grid-v2:{resolved}"
    key = (identity, stat.st_size, stat.st_mtime_ns)
    while True:
        with _SHARED_GRID_LOCK:
            cached = _SHARED_GRID_CACHE.get(key)
            if cached is not None:
                _SHARED_GRID_CACHE.move_to_end(key)
                return cached
            event = _SHARED_GRID_INFLIGHT.get(key)
            if event is None:
                event = threading.Event()
                _SHARED_GRID_INFLIGHT[key] = event
                break
        if not event.wait(timeout=SHARED_GRID_DECODE_WAIT_TIMEOUT_SECONDS):
            raise TimeoutError("等待共享 GFS 解码超时。")
    try:
        decoded = load_or_decode_mmap_grid(resolved)
    except BaseException:
        with _SHARED_GRID_LOCK:
            _SHARED_GRID_INFLIGHT.pop(key, None)
            event.set()
        raise
    with _SHARED_GRID_LOCK:
        _SHARED_GRID_CACHE[key] = decoded
        _SHARED_GRID_CACHE.move_to_end(key)
        while len(_SHARED_GRID_CACHE) > _SHARED_GRID_CACHE_LIMIT:
            _SHARED_GRID_CACHE.popitem(last=False)
            # NOTE: Do NOT call _close_decoded_grid() here.
            # Explicit mmap close() causes SIGSEGV when concurrent worker
            # threads still hold references to the evicted grid's arrays.
            # Python GC closes the mmap FD when the last reference drops.
        _SHARED_GRID_INFLIGHT.pop(key, None)
        event.set()
    return decoded


def ensure_shared_decoded_grid_cache_capacity(required: int) -> int:
    """Grow the process-wide mmap cache without invalidating active regional jobs."""
    global _SHARED_GRID_CACHE_LIMIT, _DECODED_GRID_DISK_LIMIT
    capacity = max(1, int(required))
    with _SHARED_GRID_LOCK:
        _SHARED_GRID_CACHE_LIMIT = max(_SHARED_GRID_CACHE_LIMIT, capacity)
        _DECODED_GRID_DISK_LIMIT = max(_DECODED_GRID_DISK_LIMIT, capacity)
        return _SHARED_GRID_CACHE_LIMIT


def clear_shared_decoded_grid_cache() -> None:
    with _SHARED_GRID_LOCK:
        for grid in _SHARED_GRID_CACHE.values():
            _close_decoded_grid(grid)
        _SHARED_GRID_CACHE.clear()


def decoded_grid_cache_path(path: Path) -> Path:
    resolved = path.resolve()
    stat = resolved.stat()
    signature = hashlib.sha256(
        f"{resolved}:{stat.st_size}:{stat.st_mtime_ns}:v{_DECODED_GRID_CACHE_VERSION}".encode("utf-8")
    ).hexdigest()[:20]
    kind = "decoded" if _gfs_decode_backend() == "python" else "decoded-rust-grid-v2"
    return derived_cache_dir(resolved, kind) / f"{resolved.stem}-{signature}"


def load_mmap_decoded_grid(cache_path: Path) -> _DecodedGfsGrid:
    manifest = json.loads((cache_path / "manifest.json").read_text(encoding="utf-8"))
    if int(manifest.get("version", -1)) != _DECODED_GRID_CACHE_VERSION:
        raise ValueError("decoded GFS cache version mismatch")
    coordinates = np.load(cache_path / "coordinates.npy", mmap_mode="r", allow_pickle=False)
    field_matrix = np.load(cache_path / "fields.npy", mmap_mode="r", allow_pickle=False)
    if coordinates.ndim != 2 or coordinates.shape[0] != 2:
        raise ValueError("decoded GFS coordinate cache has an invalid shape")
    if field_matrix.ndim != 2 or field_matrix.shape[1] != coordinates.shape[1]:
        raise ValueError("decoded GFS field cache has an invalid shape")
    latitudes = coordinates[0]
    longitudes = coordinates[1]
    fields = {
        (str(item["level_type"]), int(item["level"]), str(item["name"])):
            field_matrix[int(item["index"])]
        for item in manifest["fields"]
    }
    return _DecodedGfsGrid(latitudes=latitudes, longitudes=longitudes, fields=fields)


def write_mmap_decoded_grid(cache_path: Path, grid: _DecodedGfsGrid) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_name(
        f".{cache_path.name}.tmp-{os.getpid()}-{threading.get_ident()}"
    )
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    try:
        point_count = len(grid.latitudes)
        coordinates = np.lib.format.open_memmap(
            temporary / "coordinates.npy",
            mode="w+",
            dtype=np.float32,
            shape=(2, point_count),
        )
        coordinates[0] = np.asarray(grid.latitudes, dtype=np.float32)
        coordinates[1] = np.asarray(grid.longitudes, dtype=np.float32)
        coordinates.flush()

        sorted_fields = sorted(grid.fields.items())
        field_matrix = np.lib.format.open_memmap(
            temporary / "fields.npy",
            mode="w+",
            dtype=np.float32,
            shape=(len(sorted_fields), point_count),
        )
        fields = []
        for index, ((level_type, level, name), values) in enumerate(sorted_fields):
            if len(values) != point_count:
                raise ValueError("decoded GFS fields do not share one grid")
            field_matrix[index] = np.asarray(values, dtype=np.float32)
            fields.append(
                {
                    "level_type": level_type,
                    "level": level,
                    "name": name,
                    "index": index,
                }
            )
        field_matrix.flush()
        del coordinates, field_matrix
        (temporary / "manifest.json").write_text(
            json.dumps(
                {"version": _DECODED_GRID_CACHE_VERSION, "fields": fields},
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, cache_path)
        prune_mmap_decoded_grids(cache_path.parent, keep=cache_path)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def prune_mmap_decoded_grids(cache_root: Path, keep: Path) -> None:
    lock_path = cache_root / ".prune.lock"
    with lock_path.open("a+b") as lock_handle:
        _acquire_gfs_file_lock(
            lock_handle,
            timeout_seconds=GFS_FILE_LOCK_TIMEOUT_SECONDS,
        )
        candidates = sorted(
            (
                path
                for path in cache_root.iterdir()
                if path.is_dir()
                and not path.name.startswith(".")
                and path != keep
                and (path / "manifest.json").is_file()
            ),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        for stale in candidates[max(0, _DECODED_GRID_DISK_LIMIT - 1):]:
            shutil.rmtree(stale, ignore_errors=True)


def load_or_decode_mmap_grid(path: Path) -> _DecodedGfsGrid:
    cache_path = decoded_grid_cache_path(path)
    manifest_path = cache_path / "manifest.json"
    if manifest_path.is_file():
        try:
            return load_mmap_decoded_grid(cache_path)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            pass
    lock_path = path.with_suffix(path.suffix + ".decode.lock")
    with lock_path.open("a+b") as lock_handle:
        _acquire_gfs_file_lock(
            lock_handle,
            timeout_seconds=GFS_FILE_LOCK_TIMEOUT_SECONDS,
        )
        if manifest_path.is_file():
            try:
                return load_mmap_decoded_grid(cache_path)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                shutil.rmtree(cache_path, ignore_errors=True)
        decoded = decode_grib_grid(path)
        if not isinstance(decoded, _DecodedGfsGrid):
            return decoded
        write_mmap_decoded_grid(cache_path, decoded)
        return load_mmap_decoded_grid(cache_path)


def _decoded_values_with_bitmap(gid) -> np.ndarray:
    """Decode one GRIB value array without converting bitmap gaps to data."""
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
        raise RuntimeError("GFS GRIB bitmap is declared but cannot be decoded") from exc
    if bitmap.shape != values.shape:
        raise RuntimeError("GFS GRIB bitmap and values have different shapes")
    decoded = np.array(values, dtype=np.float32, copy=True)
    decoded[bitmap == 0] = np.nan
    return decoded


def _gfs_decode_backend() -> str:
    backend = os.environ.get("FIRESKY_GFS_DECODE_BACKEND", "python").strip().lower()
    if backend not in {"python", "rust"}:
        raise ValueError("FIRESKY_GFS_DECODE_BACKEND must be python or rust")
    return backend


def decode_grib_grid(path: Path) -> _DecodedGfsGrid:
    if _gfs_decode_backend() == "rust":
        from firesky.rust_grid import decode_gfs_grid
        return decode_gfs_grid(path)
    latitudes: np.ndarray | None = None
    longitudes: np.ndarray | None = None
    point_count: int | None = None
    fields: Dict[Tuple[str, int, str], np.ndarray] = {}
    with path.open("rb") as handle:
        while True:
            gid = codes_grib_new_from_file(handle)
            if gid is None:
                break
            try:
                short_name = str(codes_get(gid, "shortName"))
                level_type = str(codes_get(gid, "typeOfLevel"))
                level = int(codes_get(gid, "level"))
                keep = (
                    (
                        level_type == "isobaricInhPa"
                        and level in GFS_DIAGNOSTIC_REQUEST_PRESSURE_LEVELS
                    )
                    or (
                        level_type == "surface"
                        and level == 0
                        and short_name in {"orog", "vis", "prate", "t", "dpt"}
                    )
                    or (
                        level_type in {
                            "highCloudLayer",
                            "middleCloudLayer",
                            "lowCloudLayer",
                        }
                        and short_name in {
                            "hcc",
                            "avg_hcc",
                            "hcdc",
                            "tcc",
                            "tcdc",
                            "mcc",
                            "avg_mcc",
                            "mcdc",
                            "lcc",
                            "avg_lcc",
                            "lcdc",
                        }
                    )
                    or (
                        level_type in CLOUD_BOUNDARY_LEVEL_TYPES
                        and short_name in {"pres", "avg_pres"}
                    )
                )
                if not keep:
                    continue
                values = _decoded_values_with_bitmap(gid)
                if latitudes is None:
                    latitudes = np.round(
                        np.asarray(codes_get_array(gid, "latitudes"), dtype=np.float32),
                        4,
                    )
                    raw_longitudes = np.asarray(codes_get_array(gid, "longitudes"), dtype=np.float32)
                    longitudes = np.round(((raw_longitudes + 180.0) % 360.0) - 180.0, 4)
                    point_count = len(values)
                elif len(values) != point_count:
                    raise RuntimeError("GFS GRIB 消息没有使用统一网格，无法共享解码结果。")
                values.setflags(write=False)
                fields[(level_type, level, short_name)] = values
            finally:
                codes_release(gid)
    if latitudes is None or longitudes is None or not fields:
        raise RuntimeError(f"GFS GRIB 没有可用的共享格点数据：{path}")
    latitudes.setflags(write=False)
    longitudes.setflags(write=False)
    return _DecodedGfsGrid(latitudes=latitudes, longitudes=longitudes, fields=fields)


def gfs_diagnostic_availability(grid: _DecodedGfsGrid) -> dict[str, object]:
    """Describe optional GFS low-level hydrometeor fields without scoring them.

    NOMADS may omit a selector from the returned GRIB even when the request was
    accepted.  Returning an explicit unavailable entry keeps that distinction
    visible to diagnostic consumers and avoids filling a missing field with a
    synthetic zero.
    """
    available: list[dict[str, object]] = []
    unavailable: list[dict[str, object]] = []
    for level in GFS_DIAGNOSTIC_PRESSURE_LEVELS:
        for variable in GFS_DIAGNOSTIC_HYDROMETEOR_VARIABLES:
            key = ("isobaricInhPa", level, variable.lower())
            values = grid.fields.get(key)
            values_array = None if values is None else np.asarray(values)
            item = {
                "pressure_hpa": level,
                "variable": variable,
                "unit": GFS_DIAGNOSTIC_HYDROMETEOR_UNITS,
            }
            is_available = (
                values_array is not None
                and values_array.ndim == 1
                and len(values_array) == len(grid.latitudes)
                and len(values_array) > 0
                and np.all(np.isfinite(values_array))
            )
            (available if is_available else unavailable).append(item)
    return {
        "schema_version": GFS_DIAGNOSTIC_REQUEST_SCHEMA_VERSION,
        "requested_pressure_levels_hpa": list(GFS_DIAGNOSTIC_PRESSURE_LEVELS),
        "requested_variables": list(GFS_DIAGNOSTIC_HYDROMETEOR_VARIABLES),
        "available": available,
        "unavailable": unavailable,
        "complete": not unavailable,
        "score_consumed": False,
    }


def require_gfs_diagnostic_fields(
    grid: _DecodedGfsGrid,
) -> dict[tuple[int, str], np.ndarray]:
    """Return all requested diagnostic fields or fail with their exact gaps."""
    availability = gfs_diagnostic_availability(grid)
    unavailable = availability["unavailable"]
    if unavailable:
        missing = ", ".join(
            f"{item['variable']}@{item['pressure_hpa']}hPa"
            for item in unavailable
        )
        raise RuntimeError(
            f"GFS diagnostic input is incomplete (schema "
            f"{GFS_DIAGNOSTIC_REQUEST_SCHEMA_VERSION}); unavailable: {missing}"
        )
    return {
        (level, variable): grid.fields[("isobaricInhPa", level, variable.lower())]
        for level in GFS_DIAGNOSTIC_PRESSURE_LEVELS
        for variable in GFS_DIAGNOSTIC_HYDROMETEOR_VARIABLES
    }


def subset_decoded_grid(
    grid: _DecodedGfsGrid,
    bbox: Tuple[float, float, float, float],
) -> _DecodedGfsGrid:
    """Crop any covering decoded grid to one canonical coordinate layout."""
    leftlon, rightlon, toplat, bottomlat = bbox
    mask = (grid.latitudes <= toplat) & (grid.latitudes >= bottomlat)
    if leftlon <= rightlon:
        mask &= (grid.longitudes >= leftlon) & (grid.longitudes <= rightlon)
    else:
        mask &= (grid.longitudes >= leftlon) | (grid.longitudes <= rightlon)
    indices = np.flatnonzero(mask)
    if indices.size == 0:
        raise RuntimeError(f"GFS covering grid does not contain requested bbox {bbox}")

    latitudes = np.asarray(grid.latitudes[indices], dtype=np.float32)
    longitudes = np.asarray(grid.longitudes[indices], dtype=np.float32)
    fields = {
        key: np.asarray(values[indices], dtype=np.float32)
        for key, values in grid.fields.items()
    }
    latitudes.setflags(write=False)
    longitudes.setflags(write=False)
    for values in fields.values():
        values.setflags(write=False)
    return _DecodedGfsGrid(latitudes=latitudes, longitudes=longitudes, fields=fields)


def features_from_decoded_grid(
    grid: _DecodedGfsGrid,
    observer: GeoPoint,
    radius_km: float,
) -> List[GridCellFeature]:
    leftlon, rightlon, toplat, bottomlat = bounding_box(observer, radius_km)
    mask = (grid.latitudes <= toplat) & (grid.latitudes >= bottomlat)
    if leftlon <= rightlon:
        mask &= (grid.longitudes >= leftlon) & (grid.longitudes <= rightlon)
    else:
        mask &= (grid.longitudes >= leftlon) | (grid.longitudes <= rightlon)
    indices = np.flatnonzero(mask)
    if len(indices) == 0:
        return []

    latitudes = grid.latitudes[indices].astype(np.float64, copy=False)
    longitudes = grid.longitudes[indices].astype(np.float64, copy=False)
    distances, bearings = vectorized_distance_and_bearing(observer, latitudes, longitudes)
    surface_visibility = selected_field(grid, "surface", 0, "vis", indices)
    precip_rate = selected_field(grid, "surface", 0, "prate", indices)
    high_cloud = next(
        (
            selected_field(grid, "highCloudLayer", 0, name, indices)
            for name in ("hcc", "hcdc", "tcc", "tcdc")
            if ("highCloudLayer", 0, name) in grid.fields
        ),
        None,
    )

    features: List[GridCellFeature] = []
    for level in PRESSURE_LEVELS:
        geopotential = selected_field(grid, "isobaricInhPa", level, "gh", indices)
        humidity = selected_field(grid, "isobaricInhPa", level, "r", indices)
        if geopotential is None and humidity is None:
            continue
        _validate_vertical_prior_fields(level, geopotential, humidity)
        temperature = selected_field(grid, "isobaricInhPa", level, "t", indices)
        omega = selected_field(grid, "isobaricInhPa", level, "w", indices)
        u_wind = selected_field(grid, "isobaricInhPa", level, "u", indices)
        v_wind = selected_field(grid, "isobaricInhPa", level, "v", indices)
        cloud_cover = next(
            (
                selected_field(grid, "isobaricInhPa", level, name, indices)
                for name in ("tcc", "tcdc")
                if ("isobaricInhPa", level, name) in grid.fields
            ),
            None,
        )
        if level == 300 and high_cloud is not None:
            cloud_cover = (
                high_cloud
                if cloud_cover is None
                else np.where(np.isfinite(cloud_cover), cloud_cover, high_cloud)
            )
        cloud_liquid = selected_field(grid, "isobaricInhPa", level, "clwmr", indices)
        cloud_ice = selected_field(grid, "isobaricInhPa", level, "icmr", indices)
        for index in range(len(indices)):
            features.append(
                GridCellFeature(
                    lat=float(latitudes[index]),
                    lon=float(longitudes[index]),
                    distance_km=float(distances[index]),
                    bearing_deg=float(bearings[index]),
                    pressure_hpa=level,
                    geopotential_height_m=float(geopotential[index]) if geopotential is not None else 0.0,
                    rh_percent=float(humidity[index]) if humidity is not None else 0.0,
                    temperature_k=float(temperature[index]) if temperature is not None else None,
                    omega_pa_s=float(omega[index]) if omega is not None else None,
                    u_ms=float(u_wind[index]) if u_wind is not None else None,
                    v_ms=float(v_wind[index]) if v_wind is not None else None,
                    cloud_fraction_percent=float(cloud_cover[index]) if cloud_cover is not None else None,
                    cloud_liquid_kg_kg=float(cloud_liquid[index]) if cloud_liquid is not None else None,
                    cloud_ice_kg_kg=float(cloud_ice[index]) if cloud_ice is not None else None,
                    surface_visibility_m=(
                        float(surface_visibility[index]) if surface_visibility is not None else None
                    ),
                    precip_rate_kg_m2_s=float(precip_rate[index]) if precip_rate is not None else None,
                )
            )
    return features


def native_features_from_decoded_grid(
    grid: _DecodedGfsGrid,
    observer: GeoPoint,
    radius_km: float,
) -> NativeSnapshotFeatures:
    leftlon, rightlon, toplat, bottomlat = bounding_box(observer, radius_km)
    mask = (grid.latitudes <= toplat) & (grid.latitudes >= bottomlat)
    if leftlon <= rightlon:
        mask &= (grid.longitudes >= leftlon) & (grid.longitudes <= rightlon)
    else:
        mask &= (grid.longitudes >= leftlon) | (grid.longitudes <= rightlon)
    indices = np.flatnonzero(mask)
    if len(indices) == 0:
        return NativeSnapshotFeatures(
            NativeFeatureBuffer.from_numpy(np.empty((0, FIELD_COUNT), dtype=np.float64))
        )

    latitudes = grid.latitudes[indices].astype(np.float64, copy=False)
    longitudes = grid.longitudes[indices].astype(np.float64, copy=False)
    distances, bearings = vectorized_distance_and_bearing(observer, latitudes, longitudes)
    levels = [
        level
        for level in PRESSURE_LEVELS
        if ("isobaricInhPa", level, "gh") in grid.fields
        or ("isobaricInhPa", level, "r") in grid.fields
    ]
    rows = np.full((len(levels) * len(indices), FIELD_COUNT), np.nan, dtype=np.float64)
    surface_visibility = selected_field(grid, "surface", 0, "vis", indices)
    precip_rate = selected_field(grid, "surface", 0, "prate", indices)
    high_cloud = next(
        (
            selected_field(grid, "highCloudLayer", 0, name, indices)
            for name in ("hcc", "hcdc", "tcc", "tcdc")
            if ("highCloudLayer", 0, name) in grid.fields
        ),
        None,
    )

    for level_index, level in enumerate(levels):
        row_slice = slice(level_index * len(indices), (level_index + 1) * len(indices))
        rows[row_slice, 0] = latitudes
        rows[row_slice, 1] = longitudes
        rows[row_slice, 2] = distances
        rows[row_slice, 3] = bearings
        rows[row_slice, 4] = level
        geopotential = selected_field(grid, "isobaricInhPa", level, "gh", indices)
        humidity = selected_field(grid, "isobaricInhPa", level, "r", indices)
        if geopotential is None and humidity is None:
            continue
        _validate_vertical_prior_fields(level, geopotential, humidity)
        assign_selected_field(rows, row_slice, 5, grid, level, "gh", indices)
        assign_selected_field(rows, row_slice, 6, grid, level, "r", indices)
        assign_selected_field(rows, row_slice, 7, grid, level, "t", indices)
        assign_selected_field(rows, row_slice, 8, grid, level, "w", indices)
        assign_selected_field(rows, row_slice, 9, grid, level, "u", indices)
        assign_selected_field(rows, row_slice, 10, grid, level, "v", indices)
        cloud_cover = next(
            (
                selected_field(grid, "isobaricInhPa", level, name, indices)
                for name in ("tcc", "tcdc")
                if ("isobaricInhPa", level, name) in grid.fields
            ),
            None,
        )
        if level == 300 and high_cloud is not None:
            cloud_cover = (
                high_cloud
                if cloud_cover is None
                else np.where(np.isfinite(cloud_cover), cloud_cover, high_cloud)
            )
        if cloud_cover is not None:
            rows[row_slice, 11] = cloud_cover
        assign_selected_field(rows, row_slice, 12, grid, level, "clwmr", indices)
        assign_selected_field(rows, row_slice, 13, grid, level, "icmr", indices)
        if surface_visibility is not None:
            rows[row_slice, 18] = surface_visibility
        if precip_rate is not None:
            rows[row_slice, 19] = precip_rate
    return NativeSnapshotFeatures(NativeFeatureBuffer.from_numpy(rows))


def assign_selected_field(
    rows: np.ndarray,
    row_slice: slice,
    column: int,
    grid: _DecodedGfsGrid,
    level: int,
    short_name: str,
    indices: np.ndarray,
    default: float | None = None,
) -> None:
    values = selected_field(grid, "isobaricInhPa", level, short_name, indices)
    if values is not None:
        rows[row_slice, column] = values
    elif default is not None:
        rows[row_slice, column] = default


def _validate_vertical_prior_fields(
    level: int,
    geopotential: np.ndarray | None,
    humidity: np.ndarray | None,
) -> None:
    missing = [
        name
        for name, values in (("gh", geopotential), ("r", humidity))
        if values is None
    ]
    if missing:
        raise ValueError(
            f"GFS vertical prior field missing at {level} hPa: {', '.join(missing)}"
        )
    if not np.all(np.isfinite(geopotential)) or not np.all(np.isfinite(humidity)):
        raise ValueError(
            f"GFS vertical prior field is non-finite at {level} hPa"
        )


def selected_field(
    grid: _DecodedGfsGrid,
    level_type: str,
    level: int,
    short_name: str,
    indices: np.ndarray,
) -> np.ndarray | None:
    values = grid.fields.get((level_type, level, short_name))
    return values[indices] if values is not None else None


def terrain_elevation_at_point(path: Path, point: GeoPoint) -> float:
    """Read the exact GRIB surface orography used by a snapshot.

    This is deliberately a separate read from pressure-level parsing.  A
    pressure height is not terrain, and treating 925/850 hPa as the surface
    silently breaks high-altitude nowcasts.
    """

    grid = load_or_decode_mmap_grid(path)
    values = grid.fields.get(("surface", 0, "orog"))
    if values is None or values.size == 0:
        raise RuntimeError("GFS snapshot has no surface orography for satellite vertical prior")
    distances, _ = vectorized_distance_and_bearing(
        point,
        np.asarray(grid.latitudes, dtype=np.float64),
        np.asarray(grid.longitudes, dtype=np.float64),
    )
    index = int(np.argmin(distances))
    terrain = float(values[index])
    if not math.isfinite(terrain) or terrain < -500.0 or terrain > 9000.0:
        raise RuntimeError("GFS surface orography is invalid")
    return terrain


def vectorized_distance_and_bearing(
    observer: GeoPoint,
    latitudes: np.ndarray,
    longitudes: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    observer_lat = math.radians(observer.lat)
    observer_lon = math.radians(observer.lon)
    target_lats = np.radians(latitudes)
    target_lons = np.radians(longitudes)
    delta_lat = target_lats - observer_lat
    delta_lon = (target_lons - observer_lon + math.pi) % (2.0 * math.pi) - math.pi
    a = np.sin(delta_lat / 2.0) ** 2 + math.cos(observer_lat) * np.cos(target_lats) * np.sin(delta_lon / 2.0) ** 2
    distances = EARTH_RADIUS_KM * 2.0 * np.arctan2(np.sqrt(a), np.sqrt(np.maximum(0.0, 1.0 - a)))
    y = np.sin(delta_lon) * np.cos(target_lats)
    x = math.cos(observer_lat) * np.sin(target_lats) - math.sin(observer_lat) * np.cos(target_lats) * np.cos(delta_lon)
    bearings = (np.degrees(np.arctan2(y, x)) + 360.0) % 360.0
    return distances, bearings


def canonical_subset_bbox(
    bbox: Tuple[float, float, float, float],
    grid_degrees: float = 0.25,
) -> Tuple[float, float, float, float]:
    leftlon, rightlon, toplat, bottomlat = bbox
    leftlon = max(-180.0, math.floor(leftlon / grid_degrees) * grid_degrees)
    rightlon = min(180.0, math.ceil(rightlon / grid_degrees) * grid_degrees)
    toplat = min(90.0, math.ceil(toplat / grid_degrees) * grid_degrees)
    bottomlat = max(-90.0, math.floor(bottomlat / grid_degrees) * grid_degrees)
    return leftlon, rightlon, toplat, bottomlat


def bbox_contains(
    outer: Tuple[float, float, float, float],
    inner: Tuple[float, float, float, float],
) -> bool:
    outer_left, outer_right, outer_top, outer_bottom = outer
    inner_left, inner_right, inner_top, inner_bottom = inner
    return (
        outer_left <= inner_left
        and outer_right >= inner_right
        and outer_top >= inner_top
        and outer_bottom <= inner_bottom
    )
